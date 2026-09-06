import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import mean, median
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import create_engine, select, func, UniqueConstraint
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session

from app.migrations import migrate_database

APP_NAME = "ChainLoop"
APP_VERSION = "0.7.0"
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# --- Runtime configuration -------------------------------------------------
# Keep persistent application state in the bind-mounted /data directory by default.
DEFAULT_DATABASE_URL = "sqlite:////data/chainloop.db"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)

APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8080").rstrip("/")
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me")
CHAINLOOP_TIMEZONE = os.getenv("CHAINLOOP_TIMEZONE", "Europe/Amsterdam")
APP_TZ = ZoneInfo(CHAINLOOP_TIMEZONE)

STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET", "")
STRAVA_REDIRECT_URI = os.getenv("STRAVA_REDIRECT_URI", f"{APP_BASE_URL}/auth/strava/callback")
STRAVA_SCOPES = os.getenv("STRAVA_SCOPES", "activity:read_all")
STRAVA_API_BASE_URL = os.getenv("STRAVA_API_BASE_URL", "https://www.strava.com/api/v3").rstrip("/")
STRAVA_VERIFY_TOKEN = os.getenv("STRAVA_VERIFY_TOKEN", "")
STRAVA_AUTO_SYNC = os.getenv("STRAVA_AUTO_SYNC", "true").strip().lower() in {"1", "true", "yes", "on"}
STRAVA_SYNC_TIME = os.getenv("STRAVA_SYNC_TIME", "21:00").strip()
# Optional comma-separated list; when set it takes precedence over STRAVA_SYNC_TIME.
STRAVA_SYNC_TIMES = os.getenv("STRAVA_SYNC_TIMES", "").strip()

PUSHOVER_APP_TOKEN = os.getenv("PUSHOVER_APP_TOKEN", "")
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY", "")

def sqlite_database_path(database_url: str) -> Path | None:
    """Resolve a file-backed SQLite URL without opening the database."""
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return None
    database = url.database
    if not database or database == ":memory:":
        return None
    if database.startswith("file:"):
        database = database[5:].split("?", 1)[0]
    return Path(database).expanduser().resolve()


def validate_test_database_url(database_url: str) -> None:
    """Prevent pytest imports from touching persistent or arbitrary databases."""
    if os.getenv("CHAINLOOP_TESTING") != "1":
        return
    root_value = os.getenv("CHAINLOOP_TEST_TMPDIR")
    if not root_value:
        raise RuntimeError("CHAINLOOP_TEST_TMPDIR is required when CHAINLOOP_TESTING=1")
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        raise RuntimeError("Tests may only use SQLite databases")
    database_path = sqlite_database_path(database_url)
    if database_path is None:
        return
    temporary_root = Path(root_value).expanduser().resolve()
    try:
        database_path.relative_to(temporary_root)
    except ValueError as exc:
        raise RuntimeError(
            f"Refusing test database outside pytest temporary directory: {database_path}"
        ) from exc


def create_database_engine(database_url: str) -> Engine:
    """Create ChainLoop's SQLite engine through one testable safety seam."""
    validate_test_database_url(database_url)
    return create_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 30},
    )


def migrate_legacy_database(database_url: str) -> None:
    """Preserve the original production filename migration."""
    if database_url != DEFAULT_DATABASE_URL:
        return
    legacy_db = Path("/data/chain_tracker.db")
    current_db = Path("/data/chainloop.db")
    if legacy_db.exists() and not current_db.exists():
        legacy_db.rename(current_db)


# Engine construction is lazy and performs no filesystem or database I/O. The
# filename migration and schema migrations run only from FastAPI lifespan.
validate_test_database_url(DATABASE_URL)
engine = create_database_engine(DATABASE_URL)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
signer = URLSafeTimedSerializer(SESSION_SECRET, salt="chainloop-oauth-state")
sync_lock = threading.Lock()
scheduler_stop = threading.Event()
scheduler_thread: threading.Thread | None = None


# --- Database models -------------------------------------------------------
class Base(DeclarativeBase):
    pass


class Person(Base):
    __tablename__ = "people"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


class Bike(Base):
    __tablename__ = "bikes"
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int | None]
    name: Mapped[str]
    strava_gear_id: Mapped[str | None] = mapped_column(unique=True)
    chain_spec_id: Mapped[int | None]
    warning_km: Mapped[float] = mapped_column(default=500)
    change_km: Mapped[float] = mapped_column(default=600)
    overdue_km: Mapped[float] = mapped_column(default=800)
    tracking_start_at: Mapped[datetime | None]


class ChainSpec(Base):
    __tablename__ = "chain_specs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    speeds: Mapped[int | None]
    link_count: Mapped[int | None]
    manufacturer: Mapped[str | None]
    model: Mapped[str | None]


class WaxProduct(Base):
    __tablename__ = "wax_products"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    notes: Mapped[str | None]
    archived: Mapped[bool] = mapped_column(default=False)


class Chain(Base):
    __tablename__ = "chains"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True)
    bike_id: Mapped[int]
    chain_spec_id: Mapped[int]
    status: Mapped[str] = mapped_column(default="READY")
    first_used_at: Mapped[datetime | None]
    total_km: Mapped[float] = mapped_column(default=0)
    km_since_wax: Mapped[float] = mapped_column(default=0)
    current_wear_percent: Mapped[float | None]
    last_wear_at: Mapped[datetime | None]
    retired_at: Mapped[datetime | None]


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_activity_source_external"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str]
    external_id: Mapped[str]
    occurred_at: Mapped[datetime]
    distance_km: Mapped[float]
    bike_id: Mapped[int | None]
    credited_chain_id: Mapped[int | None]
    processed: Mapped[bool] = mapped_column(default=False)
    excluded: Mapped[bool] = mapped_column(default=False)
    raw_gear_id: Mapped[str | None]
    raw_name: Mapped[str | None]


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime]
    event_type: Mapped[str]
    chain_id: Mapped[int | None]
    bike_id: Mapped[int | None]
    activity_id: Mapped[int | None]
    distance_km: Mapped[float | None]
    note: Mapped[str | None]
    metadata_json: Mapped[str | None]


class WearMeasurement(Base):
    __tablename__ = "wear_measurements"
    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int]
    measured_at: Mapped[datetime]
    wear_percent: Mapped[float]
    total_km_at_measurement: Mapped[float | None]
    timing: Mapped[str] = mapped_column(default="before_wax")
    note: Mapped[str | None]


class WaxEvent(Base):
    __tablename__ = "wax_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int]
    wax_product_id: Mapped[int]
    applied_at: Mapped[datetime]
    km_since_previous_wax: Mapped[float]
    note: Mapped[str | None]


class StravaToken(Base):
    __tablename__ = "strava_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[str]
    access_token: Mapped[str]
    refresh_token: Mapped[str]
    expires_at: Mapped[int]


class StravaGear(Base):
    __tablename__ = "strava_gears"
    gear_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str | None]
    distance_km: Mapped[float | None]
    primary: Mapped[bool | None]
    updated_at: Mapped[datetime]


class NotificationState(Base):
    __tablename__ = "notification_states"
    __table_args__ = (UniqueConstraint("chain_id", "wax_cycle_key", "level", name="uq_notification_cycle_level"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int]
    wax_cycle_key: Mapped[str]
    level: Mapped[str]
    state: Mapped[str]  # SENT or SUPPRESSED
    created_at: Mapped[datetime]


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime]
    completed_at: Mapped[datetime | None]
    trigger: Mapped[str]
    success: Mapped[bool]
    imported: Mapped[int] = mapped_column(default=0)
    processed: Mapped[int] = mapped_column(default=0)
    added_km: Mapped[float] = mapped_column(default=0)
    error: Mapped[str | None]


class MigrationMarker(Base):
    __tablename__ = "migration_markers"
    key: Mapped[str] = mapped_column(primary_key=True)
    applied_at: Mapped[datetime]


def now() -> datetime:
    return datetime.now(timezone.utc)


def seed() -> None:
    """Do not invent equipment on a fresh installation.

    Since v0.7 the Administration UI can create riders, chain specifications,
    bikes, chains and wax products. Existing databases are never modified here.
    """
    return


def current_chain(db: Session, bike_id: int) -> Chain | None:
    return db.scalar(select(Chain).where(Chain.bike_id == bike_id, Chain.status == "IN_USE"))


def latest_wax_event(db: Session, chain_id: int) -> WaxEvent | None:
    return db.scalar(
        select(WaxEvent)
        .where(WaxEvent.chain_id == chain_id)
        .order_by(WaxEvent.applied_at.desc(), WaxEvent.id.desc())
        .limit(1)
    )


def latest_wax_details(db: Session, chain_id: int) -> dict | None:
    row = db.execute(
        select(WaxEvent, WaxProduct.name)
        .join(WaxProduct, WaxEvent.wax_product_id == WaxProduct.id)
        .where(WaxEvent.chain_id == chain_id)
        .order_by(WaxEvent.applied_at.desc(), WaxEvent.id.desc())
        .limit(1)
    ).first()
    if not row:
        return None
    event, product_name = row
    return {
        "id": event.id,
        "product": product_name,
        "product_id": event.wax_product_id,
        "applied_at": event.applied_at,
        "km_since_previous_wax": event.km_since_previous_wax,
    }


def latest_wax_product_name(db: Session, chain_id: int) -> str | None:
    details = latest_wax_details(db, chain_id)
    return details["product"] if details else None


