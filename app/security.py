"""Browser request integrity and safe deployment configuration, not authentication.

Every caller able to reach ChainLoop is trusted. These controls do not consume
identity headers or provide access control; operators supply that externally.
"""
import ipaddress
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from itsdangerous import BadData, URLSafeTimedSerializer
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

SESSION_COOKIE = "chainloop_session"
SESSION_SECONDS = 8 * 60 * 60
MAX_FORM_BYTES = 64 * 1024
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
# This endpoint is an external callback stub, not a browser mutation. Do not
# extend this exception to a prefix or to integration control endpoints.
CSRF_EXEMPT = {("POST", "/webhooks/strava")}
logger = logging.getLogger("chainloop.security")


def parse_host(value: str) -> tuple[str, int | None]:
    """Parse an HTTP authority without accepting URL syntax or ambiguous hosts."""
    if not value or not value.isascii() or "%" in value or any(c.isspace() for c in value):
        raise ValueError("Invalid host")
    port_text = None
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            raise ValueError("Invalid host")
        host = str(ipaddress.IPv6Address(value[1:end]))
        rest = value[end + 1:]
        if rest:
            if not rest.startswith(":"):
                raise ValueError("Invalid host")
            port_text = rest[1:]
    else:
        if value.count(":") > 1:
            raise ValueError("IPv6 Host must be bracketed")
        host, separator, port_text = value.partition(":")
        port_text = port_text if separator else None
        host = host.lower()
        if len(host) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in host.split(".")
        ):
            raise ValueError("Invalid host")
        if re.fullmatch(r"[0-9.]+", host):
            host = str(ipaddress.IPv4Address(host))
    port = None
    if port_text is not None:
        if not re.fullmatch(r"[0-9]{1,5}", port_text) or not 1 <= int(port_text) <= 65535:
            raise ValueError("Invalid port")
        port = int(port_text)
    return host, port


def normalized_url(value: str) -> str:
    if not value or any(c.isspace() for c in value) or "\\" in value:
        raise ValueError("Invalid URL")
    url = urlsplit(value)
    if url.scheme not in {"https", "http"} or url.query or url.fragment or "?" in value or "#" in value:
        raise ValueError("Invalid URL")
    host, port = parse_host(url.netloc)
    authority = f"[{host}]" if ":" in host else host
    if port and port != (443 if url.scheme == "https" else 80):
        authority += f":{port}"
    return f"{url.scheme}://{authority}{url.path}"


@dataclass(frozen=True)
class SecuritySettings:
    base_url: str
    secret: str = field(repr=False)
    allowed_hosts: frozenset[str]
    secure_cookie: bool
    callback_url: str

    @classmethod
    def from_env(cls):
        secret = os.getenv("SESSION_SECRET", "")
        if (len(secret.encode()) < 32 or secret != secret.strip()
                or secret.lower() in {"replace-with-a-long-random-secret", "change-me"}
                or "replace-with" in secret.lower()):
            raise RuntimeError("SESSION_SECRET must be a generated random secret of at least 32 bytes")
        dev_value = os.getenv("CHAINLOOP_DEV_ALLOW_HTTP", "false").lower()
        if dev_value not in {"true", "false"}:
            raise RuntimeError("CHAINLOOP_DEV_ALLOW_HTTP must be true or false")
        try:
            base = normalized_url(os.getenv("APP_BASE_URL", "").rstrip("/"))
            url = urlsplit(base)
            if url.path:
                raise ValueError("Subpaths are not supported")
            host, _ = parse_host(url.netloc)
            if url.scheme == "http" and not (
                dev_value == "true" and host in {"localhost", "127.0.0.1", "::1"}
            ):
                raise ValueError("HTTP requires explicit loopback development mode")
        except ValueError:
            raise RuntimeError("APP_BASE_URL must be a canonical HTTPS origin (HTTP only in explicit loopback development mode)") from None
        callback = base + "/auth/strava/callback"
        try:
            explicit = os.getenv("STRAVA_REDIRECT_URI", callback)
            if normalized_url(explicit) != callback:
                raise ValueError("Callback mismatch")
        except ValueError:
            raise RuntimeError("STRAVA_REDIRECT_URI must match APP_BASE_URL + /auth/strava/callback") from None
        hosts = {host}
        try:
            for item in os.getenv("CHAINLOOP_ALLOWED_HOSTS", "").split(","):
                item = item.strip()
                if not item:
                    continue
                # Configuration accepts bare IPv6, whereas HTTP Host uses brackets.
                if item == "::1":
                    item = "[::1]"
                extra, port = parse_host(item)
                if port is not None:
                    raise ValueError("Configure hosts without ports")
                hosts.add(extra)
        except ValueError:
            raise RuntimeError("CHAINLOOP_ALLOWED_HOSTS must contain exact hostnames/IPs without ports or wildcards") from None
        return cls(base, secret, frozenset(hosts), url.scheme == "https", callback)


def new_session() -> dict:
    return {"sid": secrets.token_urlsafe(32), "csrf": secrets.token_urlsafe(32), "issued": int(time.time())}


def session_signer(settings: SecuritySettings):
    return URLSafeTimedSerializer(settings.secret, salt="chainloop-browser-session")


