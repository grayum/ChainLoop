import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app import main
from app.migrations import MigrationError


@pytest.mark.parametrize("database_url", [
    "sqlite:////data/chainloop.db",
    "sqlite:///relative.db",
    "sqlite:///file:/data/chainloop.db?uri=true",
    "sqlite:///file:relative.db?uri=true",
    "postgresql://localhost/chainloop",
])
def test_test_mode_rejects_database_outside_temporary_root(database_url):
    with pytest.raises(RuntimeError):
        main.create_database_engine(database_url)


def test_test_mode_accepts_file_uri_inside_temporary_root(database_path):
    engine = main.create_database_engine(f"sqlite:///file:{database_path}?uri=true")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1
    assert database_path.exists()
    engine.dispose()


def test_import_does_not_create_configured_database(database_path):
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database_path}"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.main; assert not __import__('pathlib').Path(" + repr(str(database_path)) + ").exists()",
        ],
        cwd=Path(__file__).parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not database_path.exists()


def test_lifespan_initializes_configured_temporary_database(database_path):
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database_path}"
    env["STRAVA_AUTO_SYNC"] = "false"
    script = """
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    assert client.get('/health').status_code == 200
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    engine = main.create_database_engine(f"sqlite:///{database_path}")
    assert "chains" in inspect(engine).get_table_names()
    assert "migration_markers" in inspect(engine).get_table_names()
    assert "schema_migrations" in inspect(engine).get_table_names()
    engine.dispose()


def test_migration_failure_prevents_scheduler_start(monkeypatch):
    scheduler_started = False

    def fail_migration(*args):
        raise MigrationError("Migration 2 (v0.7.0-schema) failed: test failure")

    def track_scheduler():
        nonlocal scheduler_started
        scheduler_started = True

    monkeypatch.setattr(main, "migrate_database", fail_migration)
    monkeypatch.setattr(main, "setup_scheduler", track_scheduler)

    with pytest.raises(MigrationError, match="Migration 2"):
        with TestClient(main.app):
            pass
    assert scheduler_started is False


def test_legacy_filename_is_moved_only_when_destination_is_absent(tmp_path, monkeypatch):
    legacy = tmp_path / "chain_tracker.db"
    current = tmp_path / "chainloop.db"
    legacy.write_bytes(b"legacy")

    def mapped_path(value):
        return legacy if value == "/data/chain_tracker.db" else current

    monkeypatch.setattr(main, "Path", mapped_path)
    main.migrate_legacy_database(main.DEFAULT_DATABASE_URL)

    assert not legacy.exists()
    assert current.read_bytes() == b"legacy"


def test_legacy_filename_is_not_moved_over_existing_destination(tmp_path, monkeypatch):
    legacy = tmp_path / "chain_tracker.db"
    current = tmp_path / "chainloop.db"
    legacy.write_bytes(b"legacy")
    current.write_bytes(b"current")

    def mapped_path(value):
        return legacy if value == "/data/chain_tracker.db" else current

    monkeypatch.setattr(main, "Path", mapped_path)
    main.migrate_legacy_database(main.DEFAULT_DATABASE_URL)

    assert legacy.read_bytes() == b"legacy"
    assert current.read_bytes() == b"current"