def wax_cycle_summary(db: Session, chain_id: int) -> dict:
    """Return user-facing wax treatment/cycle numbering.

    The first wax treatment starts cycle 1. A cycle is considered completed when
    a later wax event closes that interval. Chains without a wax event have no
    active cycle rather than a misleading 'cycle 0'.
    """
    treatments = int(db.scalar(select(func.count(WaxEvent.id)).where(WaxEvent.chain_id == chain_id)) or 0)
    return {
        "treatments": treatments,
        "current_cycle": treatments if treatments else None,
        "completed_cycles": max(0, treatments - 1),
    }


def active_wax_products(db: Session) -> list[WaxProduct]:
    return db.scalars(select(WaxProduct).where(WaxProduct.archived.is_(False)).order_by(WaxProduct.name)).all()


# A wax event ID is a stable boundary for notification deduplication and statistics.
def wax_cycle_key(db: Session, chain_id: int, at: datetime | None = None) -> str:
    query = select(WaxEvent).where(WaxEvent.chain_id == chain_id)
    if at is not None:
        query = query.where(WaxEvent.applied_at <= at)
    event = db.scalar(query.order_by(WaxEvent.applied_at.desc(), WaxEvent.id.desc()).limit(1))
    return f"wax:{event.id}" if event else "wax:none"


def record_event(
    db: Session,
    event_type: str,
    chain_id: int | None = None,
    bike_id: int | None = None,
    activity_id: int | None = None,
    distance_km: float | None = None,
    note: str | None = None,
    metadata: dict | None = None,
    created_at: datetime | None = None,
) -> None:
    db.add(
        Event(
            created_at=created_at or now(),
            event_type=event_type,
            chain_id=chain_id,
            bike_id=bike_id,
            activity_id=activity_id,
            distance_km=distance_km,
            note=note,
            metadata_json=json.dumps(metadata) if metadata is not None else None,
        )
    )


