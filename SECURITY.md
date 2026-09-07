# Deployment security and Phase 4A baseline

**ChainLoop does not provide authentication or authorization. Anyone able to reach
it is fully trusted and can read, administer and change the installation. Do not
expose ChainLoop directly to the public Internet.**

## Operator-controlled trust boundary

The supported production model is:

```text
client
  -> private/internal network
  -> reverse proxy
  -> external authentication/access policy
  -> ChainLoop
```

Use an operator-controlled policy such as Traefik BasicAuth, Authelia, Authentik,
forward-auth, VPN access, IP allowlisting or equivalent protection. HTTPS or
network location alone does not establish access control. Verify that unauthorized
clients are blocked and that no backend route bypasses the selected policy.

The example Compose deployment uses Traefik without a published application port.
Do not add a public `8080:8080` port mapping as a production shortcut. Restrict
membership of the backend Docker network to trusted services. Apply access policy
to application pages, administration, maintenance, corrections, OAuth initiation,
synchronization, notification testing and JSON APIs. Proxy identity headers are
neither consumed nor needed; ChainLoop has no authenticated identity or roles.

Home Assistant may read existing GET APIs through operator-managed machine access
policy. Do not create a blanket API bypass or grant mutation access just to read
sensors. No Home Assistant features or native API credentials are added here.

Keep the Strava OAuth callback behind external policy when the provider supports
its return flow. Any necessary exception must match only the callback, which still
requires session-correlated OAuth state. `/webhooks/strava` POST is currently a
no-op acknowledgment, not activity ingestion; do not enable a broad webhook bypass.
The existing verification GET is not a supported complete webhook integration.

Rate limiting belongs primarily at the proxy. Concentrate it on OAuth initiation,
manual synchronization and Pushover tests; preserve scheduler traffic. Set proxy
request-size and concurrency limits. The application sync lock is process-local
and is not a sustained abuse control. Run one application worker for the existing
scheduler model.

## Required production configuration

- `APP_BASE_URL`: canonical external HTTPS origin, for example
  `https://chainloop.example.com`. A trailing slash is normalized; deployment under
  an application subpath is not supported. Userinfo, queries and fragments are rejected.
- `STRAVA_REDIRECT_URI`: retained for compatibility; if present it must normalize to
  `APP_BASE_URL` plus `/auth/strava/callback`. A mismatch fails startup.
- `SESSION_SECRET`: mandatory, at least 32 bytes, generated randomly. Empty/known
  placeholder values fail startup; length validation cannot establish randomness.
- `CHAINLOOP_ALLOWED_HOSTS`: optional additional exact hostnames/IP addresses,
  comma-separated, without ports or wildcards. The canonical hostname is always
  allowed. The Compose example adds `127.0.0.1,localhost` for its internal healthcheck.
- `CHAINLOOP_DEV_ALLOW_HTTP`: false by default; production does not require it.

Generate the secret with:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Keep it stable across restarts. Rotating it invalidates browser CSRF sessions and
pending OAuth flows; users reload forms and restart authorization. It does not
alter stored Strava access/refresh tokens or maintenance history.

Host parsing rejects duplicate Host headers, malformed syntax and unexpected
hosts, while accepting valid optional ports, IPv4 and bracketed IPv6. Allowed-host
checks are not authentication. The container disables forwarded-header processing.
Security-sensitive URLs and origin checks always use `APP_BASE_URL`, never arbitrary
`Forwarded`, `X-Forwarded-Host` or other proxy headers. Traefik should preserve the
canonical Host. Application redirects are relative; trailing-slash route aliases
are not automatically redirected.

TLS enforcement and HSTS are reverse-proxy responsibilities. The application does
not emit HSTS. Do not enable broad proxy trust such as `FORWARDED_ALLOW_IPS=*`.

## Browser integrity, not login sessions

The signed `chainloop_session` cookie contains random session/CSRF values, an
issuance timestamp and, during authorization, a pending OAuth nonce. It contains
no identity, password, access token or refresh token. Signing is not encryption.

Production cookie attributes are `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`,
with no Domain attribute and an eight-hour absolute lifetime. Session creation is
lazy, when rendering forms or initiating OAuth. Session and form tokens use 32
random bytes each. Tokens remain stable across tabs within the session.

Every ordinary browser mutation requires exactly one `csrf_token` form field
matching the valid session with constant-time comparison. Missing/expired/foreign
sessions or tokens return 403 before handlers, database mutations, file writes or
outbound integration calls. Reload the form and try again; no automatic retry is
performed. Origin/Referer checks add defense in depth but never replace the token.

The CSRF gate also bounds forms to 64 KiB and 100 fields and refuses file parts,
so validating a token cannot create upload temporary files. ChainLoop has no
upload feature. Existing scripted POSTs need the same session/form flow; returning
JSON does not exempt a mutation. Read-only JSON APIs are unchanged except for
redacting historical integration-error notes.

Strava OAuth state is separate from form CSRF. It remains signed and expires in
ten minutes, and now correlates a random nonce and session ID with the initiating
browser. A normal completed callback removes the pending nonce in the updated
cookie, rejecting normal browser replay. Starting another OAuth flow replaces the
pending nonce. A copied older valid signed cookie is not revoked server-side:
stateless sessions cannot guarantee one-time revocation, and Strava also enforces
its authorization-code lifetime/reuse rules. Callback tests model this limitation
explicitly. Callbacks do not require ordinary form CSRF tokens.

## Browser headers and output

