from sqlalchemy import select
from sqlalchemy.orm import Session

from app import main


def test_backfill_suppresses_reached_thresholds_without_sending(db_engine, configured_chain, monkeypatch):
    monkeypatch.setattr(main, "PUSHOVER_APP_TOKEN", "configured")
    monkeypatch.setattr(main, "PUSHOVER_USER_KEY", "configured")
    monkeypatch.setattr(main, "send_pushover", lambda *args: (_ for _ in ()).throw(AssertionError("notification sent")))
    with Session(db_engine) as db:
        bike = db.get(main.Bike, configured_chain["bike_id"])
        chain = db.get(main.Chain, configured_chain["chain_id"])
        chain.km_since_wax = 650
        main.suppress_reached_thresholds(db, bike, chain)
        db.commit()
        states = db.scalars(select(main.NotificationState)).all()
        assert {(state.level, state.state) for state in states} == {
            ("WARNING", "SUPPRESSED"), ("CHANGE", "SUPPRESSED")
        }