def format_local_datetime(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(APP_TZ).strftime("%Y-%m-%d %H:%M:%S")


templates.env.filters["localdt"] = format_local_datetime


# --- Maintenance and notifications ---------------------------------------
def service_status(bike: Bike, chain: Chain | None) -> dict:
    if not chain:
        return {"level": "NO_CHAIN", "label": "No active chain", "css": "bad", "emoji": "⛔️"}
    km = chain.km_since_wax
    if km >= bike.overdue_km:
        return {"level": "OVERDUE", "label": "Overdue", "css": "bad", "emoji": "⛔️"}
    if km >= bike.change_km:
        return {"level": "CHANGE", "label": "Change due", "css": "bad", "emoji": "🔧"}
    if km >= bike.warning_km:
        return {"level": "WARNING", "label": "Approaching service", "css": "warn", "emoji": "⚠️"}
    return {"level": "OK", "label": "OK", "css": "ok", "emoji": "✓"}


def send_pushover(title: str, message: str) -> tuple[bool, str | None]:
    if not PUSHOVER_APP_TOKEN or not PUSHOVER_USER_KEY:
        return False, "Pushover is not configured"
    try:
        response = httpx.post(
            "https://api.pushover.net/1/messages.json",
            data={
                "token": PUSHOVER_APP_TOKEN,
                "user": PUSHOVER_USER_KEY,
                "title": title,
                "message": message,
            },
            timeout=15,
        )
        response.raise_for_status()
        return True, None
    except httpx.HTTPError as exc:
        return False, str(exc)


# NotificationState is deliberately separate from Event: events are audit history,
# while this table answers the operational question "may I notify again this wax cycle?".
def notification_already_handled(db: Session, chain_id: int, cycle_key: str, level: str) -> bool:
    return db.scalar(
        select(NotificationState.id).where(
            NotificationState.chain_id == chain_id,
            NotificationState.wax_cycle_key == cycle_key,
            NotificationState.level == level,
        ).limit(1)
    ) is not None


def mark_notification(db: Session, chain_id: int, cycle_key: str, level: str, state: str) -> None:
    if not notification_already_handled(db, chain_id, cycle_key, level):
        db.add(NotificationState(chain_id=chain_id, wax_cycle_key=cycle_key, level=level, state=state, created_at=now()))


def check_distance_thresholds(db: Session, bike: Bike, chain: Chain) -> None:
    """Send each threshold at most once per wax cycle; failed deliveries can retry later."""
    if not PUSHOVER_APP_TOKEN or not PUSHOVER_USER_KEY:
        return

    cycle_key = wax_cycle_key(db, chain.id)
    levels = [
        ("WARNING", bike.warning_km, f"⚠️ {chain.code} approaching service — {chain.km_since_wax:.0f} km since wax"),
        ("CHANGE", bike.change_km, f"🔧 {chain.code} change due — {chain.km_since_wax:.0f} km since wax"),
        ("OVERDUE", bike.overdue_km, f"⛔️ {chain.code} overdue — {chain.km_since_wax:.0f} km since wax"),
    ]

    for level, threshold, message in levels:
        if chain.km_since_wax < threshold or notification_already_handled(db, chain.id, cycle_key, level):
            continue
        sent, error = send_pushover(f"ChainLoop · {bike.name}", message)
        record_event(
            db,
            f"PUSHOVER_{level}_{'SENT' if sent else 'FAILED'}",
            chain_id=chain.id,
            bike_id=bike.id,
            note=error,
            metadata={"threshold_km": threshold, "km_since_wax": chain.km_since_wax, "wax_cycle_key": cycle_key},
        )
        if sent:
            mark_notification(db, chain.id, cycle_key, level, "SENT")

    if chain.km_since_wax >= bike.change_km:
        ready = db.scalar(
            select(Chain.id)
            .where(
                Chain.bike_id == bike.id,
                Chain.status == "READY",
                Chain.id != chain.id,
                select(WaxEvent.id).where(WaxEvent.chain_id == Chain.id).exists(),
            )
            .limit(1)
        )
        if ready is None and not notification_already_handled(db, chain.id, cycle_key, "NO_SPARE"):
            sent, error = send_pushover(
                f"ChainLoop · {bike.name}",
                f"⚠️ {bike.name} needs a chain change but no waxed spare is ready.",
            )
            record_event(
                db,
                f"PUSHOVER_NO_SPARE_{'SENT' if sent else 'FAILED'}",
                chain_id=chain.id,
                bike_id=bike.id,
                note=error,
                metadata={"km_since_wax": chain.km_since_wax, "wax_cycle_key": cycle_key},
            )
            if sent:
                mark_notification(db, chain.id, cycle_key, "NO_SPARE", "SENT")


# Historical imports may cross thresholds; mark them handled without generating retroactive alerts.
def suppress_reached_thresholds(db: Session, bike: Bike, chain: Chain) -> None:
    cycle_key = wax_cycle_key(db, chain.id)
    for level, threshold in [
        ("WARNING", bike.warning_km),
        ("CHANGE", bike.change_km),
        ("OVERDUE", bike.overdue_km),
    ]:
        if chain.km_since_wax >= threshold:
            mark_notification(db, chain.id, cycle_key, level, "SUPPRESSED")


def ensure_activity_datetime(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


# --- Ride accounting -------------------------------------------------------
def adjust_chain_cycle_distance(db: Session, chain: Chain, occurred_at: datetime, delta_km: float) -> None:
    """Adjust the correct wax interval for a ride without rewriting history."""
    occurred = ensure_activity_datetime(occurred_at)
    chain.total_km += delta_km
    if chain.total_km < -0.001:
        raise HTTPException(400, "Correction would make chain lifetime distance negative")
    chain.total_km = max(0.0, chain.total_km)

    # A corrected old ride belongs to the wax interval that ended at the next wax event;
    # only rides after the latest wax are allowed to change the live km_since_wax counter.
    next_wax = db.scalar(
        select(WaxEvent)
        .where(WaxEvent.chain_id == chain.id, WaxEvent.applied_at > occurred)
        .order_by(WaxEvent.applied_at.asc(), WaxEvent.id.asc())
        .limit(1)
    )
    if next_wax:
        next_wax.km_since_previous_wax += delta_km
        if next_wax.km_since_previous_wax < -0.001:
            raise HTTPException(400, "Correction would make a historical wax interval negative")
        next_wax.km_since_previous_wax = max(0.0, next_wax.km_since_previous_wax)
    else:
        chain.km_since_wax += delta_km
        if chain.km_since_wax < -0.001:
            raise HTTPException(400, "Correction would make current wax distance negative")
        chain.km_since_wax = max(0.0, chain.km_since_wax)


# Keep attribution to a physical chain explicit so later corrections remain reversible.
def apply_activity_to_chain(db: Session, activity: Activity, bike: Bike, chain: Chain, notify: bool = True) -> bool:
    if activity.processed or activity.excluded:
        return False
    if bike.tracking_start_at is None:
        return False
    occurred = ensure_activity_datetime(activity.occurred_at)
    start = ensure_activity_datetime(bike.tracking_start_at)
    if occurred < start:
        return False
    if chain.bike_id != bike.id:
        return False
    # Retirement prevents new attribution, but it must not erase the chain's
    # historical eligibility for rides that occurred before it was retired.
    if chain.status == "RETIRED" and (
        chain.retired_at is None or occurred > ensure_activity_datetime(chain.retired_at)
    ):
        return False
    if chain.first_used_at and occurred < ensure_activity_datetime(chain.first_used_at):
        return False

    adjust_chain_cycle_distance(db, chain, activity.occurred_at, activity.distance_km)
    activity.processed = True
    activity.credited_chain_id = chain.id
    record_event(
        db,
        "RIDE_ADDED",
        chain_id=chain.id,
        bike_id=bike.id,
        activity_id=activity.id,
        distance_km=activity.distance_km,
        metadata={"source": activity.source, "external_id": activity.external_id},
    )
    if notify and chain.status == "IN_USE":
        check_distance_thresholds(db, bike, chain)
    return True


def apply_activity(db: Session, activity: Activity, notify: bool = True) -> bool:
    if activity.processed or activity.excluded or not activity.bike_id:
        return False
    bike = db.get(Bike, activity.bike_id)
    if not bike:
        return False
    chain = current_chain(db, bike.id)
    if not chain:
        return False
    return apply_activity_to_chain(db, activity, bike, chain, notify=notify)


# Corrections always undo the old accounting first; totals are never edited "in place" invisibly.
def reverse_activity_credit(db: Session, activity: Activity) -> None:
    if not activity.processed:
        return
    if not activity.credited_chain_id:
        event = db.scalar(
            select(Event)
            .where(Event.activity_id == activity.id, Event.event_type == "RIDE_ADDED", Event.chain_id.is_not(None))
            .order_by(Event.id.desc())
            .limit(1)
        )
        activity.credited_chain_id = event.chain_id if event else None
    if not activity.credited_chain_id:
        raise HTTPException(400, "Cannot determine which chain was credited for this activity")

    chain = db.get(Chain, activity.credited_chain_id)
    if not chain:
        raise HTTPException(400, "Credited chain no longer exists")
    adjust_chain_cycle_distance(db, chain, activity.occurred_at, -activity.distance_km)
    record_event(
        db,
        "RIDE_REVERSED",
        chain_id=chain.id,
        bike_id=activity.bike_id,
        activity_id=activity.id,
        distance_km=-activity.distance_km,
        metadata={"source": activity.source, "external_id": activity.external_id},
    )
    activity.processed = False
    activity.credited_chain_id = None


# --- Strava integration ----------------------------------------------------
def strava_token(db: Session) -> str | None:
    token = db.scalar(select(StravaToken).order_by(StravaToken.id.desc()))
    if not token:
        return None
    # Refresh slightly before expiry so a sync cannot start with an almost-expired token.
    if token.expires_at <= int(now().timestamp()) + 60:
        response = httpx.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": STRAVA_CLIENT_ID,
                "client_secret": STRAVA_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        token.access_token = data["access_token"]
        token.refresh_token = data.get("refresh_token", token.refresh_token)
        token.expires_at = data["expires_at"]
        db.commit()
    return token.access_token


def build_oauth_state() -> str:
    return signer.dumps({"ts": int(now().timestamp())})


def validate_oauth_state(state: str) -> None:
    if not state:
        raise HTTPException(400, "Missing OAuth state")
    try:
        signer.loads(state, max_age=600)
    except SignatureExpired as exc:
        raise HTTPException(400, "Expired OAuth state") from exc
    except BadSignature as exc:
        raise HTTPException(400, "Invalid OAuth state") from exc


# Gear metadata is informational; a transient gear lookup failure must not abort ride import.
def refresh_strava_gear(db: Session, token: str, gear_id: str) -> None:
    try:
        response = httpx.get(
            f"{STRAVA_API_BASE_URL}/gear/{gear_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError:
        return
    gear = db.get(StravaGear, gear_id)
    if gear is None:
        gear = StravaGear(gear_id=gear_id, updated_at=now())
        db.add(gear)
    gear.name = data.get("name")
    gear.distance_km = float(data.get("distance", 0)) / 1000 if data.get("distance") is not None else None
    gear.primary = data.get("primary")
    gear.updated_at = now()


def unmapped_gears(db: Session) -> list[dict]:
    rows = db.execute(
        select(Activity.raw_gear_id, func.count(Activity.id), func.sum(Activity.distance_km))
        .where(
            Activity.source == "strava",
            Activity.raw_gear_id.is_not(None),
            Activity.bike_id.is_(None),
            Activity.excluded.is_(False),
        )
        .group_by(Activity.raw_gear_id)
        .order_by(func.count(Activity.id).desc())
    ).all()
    result = []
    for gear_id, ride_count, total_km in rows:
        gear = db.get(StravaGear, gear_id)
        result.append({
            "gear_id": gear_id,
            "name": gear.name if gear else None,
            "ride_count": ride_count,
            "total_km": round(total_km or 0, 1),
        })
    return result


def initialization_activities(db: Session, bike: Bike) -> list[Activity]:
    if not bike.strava_gear_id or bike.tracking_start_at is not None:
        return []
    return db.scalars(
        select(Activity)
        .where(
            Activity.source == "strava",
            Activity.bike_id == bike.id,
            Activity.raw_gear_id == bike.strava_gear_id,
            Activity.excluded.is_(False),
            Activity.processed.is_(False),
        )
        .order_by(Activity.occurred_at.asc(), Activity.id.asc())
    ).all()


def parse_utc_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise HTTPException(400, "Date must use YYYY-MM-DD") from exc


# --- Background scheduler -------------------------------------------------
def parse_sync_clock(value: str) -> tuple[int, int]:
    try:
        hour_str, minute_str = value.strip().split(":", 1)
        hour, minute = int(hour_str), int(minute_str)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError
        return hour, minute
    except ValueError as exc:
        raise RuntimeError("Strava sync times must use 24-hour HH:MM format") from exc


def configured_sync_clocks() -> list[tuple[int, int]]:
    """Return sorted unique daily sync times with backwards compatibility.

    STRAVA_SYNC_TIMES=07:00,13:00,21:00 takes precedence when present. Existing
    installs that only set STRAVA_SYNC_TIME keep their previous behaviour.
    """
    raw_values = [item.strip() for item in STRAVA_SYNC_TIMES.split(",") if item.strip()] if STRAVA_SYNC_TIMES else [STRAVA_SYNC_TIME]
    clocks = sorted(set(parse_sync_clock(value) for value in raw_values))
    if not clocks:
        raise RuntimeError("At least one Strava sync time is required when automatic sync is enabled")
    return clocks


def configured_sync_times_display() -> str:
    return ", ".join(f"{hour:02d}:{minute:02d}" for hour, minute in configured_sync_clocks())


# Manual and scheduled syncs share the exact same import path and global lock.
def perform_strava_sync(trigger: str = "manual") -> dict:
    if not sync_lock.acquire(blocking=False):
        raise RuntimeError("A Strava sync is already running")

    run_id = None
    try:
        with Session(engine) as db:
            run = SyncRun(started_at=now(), completed_at=None, trigger=trigger, success=False, imported=0, processed=0, added_km=0, error=None)
            db.add(run)
            db.commit()
            db.refresh(run)
            run_id = run.id

        with Session(engine) as db:
            token = strava_token(db)
            if not token:
                raise RuntimeError("Connect Strava first")

            page = 1
            imported = 0
            processed = 0
            added_km = 0.0
            seen_gear_ids: set[str] = set()

            while page <= 5:
                response = httpx.get(
                    f"{STRAVA_API_BASE_URL}/athlete/activities",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"page": page, "per_page": 100},
                    timeout=30,
                )
                response.raise_for_status()
                rows = response.json()
                if not rows:
                    break

                for item in rows:
                    if item.get("sport_type") not in {
                        "Ride", "VirtualRide", "MountainBikeRide", "GravelRide",
                        "EBikeRide", "EMountainBikeRide", "Velomobile",
                    }:
                        continue
                    external_id = str(item["id"])
                    existing = db.scalar(select(Activity).where(Activity.source == "strava", Activity.external_id == external_id))
                    if existing:
                        if existing.raw_gear_id:
                            seen_gear_ids.add(existing.raw_gear_id)
                        continue

                    gear_id = item.get("gear_id")
                    if gear_id:
                        seen_gear_ids.add(gear_id)
                    bike = db.scalar(select(Bike).where(Bike.strava_gear_id == gear_id)) if gear_id else None
                    occurred_at = datetime.fromisoformat(item["start_date"].replace("Z", "+00:00"))
                    activity = Activity(
                        source="strava",
                        external_id=external_id,
                        occurred_at=occurred_at,
                        distance_km=float(item.get("distance", 0)) / 1000,
                        bike_id=bike.id if bike else None,
                        credited_chain_id=None,
                        raw_gear_id=gear_id,
                        raw_name=item.get("name"),
                    )
                    db.add(activity)
                    db.flush()
                    if apply_activity(db, activity):
                        processed += 1
                        added_km += activity.distance_km
                    imported += 1

                if len(rows) < 100:
                    break
                page += 1

            for gear_id in seen_gear_ids:
                refresh_strava_gear(db, token, gear_id)

            run = db.get(SyncRun, run_id)
            run.completed_at = now()
            run.success = True
            run.imported = imported
            run.processed = processed
            run.added_km = added_km
            db.commit()
            return {"imported": imported, "processed": processed, "added_km": round(added_km, 2)}

    except Exception as exc:
        if run_id is not None:
            with Session(engine) as db:
                run = db.get(SyncRun, run_id)
                if run:
                    run.completed_at = now()
                    run.success = False
                    run.error = str(exc)
                    db.commit()
        raise
    finally:
        sync_lock.release()


def scheduled_strava_sync() -> None:
    try:
        perform_strava_sync(trigger="scheduled")
    except Exception:
        # Error details are persisted in SyncRun and surfaced in the dashboard.
        return


def seconds_until_next_sync() -> float:
    current = datetime.now(APP_TZ)
    targets = []
    for hour, minute in configured_sync_clocks():
        target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= current:
            target = target + timedelta(days=1)
        targets.append(target)
    next_target = min(targets)
    return max(1.0, (next_target - current).total_seconds())


# The lightweight scheduler avoids a second container/cron dependency for one daily task.
def scheduler_loop() -> None:
    while not scheduler_stop.is_set():
        wait_seconds = seconds_until_next_sync()
        if scheduler_stop.wait(wait_seconds):
            break
        scheduled_strava_sync()


def setup_scheduler() -> None:
    global scheduler_thread
    if not STRAVA_AUTO_SYNC:
        return
    configured_sync_clocks()  # fail fast on invalid configuration
    scheduler_stop.clear()
    scheduler_thread = threading.Thread(target=scheduler_loop, name="chainloop-strava-sync", daemon=True)
    scheduler_thread.start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    migrate_legacy_database(DATABASE_URL)
    migrate_database(engine, Base.metadata)
    setup_scheduler()
    yield
    scheduler_stop.set()
    if scheduler_thread and scheduler_thread.is_alive():
        scheduler_thread.join(timeout=2)


# --- Web application -------------------------------------------------------
app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def latest_sync(db: Session, successful_only: bool = False) -> SyncRun | None:
    query = select(SyncRun)
    if successful_only:
        query = query.where(SyncRun.success.is_(True))
    return db.scalar(query.order_by(SyncRun.started_at.desc(), SyncRun.id.desc()).limit(1))


def ready_chain_count(db: Session, bike_id: int) -> int:
    """Count only physically prepared spares: READY plus at least one wax event."""
    ready = db.scalars(select(Chain).where(Chain.bike_id == bike_id, Chain.status == "READY")).all()
    return sum(1 for chain in ready if latest_wax_event(db, chain.id) is not None)


def needs_wear_before_wax(db: Session, chain: Chain) -> bool:
    if chain.status != "NEEDS_WAX":
        return False
    wax = latest_wax_event(db, chain.id)
    if chain.last_wear_at is None:
        return True
    if wax is None:
        return False
    last_wear = ensure_activity_datetime(chain.last_wear_at)
    waxed = ensure_activity_datetime(wax.applied_at)
    return last_wear <= waxed


# View helpers keep SQL aggregation out of Jinja templates.
def chain_history_context(db: Session, chain: Chain) -> dict:
    bike = db.get(Bike, chain.bike_id)
    wear = db.scalars(select(WearMeasurement).where(WearMeasurement.chain_id == chain.id).order_by(WearMeasurement.measured_at.desc())).all()
    wax_rows = db.execute(
        select(WaxEvent, WaxProduct.name)
        .join(WaxProduct, WaxEvent.wax_product_id == WaxProduct.id)
        .where(WaxEvent.chain_id == chain.id)
        .order_by(WaxEvent.applied_at.desc(), WaxEvent.id.desc())
    ).all()
    events = db.scalars(select(Event).where(Event.chain_id == chain.id).order_by(Event.created_at.desc(), Event.id.desc()).limit(250)).all()
    activities = db.scalars(
        select(Activity).where(Activity.credited_chain_id == chain.id).order_by(Activity.occurred_at.desc()).limit(250)
    ).all()
    return {"bike": bike, "wear": wear, "wax_rows": wax_rows, "events": events, "activities": activities}


def wax_cycle_records(db: Session) -> list[dict]:
    """Return completed wax cycles, assigning interval distance to the wax that produced it."""
    products = {p.id: p for p in db.scalars(select(WaxProduct)).all()}
    records: list[dict] = []
    for chain in db.scalars(select(Chain).order_by(Chain.code)).all():
        events = db.scalars(
            select(WaxEvent).where(WaxEvent.chain_id == chain.id).order_by(WaxEvent.applied_at.asc(), WaxEvent.id.asc())
        ).all()
        for idx in range(1, len(events)):
            prior, ending = events[idx - 1], events[idx]
            product = products.get(prior.wax_product_id)
            records.append({
                "chain_id": chain.id,
                "chain_code": chain.code,
                "product_id": prior.wax_product_id,
                "product": product.name if product else f"Product {prior.wax_product_id}",
                "start": prior.applied_at,
                "end": ending.applied_at,
                "km": ending.km_since_previous_wax,
            })
    return records


def wax_statistics(db: Session) -> list[dict]:
    records = wax_cycle_records(db)
    products = db.scalars(select(WaxProduct).order_by(WaxProduct.name)).all()
    stats = []
    for product in products:
        completed = [r["km"] for r in records if r["product_id"] == product.id]
        current_km = 0.0
        for chain in db.scalars(select(Chain)).all():
            latest = latest_wax_event(db, chain.id)
            if latest and latest.wax_product_id == product.id:
                current_km += chain.km_since_wax
        stats.append({
            "product": product.name,
            "completed_cycles": len(completed),
            "average": round(mean(completed), 1) if completed else None,
            "median": round(median(completed), 1) if completed else None,
            "shortest": round(min(completed), 1) if completed else None,
            "longest": round(max(completed), 1) if completed else None,
            "total_km": round(sum(completed) + current_km, 1),
        })
    return stats


def wear_graph_series(db: Session) -> list[dict]:
    series = []
    for chain in db.scalars(select(Chain).order_by(Chain.code)).all():
        rows = db.scalars(
            select(WearMeasurement)
            .where(WearMeasurement.chain_id == chain.id, WearMeasurement.total_km_at_measurement.is_not(None))
            .order_by(WearMeasurement.total_km_at_measurement.asc())
        ).all()
        if rows:
            series.append({"chain": chain.code, "points": [(r.total_km_at_measurement, r.wear_percent) for r in rows]})
    return series


# --- Routes: dashboard and static assets ---------------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with Session(engine) as db:
        bikes = db.scalars(select(Bike).order_by(Bike.name)).all()
        chains = db.scalars(select(Chain).order_by(Chain.code)).all()
        wax_products = active_wax_products(db)
        strava_connected = db.scalar(select(StravaToken.id).limit(1)) is not None
        wax_by_chain = {chain.id: latest_wax_product_name(db, chain.id) for chain in chains}
        wax_details_by_chain = {chain.id: latest_wax_details(db, chain.id) for chain in chains}
        wax_cycles_by_chain = {chain.id: wax_cycle_summary(db, chain.id) for chain in chains}
        recent_activities = db.scalars(select(Activity).order_by(Activity.occurred_at.desc()).limit(15)).all()
        bikes_by_id = {bike.id: bike for bike in bikes}
        init_activities_by_bike = {bike.id: initialization_activities(db, bike) for bike in bikes}
        init_summary_by_bike = {
            bike.id: {
                "ride_count": len(init_activities_by_bike[bike.id]),
                "total_km": round(sum(a.distance_km for a in init_activities_by_bike[bike.id]), 1),
            }
            for bike in bikes
        }
        service_by_bike = {bike.id: service_status(bike, current_chain(db, bike.id)) for bike in bikes}
        ready_by_bike = {bike.id: ready_chain_count(db, bike.id) for bike in bikes}
        wear_prompt_by_chain = {chain.id: needs_wear_before_wax(db, chain) for chain in chains}
        last_sync = latest_sync(db)
        last_successful_sync = latest_sync(db, successful_only=True)

        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "bikes": bikes,
                "bikes_by_id": bikes_by_id,
                "chains": chains,
                "wax_products": wax_products,
                "wax_by_chain": wax_by_chain,
                "wax_details_by_chain": wax_details_by_chain,
                "wax_cycles_by_chain": wax_cycles_by_chain,
                "recent_activities": recent_activities,
                "init_activities_by_bike": init_activities_by_bike,
                "init_summary_by_bike": init_summary_by_bike,
                "service_by_bike": service_by_bike,
                "ready_by_bike": ready_by_bike,
                "wear_prompt_by_chain": wear_prompt_by_chain,
                "sync_imported": request.query_params.get("sync_imported"),
                "sync_processed": request.query_params.get("sync_processed"),
                "sync_added_km": request.query_params.get("sync_added_km"),
                "sync_error": request.query_params.get("sync_error"),
                "backfilled_count": request.query_params.get("backfilled_count"),
                "backfilled_km": request.query_params.get("backfilled_km"),
                "strava_configured": bool(STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET),
                "strava_connected": strava_connected,
                "pushover_configured": bool(PUSHOVER_APP_TOKEN and PUSHOVER_USER_KEY),
                "unmapped_gears": unmapped_gears(db),
                "last_sync": last_sync,
                "last_successful_sync": last_successful_sync,
                "auto_sync": STRAVA_AUTO_SYNC,
                "sync_times": configured_sync_times_display() if STRAVA_AUTO_SYNC else "",
                "timezone_name": CHAINLOOP_TIMEZONE,
            },
        )


