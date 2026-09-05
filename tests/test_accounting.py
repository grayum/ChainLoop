from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app import main


def _activity(db, *, external_id, occurred_at, distance, bike_id):
    activity = main.Activity(source="strava", external_id=external_id,
                             occurred_at=occurred_at, distance_km=distance,
                             bike_id=bike_id, credited_chain_id=None,
                             processed=False, excluded=False,
                             raw_gear_id="gear-example", raw_name="Example Ride")
    db.add(activity)
    db.flush()
    return activity


def test_current_ride_updates_lifetime_and_current_cycle(db_engine, configured_chain):
    with Session(db_engine) as db:
        chain = db.get(main.Chain, configured_chain["chain_id"])
        bike = db.get(main.Bike, configured_chain["bike_id"])
        activity = _activity(db, external_id="current", occurred_at=datetime(2025, 1, 10, tzinfo=timezone.utc), distance=42.5, bike_id=bike.id)
        assert main.apply_activity_to_chain(db, activity, bike, chain, notify=False)
        db.commit()
        assert chain.total_km == 42.5
        assert chain.km_since_wax == 42.5
        assert activity.credited_chain_id == chain.id


def test_historical_ride_updates_closed_wax_interval(db_engine, configured_chain):
    with Session(db_engine) as db:
        chain = db.get(main.Chain, configured_chain["chain_id"])
        bike = db.get(main.Bike, configured_chain["bike_id"])
        ending_wax = main.WaxEvent(chain_id=chain.id, wax_product_id=configured_chain["wax_id"],
                                    applied_at=datetime(2025, 2, 1, tzinfo=timezone.utc),
                                    km_since_previous_wax=100, note=None)
        db.add(ending_wax); db.flush()
        activity = _activity(db, external_id="historical", occurred_at=datetime(2025, 1, 15, tzinfo=timezone.utc), distance=25, bike_id=bike.id)
        assert main.apply_activity_to_chain(db, activity, bike, chain, notify=False)
        db.commit()
        assert chain.total_km == 25
        assert chain.km_since_wax == 0
        assert ending_wax.km_since_previous_wax == 125


def test_reversing_activity_restores_both_counters(db_engine, configured_chain):
    with Session(db_engine) as db:
        chain = db.get(main.Chain, configured_chain["chain_id"])
        bike = db.get(main.Bike, configured_chain["bike_id"])
        activity = _activity(db, external_id="reverse", occurred_at=datetime(2025, 1, 10, tzinfo=timezone.utc), distance=18, bike_id=bike.id)
        main.apply_activity_to_chain(db, activity, bike, chain, notify=False)
        main.reverse_activity_credit(db, activity)
        db.commit()
        assert chain.total_km == 0
        assert chain.km_since_wax == 0
        assert not activity.processed
        assert activity.credited_chain_id is None


@pytest.mark.xfail(strict=True, reason="Known defect: a historical correction cannot credit a chain that is now retired")
def test_historical_activity_can_be_credited_to_now_retired_chain(db_engine, configured_chain):
    with Session(db_engine) as db:
        chain = db.get(main.Chain, configured_chain["chain_id"])
        bike = db.get(main.Bike, configured_chain["bike_id"])
        chain.status = "RETIRED"
        chain.retired_at = datetime(2025, 3, 1, tzinfo=timezone.utc)
        activity = _activity(db, external_id="retired-history", occurred_at=datetime(2025, 2, 1, tzinfo=timezone.utc), distance=30, bike_id=bike.id)
        assert main.apply_activity_to_chain(db, activity, bike, chain, notify=False)
