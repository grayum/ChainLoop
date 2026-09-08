"""Phase 4B validation, external-payload and request-bound regressions."""

import re
from contextlib import contextmanager
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import main, validation


@pytest.fixture
def client(db_engine, monkeypatch):
    monkeypatch.setattr(main, "engine", db_engine)
    with TestClient(main.app, base_url="https://testserver") as value:
        yield value


def csrf(client):
    response = client.get("/admin")
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text)[1]


@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "+inf", "-inf", "Infinity", "-Infinity"])
def test_nonfinite_form_numbers_are_rejected(client, configured_chain, value):
    response = client.post(
        f"/chains/{configured_chain['chain_id']}/wear",
        data={"csrf_token": csrf(client), "wear_percent": value},
    )
    assert response.status_code == 422
    assert response.json()["detail"] in {"Invalid request input", "Wear percentage is outside the allowed range"}


@pytest.mark.parametrize("value, expected", [("0", 303), ("2", 303), ("2.01", 422), ("-0.01", 422)])
def test_chain_wear_uses_bicycle_elongation_range(client, configured_chain, value, expected):
    response = client.post(
        f"/chains/{configured_chain['chain_id']}/wear",
        data={"csrf_token": csrf(client), "wear_percent": value, "timing": "before_wax"},
        follow_redirects=False,
    )
    assert response.status_code == expected


@pytest.mark.parametrize("field,value", [
    ("name", "X" * 101),
    ("name", "ok\x00bad"),
])
def test_oversized_or_control_character_names_are_rejected(client, field, value):
    response = client.post("/admin/people", data={"csrf_token": csrf(client), field: value})
    assert response.status_code == 422


@pytest.mark.parametrize("code", ["../CHAIN", "/ABSOLUTE", "A/B", "A B", "X" * 65])
def test_path_like_and_oversized_chain_codes_are_rejected(client, configured_chain, code):
    response = client.post("/admin/chains", data={
        "csrf_token": csrf(client), "code": code, "bike_id": configured_chain["bike_id"],
        "condition": "new", "initial_total_km": "0",
    })
    assert response.status_code == 422


@pytest.mark.parametrize("field,value", [
    ("speeds", "not-a-number"), ("speeds", "25"), ("link_count", "501"),
])
def test_chain_spec_optional_numbers_are_controlled(client, field, value):
    response = client.post("/admin/specs", data={
        "csrf_token": csrf(client), "name": "Example", field: value,
    })
    assert response.status_code == 422


def test_enum_and_checkbox_values_are_strict(client, configured_chain):
    response = client.post(f"/chains/{configured_chain['chain_id']}/wear", data={
        "csrf_token": csrf(client), "wear_percent": "0.5", "timing": "unexpected",
    })
    assert response.status_code == 422
    response = client.post("/sync/strava", data={"csrf_token": csrf(client), "ui": "maybe"})
    assert response.status_code == 422


def test_nonexistent_correction_relationship_is_not_unassigned(client, db_engine, configured_chain):
    with Session(db_engine) as db:
        activity = main.Activity(source="strava", external_id="987", occurred_at=main.now(), distance_km=1,
                                 bike_id=None, credited_chain_id=None, processed=False, excluded=False,
                                 raw_gear_id=None, raw_name="Synthetic")
        db.add(activity); db.commit(); activity_id = activity.id
    response = client.post(f"/activities/{activity_id}/correct", data={
        "csrf_token": csrf(client), "bike_id": "999999", "chain_id": "", "distance_km": "1",
        "note": "Keep this exact reason", "excluded": "on",
    })
    assert response.status_code == 404
    with Session(db_engine) as db:
        assert db.get(main.Activity, activity_id).bike_id is None


@pytest.mark.parametrize("value", ["2025-02-30", "01/01/2025", "1899-12-31", "2999-01-01"])
def test_malformed_or_out_of_range_dates_are_rejected(client, configured_chain, value):
    response = client.post(f"/chains/{configured_chain['chain_id']}/initial-wax", data={
        "csrf_token": csrf(client), "wax_product_id": configured_chain["wax_id"], "wax_date": value,
    })
    assert response.status_code == 422


def test_duplicate_wax_product_is_a_conflict(client, db_engine):
    with Session(db_engine) as db:
        db.add(main.WaxProduct(name="Duplicate Wax", notes=None, archived=False)); db.commit()
    response = client.post("/admin/waxes", data={"csrf_token": csrf(client), "name": "Duplicate Wax"})
    assert response.status_code == 409
    assert "sqlite" not in response.text.lower()