@app.get("/favicon.ico")
def favicon_ico():
    return FileResponse(STATIC_DIR / "favicon.ico")


@app.get("/favicon.png")
def favicon_png():
    return FileResponse(STATIC_DIR / "favicon.png")


# --- Routes: Strava OAuth and synchronization -----------------------------
@app.get("/auth/strava")
def strava_auth():
    if not STRAVA_CLIENT_ID:
        raise HTTPException(503, "STRAVA_CLIENT_ID is not configured")
    query = urlencode({
        "client_id": STRAVA_CLIENT_ID,
        "redirect_uri": STRAVA_REDIRECT_URI,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": STRAVA_SCOPES,
        "state": build_oauth_state(),
    })
    return RedirectResponse(f"https://www.strava.com/oauth/authorize?{query}")


@app.get("/auth/strava/callback")
def strava_callback(code: str, state: str = "", scope: str = ""):
    validate_oauth_state(state)
    response = httpx.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    with Session(engine) as db:
        athlete_id = str(data["athlete"]["id"])
        existing = db.scalar(select(StravaToken).where(StravaToken.athlete_id == athlete_id))
        if existing:
            existing.access_token = data["access_token"]
            existing.refresh_token = data["refresh_token"]
            existing.expires_at = data["expires_at"]
        else:
            db.add(StravaToken(
                athlete_id=athlete_id,
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                expires_at=data["expires_at"],
            ))
        db.commit()
    return RedirectResponse("/")


