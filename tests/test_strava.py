from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import main


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_repeated_strava_sync_imports_and_credits_activity_once(db_engine, configured_chain, monkeypatch):
    activity_payload = [{
        "id": 123456,
        "sport_type": "Ride",
        "start_date": "2025-01-10T10:00:00Z",
        "distance": 32000,
        "gear_id": "gear-example",
        "name": "Example Ride",
    }]

    def fake_get(method, url, maximum, **kwargs):
        if url.endswith("/athlete/activities"):
            return activity_payload
        if "/gear/" in url:
            return {"name": "Example Bike", "distance": 32000, "primary": True}
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(main, "engine", db_engine)
    monkeypatch.setattr(main, "strava_token", lambda db: "test-token")
    monkeypatch.setattr(main, "bounded_json_request", fake_get)

    first = main.perform_strava_sync()
    second = main.perform_strava_sync()

    assert first == {"imported": 1, "processed": 1, "added_km": 32.0, "skipped": 0}
    assert second == {"imported": 0, "processed": 0, "added_km": 0.0, "skipped": 0}
    with Session(db_engine) as db:
        assert db.scalar(select(func.count(main.Activity.id))) == 1
        chain = db.get(main.Chain, configured_chain["chain_id"])
        assert chain.total_km == 32
        assert chain.km_since_wax == 32
        assert db.scalar(select(func.count(main.Event.id)).where(main.Event.event_type == "RIDE_ADDED")) == 1
