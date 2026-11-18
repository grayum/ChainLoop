"""Strava 2027 identities and official API-base compatibility regressions."""

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from app import main, validation
from app.migrations import CURRENT_SCHEMA_VERSION, database_schema_version


LARGE_IDS = [9007199254740993, 9223372036854775807]


@pytest.mark.parametrize("value", [1, *LARGE_IDS])
@pytest.mark.parametrize("representation", ["numeric", "string", "dual"])
def test_canonical_id_preserves_exact_decimal_value(value, representation):
    payload = identity(value, representation)
    assert validation.canonical_strava_id(payload) == str(value)


def identity(value, representation):
    return {
        "numeric": {"id": value},
        "string": {"id_str": str(value)},
        "dual": {"id": value, "id_str": str(value)},
    }[representation]


INVALID_IDENTITIES = [
    {}, None, [],
    *[{"id": value} for value in [0, -1, True, False, 1.0, float("nan"),
                                 float("inf"), None, "1", 9223372036854775808]],
    *[{"id_str": value} for value in [None, True, 1, 1.0, "", "0", "-1", "01",
        "１２３", "١٢٣", " 123", "123 ", "123\n", "+123", "1.0", "1e3",
        "NaN", "Infinity", "9223372036854775808", "1" * 1000]],
    {"id": 1, "id_str": "2"},
    # A valid alternate field must never hide a present invalid field.
    *[{"id": 1, "id_str": value} for value in [None, 1, "01", "private-invalid-id"]],
    *[{"id": value, "id_str": "1"} for value in [None, True, 1.0, "1", 0, -1,
                                                               9223372036854775808]],
]


@pytest.mark.parametrize("payload", INVALID_IDENTITIES)
def test_canonical_id_rejects_invalid_or_contradictory_fields(payload):
    with pytest.raises(ValueError, match="^invalid Strava ID$"):
        validation.canonical_strava_id(payload)


def test_canonical_id_supports_configurable_keys():
    # Generic key names demonstrate reuse, without assuming a webhook contract.
    assert validation.canonical_strava_id(
        {"number": LARGE_IDS[0], "decimal": str(LARGE_IDS[0])},
        id_key="number", id_str_key="decimal",
    ) == str(LARGE_IDS[0])


@pytest.fixture
def client(db_engine, monkeypatch):
    monkeypatch.setattr(main, "engine", db_engine)
    with TestClient(main.app, base_url="https://testserver") as value:
        yield value


def mock_sync(monkeypatch, db_engine, records):
    monkeypatch.setattr(main, "engine", db_engine)
    monkeypatch.setattr(main, "strava_token", lambda db: "synthetic-token")
    monkeypatch.setattr(main, "bounded_json_request", lambda method, url, maximum, **kw:
                        records if url.endswith("/athlete/activities") else {})


def ride(fields):
    return fields | {"sport_type": "Ride", "start_date": "2025-01-10T10:00:00Z",
                     "distance": 32000, "gear_id": "gear-example", "name": "Synthetic ride"}


@pytest.mark.parametrize("value", LARGE_IDS)
@pytest.mark.parametrize("first_mode", ["numeric", "string"])
def test_sync_identity_transitions_preserve_history_and_json(
    client, db_engine, configured_chain, monkeypatch, value, first_mode,
):
    # Close the historical interval before import to exercise historical accounting.
    with Session(db_engine) as db:
        ending_wax = main.WaxEvent(
            chain_id=configured_chain["chain_id"], wax_product_id=configured_chain["wax_id"],
            applied_at=datetime(2025, 2, 1, tzinfo=timezone.utc),
            km_since_previous_wax=100, note=None,
        )
        db.add(ending_wax)
        db.commit()
        wax_id = ending_wax.id
    records = [ride(identity(value, first_mode))]
    mock_sync(monkeypatch, db_engine, records)
    assert main.perform_strava_sync() == {"imported": 1, "processed": 1, "added_km": 32.0, "skipped": 0}
    with Session(db_engine) as db:
        activity = db.scalar(select(main.Activity))
        activity_id = activity.id
        original_events = db.scalars(select(main.Event.metadata_json).order_by(main.Event.id)).all()
    for mode in ["dual", "numeric", "string"]:
        records[:] = [ride(identity(value, mode))]
        assert main.perform_strava_sync() == {"imported": 0, "processed": 0, "added_km": 0.0, "skipped": 0}
    with Session(db_engine) as db:
        assert db.scalar(select(func.count(main.Activity.id))) == 1
        activity = db.get(main.Activity, activity_id)
        assert activity.external_id == str(value)
        assert activity.credited_chain_id == configured_chain["chain_id"]
        chain = db.get(main.Chain, configured_chain["chain_id"])
        assert chain.total_km == 32
        assert chain.km_since_wax == 0
        assert db.get(main.WaxEvent, wax_id).km_since_previous_wax == 132
        assert db.scalar(select(func.count(main.Event.id)).where(main.Event.event_type == "RIDE_ADDED")) == 1
        assert db.scalars(select(main.Event.metadata_json).order_by(main.Event.id)).all() == original_events
        assert db.scalar(text("SELECT typeof(external_id) FROM activities")) == "text"
    response = client.get("/api/activities/recent")
    assert response.status_code == 200
    assert response.json()[0]["external_id"] == str(value)
    assert f'"external_id":"{value}"' in response.text
    response = client.get(f"/api/history/{configured_chain['chain_id']}")
    added = [event for event in response.json() if event["event_type"] == "RIDE_ADDED"]
    assert added[0]["metadata"]["external_id"] == str(value)
    assert f'"external_id":"{value}"' in response.text
    page = client.get(f"/activities/{activity_id}")
    assert page.status_code == 200
    assert f'<span class="mono">{value}</span>' in page.text