@app.post("/sync/strava")
def sync_strava(ui: bool = Form(False)):
    try:
        result = perform_strava_sync(trigger="manual")
    except Exception as exc:
        if ui:
            return RedirectResponse(f"/?{urlencode({'sync_error': str(exc)})}", status_code=303)
        raise HTTPException(502, f"Strava sync failed: {exc}") from exc
    if ui:
        return RedirectResponse(
            f"/?sync_imported={result['imported']}&sync_processed={result['processed']}&sync_added_km={result['added_km']:.2f}",
            status_code=303,
        )
    return result


@app.post("/bikes/{bike_id}/map-gear")
def map_bike_gear(bike_id: int, gear_id: str = Form(...)):
    with Session(engine) as db:
        bike = db.get(Bike, bike_id)
        if not bike:
            raise HTTPException(404, "Bike not found")
        other = db.scalar(select(Bike).where(Bike.strava_gear_id == gear_id, Bike.id != bike_id))
        if other:
            raise HTTPException(400, f"Gear ID already mapped to bike {other.name}")
        bike.strava_gear_id = gear_id or None
        activities = db.scalars(select(Activity).where(Activity.source == "strava", Activity.raw_gear_id == gear_id, Activity.excluded.is_(False))).all()
        assigned = 0
        processed = 0
        for activity in activities:
            if activity.bike_id is None:
                activity.bike_id = bike.id
                assigned += 1
            if apply_activity(db, activity):
                processed += 1
        record_event(db, "STRAVA_GEAR_MAPPED", bike_id=bike.id, note=f"Mapped {gear_id} to {bike.name}", metadata={
            "gear_id": gear_id, "assigned_activities": assigned, "processed_activities": processed,
        })
        db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/bikes/{bike_id}/tracking/initialize-history")
def initialize_tracking_history(
    bike_id: int,
    chain_id: int = Form(...),
    start_activity_id: int = Form(...),
    wax_product_id: int = Form(...),
    wax_date: str = Form(...),
    note: str = Form(""),
):
    with Session(engine) as db:
        bike = db.get(Bike, bike_id)
        chain = db.get(Chain, chain_id)
        start_activity = db.get(Activity, start_activity_id)
        wax = db.get(WaxProduct, wax_product_id)
        if not bike:
            raise HTTPException(404, "Bike not found")
        if bike.tracking_start_at is not None:
            raise HTTPException(400, "Tracking has already been started for this bike")
        if not bike.strava_gear_id:
            raise HTTPException(400, "Map a Strava gear to this bike first")
        if not chain or chain.bike_id != bike.id or chain.status != "IN_USE":
            raise HTTPException(400, "Select the currently installed chain")
        if not start_activity or start_activity.source != "strava" or start_activity.bike_id != bike.id:
            raise HTTPException(400, "Invalid starting activity")
        if start_activity.raw_gear_id != bike.strava_gear_id or start_activity.excluded:
            raise HTTPException(400, "Starting activity does not match the mapped Strava gear")
        if not wax:
            raise HTTPException(400, "Wax product not found")
        if chain.total_km != 0 or chain.km_since_wax != 0:
            raise HTTPException(400, "Historical initialization requires zero chain counters")
        if db.scalar(select(Activity.id).where(Activity.bike_id == bike.id, Activity.processed.is_(True)).limit(1)):
            raise HTTPException(400, "This bike already has processed activities")
        if db.scalar(select(WaxEvent.id).where(WaxEvent.chain_id == chain.id).limit(1)):
            raise HTTPException(400, "The active chain already has a wax event")

        waxed_at = parse_utc_date(wax_date)
        start_at = ensure_activity_datetime(start_activity.occurred_at)
        if waxed_at > start_at:
            raise HTTPException(400, "Wax date cannot be after the selected first ride")

        bike.tracking_start_at = start_at
        chain.first_used_at = start_at
        db.add(WaxEvent(chain_id=chain.id, wax_product_id=wax.id, applied_at=waxed_at, km_since_previous_wax=0, note=note or "Initial wax recorded during historical setup"))
        db.flush()
        record_event(db, "CHAIN_WAXED", chain_id=chain.id, bike_id=bike.id, note=note or "Initial historical wax event", metadata={
            "wax_product_id": wax.id, "wax_product": wax.name, "applied_at": waxed_at.isoformat(), "historical_initialization": True,
        }, created_at=waxed_at)

        activities = db.scalars(
            select(Activity)
            .where(
                Activity.source == "strava",
                Activity.bike_id == bike.id,
                Activity.raw_gear_id == bike.strava_gear_id,
                Activity.occurred_at >= start_at,
                Activity.excluded.is_(False),
            )
            .order_by(Activity.occurred_at.asc(), Activity.id.asc())
        ).all()
        backfilled_count = 0
        backfilled_km = 0.0
        for activity in activities:
            if apply_activity(db, activity, notify=False):
                backfilled_count += 1
                backfilled_km += activity.distance_km
        suppress_reached_thresholds(db, bike, chain)
        record_event(db, "TRACKING_INITIALIZED_FROM_HISTORY", chain_id=chain.id, bike_id=bike.id, note=note or None, metadata={
            "tracking_start_at": start_at.isoformat(),
            "start_activity_id": start_activity.id,
            "start_activity_external_id": start_activity.external_id,
            "wax_date": waxed_at.isoformat(),
            "wax_product_id": wax.id,
            "backfilled_activities": backfilled_count,
            "backfilled_km": round(backfilled_km, 3),
            "notifications_suppressed": True,
        })
        db.commit()
    return RedirectResponse(f"/?backfilled_count={backfilled_count}&backfilled_km={backfilled_km:.1f}", status_code=303)


@app.post("/bikes/{bike_id}/tracking/start")
def start_tracking_now(bike_id: int):
    with Session(engine) as db:
        bike = db.get(Bike, bike_id)
        if not bike:
            raise HTTPException(404, "Bike not found")
        if bike.tracking_start_at is not None:
            raise HTTPException(400, "Tracking has already started")
        bike.tracking_start_at = now()
        record_event(db, "TRACKING_INITIALIZED", bike_id=bike.id, note="Tracking started from this point forward")
        db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/chains/{chain_id}/adjust")
def adjust_chain_distance(
    chain_id: int,
    total_delta_km: float = Form(0),
    since_wax_delta_km: float = Form(0),
    note: str = Form(...),
):
    if not note.strip():
        raise HTTPException(400, "A reason is required for manual distance adjustments")
    if abs(total_delta_km) < 0.0001 and abs(since_wax_delta_km) < 0.0001:
        raise HTTPException(400, "Enter a non-zero distance adjustment")
    with Session(engine) as db:
        chain = db.get(Chain, chain_id)
        if not chain:
            raise HTTPException(404, "Chain not found")
        new_total = chain.total_km + total_delta_km
        new_since_wax = chain.km_since_wax + since_wax_delta_km
        if new_total < 0 or new_since_wax < 0:
            raise HTTPException(400, "Distance adjustment would make a value negative")
        chain.total_km = new_total
        chain.km_since_wax = new_since_wax
        record_event(db, "DISTANCE_ADJUSTMENT", chain_id=chain.id, bike_id=chain.bike_id, note=note.strip(), metadata={
            "total_delta_km": total_delta_km, "since_wax_delta_km": since_wax_delta_km,
        })
        db.commit()
    return RedirectResponse(f"/chains/{chain_id}", status_code=303)


# --- Routes: maintenance --------------------------------------------------
@app.post("/chains/{chain_id}/wear")
def add_wear(chain_id: int, wear_percent: float = Form(...), timing: str = Form("before_wax"), note: str = Form("")):
    with Session(engine) as db:
        chain = db.get(Chain, chain_id)
        if not chain:
            raise HTTPException(404, "Chain not found")
        if chain.status == "RETIRED":
            raise HTTPException(400, "Cannot add a normal wear measurement to a retired chain")
        measurement = WearMeasurement(
            chain_id=chain_id,
            measured_at=now(),
            wear_percent=wear_percent,
            total_km_at_measurement=chain.total_km,
            timing=timing,
            note=note or None,
        )
        db.add(measurement)
        chain.current_wear_percent = wear_percent
        chain.last_wear_at = measurement.measured_at
        record_event(db, "WEAR_MEASURED", chain_id=chain_id, bike_id=chain.bike_id, note=note or None, metadata={
            "wear_percent": wear_percent, "timing": timing, "total_km": chain.total_km,
        })
        db.commit()
    return RedirectResponse(request_referrer_or_chain(chain_id), status_code=303)


def request_referrer_or_chain(chain_id: int) -> str:
    # Kept intentionally deterministic for POST redirects; dashboard users can navigate back normally.
    return f"/chains/{chain_id}"


