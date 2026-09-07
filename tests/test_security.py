"""HTTP-level Phase 4A regressions; no real credentials or outbound traffic."""
import json
import logging
import re
import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app import main, security


@pytest.fixture
def client(db_engine, monkeypatch):
    monkeypatch.setattr(main, "engine", db_engine)
    with TestClient(main.app, base_url="https://testserver") as client:
        yield client


def csrf(client):
    response = client.get("/admin")
    assert response.status_code == 200
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text)[1]


def cookie_data(client):
    return security.session_signer(main.SECURITY).loads(client.cookies.get(security.SESSION_COOKIE))


def set_session(client, data, signer=None):
    client.cookies.clear()
    client.cookies.set(security.SESSION_COOKIE, (signer or security.session_signer(main.SECURITY)).dumps(data))


BROWSER_POSTS = [route.path for route in main.app.routes
                 if "POST" in getattr(route, "methods", set()) and route.path != "/webhooks/strava"]


@pytest.mark.parametrize("path", BROWSER_POSTS)
def test_every_browser_mutation_rejects_before_side_effects(client, db_engine, monkeypatch, path):
    token = csrf(client)
    def forbidden(*args, **kwargs):
        pytest.fail("Rejected request reached a side-effect boundary")
    event.listen(db_engine, "before_cursor_execute", forbidden)
    monkeypatch.setattr(main.httpx, "get", forbidden)
    monkeypatch.setattr(main.httpx, "post", forbidden)
    monkeypatch.setattr(main, "setup_scheduler", forbidden)
    monkeypatch.setattr(main, "migrate_legacy_database", forbidden)
    monkeypatch.setattr(main, "perform_strava_sync", forbidden)
    monkeypatch.setattr(main, "send_pushover", forbidden)
    concrete = re.sub(r"\{[^}]+\}", "1", path)
    try:
        for data in ({}, {"csrf_token": "wrong"}):
            response = client.post(concrete, data=data, follow_redirects=False)
            assert response.status_code == 403
            assert "set-cookie" not in response.headers
    finally:
        event.remove(db_engine, "before_cursor_execute", forbidden)
    assert token == cookie_data(client)["csrf"]


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://testserver"}, {"Referer": "https://testserver/admin"}])
def test_csrf_accepts_valid_form_and_persists_change(client, db_engine, headers):
    response = client.post("/admin/people", data={"name": "Synthetic Rider", "csrf_token": csrf(client)}, headers=headers,
                           follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    with Session(db_engine) as db:
        assert db.scalar(select(main.Person.name)) == "Synthetic Rider"


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.invalid"}, {"Origin": "null"},
                                      {"Referer": "https://evil.invalid/admin"},
                                      {"Origin": "https://testserver.evil.invalid"}])
def test_csrf_origin_defense_does_not_replace_token(client, headers):
    token = csrf(client)
    assert client.post("/admin/people", data={"name": "Example", "csrf_token": token}, headers=headers).status_code == 403
    assert client.post("/admin/people", data={"name": "Example"}, headers={"Origin": "https://testserver"}).status_code == 403


@pytest.mark.parametrize("mode", ["missing", "tampered", "expired", "foreign", "rotated", "duplicate"])
def test_csrf_invalid_sessions_cannot_reuse_token(client, mode):
    token = csrf(client)
    data = cookie_data(client)
    if mode == "missing": client.cookies.clear()
    elif mode == "tampered":
        client.cookies.clear(); client.cookies.set(security.SESSION_COOKIE, "invalid-cookie")
    elif mode == "expired":
        data["issued"] = int(time.time()) - security.SESSION_SECONDS - 1; set_session(client, data)
    elif mode == "foreign": set_session(client, security.new_session())
    elif mode == "rotated": set_session(client, data, URLSafeTimedSerializer("a-different-generated-test-secret-value", salt="chainloop-browser-session"))
    elif mode == "duplicate":
        cookie = client.cookies.get(security.SESSION_COOKIE)
        client.cookies.clear()
        response = client.post("/admin/people", data={"name": "Example", "csrf_token": token},
                               headers={"Cookie": f"chainloop_session={cookie}; chainloop_session={cookie}"})
        assert response.status_code == 403
        return
    assert client.post("/admin/people", data={"name": "Example", "csrf_token": token}).status_code == 403