@pytest.mark.parametrize("fields", [
    {"id": LARGE_IDS[0], "id_str": "private-invalid-id"},
    {"id": LARGE_IDS[0], "id_str": str(LARGE_IDS[0] + 1)},
    {"id": None, "id_str": str(LARGE_IDS[0])},
])
def test_sync_skips_invalid_identity_then_imports_corrected_record(
    db_engine, configured_chain, monkeypatch, caplog, fields,
):
    records = [ride(fields)]
    mock_sync(monkeypatch, db_engine, records)
    assert main.perform_strava_sync() == {"imported": 0, "processed": 0, "added_km": 0.0, "skipped": 1}
    with Session(db_engine) as db:
        assert db.scalar(select(func.count(main.Activity.id))) == 0
        assert db.scalar(select(func.count(main.Event.id))) == 0
        chain = db.get(main.Chain, configured_chain["chain_id"])
        assert chain.total_km == chain.km_since_wax == 0
    assert "private-invalid-id" not in caplog.text
    assert str(LARGE_IDS[0]) not in caplog.text
    assert "Synthetic ride" not in caplog.text
    records[:] = [ride(identity(LARGE_IDS[0], "dual"))]
    assert main.perform_strava_sync()["imported"] == 1
    assert main.perform_strava_sync()["imported"] == 0