@app.post("/chains/{chain_id}/wax")
def add_wax(
    chain_id: int,
    wax_product_id: int = Form(...),
    note: str = Form(""),
    confirm_without_wear: bool = Form(False),
):
    with Session(engine) as db:
        chain = db.get(Chain, chain_id)
        wax = db.get(WaxProduct, wax_product_id)
        if not chain or not wax:
            raise HTTPException(404, "Chain or wax product not found")
        if chain.status == "RETIRED":
            raise HTTPException(400, "Cannot wax a retired chain")
        if wax.archived:
            raise HTTPException(400, "Archived wax products cannot be used for new treatments")
        if needs_wear_before_wax(db, chain) and not confirm_without_wear:
            raise HTTPException(400, "No wear measurement has been recorded during this wax cycle. Measure first, or explicitly continue without it.")

        missing_wear = needs_wear_before_wax(db, chain)
        previous_wax = latest_wax_event(db, chain.id)
        initial_treatment = previous_wax is None
        completed_km = 0.0 if initial_treatment else chain.km_since_wax
        wax_event = WaxEvent(chain_id=chain_id, wax_product_id=wax_product_id, applied_at=now(), km_since_previous_wax=completed_km, note=note or None)
        db.add(wax_event)
        db.flush()
        chain.km_since_wax = 0
        if chain.status in {"NEEDS_WAX", "NEW"}:
            chain.status = "READY"
        record_event(db, "CHAIN_INITIAL_WAX" if initial_treatment else "CHAIN_WAXED", chain_id=chain_id, bike_id=chain.bike_id, note=note or None, metadata={
            "wax_product_id": wax.id, "wax_product": wax.name, "completed_interval_km": completed_km,
            "wax_cycle": wax_cycle_summary(db, chain.id)["current_cycle"],
            "continued_without_wear": bool(confirm_without_wear and missing_wear),
        })
        db.commit()
    return RedirectResponse(f"/chains/{chain_id}", status_code=303)


@app.post("/bikes/{bike_id}/swap")
def swap_chain(bike_id: int, chain_id: int = Form(...)):
    with Session(engine) as db:
        bike = db.get(Bike, bike_id)
        new_chain = db.get(Chain, chain_id)
        if not bike or not new_chain or new_chain.bike_id != bike_id or new_chain.status != "READY":
            raise HTTPException(400, "Invalid chain for swap")
        if latest_wax_event(db, new_chain.id) is None:
            raise HTTPException(400, "That chain is marked READY but has no recorded wax treatment. Record its initial wax first.")
        active_chains = db.scalars(select(Chain).where(Chain.bike_id == bike_id, Chain.status == "IN_USE")).all()
        if len(active_chains) > 1:
            raise HTTPException(409, "Database contains more than one active chain; repair this before swapping")
        old_chain = active_chains[0] if active_chains else None
        if old_chain and old_chain.id == new_chain.id:
            raise HTTPException(400, "That chain is already installed")
        if old_chain:
            old_chain.status = "NEEDS_WAX"
            record_event(db, "CHAIN_REMOVED", chain_id=old_chain.id, bike_id=bike_id)
        new_chain.status = "IN_USE"
        if new_chain.first_used_at is None:
            new_chain.first_used_at = now()
        record_event(db, "CHAIN_INSTALLED", chain_id=new_chain.id, bike_id=bike_id)
        db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/chains/{chain_id}/retire")
def retire_chain(
    chain_id: int,
    wear_percent: float = Form(...),
    reason: str = Form(...),
    note: str = Form(""),
):
    allowed_reasons = {"wear_limit", "damage", "replaced_drivetrain", "other"}
    if reason not in allowed_reasons:
        raise HTTPException(400, "Invalid retirement reason")
    with Session(engine) as db:
        chain = db.get(Chain, chain_id)
        if not chain:
            raise HTTPException(404, "Chain not found")
        if chain.status == "IN_USE":
            raise HTTPException(400, "Swap the installed chain before retiring it")
        if chain.status == "RETIRED":
            raise HTTPException(400, "Chain is already retired")
        measured_at = now()
        db.add(WearMeasurement(
            chain_id=chain.id,
            measured_at=measured_at,
            wear_percent=wear_percent,
            total_km_at_measurement=chain.total_km,
            timing="retirement",
            note=note or None,
        ))
        chain.current_wear_percent = wear_percent
        chain.last_wear_at = measured_at
        chain.retired_at = measured_at
        chain.status = "RETIRED"
        record_event(db, "WEAR_MEASURED", chain_id=chain.id, bike_id=chain.bike_id, note=note or None, metadata={
            "wear_percent": wear_percent, "timing": "retirement", "total_km": chain.total_km,
        })
        record_event(db, "CHAIN_RETIRED", chain_id=chain.id, bike_id=chain.bike_id, note=note or None, metadata={
            "reason": reason, "final_wear_percent": wear_percent, "total_km": chain.total_km,
        })
        db.commit()
    return RedirectResponse(f"/chains/{chain_id}", status_code=303)


@app.post("/chains/{chain_id}/initial-wax")
def record_initial_wax(
    chain_id: int,
    wax_product_id: int = Form(...),
    wax_date: str = Form(...),
    note: str = Form(""),
):
    """Record treatment #1 for legacy/prepared chains that have no wax history yet."""
    with Session(engine) as db:
        chain = db.get(Chain, chain_id)
        wax = db.get(WaxProduct, wax_product_id)
        if not chain or not wax:
            raise HTTPException(404, "Chain or wax product not found")
        if chain.status == "RETIRED":
            raise HTTPException(400, "Cannot prepare a retired chain")
        if wax.archived:
            raise HTTPException(400, "Archived wax products cannot be used for new treatments")
        if latest_wax_event(db, chain.id):
            raise HTTPException(400, "This chain already has wax history")
        applied_at = parse_utc_date(wax_date)
        db.add(WaxEvent(
            chain_id=chain.id,
            wax_product_id=wax.id,
            applied_at=applied_at,
            km_since_previous_wax=0,
            note=note or "Initial wax treatment",
        ))
        db.flush()
        # Legacy READY chains may already have accurately accumulated distance.
        # Recording their missing treatment establishes cycle 1; it is not a
        # new treatment that closes and resets that live cycle.
        if chain.status == "NEW":
            chain.status = "READY"
        record_event(db, "CHAIN_INITIAL_WAX", chain_id=chain.id, bike_id=chain.bike_id, note=note or None, metadata={
            "wax_product_id": wax.id,
            "wax_product": wax.name,
            "wax_cycle": 1,
        }, created_at=applied_at)
        db.commit()
    return RedirectResponse(f"/chains/{chain_id}", status_code=303)


@app.get("/chains/{chain_id}", response_class=HTMLResponse)
def chain_detail(request: Request, chain_id: int):
    with Session(engine) as db:
        chain = db.get(Chain, chain_id)
        if not chain:
            raise HTTPException(404, "Chain not found")
        ctx = chain_history_context(db, chain)
        wax_products = active_wax_products(db)
        return templates.TemplateResponse(request=request, name="chain.html", context={
            "app_name": APP_NAME, "app_version": APP_VERSION, "chain": chain, "wax_products": wax_products,
            "wax": latest_wax_details(db, chain.id), "wax_cycle": wax_cycle_summary(db, chain.id),
            "wear_prompt": needs_wear_before_wax(db, chain), **ctx,
        })


# --- Routes: administration -----------------------------------------------
def wax_product_usage_count(db: Session, wax_product_id: int) -> int:
    return int(db.scalar(select(func.count(WaxEvent.id)).where(WaxEvent.wax_product_id == wax_product_id)) or 0)


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    with Session(engine) as db:
        people = db.scalars(select(Person).order_by(Person.name)).all()
        specs = db.scalars(select(ChainSpec).order_by(ChainSpec.name)).all()
        bikes = db.scalars(select(Bike).order_by(Bike.name)).all()
        chains = db.scalars(select(Chain).order_by(Chain.code)).all()
        wax_products = db.scalars(select(WaxProduct).order_by(WaxProduct.archived, WaxProduct.name)).all()
        usage = {w.id: wax_product_usage_count(db, w.id) for w in wax_products}
        cycle_by_chain = {c.id: wax_cycle_summary(db, c.id) for c in chains}
        return templates.TemplateResponse(request=request, name="admin.html", context={
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "people": people,
            "specs": specs,
            "bikes": bikes,
            "chains": chains,
            "wax_products": wax_products,
            "wax_usage": usage,
            "cycle_by_chain": cycle_by_chain,
            "active_wax_products": [w for w in wax_products if not w.archived],
        })