The CSP permits local scripts/styles and a fresh response-specific nonce for the
JSON chart data block. Executable chart JavaScript is a static local asset. It
contains no `unsafe-inline`, `unsafe-eval` or wildcard script sources. Inline
styles/event handlers are disallowed. Jinja autoescaping, `tojson` and DOM
`textContent` preserve contextual encoding; notes remain plain text.

Policies include `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'none'`,
`form-action 'self'`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: strict-origin` and disabled camera/microphone/geolocation permissions.
Framing ChainLoop is disabled, without affecting JSON API integrations.
`strict-origin` omits referrer paths and queries. Unlike `no-referrer`, it does not
make Chromium send `Origin: null` on ordinary same-origin form POSTs.

Headers cover normal pages, redirects, errors and static responses. Non-static
responses use `Cache-Control: no-store`; static assets retain their normal caching.
A response that has already begun streaming cannot be replaced with a new error.

## Health, errors and sensitive storage

`/health` reports only `status`, `app`, `version`; a failed database check returns
503 with `status: unavailable`. Configuration, schedules and integration errors
are no longer exposed. Interactive OpenAPI/Swagger/ReDoc routes are disabled.

Integration failures use generic messages rather than raw exceptions or URLs.
Old sync errors are not shown, and historical failed-notification notes are
redacted in HTML/API output without rewriting SQLite history. Ordinary maintenance
notes retain their text. Request-validation responses do not echo submitted values.
Unhandled request logs report exception categories, not traceback/parameter data.
Startup migration failures still prevent startup; diagnostics omit raw database
exception details. Consult upgrade/schema guidance and backups before retrying.

Uvicorn access logging omits query strings. Configure the reverse proxy and any
external logging/tracing system to omit/redact OAuth callback queries, cookies,
authorization headers, request bodies and secret configuration too. Do not enable
HTTP client wire/debug logging on a live installation.

SQLite stores Strava OAuth access/refresh tokens in plaintext. Treat the database,
journal/WAL/SHM files, configuration and backups as credentials: restrict access
and protect backup storage. No ad-hoc encryption-at-rest or token-storage migration
is introduced. Anyone who can read these files may obtain integration credentials.

## Explicit Docker Sandbox HTTP development

Use synthetic data and a disposable configuration, never production secrets/data:

```dotenv
APP_BASE_URL=http://127.0.0.1:18080
CHAINLOOP_DEV_ALLOW_HTTP=true
CHAINLOOP_ALLOWED_HOSTS=127.0.0.1,localhost
SESSION_SECRET=<generated-random-secret>
STRAVA_AUTO_SYNC=false
```

Omit `STRAVA_REDIRECT_URI` or set the matching loopback callback. The HTTP exception
accepts only `localhost`, `127.0.0.1` and `::1`; it never permits LAN/public HTTP
origins. It disables only cookie Secure for an HTTP canonical origin. HttpOnly,
SameSite, signing, expiry, CSRF, Host validation and browser headers remain enabled.
A startup notice identifies development mode. Secure cookie behavior is independent
of whether the internal reverse-proxy connection uses HTTP.

The disposable ChainLoop container listens on `0.0.0.0:8080`; nested Docker publishes
sandbox `0.0.0.0:8080` to container `8080`. The outer Sandbox maps host
`127.0.0.1:18080` to sandbox `8080`. Never publish nested Docker on sandbox port
18080, and do not change production Compose for this test setup.

Verify sandbox `http://127.0.0.1:8080/health` and host
`http://127.0.0.1:18080/health`. Use the canonical host URL for browser mutations;
origin validation intentionally rejects forms submitted from a different port.

## Phase boundary

Phase 4A does not introduce authentication, RBAC, proxy identity handling, native
API credentials, uploads, webhook ingestion or Home Assistant features. Full
input/domain validation, configurable outbound URL/SSRF controls, non-root/container
filesystem hardening and broader abuse controls remain Phase 4B. Accounting,
activity attribution, wax-cycle semantics, migration execution and notification
rules are unchanged. Do not mistake this baseline for safe direct Internet exposure.

## Dependency audit snapshot (2026-09-07)

Before version edits, `pip-audit 2.10.1 -r requirements.txt` reported 19 entries
across three packages, representing 17 unique advisories (two Starlette entries
were duplicated by the advisory source):

| Package before | Reported CVEs | Remediation |
|---|---|---|
| Jinja2 3.1.4 | 2024-56201, 2024-56326, 2025-27516 | 3.1.6 |
| python-multipart 0.0.9 | 2024-53981, 2026-24486, 2026-40347, 2026-42561, 2026-53538, 2026-53539, 2026-53540 | 0.0.31 |
| Starlette 0.38.6 | 2024-47874, 2025-54121, 2026-48710, 2026-48817, 2026-48818, 2026-54282, 2026-54283 | 1.3.1 |

Form parser CPU/memory exhaustion is relevant to the current form routes even
without uploads. Jinja findings require untrusted template control, which this
application does not provide. Windows-specific StaticFiles, custom HTTPEndpoint
and nondefault multipart upload/direct-parser findings do not match this deployment.

FastAPI moves from 0.115.0 to 0.136.0 because the former constrains Starlette below
the patched release. Other direct runtime dependency versions are unchanged.
The post-update requirements audit reports **zero known vulnerabilities**. This
is a point-in-time Python dependency audit, not an operating-system image scan.

Two test-only deprecation warnings remain: Starlette's legacy HTTPX TestClient
integration and AnyIO's `BlockingPortal` alias. The IPv6 test exercises the wire
Host directly because that legacy test transport cannot split an IPv6 netloc.
These do not justify unrelated runtime/client migrations in Phase 4A.
