"""Strict validation at ChainLoop's browser, configuration and Strava boundaries."""

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import HTTPException

MAX_NAME = 100
MAX_CODE = 64
MAX_NOTE = 2_000
MAX_EXTERNAL_NAME = 255
MAX_DISTANCE_KM = 1_000_000.0
MAX_ACTIVITY_KM = 10_000.0
MAX_THRESHOLD_KM = 100_000.0
MAX_SQLITE_ID = 9_223_372_036_854_775_807
MAX_TOKEN_RESPONSE = 64 * 1024
MAX_GEAR_RESPONSE = 64 * 1024
MAX_ACTIVITY_RESPONSE = 2 * 1024 * 1024
CHAIN_CODE_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,63}\Z")
GEAR_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
RIDE_TYPES = frozenset({
    "Ride", "VirtualRide", "MountainBikeRide", "GravelRide", "EBikeRide",
    "EMountainBikeRide", "Velomobile",
})


def bounded_text(value: str, label: str, maximum: int, *, required: bool = False,
                 trim: bool = False) -> str:
    if not isinstance(value, str):
        raise HTTPException(422, "Invalid request input")
    result = value.strip() if trim else value
    if required and not result.strip():
        raise HTTPException(400, f"{label} is required")
    if len(result) > maximum or CONTROL_PATTERN.search(result):
        raise HTTPException(422, "Invalid request input")
    return result