@app.post("/admin/people")
def admin_add_person(name: str = Form(...)):
    name = name.strip()
    if not name:
        raise HTTPException(400, "Rider name is required")
    with Session(engine) as db:
        db.add(Person(name=name))
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/people/{person_id}")
def admin_edit_person(person_id: int, name: str = Form(...)):
    name = name.strip()
    if not name:
        raise HTTPException(400, "Rider name is required")
    with Session(engine) as db:
        person = db.get(Person, person_id)
        if not person:
            raise HTTPException(404, "Rider not found")
        person.name = name
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/specs")
def admin_add_spec(
    name: str = Form(...),
    manufacturer: str = Form(""),
    model: str = Form(""),
    speeds: str = Form(""),
    link_count: str = Form(""),
):
    name = name.strip()
    if not name:
        raise HTTPException(400, "Chain specification name is required")
    with Session(engine) as db:
        db.add(ChainSpec(
            name=name,
            manufacturer=manufacturer.strip() or None,
            model=model.strip() or None,
            speeds=int(speeds) if speeds.strip() else None,
            link_count=int(link_count) if link_count.strip() else None,
        ))
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/specs/{spec_id}")
def admin_edit_spec(
    spec_id: int,
    name: str = Form(...),
    manufacturer: str = Form(""),
    model: str = Form(""),
    speeds: str = Form(""),
    link_count: str = Form(""),
):
    with Session(engine) as db:
        spec = db.get(ChainSpec, spec_id)
        if not spec:
            raise HTTPException(404, "Chain specification not found")
        spec.name = name.strip() or spec.name
        spec.manufacturer = manufacturer.strip() or None
        spec.model = model.strip() or None
        spec.speeds = int(speeds) if speeds.strip() else None
        spec.link_count = int(link_count) if link_count.strip() else None
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/bikes")
def admin_add_bike(
    name: str = Form(...),
    person_id: int = Form(...),
    chain_spec_id: int = Form(...),
    warning_km: float = Form(500),
    change_km: float = Form(600),
    overdue_km: float = Form(800),
):
    if not name.strip():
        raise HTTPException(400, "Bike name is required")
    if not (0 <= warning_km <= change_km <= overdue_km):
        raise HTTPException(400, "Thresholds must satisfy warning <= change <= overdue")
    with Session(engine) as db:
        if not db.get(Person, person_id) or not db.get(ChainSpec, chain_spec_id):
            raise HTTPException(400, "Invalid rider or chain specification")
        db.add(Bike(
            name=name.strip(), person_id=person_id, chain_spec_id=chain_spec_id,
            warning_km=warning_km, change_km=change_km, overdue_km=overdue_km,
            tracking_start_at=None, strava_gear_id=None,
        ))
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/bikes/{bike_id}")
def admin_edit_bike(
    bike_id: int,
    name: str = Form(...),
    person_id: int = Form(...),
    chain_spec_id: int = Form(...),
    warning_km: float = Form(...),
    change_km: float = Form(...),
    overdue_km: float = Form(...),
):
    if not (0 <= warning_km <= change_km <= overdue_km):
        raise HTTPException(400, "Thresholds must satisfy warning <= change <= overdue")
    with Session(engine) as db:
        bike = db.get(Bike, bike_id)
        if not bike or not db.get(Person, person_id) or not db.get(ChainSpec, chain_spec_id):
            raise HTTPException(404, "Bike, rider or chain specification not found")
        if bike.chain_spec_id != chain_spec_id:
            incompatible = db.scalar(select(Chain.id).where(Chain.bike_id == bike.id, Chain.chain_spec_id != chain_spec_id).limit(1))
            if incompatible:
                raise HTTPException(400, "Cannot change this bike's chain specification while assigned chains use the existing specification")
        bike.name = name.strip()
        bike.person_id = person_id
        bike.chain_spec_id = chain_spec_id
        bike.warning_km = warning_km
        bike.change_km = change_km
        bike.overdue_km = overdue_km
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/chains")
def admin_add_chain(
    code: str = Form(...),
    bike_id: int = Form(...),
    condition: str = Form("new"),
    initial_total_km: float = Form(0),
    initial_wear_percent: str = Form(""),
    first_used_date: str = Form(""),
    wax_product_id: str = Form(""),
    wax_date: str = Form(""),
):
    code = code.strip().upper()
    if not code:
        raise HTTPException(400, "Chain code is required")
    if condition not in {"new", "ready"}:
        raise HTTPException(400, "Condition must be new or ready")
    if initial_total_km < 0:
        raise HTTPException(400, "Initial distance cannot be negative")
    with Session(engine) as db:
        bike = db.get(Bike, bike_id)
        if not bike or not bike.chain_spec_id:
            raise HTTPException(400, "Bike must have a chain specification")
        if db.scalar(select(Chain.id).where(Chain.code == code)):
            raise HTTPException(400, "Chain code already exists")
        first_used = parse_utc_date(first_used_date) if first_used_date else None
        if initial_wear_percent.strip() and float(initial_wear_percent) < 0:
            raise HTTPException(400, "Initial wear cannot be negative")
        chain = Chain(
            code=code,
            bike_id=bike.id,
            chain_spec_id=bike.chain_spec_id,
            status="NEW" if condition == "new" else "READY",
            first_used_at=first_used,
            total_km=initial_total_km,
            km_since_wax=0,
            current_wear_percent=float(initial_wear_percent) if initial_wear_percent.strip() else None,
            last_wear_at=now() if initial_wear_percent.strip() else None,
            retired_at=None,
        )
        db.add(chain)
        db.flush()
        record_event(db, "CHAIN_CREATED", chain_id=chain.id, bike_id=bike.id, metadata={"condition": condition, "initial_total_km": initial_total_km})
        if initial_total_km:
            record_event(db, "DISTANCE_ADJUSTMENT", chain_id=chain.id, bike_id=bike.id, distance_km=initial_total_km, note="Initial chain distance", metadata={"total_delta_km": initial_total_km, "since_wax_delta_km": 0})
        if initial_wear_percent.strip():
            measured = float(initial_wear_percent)
            db.add(WearMeasurement(chain_id=chain.id, measured_at=now(), wear_percent=measured, total_km_at_measurement=initial_total_km, timing="initial", note="Initial chain wear"))
            record_event(db, "WEAR_MEASURED", chain_id=chain.id, bike_id=bike.id, metadata={"wear_percent": measured, "timing": "initial", "total_km": initial_total_km})
        if condition == "ready":
            if not wax_product_id or not wax_date:
                raise HTTPException(400, "A ready chain requires its initial wax product and date")
            wax = db.get(WaxProduct, int(wax_product_id))
            if not wax or wax.archived:
                raise HTTPException(400, "Select an active wax product")
            applied_at = parse_utc_date(wax_date)
            if first_used and applied_at > first_used:
                raise HTTPException(400, "Initial wax date cannot be after the first-used date")
            db.add(WaxEvent(chain_id=chain.id, wax_product_id=wax.id, applied_at=applied_at, km_since_previous_wax=0, note="Initial wax treatment"))
            db.flush()
            record_event(db, "CHAIN_INITIAL_WAX", chain_id=chain.id, bike_id=bike.id, created_at=applied_at, metadata={"wax_product_id": wax.id, "wax_product": wax.name, "wax_cycle": 1})
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/chains/{chain_id}")
def admin_edit_chain(chain_id: int, code: str = Form(...), bike_id: int = Form(...)):
    code = code.strip().upper()
    with Session(engine) as db:
        chain = db.get(Chain, chain_id)
        target_bike = db.get(Bike, bike_id)
        if not chain or not target_bike:
            raise HTTPException(404, "Chain or target bike not found")
        duplicate = db.scalar(select(Chain.id).where(Chain.code == code, Chain.id != chain.id))
        if duplicate:
            raise HTTPException(400, "Chain code already exists")
        if chain.bike_id != target_bike.id:
            if chain.status not in {"NEW", "READY"}:
                raise HTTPException(400, "Only NEW or READY chains can be reassigned")
            if target_bike.chain_spec_id != chain.chain_spec_id:
                raise HTTPException(400, "Target bike uses an incompatible chain specification")
            old_bike_id = chain.bike_id
            chain.bike_id = target_bike.id
            record_event(db, "CHAIN_REASSIGNED", chain_id=chain.id, bike_id=target_bike.id, metadata={"from_bike_id": old_bike_id, "to_bike_id": target_bike.id})
        chain.code = code
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/waxes")
def admin_add_wax_product(name: str = Form(...), notes: str = Form("")):
    name = name.strip()
    if not name:
        raise HTTPException(400, "Wax product name is required")
    with Session(engine) as db:
        if db.scalar(select(WaxProduct.id).where(WaxProduct.name == name)):
            raise HTTPException(400, "Wax product already exists")
        db.add(WaxProduct(name=name, notes=notes.strip() or None, archived=False))
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/waxes/{wax_id}")
def admin_edit_wax_product(wax_id: int, name: str = Form(...), notes: str = Form("")):
    if not name.strip():
        raise HTTPException(400, "Wax product name is required")
    with Session(engine) as db:
        wax = db.get(WaxProduct, wax_id)
        if not wax:
            raise HTTPException(404, "Wax product not found")
        duplicate = db.scalar(select(WaxProduct.id).where(WaxProduct.name == name.strip(), WaxProduct.id != wax.id))
        if duplicate:
            raise HTTPException(400, "Wax product name already exists")
        wax.name = name.strip()
        wax.notes = notes.strip() or None
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/waxes/{wax_id}/archive")
def admin_archive_wax_product(wax_id: int):
    with Session(engine) as db:
        wax = db.get(WaxProduct, wax_id)
        if not wax:
            raise HTTPException(404, "Wax product not found")
        wax.archived = True
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/waxes/{wax_id}/reactivate")
def admin_reactivate_wax_product(wax_id: int):
    with Session(engine) as db:
        wax = db.get(WaxProduct, wax_id)
        if not wax:
            raise HTTPException(404, "Wax product not found")
        wax.archived = False
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/waxes/{wax_id}/delete")
def admin_delete_wax_product(wax_id: int):
    with Session(engine) as db:
        wax = db.get(WaxProduct, wax_id)
        if not wax:
            raise HTTPException(404, "Wax product not found")
        if wax_product_usage_count(db, wax.id):
            raise HTTPException(400, "Wax products with historical use must be archived, not deleted")
        db.delete(wax)
        db.commit()
    return RedirectResponse("/admin", status_code=303)


