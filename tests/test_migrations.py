import concurrent.futures
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app import main
from app.migrations import (
    CURRENT_SCHEMA_VERSION,
    LEGACY_BACKFILL_MARKER,
    MIGRATIONS,
    MigrationError,
    database_schema_version,
)


def load_v04(engine) -> None:
    fixture = Path(__file__).parent / "fixtures" / "v04.sql"
    with engine.begin() as connection:
        connection.connection.executescript(fixture.read_text())


def create_current_pre_ledger(engine) -> None:
    main.Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_chains_one_in_use_per_bike "
            "ON chains(bike_id) WHERE status='IN_USE'"
        )
        connection.execute(text(
            "INSERT INTO migration_markers (key, applied_at) VALUES (:key, CURRENT_TIMESTAMP)"
        ), {"key": LEGACY_BACKFILL_MARKER})


def ledger_rows(engine):
    with engine.connect() as connection:
        return connection.execute(text(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        )).all()


def test_fresh_database_bootstraps_current_schema(database_path):
    engine = main.create_database_engine(f"sqlite:///{database_path}")

    assert main.migrate_database(engine, main.Base.metadata) == CURRENT_SCHEMA_VERSION

    assert database_schema_version(engine) == CURRENT_SCHEMA_VERSION
    assert ledger_rows(engine) == [(migration.version, migration.name) for migration in MIGRATIONS]
    assert set(inspect(engine).get_table_names()) >= {"chains", "migration_markers", "schema_migrations"}
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT count(*) FROM migration_markers WHERE key=:key"
        ), {"key": LEGACY_BACKFILL_MARKER}) == 1
    engine.dispose()


def test_v04_fixture_is_upgraded_additively(database_path):
    engine = main.create_database_engine(f"sqlite:///{database_path}")
    load_v04(engine)

    main.migrate_database(engine, main.Base.metadata)

    inspector = inspect(engine)
    assert "tracking_start_at" in {column["name"] for column in inspector.get_columns("bikes")}
    assert "credited_chain_id" in {column["name"] for column in inspector.get_columns("activities")}
    assert "total_km_at_measurement" in {column["name"] for column in inspector.get_columns("wear_measurements")}
    assert "archived" in {column["name"] for column in inspector.get_columns("wax_products")}
    with Session(engine) as db:
        chain = db.get(main.Chain, 1)
        activity = db.get(main.Activity, 1)
        assert chain.total_km == 650
        assert chain.km_since_wax == 650
        assert activity.credited_chain_id == 1
        assert db.get(main.MigrationMarker, LEGACY_BACKFILL_MARKER)
        states = db.scalars(main.select(main.NotificationState)).all()
        assert {(state.level, state.state) for state in states} >= {
            ("WARNING", "SENT"),
            ("CHANGE", "SUPPRESSED"),
        }
    assert database_schema_version(engine) == CURRENT_SCHEMA_VERSION
    engine.dispose()


def test_current_pre_ledger_database_is_adopted_without_rewriting_markers(database_path):
    engine = main.create_database_engine(f"sqlite:///{database_path}")
    create_current_pre_ledger(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO migration_markers (key, applied_at) VALUES ('operator-history', CURRENT_TIMESTAMP)"
        ))

    main.migrate_database(engine, main.Base.metadata)

    with engine.connect() as connection:
        markers = set(connection.scalars(text("SELECT key FROM migration_markers")))
    assert markers == {LEGACY_BACKFILL_MARKER, "operator-history"}
    assert database_schema_version(engine) == CURRENT_SCHEMA_VERSION
    engine.dispose()


def test_repeated_startup_is_idempotent(database_path):
    engine = main.create_database_engine(f"sqlite:///{database_path}")
    main.migrate_database(engine, main.Base.metadata)
    first = ledger_rows(engine)

    main.migrate_database(engine, main.Base.metadata)

    assert ledger_rows(engine) == first
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM migration_markers")) == 1
    engine.dispose()