def finite_number(value: float, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise HTTPException(422, f"{label} is outside the allowed range")
    return value


def positive_id(value: int, label: str = "ID") -> int:
    if isinstance(value, bool) or not 1 <= value <= MAX_SQLITE_ID:
        raise HTTPException(422, f"Invalid {label}")
    return value


def optional_positive_id(value: str, label: str = "ID") -> int | None:
    value = bounded_text(value, label, 32, trim=True)
    if not value:
        return None
    if not value.isascii() or not value.isdecimal():
        raise HTTPException(422, "Invalid request input")
    return positive_id(int(value), label)


def optional_integer(value: str, label: str, minimum: int, maximum: int) -> int | None:
    value = bounded_text(value, label, 16, trim=True)
    if not value:
        return None
    if not re.fullmatch(r"[0-9]+", value):
        raise HTTPException(422, "Invalid request input")
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise HTTPException(422, f"{label} is outside the allowed range")
    return parsed


def optional_finite_number(value: str, label: str, minimum: float, maximum: float) -> float | None:
    value = bounded_text(value, label, 64, trim=True)
    if not value:
        return None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise HTTPException(422, "Invalid request input") from exc
    return finite_number(parsed, label, minimum, maximum)


def chain_code(value: str) -> str:
    value = bounded_text(value, "Chain code", MAX_CODE, required=True, trim=True).upper()
    if not CHAIN_CODE_PATTERN.fullmatch(value):
        raise HTTPException(422, "Invalid chain code")
    return value


def gear_id(value: str) -> str:
    value = bounded_text(value, "Gear ID", MAX_CODE, required=True, trim=True)
    if not GEAR_ID_PATTERN.fullmatch(value):
        raise HTTPException(422, "Invalid gear ID")
    return value


def local_date(value: str, app_timezone: ZoneInfo) -> datetime:
    value = bounded_text(value, "Date", 10, required=True, trim=True)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise HTTPException(422, "Date must use YYYY-MM-DD")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(422, "Date must use YYYY-MM-DD") from exc
    today = datetime.now(app_timezone).date()
    if parsed < date(1900, 1, 1) or parsed > today:
        raise HTTPException(422, "Date is outside the allowed range")
    # Preserve ChainLoop's established date-only storage convention (UTC
    # midnight); only the "today" comparison uses the configured local zone.
    return datetime.combine(parsed, datetime.min.time(), tzinfo=timezone.utc)


def strict_form_bool(value: str | bool | None, label: str) -> bool:
    if type(value) is bool:
        return value
    if value is None or value in {"", "false", "0", "off"}:
        return False
    if value in {"true", "1", "on"}:
        return True
    raise HTTPException(422, f"Invalid {label}")


def validate_timezone(value: str) -> ZoneInfo:
    if len(value) > 64 or not re.fullmatch(r"[A-Za-z0-9._+/-]+", value):
        raise RuntimeError("CHAINLOOP_TIMEZONE must be a valid IANA timezone")
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError:
        raise RuntimeError("CHAINLOOP_TIMEZONE must be a valid IANA timezone") from None


def validate_strava_api_base(value: str, allow_loopback_mock: bool) -> str:
    raw = value.rstrip("/")
    if len(raw) > 2048 or any(char.isspace() or ord(char) < 32 for char in raw):
        raise RuntimeError("STRAVA_API_BASE_URL is not an allowed Strava API endpoint")
    try:
        parsed = urlsplit(raw)
        parsed.port  # Reject malformed/out-of-range ports before creating a client.
    except ValueError:
        raise RuntimeError("STRAVA_API_BASE_URL is not an allowed Strava API endpoint") from None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("STRAVA_API_BASE_URL is not an allowed Strava API endpoint")
    if raw == "https://www.strava.com/api/v3":
        return raw
    loopbacks = {"localhost", "127.0.0.1", "::1"}
    if (allow_loopback_mock and parsed.scheme in {"http", "https"}
            and parsed.hostname in loopbacks and parsed.path.rstrip("/") == "/api/v3"):
        return raw
    raise RuntimeError("STRAVA_API_BASE_URL is not an allowed Strava API endpoint")


def bounded_json_request(method: str, url: str, maximum: int, **kwargs) -> Any:
    """Read a bounded JSON response without following redirects or trusting proxy env."""
    size = 0
    chunks: list[bytes] = []
    headers = dict(kwargs.pop("headers", {}))
    headers["Accept-Encoding"] = "identity"
    with httpx.stream(method, url, headers=headers, follow_redirects=False, verify=True, trust_env=False, **kwargs) as response:
        response.raise_for_status()
        # Reject compressed responses instead of allowing a decompression bomb
        # to allocate memory before the application can enforce its byte budget.
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            raise ValueError("External response used an unsupported encoding")
        for chunk in response.iter_raw(chunk_size=8192):
            size += len(chunk)
            if size > maximum:
                raise ValueError("External response exceeded its size limit")
            chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("External response was not valid JSON") from exc


def _strict_int(value: Any, minimum: int = 1, maximum: int = MAX_SQLITE_ID) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid integer")
    return value


def _strict_string(value: Any, maximum: int, *, pattern: re.Pattern | None = None) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or CONTROL_PATTERN.search(value):
        raise ValueError("invalid string")
    if pattern and not pattern.fullmatch(value):
        raise ValueError("invalid string")
    return value


def validate_strava_token_payload(data: Any, *, require_athlete: bool) -> dict:
    if not isinstance(data, dict):
        raise ValueError("invalid token payload")
    access = _strict_string(data.get("access_token"), 4096)
    refresh = data.get("refresh_token")
    if refresh is not None:
        refresh = _strict_string(refresh, 4096)
    expires = _strict_int(data.get("expires_at"), 1)
    result = {"access_token": access, "refresh_token": refresh, "expires_at": expires}
    if require_athlete:
        if refresh is None:
            raise ValueError("invalid refresh token")
        athlete = data.get("athlete")
        if not isinstance(athlete, dict):
            raise ValueError("invalid athlete payload")
        result["athlete_id"] = str(_strict_int(athlete.get("id")))
    return result


def validate_strava_activity(item: Any, current_time: datetime) -> dict:
    if not isinstance(item, dict):
        raise ValueError("record_shape")
    external_id = str(_strict_int(item.get("id")))
    sport_type = _strict_string(item.get("sport_type"), 64)
    timestamp = _strict_string(item.get("start_date"), 64)
    try:
        occurred = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp") from exc
    if occurred.tzinfo is None:
        raise ValueError("timestamp")
    try:
        occurred = occurred.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError("timestamp_range") from None
    if occurred < datetime(2000, 1, 1, tzinfo=timezone.utc) or occurred > current_time + timedelta(days=1):
        raise ValueError("timestamp_range")
    distance_m = item.get("distance")
    if isinstance(distance_m, bool) or not isinstance(distance_m, (int, float)):
        raise ValueError("distance_type")
    if not 0 <= distance_m <= MAX_ACTIVITY_KM * 1000:
        raise ValueError("distance_range")
    distance_km = float(distance_m) / 1000
    if not math.isfinite(distance_km) or not 0 <= distance_km <= MAX_ACTIVITY_KM:
        raise ValueError("distance_range")
    raw_gear = item.get("gear_id")
    if raw_gear is not None:
        raw_gear = _strict_string(raw_gear, MAX_CODE, pattern=GEAR_ID_PATTERN)
    raw_name = item.get("name")
    if raw_name is not None:
        raw_name = _strict_string(raw_name, MAX_EXTERNAL_NAME)
    return {"external_id": external_id, "sport_type": sport_type, "occurred_at": occurred,
            "distance_km": distance_km, "gear_id": raw_gear, "name": raw_name}


def validate_strava_gear_payload(data: Any) -> dict:
    if not isinstance(data, dict):
        raise ValueError("invalid gear payload")
    name = data.get("name")
    if name is not None:
        name = _strict_string(name, MAX_EXTERNAL_NAME)
    distance = data.get("distance")
    distance_km = None
    if distance is not None:
        if isinstance(distance, bool) or not isinstance(distance, (int, float)):
            raise ValueError("invalid gear distance")
        if not 0 <= distance <= MAX_DISTANCE_KM * 1000:
            raise ValueError("invalid gear distance")
        distance_km = float(distance) / 1000
        if not math.isfinite(distance_km) or not 0 <= distance_km <= MAX_DISTANCE_KM:
            raise ValueError("invalid gear distance")
    primary = data.get("primary")
    if primary is not None and type(primary) is not bool:
        raise ValueError("invalid gear primary flag")
    return {"name": name, "distance_km": distance_km, "primary": primary}