def test_integrity_race_rolls_back_and_returns_redacted_conflict():
    class FailingSession:
        rolled_back = False
        def commit(self):
            from sqlalchemy.exc import IntegrityError
            raise IntegrityError("INSERT secret", {"token": "secret"}, RuntimeError("private database path"))
        def rollback(self): self.rolled_back = True
    session = FailingSession()
    with pytest.raises(HTTPException) as error:
        main.commit_or_conflict(session, "Value already exists")
    assert error.value.status_code == 409
    assert error.value.detail == "Value already exists"
    assert session.rolled_back


def test_webhook_and_query_bounds_apply_without_adding_ingestion(client):
    assert client.post("/webhooks/strava", content=b"x" * (16 * 1024 + 1)).status_code == 413
    assert client.get("/webhooks/strava?" + "x" * 4097).status_code == 400
    assert client.post("/webhooks/strava", json={"ignored": True}).json() == {"ok": True}


@pytest.mark.parametrize("url", [
    "http://www.strava.com/api/v3", "https://example.com/api/v3", "https://127.0.0.1/api/v3",
    "https://169.254.169.254/api/v3", "https://www.strava.com:444/api/v3",
    "https://user:pass@www.strava.com/api/v3", "https://www.strava.com/api/v3?target=x",
])
def test_production_strava_base_rejects_dangerous_urls(url):
    with pytest.raises(RuntimeError):
        validation.validate_strava_api_base(url, False)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_explicit_development_mock_is_loopback_only(host):
    assert validation.validate_strava_api_base(f"http://{host}:9000/api/v3", True)


def test_strava_activity_validation_rejects_malformed_values_without_raw_output():
    current = datetime(2026, 1, 1, tzinfo=timezone.utc)
    base = {"id": 1, "sport_type": "Ride", "start_date": "2025-01-01T10:00:00Z",
            "distance": 1000, "gear_id": "gear-1", "name": "Ride"}
    for changes in [
        {"id": "../secret"}, {"distance": float("nan")}, {"distance": -1},
        {"start_date": "not-a-date"}, {"gear_id": "../../metadata"}, {"name": "x" * 256},
    ]:
        with pytest.raises(ValueError) as error:
            validation.validate_strava_activity(base | changes, current)
        assert "secret" not in str(error.value) and "metadata" not in str(error.value)


def test_sync_skips_malformed_record_and_keeps_valid_record(db_engine, configured_chain, monkeypatch, caplog):
    rows = [
        {"id": "../../bad", "sport_type": "Ride", "start_date": "2025-01-02T00:00:00Z",
         "distance": 1000, "name": "PRIVATE NAME"},
        {"id": 42, "sport_type": "Ride", "start_date": "2025-01-03T00:00:00Z",
         "distance": 2500, "gear_id": "gear-example", "name": "Valid"},
    ]
    def request(method, url, maximum, **kwargs):
        return rows if url.endswith("/athlete/activities") else {"name": "Bike", "distance": 1, "primary": True}
    monkeypatch.setattr(main, "engine", db_engine)
    monkeypatch.setattr(main, "strava_token", lambda db: "BEARER-SECRET")
    monkeypatch.setattr(main, "bounded_json_request", request)
    result = main.perform_strava_sync()
    assert result == {"imported": 1, "processed": 1, "added_km": 2.5, "skipped": 1}
    with Session(db_engine) as db:
        assert db.scalar(select(func.count(main.Activity.id))) == 1
    assert "PRIVATE NAME" not in caplog.text and "BEARER-SECRET" not in caplog.text and "../../bad" not in caplog.text


def test_bounded_http_json_streams_and_disables_redirects(monkeypatch):
    observed = {}
    class Response:
        def raise_for_status(self): pass
        headers = {}
        def iter_raw(self, chunk_size): return iter([b'{"ok":', b'true}'])
    @contextmanager
    def stream(method, url, **kwargs):
        observed.update(kwargs)
        yield Response()
    monkeypatch.setattr(validation.httpx, "stream", stream)
    assert validation.bounded_json_request("GET", "https://www.strava.com/api/v3/test", 64) == {"ok": True}
    assert observed["follow_redirects"] is False
    assert observed["verify"] is True
    assert observed["trust_env"] is False


