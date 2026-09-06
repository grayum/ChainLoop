"""Ordered, append-only database migrations for ChainLoop.

Once a migration version/name has shipped it must never be changed. New schema
work is added as a new entry at the end of ``MIGRATIONS``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.schema import MetaData


CURRENT_SCHEMA_VERSION = 2
LEDGER_TABLE = "schema_migrations"
LEGACY_BACKFILL_MARKER = "v0.5.0-data-backfill"


class MigrationError(RuntimeError):
    """A safe, operator-facing migration failure."""


class MigrationStateError(MigrationError):
    """The database cannot be classified or safely migrated."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[Connection], None]
    validate: Callable[[Connection], None]


LEGACY_REQUIRED_COLUMNS = {
    "people": {"id", "name"},
    "chain_specs": {"id", "name", "speeds", "link_count", "manufacturer", "model"},
    "bikes": {
        "id", "person_id", "name", "strava_gear_id", "chain_spec_id",
        "warning_km", "change_km", "overdue_km",
    },
    "wax_products": {"id", "name", "notes"},
    "chains": {
        "id", "code", "bike_id", "chain_spec_id", "status", "first_used_at",
        "total_km", "km_since_wax", "current_wear_percent", "last_wear_at", "retired_at",
    },
    "activities": {
        "id", "source", "external_id", "occurred_at", "distance_km", "bike_id",
        "processed", "excluded", "raw_gear_id", "raw_name",
    },
    "events": {
        "id", "created_at", "event_type", "chain_id", "bike_id", "activity_id",
        "distance_km", "note", "metadata_json",
    },
    "wear_measurements": {"id", "chain_id", "measured_at", "wear_percent", "timing", "note"},
    "wax_events": {
        "id", "chain_id", "wax_product_id", "applied_at", "km_since_previous_wax", "note",
    },
}

CURRENT_REQUIRED_COLUMNS = {
    **LEGACY_REQUIRED_COLUMNS,
    "bikes": LEGACY_REQUIRED_COLUMNS["bikes"] | {"tracking_start_at"},
    "wax_products": LEGACY_REQUIRED_COLUMNS["wax_products"] | {"archived"},
    "activities": LEGACY_REQUIRED_COLUMNS["activities"] | {"credited_chain_id"},
    "wear_measurements": LEGACY_REQUIRED_COLUMNS["wear_measurements"] | {"total_km_at_measurement"},
    "strava_tokens": {"id", "athlete_id", "access_token", "refresh_token", "expires_at"},
    "strava_gears": {"gear_id", "name", "distance_km", "primary", "updated_at"},
    "notification_states": {
        "id", "chain_id", "wax_cycle_key", "level", "state", "created_at",
    },
    "sync_runs": {
        "id", "started_at", "completed_at", "trigger", "success", "imported",
        "processed", "added_km", "error",
    },
    "migration_markers": {"key", "applied_at"},
}

