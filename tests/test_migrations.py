from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app import main


def test_v04_fixture_is_upgraded_additively(database_path):
    fixture = Path(__file__).parent / "fixtures" / "v04.sql"
    engine = main.create_database_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.connection.executescript(fixture.read_text())

    main.ensure_schema(engine)
    main.post_schema_data_migrations(engine)

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
        assert db.get(main.MigrationMarker, "v0.5.0-data-backfill")
        states = db.scalars(main.select(main.NotificationState)).all()
        assert {(state.level, state.state) for state in states} >= {
            ("WARNING", "SENT"),
            ("CHANGE", "SUPPRESSED"),
        }
    main.post_schema_data_migrations(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM migration_markers")) == 1
    engine.dispose()