def read_session(request: Request, settings: SecuritySettings) -> dict:
    # Reject duplicate cookies rather than selecting an ambiguous session.
    cookie_values = [part.strip() for value in request.headers.getlist("cookie") for part in value.split(";")]
    if sum(part.startswith(SESSION_COOKIE + "=") for part in cookie_values) != 1:
        return {}
    try:
        data = session_signer(settings).loads(request.cookies.get(SESSION_COOKIE, ""), max_age=SESSION_SECONDS)
        if not isinstance(data, dict) or not all(
            isinstance(data.get(key), str) and TOKEN_PATTERN.fullmatch(data[key]) for key in ("sid", "csrf")
        ):
            return {}
        if type(data.get("issued")) is not int or not 0 <= time.time() - data["issued"] < SESSION_SECONDS:
            return {}
        return data
    except (BadData, TypeError, ValueError):
        return {}


class SafeAccessLog(logging.Filter):
    """Uvicorn access log arguments include URL queries, including OAuth codes."""
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            args[2] = str(args[2]).split("?", 1)[0]
            record.args = tuple(args)
        return True


def configure_safe_logging():
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, SafeAccessLog) for f in access.filters):
        access.addFilter(SafeAccessLog())
    # HTTP client informational logs include configured URLs. Application logs
    # report categories, never raw integration exceptions or payloads.
    logging.getLogger("httpx").setLevel(logging.WARNING)


class BrowserSecurityMiddleware:
    def __init__(self, app, settings: SecuritySettings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        scope.setdefault("state", {})
        # A fresh nonce per response, never in the browser session.
        nonce = secrets.token_urlsafe(32)
        request.state.csp_nonce = nonce
        session = read_session(request, self.settings)
        original = session.copy()
        request.state.browser_session = session
        started = False

        async def secured_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = (
                    "default-src 'self'; script-src 'self' 'nonce-" + nonce + "'; "
                    "script-src-attr 'none'; style-src 'self'; style-src-attr 'none'; "
                    "img-src 'self'; connect-src 'self'; object-src 'none'; "
                    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                )
                headers["X-Frame-Options"] = "DENY"
                headers["X-Content-Type-Options"] = "nosniff"
                # no-referrer makes browsers send Origin: null on form POSTs.
                # strict-origin preserves the origin but never paths/queries.
                headers["Referrer-Policy"] = "strict-origin"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                if not scope["path"].startswith("/static/") and scope["path"] not in {"/favicon.ico", "/favicon.png"}:
                    headers["Cache-Control"] = "no-store"
                if session and session != original:
                    remaining = max(0, SESSION_SECONDS - (int(time.time()) - session["issued"]))
                    cookie = Response()
                    cookie.set_cookie(SESSION_COOKIE, session_signer(self.settings).dumps(session),
                                      max_age=remaining, secure=self.settings.secure_cookie,
                                      httponly=True, samesite="lax", path="/")
                    headers.append("set-cookie", cookie.headers["set-cookie"])
            await send(message)

        async def reject(status, detail):
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, secured_send)

        try:
            hosts = request.headers.getlist("host")
            try:
                if len(hosts) != 1 or parse_host(hosts[0])[0] not in self.settings.allowed_hosts:
                    raise ValueError("Untrusted host")
                if not scope["path"].startswith("/"):
                    raise ValueError("Invalid request path")
            except ValueError:
                return await reject(400, "Invalid Host or request path")

            mutation = scope["method"] not in {"GET", "HEAD", "OPTIONS"}
            if mutation and (scope["method"], scope["path"]) not in CSRF_EXEMPT:
                if not session:
                    return await reject(403, "Invalid or expired CSRF session. Reload the page and try again.")
                origins = request.headers.getlist("origin")
                referers = request.headers.getlist("referer")
                try:
                    if origins:
                        if len(origins) != 1 or normalized_url(origins[0]) != self.settings.base_url:
                            raise ValueError("Foreign origin")
                    elif referers:
                        if len(referers) != 1:
                            raise ValueError("Ambiguous referrer")
                        ref = urlsplit(referers[0])
                        if normalized_url(f"{ref.scheme}://{ref.netloc}") != self.settings.base_url:
                            raise ValueError("Foreign referrer")
                except ValueError:
                    return await reject(403, "Invalid request origin")
                # Bound parsing before token validation. No file uploads are
                # supported, so parsing must never create temporary files.
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > MAX_FORM_BYTES:
                        return await reject(413, "Form request is too large")
                content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in {"application/x-www-form-urlencoded", "multipart/form-data"}:
                    return await reject(403, "A form CSRF token is required")
                async def replay():
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                parsed = Request(scope, replay)
                try:
                    async with parsed.form(max_files=0, max_fields=100, max_part_size=MAX_FORM_BYTES) as form:
                        tokens = form.getlist("csrf_token")
                        valid = (len(tokens) == 1 and isinstance(tokens[0], str)
                                 and TOKEN_PATTERN.fullmatch(tokens[0])
                                 and secrets.compare_digest(tokens[0], session["csrf"]))
                except Exception:
                    return await reject(403, "Invalid CSRF form")
                if not valid:
                    return await reject(403, "Invalid CSRF token. Reload the page and try again.")
                receive = replay
            await self.app(scope, receive, secured_send)
        except Exception as exc:
            # Never log raw exceptions: SQL parameters, URLs or integration
            # responses can contain tokens, private notes and deployment paths.
            logger.error("Request failed (%s)", type(exc).__name__)
            if not started:
                await reject(500, "An internal error occurred")


def template_security_context(request: Request) -> dict:
    session = request.state.browser_session
    if not session:
        session.update(new_session())
    return {"csrf_token": session["csrf"], "csp_nonce": request.state.csp_nonce}
