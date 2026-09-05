import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import main


def test_activity_source_and_external_id_are_unique(db_engine):
    with Session(db_engine) as db:
        values = dict(source="strava", external_id="same", occurred_at=main.now(), distance_km=1,
                      bike_id=None, credited_chain_id=None, processed=False, excluded=False,
                      raw_gear_id=None, raw_name=None)
        db.add_all([main.Activity(**values), main.Activity(**values)])
        with pytest.raises(IntegrityError):
            db.commit()


def test_only_one_chain_can_be_in_use_per_bike(db_engine, configured_chain):
    with Session(db_engine) as db:
        original = db.get(main.Chain, configured_chain["chain_id"])
        db.add(main.Chain(code="EXAMPLE-02", bike_id=original.bike_id,
                          chain_spec_id=original.chain_spec_id, status="IN_USE",
                          first_used_at=None, total_km=0, km_since_wax=0,
                          current_wear_percent=None, last_wear_at=None, retired_at=None))
        with pytest.raises(IntegrityError):
            db.commit()