def test_interrupted_migration_with_applied_column_resumes(database_path):
    engine = main.create_database_engine(f"sqlite:///{database_path}")
    load_v04(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE activities ADD COLUMN credited_chain_id INTEGER")

    main.migrate_database(engine, main.Base.metadata)

    assert database_schema_version(engine) == CURRENT_SCHEMA_VERSION
    assert "credited_chain_id" in {
        column["name"] for column in inspect(engine).get_columns("activities")
    }
    engine.dispose()


def test_ledger_migration_with_applied_ddl_but_missing_version_resumes(database_path):
    engine = main.create_database_engine(f"sqlite:///{database_path}")
    main.migrate_database(engine, main.Base.metadata)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM schema_migrations WHERE version=2"))

    main.migrate_database(engine, main.Base.metadata)

    assert database_schema_version(engine) == CURRENT_SCHEMA_VERSION
    assert ledger_rows(engine)[-1] == (2, "v0.7.0-schema")
    engine.dispose()


def test_partially_committed_backfill_without_marker_is_safe_to_rerun(database_path):
    engine = main.create_database_engine(f"sqlite:///{database_path}")
    load_v04(engine)
    main.migrate_database(engine, main.Base.metadata)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM schema_migrations"))
        connection.execute(text(
            "DELETE FROM migration_markers WHERE key=:key"
        ), {"key": LEGACY_BACKFILL_MARKER})
        connection.exec_driver_sql("DROP TABLE schema_migrations")

    main.migrate_database(engine, main.Base.metadata)

    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT count(*) FROM notification_states "
            "WHERE chain_id=1 AND wax_cycle_key='wax:1' AND level='WARNING'"
        )) == 1
        assert connection.scalar(text(
            "SELECT count(*) FROM migration_markers WHERE key=:key"
        ), {"key": LEGACY_BACKFILL_MARKER}) == 1
    assert database_schema_version(engine) == CURRENT_SCHEMA_VERSION
    engine.dispose()


def test_gapped_ledger_is_rejected(database_path):
    engine = main.create_database_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY, name VARCHAR NOT NULL UNIQUE,
                applied_at DATETIME NOT NULL
            )
        """)
        connection.execute(text(
            "INSERT INTO schema_migrations VALUES (2, 'v0.7.0-schema', CURRENT_TIMESTAMP)"
        ))

    with pytest.raises(MigrationError, match="missing, out of order"):
        main.migrate_database(engine, main.Base.metadata)

    assert inspect(engine).get_table_names() == ["schema_migrations"]
    engine.dispose()


def test_newer_schema_is_rejected_before_other_schema_mutation(database_path):
    engine = main.create_database_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY, name VARCHAR NOT NULL UNIQUE,
                applied_at DATETIME NOT NULL
            )
        """)
        connection.execute(text(
            "INSERT INTO schema_migrations VALUES (:version, 'future-version', CURRENT_TIMESTAMP)"
        ), {"version": CURRENT_SCHEMA_VERSION + 1})

    with pytest.raises(MigrationError) as caught:
        main.migrate_database(engine, main.Base.metadata)

    message = str(caught.value)
    assert f"Database schema version: {CURRENT_SCHEMA_VERSION + 1}" in message
    assert f"Maximum supported version: {CURRENT_SCHEMA_VERSION}" in message
    assert "migrated by a newer ChainLoop version" in message
    assert inspect(engine).get_table_names() == ["schema_migrations"]
    engine.dispose()


def test_concurrent_startup_serializes_and_rechecks_ledger(database_path):
    database_url = f"sqlite:///{database_path}"

    def migrate_once():
        worker_engine = main.create_database_engine(database_url)
        try:
            return main.migrate_database(worker_engine, main.Base.metadata)
        finally:
            worker_engine.dispose()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: migrate_once(), range(2)))

    engine = main.create_database_engine(database_url)
    assert results == [CURRENT_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION]
    assert database_schema_version(engine) == CURRENT_SCHEMA_VERSION
    assert len(ledger_rows(engine)) == CURRENT_SCHEMA_VERSION
    engine.dispose()


def test_marker_claiming_completed_backfill_with_missing_schema_is_rejected(database_path):
    engine = main.create_database_engine(f"sqlite:///{database_path}")
    load_v04(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE migration_markers (
                key VARCHAR PRIMARY KEY, applied_at DATETIME NOT NULL
            )
        """)
        connection.execute(text(
            "INSERT INTO migration_markers VALUES (:key, CURRENT_TIMESTAMP)"
        ), {"key": LEGACY_BACKFILL_MARKER})

    with pytest.raises(MigrationError, match="marker exists but its required schema is incomplete"):
        main.migrate_database(engine, main.Base.metadata)

    assert "schema_migrations" not in inspect(engine).get_table_names()
    engine.dispose()
