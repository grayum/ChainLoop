import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect

from app import main


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


def test_production_style_import_initializes_configured_temporary_database(database_path):
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database_path}"
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
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
    engine.dispose()