def test_session_cookie_attributes_lifetime_and_stable_tabs(client):
    response = client.get("/admin")
    value = response.headers["set-cookie"]
    for attribute in ["Secure", "HttpOnly", "SameSite=lax", "Path=/", "Max-Age=28800"]:
        assert attribute in value
    assert "Domain=" not in value
    data = cookie_data(client)
    assert "oauth_nonce" not in data and "csp_nonce" not in data
    token = data["csrf"]
    assert csrf(client) == token
    assert "set-cookie" not in client.get("/admin").headers
    data["issued"] -= security.SESSION_SECONDS + 1
    set_session(client, data)
    assert csrf(client) != token


def test_body_limits_and_duplicate_tokens(client, monkeypatch):
    token = csrf(client)
    assert client.post("/admin/people", content=f"csrf_token={token}&csrf_token={token}&name=Example",
                       headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 403
    assert client.post("/admin/people", data={"csrf_token": token, "name": "x" * security.MAX_FORM_BYTES}).status_code == 413
    import tempfile
    monkeypatch.setattr(tempfile, "TemporaryFile", lambda *a, **kw: pytest.fail("File created during rejected CSRF"))
    assert client.post("/admin/people", data={"csrf_token": token}, files={"file": ("test.txt", b"text")}).status_code == 403
    assert client.post("/admin/people", json={"csrf_token": token}).status_code == 403


def test_multipart_form_without_files(client):
    token = csrf(client)
    assert client.post("/admin/people", files={"csrf_token": (None, token), "name": (None, "Example")},
                       follow_redirects=False).status_code == 303


@pytest.mark.parametrize("host", ["testserver", "testserver:443", "TESTSERVER:8080"])
def test_valid_hosts(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["evil.invalid", "testserver/evil", "testserver?x", "testserver#x", "user@testserver",
                                  "testserver:", "testserver:0", "testserver:65536", "testserver:abc", "testserver.",
                                  "testserver,evil.invalid", "[::1", "::1", " testserver", "testserver:1:2", "[::1%lo]"])
def test_invalid_hosts(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 400


def test_duplicate_host_and_untrusted_forwarded_headers(client, monkeypatch):
    assert client.get("/health", headers=[("host", "testserver"), ("host", "testserver")]).status_code == 400
    assert client.get("/health", headers={"host": "evil.invalid", "x-forwarded-host": "testserver"}).status_code == 400
    monkeypatch.setattr(main, "STRAVA_CLIENT_ID", "synthetic-client")
    response = client.get("/auth/strava", headers={"Forwarded": "host=evil.invalid;proto=http", "X-Forwarded-Host": "evil.invalid",
                                                  "X-Forwarded-Proto": "http"}, follow_redirects=False)
    assert parse_qs(urlsplit(response.headers["location"]).query)["redirect_uri"] == ["https://testserver/auth/strava/callback"]
    assert client.get("/admin/", follow_redirects=False).status_code == 404


@pytest.mark.parametrize("host,expected", [("127.0.0.1:8080", ("127.0.0.1", 8080)), ("[::1]:8080", ("::1", 8080)),
                                          ("[2001:db8::1]", ("2001:db8::1", None))])
def test_host_ip_parsing(host, expected):
    assert security.parse_host(host) == expected


@pytest.mark.parametrize("path,status", [("/admin", 200), ("/missing", 404), ("/chains/no-number", 422),
                                        ("/docs", 404), ("/openapi.json", 404), ("/redoc", 404)])
def test_security_headers_on_pages_and_errors(client, path, status):
    response = client.get(path)
    assert response.status_code == status
    assert_headers(response)
    assert response.headers["cache-control"] == "no-store"


def assert_headers(response):
    policy = response.headers["content-security-policy"]
    for directive in ["frame-ancestors 'none'", "object-src 'none'", "base-uri 'none'", "form-action 'self'", "style-src-attr 'none'"]:
        assert directive in policy
    assert "unsafe-" not in policy and "*" not in policy
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin"
    assert "camera=()" in response.headers["permissions-policy"]
    assert "strict-transport-security" not in response.headers


def test_static_caching_and_independent_response_nonces(client):
    first = client.get("/statistics")
    second = client.get("/statistics")
    nonce = lambda r: re.search(r"'nonce-([^']+)'", r.headers["content-security-policy"])[1]
    assert nonce(first) != nonce(second)
    assert f'nonce="{nonce(first)}"' in first.text
    response = client.get("/static/app.css")
    assert response.status_code == 200
    assert response.headers.get("cache-control") != "no-store"
    assert "set-cookie" not in response.headers
    assert "etag" in response.headers
    assert_headers(response)


def test_errors_redacted_and_headers_on_redirect_403_500_502(client, monkeypatch, caplog):
    token = csrf(client)
    sensitive = "access_token=SYNTHETIC-SECRET sqlite:////private/database.db"
    def fail(*args, **kwargs): raise RuntimeError(sensitive)
    monkeypatch.setattr(main, "perform_strava_sync", fail)
    responses = [client.post("/sync/strava", data={"csrf_token": token}, follow_redirects=False),
                 client.post("/sync/strava", data={"csrf_token": token, "ui": "true"}, follow_redirects=False),
                 client.post("/sync/strava", data={})]
    assert [r.status_code for r in responses] == [502, 303, 403]
    monkeypatch.setattr(main, "wax_product_usage_count", fail)
    with Session(main.engine) as db:
        db.add(main.WaxProduct(name="Synthetic Wax", archived=False)); db.commit()
    responses.append(client.get("/admin"))
    assert responses[-1].status_code == 500
    for r in responses:
        assert_headers(r)
        assert "SYNTHETIC-SECRET" not in r.text + str(r.headers)
        assert "/private/" not in r.text + str(r.headers)
    assert sensitive not in caplog.text


def test_pushover_and_sync_persist_only_safe_errors(client, monkeypatch):
    sensitive = "https://user:SYNTHETIC-SECRET@private.invalid/api"
    def fail(*args, **kwargs): raise httpx.ConnectError(sensitive)
    monkeypatch.setattr(main, "PUSHOVER_APP_TOKEN", "synthetic-app-token")
    monkeypatch.setattr(main, "PUSHOVER_USER_KEY", "synthetic-user-key")
    monkeypatch.setattr(main.httpx, "post", fail)
    assert main.send_pushover("Example", "Example") == (False, "Pushover delivery failed")
    monkeypatch.setattr(main, "strava_token", fail)
    with pytest.raises(httpx.ConnectError): main.perform_strava_sync()
    with Session(main.engine) as db:
        assert "SYNTHETIC-SECRET" not in db.scalar(select(main.SyncRun.error))
    assert "SYNTHETIC-SECRET" not in client.get("/").text


def oauth_start(client, monkeypatch):
    monkeypatch.setattr(main, "STRAVA_CLIENT_ID", "synthetic-client")
    response = client.get("/auth/strava", follow_redirects=False)
    assert response.status_code == 307
    assert_headers(response)
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def fake_oauth(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append(url)
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "athlete": {"id": 123456}, "access_token": "synthetic-access", "refresh_token": "synthetic-refresh",
            "expires_at": int(time.time()) + 3600})
    monkeypatch.setattr(main.httpx, "post", post)
    return calls


def test_oauth_success_and_normal_browser_replay(client, monkeypatch):
    state = oauth_start(client, monkeypatch)
    calls = fake_oauth(monkeypatch)
    response = client.get("/auth/strava/callback", params={"state": state, "code": "synthetic-code"}, follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/"
    assert "oauth_nonce" not in cookie_data(client)
    assert client.get("/auth/strava/callback", params={"state": state, "code": "synthetic-code"}).status_code == 400
    assert len(calls) == 1
    with Session(main.engine) as db:
        assert db.scalar(select(main.StravaToken.access_token)) == "synthetic-access"


@pytest.mark.parametrize("mode", ["missing", "invalid", "expired", "wrong-session"])
def test_oauth_rejection_before_outbound_calls(client, monkeypatch, mode):
    state = oauth_start(client, monkeypatch)
    if mode == "missing": state = ""
    elif mode == "invalid": state = "invalid-state"
    elif mode == "expired":
        from itsdangerous.timed import TimestampSigner
        class OldSigner(TimestampSigner):
            def get_timestamp(self): return int(time.time()) - 601
        state = URLSafeTimedSerializer(main.SESSION_SECRET, salt="chainloop-oauth-state", signer=OldSigner).dumps(main.signer.loads(state))
    elif mode == "wrong-session": set_session(client, security.new_session())
    calls = fake_oauth(monkeypatch)
    assert client.get("/auth/strava/callback", params={"state": state, "code": "synthetic-code"}).status_code == 400
    assert not calls


def test_copied_old_cookie_is_not_server_side_revoked(client, monkeypatch):
    state = oauth_start(client, monkeypatch)
    old = client.cookies.get(security.SESSION_COOKIE)
    calls = fake_oauth(monkeypatch)
    url = "/auth/strava/callback"
    assert client.get(url, params={"state": state, "code": "synthetic-code"}, follow_redirects=False).status_code == 307
    client.cookies.clear(); client.cookies.set(security.SESSION_COOKIE, old)
    # The mock accepts code reuse; real Strava rejects reused authorization codes.
    # This deliberately records the stateless cookie limitation, not a guarantee.
    assert client.get(url, params={"state": state, "code": "synthetic-code"}, follow_redirects=False).status_code == 307
    assert len(calls) == 2


def test_webhook_stub_is_not_csrf_protected(client):
    client.cookies.clear()
    assert client.post("/webhooks/strava", json={"synthetic": True}).json() == {"ok": True}
    assert client.get("/webhooks/strava").status_code == 403


def test_health_minimal_and_failure_redaction(client, monkeypatch):
    assert client.get("/health").json() == {"status": "ok", "app": "ChainLoop", "version": main.APP_VERSION}
    class BrokenSession:
        def __init__(self, *args): raise RuntimeError("secret sqlite:////private/data.db")
    monkeypatch.setattr(main, "Session", BrokenSession)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "app": "ChainLoop", "version": main.APP_VERSION}
    assert_headers(response)


def test_xss_and_all_rendered_forms_have_csrf(client, configured_chain):
    payload = '</script><script>alert("synthetic")</script><img src=x onerror=alert(1)>'
    with Session(main.engine) as db:
        chain = db.get(main.Chain, configured_chain["chain_id"])
        chain.code = payload
        db.add(main.Person(name=payload))
        db.add(main.WearMeasurement(chain_id=chain.id, measured_at=main.now(), wear_percent=.25,
                                   total_km_at_measurement=100, timing="before_wax", note=payload))
        db.commit()
    class Forms(HTMLParser):
        def __init__(self): super().__init__(); self.count = 0; self.tokens = 0
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            assert "style" not in attrs
            if tag == "form" and attrs.get("method") == "post": self.count += 1
            if tag == "input" and attrs.get("name") == "csrf_token": self.tokens += 1
    for path in ["/", "/admin", "/statistics", "/history", f'/chains/{configured_chain["chain_id"]}']:
        response = client.get(path)
        assert response.status_code == 200
        assert payload not in response.text
        parser = Forms(); parser.feed(response.text)
        assert parser.count == parser.tokens
    chart = client.get("/statistics").text
    encoded = re.search(r'<script id="chartData"[^>]*>(.*?)</script>', chart, re.S)[1]
    assert json.loads(encoded)["wear"][0]["chain"] == payload
    assert "\\u003c" in encoded


@pytest.mark.parametrize("value", ["", "change-me", "replace-with-a-long-random-secret", "short", " " * 40])
def test_secret_validation(monkeypatch, value):
    monkeypatch.setenv("SESSION_SECRET", value)
    with pytest.raises(RuntimeError, match="SESSION_SECRET") as exc: security.SecuritySettings.from_env()
    if value: assert value not in str(exc.value)


@pytest.mark.parametrize("url", ["http://public.example", "http://192.168.1.10:8080", "http://127.0.0.2:8080", "http://localhost.evil.invalid"])
def test_dev_http_cannot_enable_lan_or_public_origins(monkeypatch, url):
    monkeypatch.setenv("APP_BASE_URL", url); monkeypatch.setenv("CHAINLOOP_DEV_ALLOW_HTTP", "true")
    with pytest.raises(RuntimeError, match="APP_BASE_URL"): security.SecuritySettings.from_env()


@pytest.mark.parametrize("url", ["http://localhost:18080", "http://127.0.0.1:18080", "http://[::1]:18080"])
def test_explicit_loopback_development_cookie(monkeypatch, url):
    monkeypatch.setenv("APP_BASE_URL", url); monkeypatch.setenv("CHAINLOOP_DEV_ALLOW_HTTP", "true")
    monkeypatch.delenv("STRAVA_REDIRECT_URI", raising=False)
    settings = security.SecuritySettings.from_env()
    assert not settings.secure_cookie
    app = FastAPI()
    @app.get("/")
    def page(request: main.Request):
        security.template_security_context(request)
        return {"ok": True}
    app.add_middleware(security.BrowserSecurityMiddleware, settings=settings)
    # Starlette's legacy HTTPX test transport cannot split IPv6 netlocs.
    # Supply the exact wire Host to exercise application IPv6 handling.
    with TestClient(app, base_url="http://localhost:18080") as client:
        response = client.get("/", headers={"host": urlsplit(url).netloc})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "Secure" not in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie


@pytest.mark.parametrize("url", ["https://user:password@example.com", "https://example.com/path", "https://example.com?x=1",
                                 "https://example.com#fragment", "http://localhost:18080", "https://example.com\\evil"])
def test_invalid_canonical_urls(monkeypatch, url):
    monkeypatch.setenv("APP_BASE_URL", url)
    with pytest.raises(RuntimeError, match="APP_BASE_URL"): security.SecuritySettings.from_env()


def test_callback_normalization_and_mismatch(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://TESTSERVER:443/")
    assert security.SecuritySettings.from_env().callback_url == "https://testserver/auth/strava/callback"
    monkeypatch.setenv("STRAVA_REDIRECT_URI", "https://evil.invalid/auth/strava/callback")
    with pytest.raises(RuntimeError, match="STRAVA_REDIRECT_URI"): security.SecuritySettings.from_env()


@pytest.mark.parametrize("value", ["*", "*.example.com", "testserver:8080", "user@example.com", "example.com/path"])
def test_allowed_host_configuration_rejects_unsafe_values(monkeypatch, value):
    monkeypatch.setenv("CHAINLOOP_ALLOWED_HOSTS", value)
    with pytest.raises(RuntimeError, match="CHAINLOOP_ALLOWED_HOSTS"): security.SecuritySettings.from_env()


def test_access_log_strips_callback_queries():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1", "GET", "/auth/strava/callback?code=synthetic-secret&state=private", "1.1", 307), None)
    security.SafeAccessLog().filter(record)
    assert "synthetic-secret" not in record.getMessage()
    assert "/auth/strava/callback" in record.getMessage()


def test_legacy_errors_are_redacted_without_rewriting_history(client, configured_chain):
    sensitive = 'https://user:synthetic-secret@private.invalid/path'
    with Session(main.engine) as db:
        db.add(main.SyncRun(started_at=main.now(), completed_at=main.now(), trigger='manual', success=False,
                            imported=0, processed=0, added_km=0, error=sensitive))
        main.record_event(db, 'PUSHOVER_WARNING_FAILED', chain_id=configured_chain['chain_id'], note=sensitive)
        main.record_event(db, 'DISTANCE_ADJUSTMENT', chain_id=configured_chain['chain_id'], note='Keep ordinary text <unchanged>')
        db.commit()
    for path in ['/', '/history', '/chains/1', '/api/history/1', '/health']:
        assert 'synthetic-secret' not in client.get(path).text
    with Session(main.engine) as db:
        assert db.scalar(select(main.SyncRun.error)) == sensitive
        assert db.scalar(select(main.Event.note).where(main.Event.event_type == 'PUSHOVER_WARNING_FAILED')) == sensitive
    notes = [e['note'] for e in client.get('/api/history/1').json()]
    assert 'Keep ordinary text <unchanged>' in notes


def test_oauth_failed_exchange_is_redacted_and_clears_pending_state(client, monkeypatch):
    state = oauth_start(client, monkeypatch)
    def fail(*args, **kwargs): raise httpx.ConnectError('synthetic-secret private-url')
    monkeypatch.setattr(main.httpx, 'post', fail)
    response = client.get('/auth/strava/callback', params={'code':'synthetic-secret','state':state})
    assert response.status_code == 500
    assert 'synthetic-secret' not in response.text
    assert 'oauth_nonce' not in cookie_data(client)
    assert_headers(response)


def test_validation_errors_do_not_echo_input(client):
    response = client.post('/chains/1/wear', data={'csrf_token':csrf(client),'wear_percent':'synthetic-secret'})
    assert response.status_code == 422
    assert response.json() == {'detail':'Invalid request input'}
    response = client.post('/admin/specs', data={'csrf_token':csrf(client),'name':'Example','speeds':'synthetic-secret'})
    # Full optional-number/domain validation is Phase 4B. This phase ensures
    # existing conversion failures are controlled and do not disclose inputs.
    assert response.status_code == 500 and 'synthetic-secret' not in response.text


def test_startup_security_failure_precedes_database_creation(database_path):
    import os
    import subprocess
    import sys
    env = {**os.environ, 'SESSION_SECRET':'', 'DATABASE_URL':f'sqlite:///{database_path}'}
    result = subprocess.run([sys.executable, '-c', 'import app.main'], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'SESSION_SECRET must' in result.stderr
    assert not database_path.exists()


def test_startup_database_diagnostics_are_redacted(monkeypatch):
    def fail(*args): raise main.MigrationError('Migration 2 failed at /private/path token=synthetic-secret')
    monkeypatch.setattr(main, 'migrate_database', fail)
    monkeypatch.setattr(main, 'setup_scheduler', lambda: pytest.fail('Scheduler started after failed migration'))
    with pytest.raises(main.MigrationError) as exc:
        with TestClient(main.app): pass
    assert 'Migration 2' in str(exc.value)
    assert '/private/' not in str(exc.value) and 'synthetic-secret' not in str(exc.value)
    assert exc.value.__suppress_context__
