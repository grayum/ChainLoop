from sqlalchemy import select
from sqlalchemy.orm import Session

import pytest

from app import main


def test_successful_threshold_notification_is_sent_once(db_engine, configured_chain, monkeypatch):
    calls = []
    monkeypatch.setattr(main, "PUSHOVER_APP_TOKEN", "configured")
    monkeypatch.setattr(main, "PUSHOVER_USER_KEY", "configured")
    monkeypatch.setattr(main, "send_pushover", lambda title, message: (calls.append(message) is None, None))
    with Session(db_engine) as db:
        bike = db.get(main.Bike, configured_chain["bike_id"])
        chain = db.get(main.Chain, configured_chain["chain_id"])
        chain.km_since_wax = 510
        main.check_distance_thresholds(db, bike, chain)
        db.commit()
        main.check_distance_thresholds(db, bike, chain)
        db.commit()
        assert len([message for message in calls if "approaching service" in message]) == 1
        state = db.scalar(select(main.NotificationState).where(main.NotificationState.level == "WARNING"))
        assert state.state == "SENT"


def test_failed_notification_is_retried(db_engine, configured_chain, monkeypatch):
    calls = []
    monkeypatch.setattr(main, "PUSHOVER_APP_TOKEN", "configured")
    monkeypatch.setattr(main, "PUSHOVER_USER_KEY", "configured")
    monkeypatch.setattr(main, "send_pushover", lambda title, message: (calls.append(message) and False, "temporary failure"))
    with Session(db_engine) as db:
        bike = db.get(main.Bike, configured_chain["bike_id"])
        chain = db.get(main.Chain, configured_chain["chain_id"])
        chain.km_since_wax = 510
        main.check_distance_thresholds(db, bike, chain)
        main.check_distance_thresholds(db, bike, chain)
        assert len([message for message in calls if "approaching service" in message]) == 2
        assert db.scalar(select(main.NotificationState).where(main.NotificationState.level == "WARNING")) is None


@pytest.mark.xfail(strict=True, reason="Known defect: no-spare notification treats READY chains without wax history as usable spares")
def test_unwaxed_ready_chain_does_not_suppress_no_spare_warning(db_engine, configured_chain, monkeypatch):
    monkeypatch.setattr(main, "PUSHOVER_APP_TOKEN", "configured")
    monkeypatch.setattr(main, "PUSHOVER_USER_KEY", "configured")
    monkeypatch.setattr(main, "send_pushover", lambda title, message: (True, None))
    with Session(db_engine) as db:
        bike = db.get(main.Bike, configured_chain["bike_id"])
        active = db.get(main.Chain, configured_chain["chain_id"])
        active.km_since_wax = 610
        db.add(main.Chain(code="LEGACY-READY", bike_id=bike.id,
                          chain_spec_id=active.chain_spec_id, status="READY",
                          first_used_at=None, total_km=0, km_since_wax=0,
                          current_wear_percent=None, last_wear_at=None, retired_at=None))
        db.flush()
        main.check_distance_thresholds(db, bike, active)
        assert db.scalar(select(main.NotificationState).where(main.NotificationState.level == "NO_SPARE")) is not None

