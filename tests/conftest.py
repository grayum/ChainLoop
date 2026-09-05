import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest


# This is established before test modules import app.main. The application checks
# both variables before constructing an engine or running import-time migrations.
TEST_DATABASE_ROOT = Path(tempfile.mkdtemp(prefix="chainloop-pytest-2"))
os.environ["CHAINLOOP_TESTING"] = "1"
os.environ["CHAINLOOP_TEST_TMPDIR"] = str(TEST_DATABASE_ROOT)
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE_ROOT / 'import.db'}"
os.environ["STRAVA_AUTO_SYNC"] = "false"


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(TEST_DATABASE_ROOT, ignore_errors=True)


@pytest.fixture
def database_path() -> Path:
    return TEST_DATABASE_ROOT / f"test-{uuid.uuid4().hex}.db"


@pytest.fixture
def db_engine(database_path):
    from app import main

    engine = main.create_database_engine(f"sqlite:///{database_path}")
    main.ensure_schema(engine)
    main.post_schema_data_migrations(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def configured_chain(db_engine):
    from sqlalchemy.orm import Session
    from app import main

    with Session(db_engine) as db:
        person = main.Person(name="Example Rider")
        spec = main.ChainSpec(name="Example 12-speed", speeds=12, link_count=116, manufacturer=None, model=None)
        db.add_all([person, spec])
        db.flush()
        bike = main.Bike(
            person_id=person.id,
            name="Example Bike",
            strava_gear_id="gear-example",
            chain_spec_id=spec.id,
            warning_km=500,
            change_km=600,
            overdue_km=800,
            tracking_start_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        wax = main.WaxProduct(name="Example Wax", notes=None, archived=False)
        db.add_all([bike, wax])
        db.flush()
        chain = main.Chain(
            code="EXAMPLE-01",
            bike_id=bike.id,
            chain_spec_id=spec.id,
            status="IN_USE",
            first_used_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            total_km=0,
            km_since_wax=0,
            current_wear_percent=None,
            last_wear_at=None,
            retired_at=None,
        )
        db.add(chain)
        db.flush()
        db.add(main.WaxEvent(
            chain_id=chain.id,
            wax_product_id=wax.id,
            applied_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            km_since_previous_wax=0,
            note=None,
        ))
        db.commit()
        return {"bike_id": bike.id, "chain_id": chain.id, "wax_id": wax.id}