KNOWN_APPLICATION_TABLES = set(CURRENT_REQUIRED_COLUMNS) | {LEDGER_TABLE}


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tables(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


def _columns(connection: Connection, table: str) -> set[str]:
    return {column["name"] for column in inspect(connection).get_columns(table)}


def _validate_columns(connection: Connection, expected: dict[str, set[str]], label: str) -> None:
    tables = _tables(connection)
    missing_tables = sorted(set(expected) - tables)
    if missing_tables:
        raise MigrationStateError(f"{label} is missing required tables: {', '.join(missing_tables)}")
    for table, required in expected.items():
        missing = sorted(required - _columns(connection, table))
        if missing:
            raise MigrationStateError(
                f"{label} table {table} is missing required columns: {', '.join(missing)}"
            )


def _has_unique_columns(connection: Connection, table: str, columns: tuple[str, ...]) -> bool:
    inspector = inspect(connection)
    wanted = list(columns)
    if any(item.get("column_names") == wanted for item in inspector.get_unique_constraints(table)):
        return True
    return any(
        item.get("unique") and item.get("column_names") == wanted
        for item in inspector.get_indexes(table)
    )


def _validate_current_schema(connection: Connection) -> None:
    _validate_columns(connection, CURRENT_REQUIRED_COLUMNS, "Current schema")
    if not _has_unique_columns(connection, "activities", ("source", "external_id")):
        raise MigrationStateError("Current schema lacks unique activity source/external_id protection")
    if not _has_unique_columns(
        connection, "notification_states", ("chain_id", "wax_cycle_key", "level")
    ):
        raise MigrationStateError("Current schema lacks notification cycle uniqueness protection")
    chain_indexes = {item["name"] for item in inspect(connection).get_indexes("chains")}
    if "uq_chains_one_in_use_per_bike" not in chain_indexes:
        raise MigrationStateError("Current schema lacks one-active-chain-per-bike protection")


def _validate_legacy_candidate(connection: Connection) -> None:
    tables = _tables(connection)
    unknown = sorted(tables - KNOWN_APPLICATION_TABLES)
    if unknown:
        raise MigrationStateError(
            "Pre-ledger database contains unknown tables and cannot be adopted safely: "
            + ", ".join(unknown)
        )
    _validate_columns(connection, LEGACY_REQUIRED_COLUMNS, "Legacy schema")

    duplicates = connection.execute(text(
        "SELECT source, external_id FROM activities "
        "GROUP BY source, external_id HAVING count(*) > 1 LIMIT 1"
    )).first()
    if duplicates:
        raise MigrationStateError(
            "Legacy database contains duplicate activity source/external_id values"
        )
    active_duplicates = connection.execute(text(
        "SELECT bike_id FROM chains WHERE status='IN_USE' "
        "GROUP BY bike_id HAVING count(*) > 1 LIMIT 1"
    )).first()
    if active_duplicates:
        raise MigrationStateError("Legacy database has more than one active chain for a bike")

    if "migration_markers" in tables:
        marker_columns = _columns(connection, "migration_markers")
        if marker_columns != {"key", "applied_at"}:
            raise MigrationStateError("Legacy migration_markers table has an unsupported shape")
        marker = connection.scalar(text(
            "SELECT count(*) FROM migration_markers WHERE key=:key"
        ), {"key": LEGACY_BACKFILL_MARKER})
        if marker and not _version_one_schema_is_complete(connection):
            raise MigrationStateError(
                "Legacy backfill marker exists but its required schema is incomplete"
            )


def _version_one_schema_is_complete(connection: Connection) -> bool:
    version_one = dict(CURRENT_REQUIRED_COLUMNS)
    version_one["wax_products"] = LEGACY_REQUIRED_COLUMNS["wax_products"]
    try:
        _validate_columns(connection, version_one, "Version 1 schema")
        if not _has_unique_columns(connection, "activities", ("source", "external_id")):
            return False
        if not _has_unique_columns(
            connection, "notification_states", ("chain_id", "wax_cycle_key", "level")
        ):
            return False
        return "uq_chains_one_in_use_per_bike" in {
            item["name"] for item in inspect(connection).get_indexes("chains")
        }
    except MigrationStateError:
        return False


def _create_version_one_tables(connection: Connection) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS strava_tokens (
            id INTEGER NOT NULL PRIMARY KEY, athlete_id VARCHAR NOT NULL,
            access_token VARCHAR NOT NULL, refresh_token VARCHAR NOT NULL, expires_at INTEGER NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS strava_gears (
            gear_id VARCHAR NOT NULL PRIMARY KEY, name VARCHAR, distance_km FLOAT,
            "primary" BOOLEAN, updated_at DATETIME NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS notification_states (
            id INTEGER NOT NULL PRIMARY KEY, chain_id INTEGER NOT NULL,
            wax_cycle_key VARCHAR NOT NULL, level VARCHAR NOT NULL, state VARCHAR NOT NULL,
            created_at DATETIME NOT NULL,
            CONSTRAINT uq_notification_cycle_level UNIQUE (chain_id, wax_cycle_key, level)
        )""",
        """CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER NOT NULL PRIMARY KEY, started_at DATETIME NOT NULL, completed_at DATETIME,
            trigger VARCHAR NOT NULL, success BOOLEAN NOT NULL, imported INTEGER NOT NULL,
            processed INTEGER NOT NULL, added_km FLOAT NOT NULL, error VARCHAR
        )""",
        """CREATE TABLE IF NOT EXISTS migration_markers (
            key VARCHAR NOT NULL PRIMARY KEY, applied_at DATETIME NOT NULL
        )""",
    )
    for statement in statements:
        connection.exec_driver_sql(statement)


def _add_column(connection: Connection, table: str, column: str, definition: str) -> None:
    if column not in _columns(connection, table):
        connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _wax_cycle_key(connection: Connection, chain_id: int, at: str | None = None) -> str:
    sql = "SELECT id FROM wax_events WHERE chain_id=:chain_id"
    params: dict[str, object] = {"chain_id": chain_id}
    if at is not None:
        sql += " AND applied_at <= :at"
        params["at"] = at
    sql += " ORDER BY applied_at DESC, id DESC LIMIT 1"
    wax_id = connection.scalar(text(sql), params)
    return f"wax:{wax_id}" if wax_id is not None else "wax:none"


def _apply_legacy_backfill(connection: Connection) -> None:
    marker = connection.scalar(
        text("SELECT count(*) FROM migration_markers WHERE key=:key"),
        {"key": LEGACY_BACKFILL_MARKER},
    )
    if marker:
        return

    activities = connection.execute(text(
        "SELECT id FROM activities WHERE processed=1 AND credited_chain_id IS NULL"
    )).all()
    for (activity_id,) in activities:
        chain_id = connection.scalar(text(
            "SELECT chain_id FROM events WHERE activity_id=:activity_id "
            "AND event_type='RIDE_ADDED' AND chain_id IS NOT NULL ORDER BY id DESC LIMIT 1"
        ), {"activity_id": activity_id})
        if chain_id is not None:
            connection.execute(text(
                "UPDATE activities SET credited_chain_id=:chain_id WHERE id=:activity_id"
            ), {"chain_id": chain_id, "activity_id": activity_id})

    mapping = {
        "PUSHOVER_WARNING_SENT": "WARNING",
        "PUSHOVER_CHANGE_SENT": "CHANGE",
        "PUSHOVER_OVERDUE_SENT": "OVERDUE",
        "PUSHOVER_NO_SPARE_SENT": "NO_SPARE",
    }
    for event_type, level in mapping.items():
        events = connection.execute(text(
            "SELECT chain_id, created_at FROM events "
            "WHERE event_type=:event_type AND chain_id IS NOT NULL"
        ), {"event_type": event_type}).all()
        for chain_id, created_at in events:
            connection.execute(text(
                "INSERT OR IGNORE INTO notification_states "
                "(chain_id, wax_cycle_key, level, state, created_at) "
                "VALUES (:chain_id, :cycle, :level, 'SENT', :created_at)"
            ), {
                "chain_id": chain_id,
                "cycle": _wax_cycle_key(connection, chain_id, str(created_at)),
                "level": level,
                "created_at": created_at,
            })

    chains = connection.execute(text(
        "SELECT chains.id, chains.km_since_wax, bikes.warning_km, bikes.change_km, "
        "bikes.overdue_km FROM chains JOIN bikes ON bikes.id=chains.bike_id"
    )).all()
    for chain_id, distance, warning, change, overdue in chains:
        cycle = _wax_cycle_key(connection, chain_id)
        for level, threshold in (("WARNING", warning), ("CHANGE", change), ("OVERDUE", overdue)):
            if distance >= threshold:
                connection.execute(text(
                    "INSERT OR IGNORE INTO notification_states "
                    "(chain_id, wax_cycle_key, level, state, created_at) "
                    "VALUES (:chain_id, :cycle, :level, 'SUPPRESSED', :created_at)"
                ), {
                    "chain_id": chain_id, "cycle": cycle, "level": level,
                    "created_at": _utc_timestamp(),
                })

    connection.execute(text(
        "INSERT INTO migration_markers (key, applied_at) VALUES (:key, :applied_at)"
    ), {"key": LEGACY_BACKFILL_MARKER, "applied_at": _utc_timestamp()})


def _apply_v1(connection: Connection) -> None:
    _create_version_one_tables(connection)
    _add_column(connection, "bikes", "tracking_start_at", "DATETIME")
    _add_column(connection, "activities", "credited_chain_id", "INTEGER")
    _add_column(connection, "wear_measurements", "total_km_at_measurement", "FLOAT")
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_activities_source_external_id "
        "ON activities(source, external_id)"
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_chains_one_in_use_per_bike "
        "ON chains(bike_id) WHERE status='IN_USE'"
    )
    _apply_legacy_backfill(connection)


def _validate_v1(connection: Connection) -> None:
    if not _version_one_schema_is_complete(connection):
        raise MigrationStateError("Version 1 schema postconditions were not satisfied")
    marker = connection.scalar(text(
        "SELECT count(*) FROM migration_markers WHERE key=:key"
    ), {"key": LEGACY_BACKFILL_MARKER})
    if marker != 1:
        raise MigrationStateError("Version 1 legacy data migration marker is missing or duplicated")


def _apply_v2(connection: Connection) -> None:
    _add_column(connection, "wax_products", "archived", "BOOLEAN NOT NULL DEFAULT 0")


def _validate_v2(connection: Connection) -> None:
    _validate_current_schema(connection)


# Append only. Never edit a shipped version/name; add the next version instead.
MIGRATIONS = (
    Migration(1, "v0.5.0-schema-and-data", _apply_v1, _validate_v1),
    Migration(2, "v0.7.0-schema", _apply_v2, _validate_v2),
)


def _validate_registry() -> None:
    versions = [migration.version for migration in MIGRATIONS]
    if versions != list(range(1, CURRENT_SCHEMA_VERSION + 1)):
        raise RuntimeError("Migration registry must be contiguous and end at CURRENT_SCHEMA_VERSION")
    if len({migration.name for migration in MIGRATIONS}) != len(MIGRATIONS):
        raise RuntimeError("Migration registry names must be unique")


_validate_registry()


def _create_ledger(connection: Connection) -> None:
    connection.exec_driver_sql("""
        CREATE TABLE schema_migrations (
            version INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR NOT NULL UNIQUE,
            applied_at DATETIME NOT NULL
        )
    """)


def _ledger_rows(connection: Connection) -> list[tuple[int, str]]:
    return [tuple(row) for row in connection.execute(text(
        "SELECT version, name FROM schema_migrations ORDER BY version"
    )).all()]


def _validate_ledger(connection: Connection) -> list[tuple[int, str]]:
    columns = _columns(connection, LEDGER_TABLE)
    if columns != {"version", "name", "applied_at"}:
        raise MigrationStateError("schema_migrations has an unsupported shape")
    rows = _ledger_rows(connection)
    if rows and rows[-1][0] > CURRENT_SCHEMA_VERSION:
        raise MigrationStateError(
            f"Database schema version: {rows[-1][0]}\n"
            f"Maximum supported version: {CURRENT_SCHEMA_VERSION}\n"
            "Refusing to start because this database was migrated by a newer ChainLoop version."
        )
    expected = [(migration.version, migration.name) for migration in MIGRATIONS[:len(rows)]]
    if rows != expected:
        raise MigrationStateError(
            "schema_migrations is missing, out of order, or contains an unexpected migration name"
        )
    return rows


def _record_migration(connection: Connection, migration: Migration) -> None:
    connection.execute(text(
        "INSERT INTO schema_migrations (version, name, applied_at) "
        "VALUES (:version, :name, :applied_at)"
    ), {
        "version": migration.version,
        "name": migration.name,
        "applied_at": _utc_timestamp(),
    })


def _run_one(connection: Connection, migration: Migration) -> None:
    try:
        migration.apply(connection)
        migration.validate(connection)
        _record_migration(connection, migration)
    except MigrationError as exc:
        raise MigrationError(
            f"Migration {migration.version} ({migration.name}) failed: {exc}"
        ) from exc
    except IntegrityError as exc:
        raise MigrationError(
            f"Migration {migration.version} ({migration.name}) failed: "
            "existing data violates a required database constraint"
        ) from exc
    except SQLAlchemyError as exc:
        reason = str(getattr(exc, "orig", "database operation failed")).splitlines()[0]
        raise MigrationError(
            f"Migration {migration.version} ({migration.name}) failed: {reason}"
        ) from exc


def _bootstrap_empty(connection: Connection, metadata: MetaData) -> None:
    metadata.create_all(connection)
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX uq_chains_one_in_use_per_bike "
        "ON chains(bike_id) WHERE status='IN_USE'"
    )
    connection.execute(text(
        "INSERT INTO migration_markers (key, applied_at) VALUES (:key, :applied_at)"
    ), {"key": LEGACY_BACKFILL_MARKER, "applied_at": _utc_timestamp()})
    _validate_current_schema(connection)
    _create_ledger(connection)
    for migration in MIGRATIONS:
        migration.validate(connection)
        _record_migration(connection, migration)


def _adopt_pre_ledger(connection: Connection) -> None:
    _validate_legacy_candidate(connection)
    for migration in MIGRATIONS:
        try:
            migration.apply(connection)
            migration.validate(connection)
        except MigrationError as exc:
            raise MigrationError(
                f"Migration {migration.version} ({migration.name}) failed during adoption: {exc}"
            ) from exc
        except IntegrityError as exc:
            raise MigrationError(
                f"Migration {migration.version} ({migration.name}) failed during adoption: "
                "existing data violates a required database constraint"
            ) from exc
        except SQLAlchemyError as exc:
            reason = str(getattr(exc, "orig", "database operation failed")).splitlines()[0]
            raise MigrationError(
                f"Migration {migration.version} ({migration.name}) failed during adoption: {reason}"
            ) from exc
    _validate_current_schema(connection)
    _create_ledger(connection)
    for migration in MIGRATIONS:
        migration.validate(connection)
        _record_migration(connection, migration)


def migrate_database(engine: Engine, metadata: MetaData) -> int:
    """Bring one SQLite database to CURRENT_SCHEMA_VERSION under a write lock."""
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA busy_timeout=30000")
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            tables = _tables(connection)
            if LEDGER_TABLE in tables:
                rows = _validate_ledger(connection)  # newer-version check precedes mutation
                for migration in MIGRATIONS[len(rows):]:
                    _run_one(connection, migration)
                _validate_current_schema(connection)
            elif not tables:
                _bootstrap_empty(connection, metadata)
            else:
                _adopt_pre_ledger(connection)
            connection.exec_driver_sql("COMMIT")
        except Exception:
            connection.exec_driver_sql("ROLLBACK")
            raise
    return CURRENT_SCHEMA_VERSION


def database_schema_version(engine: Engine) -> int | None:
    """Return the ledger version, or None for a database without a ledger."""
    with engine.connect() as connection:
        if LEDGER_TABLE not in _tables(connection):
            return None
        rows = _validate_ledger(connection)
        return rows[-1][0] if rows else 0