def test_bounded_http_json_stops_when_stream_limit_is_crossed(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        headers = {}
        def iter_raw(self, chunk_size): return iter([b"1234", b"5678"])
    @contextmanager
    def stream(*args, **kwargs): yield Response()
    monkeypatch.setattr(validation.httpx, "stream", stream)
    with pytest.raises(ValueError, match="size limit"):
        validation.bounded_json_request("GET", "https://www.strava.com/api/v3/test", 4)


def test_pushover_explicitly_disables_redirects_and_keeps_tls(monkeypatch):
    observed = {}
    class Response:
        def raise_for_status(self): pass
    def post(url, **kwargs):
        observed.update(kwargs)
        return Response()
    monkeypatch.setattr(main, "PUSHOVER_APP_TOKEN", "synthetic-token")
    monkeypatch.setattr(main, "PUSHOVER_USER_KEY", "synthetic-user")
    monkeypatch.setattr(main.httpx, "post", post)
    assert main.send_pushover("Title", "Message") == (True, None)
    assert observed["follow_redirects"] is False
    assert observed["verify"] is True
    assert observed["trust_env"] is False


@pytest.mark.parametrize("zone,utc,accepted,rejected", [
    ("Pacific/Kiritimati", "2026-01-01T12:30:00+00:00", "2026-01-02", "2026-01-03"),
    ("America/Los_Angeles", "2026-01-02T01:00:00+00:00", "2026-01-01", "2026-01-02"),
])
def test_today_uses_configured_timezone(monkeypatch, zone, utc, accepted, rejected):
    from zoneinfo import ZoneInfo
    class Clock(datetime):
        @classmethod
        def now(cls, tz):
            return datetime.fromisoformat(utc).astimezone(tz)
    monkeypatch.setattr(validation, "datetime", Clock)
    assert validation.local_date(accepted, ZoneInfo(zone)).date().isoformat() == accepted
    with pytest.raises(HTTPException):
        validation.local_date(rejected, ZoneInfo(zone))


@pytest.mark.parametrize("host", ["192.168.1.1", "10.0.0.1", "172.16.0.1", "169.254.169.254", "127.0.0.2", "localhost.example.com"])
def test_mock_setting_does_not_allow_lan_or_loopback_lookalikes(host):
    with pytest.raises(RuntimeError):
        validation.validate_strava_api_base(f"http://{host}/api/v3", True)


@pytest.mark.parametrize("distance", [10**400, float("nan"), float("inf"), -float("inf"), True])
def test_strava_numeric_extremes_are_controlled(distance):
    record = {"id": 1, "sport_type": "Ride", "start_date": "2025-01-01T00:00:00Z", "distance": distance}
    with pytest.raises(ValueError):
        validation.validate_strava_activity(record, main.now())
    with pytest.raises(ValueError):
        validation.validate_strava_gear_payload({"distance": distance})


@pytest.mark.parametrize("value,expected", [("on", True), ("true", True), ("1", True), ("off", False), ("false", False), ("0", False), (None, False)])
def test_existing_checkbox_values(value, expected):
    assert validation.strict_form_bool(value, "checkbox") is expected


def test_unused_nonexistent_product_is_not_ignored(client, configured_chain):
    response = client.post("/admin/chains", data={
        "csrf_token": csrf(client), "code": "NEW-TEST", "bike_id": configured_chain["bike_id"],
        "condition": "new", "wax_product_id": "999999",
    })
    assert response.status_code == 404


def test_webhook_standard_verification_fields(client, monkeypatch):
    monkeypatch.setattr(main, "STRAVA_VERIFY_TOKEN", "synthetic-verification")
    params = {"hub.mode": "subscribe", "hub.verify_token": "synthetic-verification", "hub.challenge": "example-challenge"}
    assert client.get("/webhooks/strava", params=params).json() == {"hub.challenge": "example-challenge"}
    assert client.get("/webhooks/strava", params=params | {"hub.challenge": ""}).status_code == 403
    assert client.get("/webhooks/strava", params=params | {"hub.challenge": "x" * 513}).status_code == 422


def test_historical_wear_is_not_rewritten(client, db_engine, configured_chain):
    with Session(db_engine) as db:
        chain = db.get(main.Chain, configured_chain["chain_id"])
        chain.current_wear_percent = 2.5
        db.commit()
    assert client.get(f"/chains/{configured_chain['chain_id']}").status_code == 200
    with Session(db_engine) as db:
        assert db.get(main.Chain, configured_chain["chain_id"]).current_wear_percent == 2.5


def test_malformed_strava_record_can_be_retried_after_correction(db_engine, configured_chain, monkeypatch):
    record = {"id": 900, "sport_type": "Ride", "start_date": "2025-01-03T00:00:00Z", "distance": float("nan"), "gear_id": "gear-example", "name": "Synthetic"}
    monkeypatch.setattr(main, "engine", db_engine)
    monkeypatch.setattr(main, "strava_token", lambda db: "synthetic-token")
    monkeypatch.setattr(main, "bounded_json_request", lambda method, url, maximum, **kw: [record] if url.endswith("/athlete/activities") else {})
    assert main.perform_strava_sync()["skipped"] == 1
    with Session(db_engine) as db:
        assert db.scalar(select(func.count(main.Activity.id))) == 0
        assert db.get(main.Chain, configured_chain["chain_id"]).total_km == 0
    record["distance"] = 1200
    assert main.perform_strava_sync()["imported"] == 1
    assert main.perform_strava_sync()["imported"] == 0



def test_compressed_external_response_is_rejected_before_read(monkeypatch):
    class Response:
        headers = {"content-encoding": "gzip"}
        def raise_for_status(self): pass
        def iter_raw(self, **kwargs):
            pytest.fail("Compressed response must not be read or decompressed")
    @contextmanager
    def stream(*args, **kwargs):
        assert kwargs["headers"]["Accept-Encoding"] == "identity"
        yield Response()
    monkeypatch.setattr(validation.httpx, "stream", stream)
    with pytest.raises(ValueError, match="encoding"):
        validation.bounded_json_request("GET", "https://www.strava.com/api/v3/test", 64)


@pytest.mark.parametrize("payload", [None, [], {}, {"access_token": "x", "expires_at": True}, {"access_token": "x", "refresh_token": "y", "expires_at": 123, "athlete": {"id": "private-id"}}])
def test_malformed_token_payload_is_rejected(payload):
    with pytest.raises(ValueError):
        validation.validate_strava_token_payload(payload, require_athlete=True)


def test_historical_malformed_gear_id_never_reaches_network(monkeypatch):
    monkeypatch.setattr(main, "bounded_json_request", lambda *args, **kw: pytest.fail("Invalid gear ID reached network"))
    main.refresh_strava_gear(None, "synthetic-token", "../../private")


def test_external_redirect_is_not_followed(monkeypatch):
    requested = []
    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/"})
    @contextmanager
    def stream(method, url, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=kwargs["follow_redirects"]) as client:
            with client.stream(method, url) as response:
                yield response
    monkeypatch.setattr(validation.httpx, "stream", stream)
    with pytest.raises(httpx.HTTPStatusError):
        validation.bounded_json_request("GET", "https://www.strava.com/api/v3/test", 64)
    assert requested == ["https://www.strava.com/api/v3/test"]


def test_map_gear_integrity_race_is_controlled_before_activity_lookup(client, configured_chain, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    token = csrf(client)
    real_flush = Session.flush
    def flush(db, *args, **kwargs):
        if any(isinstance(item, main.Bike) and item.strava_gear_id == "race-gear" for item in db.dirty):
            raise IntegrityError("private SQL", {}, RuntimeError("private detail"))
        return real_flush(db, *args, **kwargs)
    monkeypatch.setattr(Session, "flush", flush)
    response = client.post(f"/bikes/{configured_chain['bike_id']}/map-gear", data={"csrf_token": token, "gear_id": "race-gear"})
    assert response.status_code == 409
    assert "private" not in response.text


def test_state_conflict_and_missing_resource_have_distinct_status(client, configured_chain):
    token = csrf(client)
    assert client.post(f"/bikes/{configured_chain['bike_id']}/swap", data={"csrf_token": token, "chain_id": configured_chain["chain_id"]}).status_code == 409
    assert client.post("/bikes/999999/swap", data={"csrf_token": token, "chain_id": configured_chain["chain_id"]}).status_code == 404


def test_strava_timestamp_underflow_is_a_malformed_record():
    with pytest.raises(ValueError, match="timestamp_range"):
        validation.validate_strava_activity({"id": 1, "sport_type": "Ride", "start_date": "0001-01-01T00:00:00+23:00", "distance": 1}, main.now())


@pytest.mark.parametrize("url", ["http://localhost:99999/api/v3", "http://localhost:bad/api/v3", "http://local\nhost/api/v3"])
def test_mock_url_malformed_ports_and_whitespace_are_rejected(url):
    with pytest.raises(RuntimeError):
        validation.validate_strava_api_base(url, True)


def test_full_sync_window_completes_without_requiring_end_of_history(db_engine, monkeypatch):
    pages = []
    def request(method, url, maximum, **kwargs):
        page = kwargs["params"]["page"]
        pages.append(page)
        return [{"id": page * 1000 + index, "sport_type": "Run", "start_date": "2025-01-01T00:00:00Z", "distance": 1000} for index in range(100)]
    monkeypatch.setattr(main, "engine", db_engine)
    monkeypatch.setattr(main, "strava_token", lambda db: "synthetic-token")
    monkeypatch.setattr(main, "bounded_json_request", request)
    assert main.perform_strava_sync() == {"imported": 0, "processed": 0, "added_km": 0.0, "skipped": 0}
    assert pages == [1, 2, 3, 4, 5]