def oauth_exchange(client, monkeypatch, athlete, access="synthetic-access"):
    monkeypatch.setattr(main, "STRAVA_CLIENT_ID", "synthetic-client")
    start = client.get("/auth/strava", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    def exchange(method, url, maximum, **kwargs):
        assert method == "POST"
        assert url == "https://www.strava.com/oauth/token"
        return {
            "athlete": athlete, "access_token": access, "refresh_token": "synthetic-refresh",
            "expires_at": 2000000000,
        }
    monkeypatch.setattr(main, "bounded_json_request", exchange)
    return client.get("/auth/strava/callback", params={"state": state, "code": "synthetic-code"},
                      follow_redirects=False)


@pytest.mark.parametrize("value", LARGE_IDS)
def test_oauth_large_identity_reconnects_exactly(client, db_engine, monkeypatch, value):
    for mode in ["numeric", "dual", "string"]:
        assert oauth_exchange(client, monkeypatch, identity(value, mode), access=f"synthetic-{mode}").status_code == 307
        with Session(db_engine) as db:
            tokens = db.scalars(select(main.StravaToken)).all()
            assert len(tokens) == 1
            assert tokens[0].athlete_id == str(value)
            assert tokens[0].access_token == f"synthetic-{mode}"
            assert db.scalar(text("SELECT typeof(athlete_id) FROM strava_tokens")) == "text"


@pytest.mark.parametrize("athlete", [
    {"id": LARGE_IDS[0], "id_str": "private-invalid-id"},
    {"id": LARGE_IDS[0], "id_str": str(LARGE_IDS[0] + 1)},
    {"id": None, "id_str": str(LARGE_IDS[0])},
    {"id": True}, {"id": 1.0}, {},
])
def test_oauth_invalid_identity_preserves_stored_state(client, db_engine, monkeypatch, athlete):
    assert oauth_exchange(client, monkeypatch, identity(LARGE_IDS[0], "numeric")).status_code == 307
    with db_engine.connect() as connection:
        original = connection.execute(text("SELECT * FROM strava_tokens")).all()
    response = oauth_exchange(client, monkeypatch, athlete, access="synthetic-replacement")
    assert response.status_code == 502
    assert response.json() == {"detail": "Strava authorization failed; retry the connection"}
    assert "private-invalid-id" not in response.text
    assert "synthetic-replacement" not in response.text
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT * FROM strava_tokens")).all() == original


def test_existing_schema_already_stores_external_identities_as_strings(db_engine):
    inspector = inspect(db_engine)
    for table, names in {
        "activities": ["external_id", "raw_gear_id"],
        "strava_tokens": ["athlete_id"], "strava_gears": ["gear_id"], "bikes": ["strava_gear_id"],
    }.items():
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        assert all(str(columns[name]["type"]) == "VARCHAR" for name in names)
    assert database_schema_version(db_engine) == CURRENT_SCHEMA_VERSION == 2


@pytest.mark.parametrize("athlete", [
    {"id": LARGE_IDS[0], "id_str": "private-invalid-id"},
    {"id": LARGE_IDS[0], "id_str": str(LARGE_IDS[0] + 1)},
    {"id_str": str(LARGE_IDS[0] + 1)}, None,
])
def test_refresh_rejects_invalid_or_changed_athlete_without_token_changes(
    client, db_engine, monkeypatch, athlete,
):
    assert oauth_exchange(client, monkeypatch, identity(LARGE_IDS[0], "numeric")).status_code == 307
    with Session(db_engine) as db:
        token = db.scalar(select(main.StravaToken))
        token.expires_at = 1
        db.commit()
    with db_engine.connect() as connection:
        original = connection.execute(text("SELECT * FROM strava_tokens")).all()
    monkeypatch.setattr(main, "bounded_json_request", lambda *args, **kw: {
        "athlete": athlete, "access_token": "synthetic-replacement", "expires_at": 2000000000,
    })
    with Session(db_engine) as db:
        with pytest.raises(ValueError):
            main.strava_token(db)
        token = db.scalar(select(main.StravaToken))
        assert token.access_token == "synthetic-access"
        assert token.expires_at == 1
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT * FROM strava_tokens")).all() == original


@pytest.mark.parametrize("include_athlete", [False, True])
def test_refresh_accepts_absent_or_matching_athlete(client, db_engine, monkeypatch, include_athlete):
    assert oauth_exchange(client, monkeypatch, identity(LARGE_IDS[0], "numeric")).status_code == 307
    with Session(db_engine) as db:
        db.scalar(select(main.StravaToken)).expires_at = 1
        db.commit()
    payload = {"access_token": "synthetic-refreshed", "expires_at": 2000000000}
    if include_athlete:
        payload["athlete"] = identity(LARGE_IDS[0], "string")
    monkeypatch.setattr(main, "bounded_json_request", lambda *args, **kw: payload)
    with Session(db_engine) as db:
        assert main.strava_token(db) == "synthetic-refreshed"
        token = db.scalar(select(main.StravaToken))
        assert token.athlete_id == str(LARGE_IDS[0])
        assert token.refresh_token == "synthetic-refresh"


@pytest.mark.parametrize("base", ["https://www.strava.com/api/v3", "https://api-v3.strava.com"])
def test_official_api_bases_are_accepted(base):
    assert validation.validate_strava_api_base(base, False) == base
    # Preserve existing trailing-slash normalization.
    assert validation.validate_strava_api_base(base + "/", False) == base


@pytest.mark.parametrize("base", ["https://www.strava.com/api/v3", "https://api-v3.strava.com"])
def test_sync_uses_configured_official_base(db_engine, monkeypatch, base):
    monkeypatch.setattr(main, "engine", db_engine)
    monkeypatch.setattr(main, "STRAVA_API_BASE_URL", validation.validate_strava_api_base(base, False))
    monkeypatch.setattr(main, "strava_token", lambda db: "synthetic-token")
    requested = []

    def request(method, url, maximum, **kwargs):
        requested.append(url)
        return []

    monkeypatch.setattr(main, "bounded_json_request", request)
    assert main.perform_strava_sync()["imported"] == 0
    assert requested == [base + "/athlete/activities"]


@pytest.mark.parametrize("base", [
    "https://api-v3.strava.com.attacker.example", "https://strava.example",
    "http://api-v3.strava.com", "https://user:pass@api-v3.strava.com",
    "https://@api-v3.strava.com", "https://api-v3.strava.com:8443",
    "https://api-v3.strava.com:443", "https://api-v3.strava.com/api/v3?x=1",
    "https://api-v3.strava.com?x=1", "https://api-v3.strava.com#fragment",
    "https://api-v3.strava.com/api/v3", "https://api-v3.strava.com/../",
    "https://api-v3.strava.com/%2e%2e", "https://api-v3.strava.com/other",
    "https://api-v3.strava.com./", "https://api-v3.strava.com\n",
])
@pytest.mark.parametrize("allow_mock", [False, True])
def test_unsafe_api_bases_are_rejected_even_with_mock_control(base, allow_mock):
    with pytest.raises(RuntimeError):
        validation.validate_strava_api_base(base, allow_mock)


def test_loopback_mock_requires_explicit_control():
    base = "http://127.0.0.1:9000/api/v3"
    with pytest.raises(RuntimeError):
        validation.validate_strava_api_base(base, False)
    assert validation.validate_strava_api_base(base, True) == base


def test_http_json_decoder_preserves_large_numeric_ids(monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def stream(*args, **kwargs):
        assert kwargs["verify"] is True
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False

        class Response:
            headers = {}

            def raise_for_status(self):
                pass

            def iter_raw(self, **kwargs):
                yield json.dumps([{"id": value} for value in LARGE_IDS]).encode()

        yield Response()

    monkeypatch.setattr(validation.httpx, "stream", stream)
    result = validation.bounded_json_request("GET", "https://www.strava.com/api/v3/athlete/activities", 1024)
    assert [validation.canonical_strava_id(item) for item in result] == [str(value) for value in LARGE_IDS]