# --- Routes: corrections and audit ---------------------------------------
@app.get("/activities/{activity_id}", response_class=HTMLResponse)
def activity_detail(request: Request, activity_id: int):
    with Session(engine) as db:
        activity = db.get(Activity, activity_id)
        if not activity:
            raise HTTPException(404, "Activity not found")
        bikes = db.scalars(select(Bike).order_by(Bike.name)).all()
        # Retired chains remain selectable for auditable corrections to rides
        # that occurred no later than their recorded retirement.
        chains = db.scalars(select(Chain).order_by(Chain.code)).all()
        credited_chain = db.get(Chain, activity.credited_chain_id) if activity.credited_chain_id else None
        return templates.TemplateResponse(request=request, name="activity.html", context={
            "app_name": APP_NAME, "app_version": APP_VERSION, "activity": activity, "bikes": bikes, "chains": chains,
            "credited_chain": credited_chain,
        })


@app.post("/activities/{activity_id}/correct")
def correct_activity(
    activity_id: int,
    bike_id: str = Form(""),
    chain_id: str = Form(""),
    distance_km: float = Form(...),
    excluded: bool = Form(False),
    note: str = Form(...),
):
    if not note.strip():
        raise HTTPException(400, "A correction reason is required")
    if distance_km < 0:
        raise HTTPException(400, "Distance cannot be negative")

    with Session(engine) as db:
        activity = db.get(Activity, activity_id)
        if not activity:
            raise HTTPException(404, "Activity not found")
        old = {
            "bike_id": activity.bike_id,
            "chain_id": activity.credited_chain_id,
            "distance_km": activity.distance_km,
            "excluded": activity.excluded,
            "processed": activity.processed,
        }
        reverse_activity_credit(db, activity)

        target_bike = db.get(Bike, int(bike_id)) if bike_id else None
        target_chain = db.get(Chain, int(chain_id)) if chain_id else None
        if target_chain and (not target_bike or target_chain.bike_id != target_bike.id):
            raise HTTPException(400, "Selected chain does not belong to selected bike")
        if target_chain and target_chain.status == "RETIRED" and (
            target_chain.retired_at is None
            or ensure_activity_datetime(activity.occurred_at) > ensure_activity_datetime(target_chain.retired_at)
        ):
            raise HTTPException(400, "Cannot credit a ride after the chain was retired")

        activity.bike_id = target_bike.id if target_bike else None
        activity.distance_km = distance_km
        activity.excluded = excluded
        activity.processed = False
        activity.credited_chain_id = None

        reapplied = False
        if not excluded and target_bike and target_chain:
            reapplied = apply_activity_to_chain(db, activity, target_bike, target_chain, notify=False)

        record_event(db, "RIDE_CORRECTED", chain_id=activity.credited_chain_id, bike_id=activity.bike_id, activity_id=activity.id, note=note.strip(), metadata={
            "old": old,
            "new": {
                "bike_id": activity.bike_id,
                "chain_id": activity.credited_chain_id,
                "distance_km": activity.distance_km,
                "excluded": activity.excluded,
                "processed": activity.processed,
            },
            "reapplied": reapplied,
            "notifications_suppressed": True,
        })
        db.commit()
    return RedirectResponse(f"/activities/{activity_id}", status_code=303)


@app.get("/history", response_class=HTMLResponse)
def audit_history(request: Request):
    with Session(engine) as db:
        events = db.scalars(select(Event).order_by(Event.created_at.desc(), Event.id.desc()).limit(300)).all()
        chains = {c.id: c for c in db.scalars(select(Chain)).all()}
        bikes = {b.id: b for b in db.scalars(select(Bike)).all()}
        activities = {a.id: a for a in db.scalars(select(Activity).where(Activity.id.in_([e.activity_id for e in events if e.activity_id]))).all()} if events else {}
        return templates.TemplateResponse(request=request, name="history.html", context={
            "app_name": APP_NAME, "app_version": APP_VERSION, "events": events, "chains": chains, "bikes": bikes, "activities": activities,
        })


# --- Routes: history, statistics and API ---------------------------------
@app.get("/statistics", response_class=HTMLResponse)
def statistics_page(request: Request):
    with Session(engine) as db:
        chains = db.scalars(select(Chain).order_by(Chain.code)).all()
        wax_records = wax_cycle_records(db)
        comparison = []
        for chain in chains:
            completed = [r["km"] for r in wax_records if r["chain_id"] == chain.id]
            cycle = wax_cycle_summary(db, chain.id)
            comparison.append({
                "chain": chain,
                "wax_treatments": cycle["treatments"],
                "current_cycle": cycle["current_cycle"],
                "completed_cycles": cycle["completed_cycles"],
                "avg_interval": round(mean(completed), 1) if completed else None,
            })
        return templates.TemplateResponse(request=request, name="statistics.html", context={
            "app_name": APP_NAME, "app_version": APP_VERSION,
            "comparison": comparison,
            "wax_stats": wax_statistics(db),
            "wax_records": wax_records,
            "wax_graph_records": [{"chain_code": r["chain_code"], "km": r["km"]} for r in wax_records],
            "wear_series": wear_graph_series(db),
        })


@app.post("/pushover/test")
def pushover_test():
    sent, error = send_pushover("ChainLoop", "ChainLoop Pushover integration is working.")
    if not sent:
        raise HTTPException(502 if PUSHOVER_APP_TOKEN and PUSHOVER_USER_KEY else 400, error or "Pushover failed")
    return RedirectResponse("/", status_code=303)


@app.get("/webhooks/strava")
def strava_webhook_verify(hub_mode: str | None = None, hub_verify_token: str | None = None, hub_challenge: str | None = None):
    if hub_mode == "subscribe" and STRAVA_VERIFY_TOKEN and hub_verify_token == STRAVA_VERIFY_TOKEN:
        return {"hub.challenge": hub_challenge}
    raise HTTPException(403, "Invalid verification")


@app.post("/webhooks/strava")
def strava_webhook():
    return JSONResponse({"ok": True})


@app.get("/api/bikes")
def api_bikes():
    with Session(engine) as db:
        last_ok = latest_sync(db, successful_only=True)
        result = []
        for bike in db.scalars(select(Bike).order_by(Bike.name)):
            chain = current_chain(db, bike.id)
            status = service_status(bike, chain)
            result.append({
                "id": bike.id,
                "name": bike.name,
                "strava_gear_id": bike.strava_gear_id,
                "tracking_start_at": bike.tracking_start_at.isoformat() if bike.tracking_start_at else None,
                "active_chain": chain.code if chain else None,
                "km_since_wax": round(chain.km_since_wax, 2) if chain else None,
                "km_total": round(chain.total_km, 2) if chain else None,
                "wear_percent": chain.current_wear_percent if chain else None,
                "wax_product": latest_wax_product_name(db, chain.id) if chain else None,
                "maintenance_status": status["level"],
                "ready_chains": ready_chain_count(db, bike.id),
                "warning_km": bike.warning_km,
                "change_km": bike.change_km,
                "overdue_km": bike.overdue_km,
                "last_strava_sync": last_ok.completed_at.isoformat() if last_ok and last_ok.completed_at else None,
            })
        return result


@app.get("/api/activities/recent")
def api_recent_activities(limit: int = 25):
    limit = max(1, min(limit, 100))
    with Session(engine) as db:
        rows = db.scalars(select(Activity).order_by(Activity.occurred_at.desc()).limit(limit)).all()
        return [{
            "id": row.id,
            "source": row.source,
            "external_id": row.external_id,
            "occurred_at": row.occurred_at.isoformat(),
            "distance_km": round(row.distance_km, 2),
            "bike_id": row.bike_id,
            "credited_chain_id": row.credited_chain_id,
            "processed": row.processed,
            "excluded": row.excluded,
            "raw_gear_id": row.raw_gear_id,
            "name": row.raw_name,
        } for row in rows]


@app.get("/api/history/{chain_id}")
def api_chain_history(chain_id: int):
    with Session(engine) as db:
        chain = db.get(Chain, chain_id)
        if not chain:
            raise HTTPException(404, "Chain not found")
        events = db.scalars(select(Event).where(Event.chain_id == chain_id).order_by(Event.created_at.asc(), Event.id.asc())).all()
        return [{
            "event_type": event.event_type,
            "created_at": event.created_at.isoformat(),
            "activity_id": event.activity_id,
            "distance_km": event.distance_km,
            "note": event.note,
            "metadata": json.loads(event.metadata_json) if event.metadata_json else None,
        } for event in events]


@app.get("/health")
def health():
    with Session(engine) as db:
        last_ok = latest_sync(db, successful_only=True)
        last = latest_sync(db)
    return {
        "status": "ok",
        "app": APP_NAME,
        "version": app.version,
        "strava_api_base_url": STRAVA_API_BASE_URL,
        "pushover_configured": bool(PUSHOVER_APP_TOKEN and PUSHOVER_USER_KEY),
        "strava_auto_sync": STRAVA_AUTO_SYNC,
        "strava_sync_times": configured_sync_times_display() if STRAVA_AUTO_SYNC else "",
        "timezone": CHAINLOOP_TIMEZONE,
        "last_successful_sync": last_ok.completed_at.isoformat() if last_ok and last_ok.completed_at else None,
        "last_sync_error": last.error if last and not last.success else None,
    }
