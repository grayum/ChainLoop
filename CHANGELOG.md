# Changelog

## 0.8.0

### BREAKING / OPERATOR ACTION — container UID/GID

The container now runs as **UID 10001, GID 10001**, replacing the previous root
runtime. Existing root-owned SQLite data may be unwritable until a one-time
ownership correction is applied. **Back up first, stop ChainLoop, confirm the exact
ChainLoop data directory, then change ownership only there when required.** Never
recursively chown an arbitrary parent directory or overwrite local `.env` or
`docker-compose.yaml`. Follow [UPGRADE.md](UPGRADE.md#v07x---v080) before starting.

Copy the runtime hardening settings into your local deployment: read-only root,
writable `/data`, bounded `/tmp` tmpfs, non-root UID/GID, all capabilities dropped
and `no-new-privileges`. Updating the tracked example does not update local files.

### Test safety and data integrity

- Added an automated test safety harness that confines SQLite access to disposable
  test directories or in-memory databases and guards against installation data.
- Fixed historical activity/correction eligibility for retired physical chains:
  rides on or before recorded retirement remain eligible; later rides are rejected.
  Tests cover lifetime/current-cycle accounting, historical closed wax intervals,
  reversal, idempotent imports and backfills without maintenance notifications.
- Made no-ready-spare notifications consistently require a READY chain with a wax
  event, matching spare counting and installation eligibility.
- Preserved existing km-since-wax when recording a missing initial treatment for
  legacy READY chains; no invented wax history or user-visible cycle 0.

### Controlled database migrations

- Added ordered startup migrations and an append-only `schema_migrations` ledger
  with version/name and schema postcondition validation. Schema version is **2**;
  historical migration names remain unchanged.
- Safely adopt supported pre-ledger databases while retaining `migration_markers`
  and historical maintenance, activity attribution and distance data.
- Refuse newer, contradictory or unsupported schema states instead of guessing.
- Removed database side effects from module imports; filename handling and database
  initialization run during controlled application startup before the scheduler.
- Serialize concurrent SQLite startup migrations with a write lock and busy timeout.

### Browser security (Phase 4A)

- Added session-bound CSRF checks before browser mutations, signed eight-hour
  sessions, mandatory random session secrets and hardened production cookies.
- Added strict Host validation and canonical HTTPS `APP_BASE_URL` handling; an
  explicit Strava redirect URI must match. Loopback HTTP requires development mode.
- Correlated time-limited Strava OAuth state with the initiating browser session.
- Added CSP and browser security headers, local chart assets and contextual output
  encoding; inline executable scripts/styles and framing are blocked.
- Limited `/health` to status/app/version, redacted integration errors and callback
  query logging, disabled interactive API docs and stopped validation-error input echoes.
- Updated Jinja2 to 3.1.6, python-multipart to 0.0.31, Starlette to 1.3.1 and
  FastAPI to 0.136.0 for security advisories and framework compatibility.

### Validation and runtime security (Phase 4B)

- Added strict bounded browser/configuration validation, finite numeric checks,
  0–2% new wear measurements and maintenance date/relationship checks.
- Validate untrusted Strava token, activity and gear payloads before use; malformed
  activities are skipped without accounting and malformed pages fail the sync.
- Hardened outbound URL/SSRF boundaries: approved HTTPS integration endpoints,
  no redirects or ambient proxies, TLS verification, explicit loopback-only mocks.
- Bounded browser requests and streamed external responses; reject unexpected
  compressed Strava responses before decompression.
- Hardened Docker with non-root UID/GID 10001, read-only root, writable `/data`,
  bounded `/tmp` tmpfs, dropped capabilities and `no-new-privileges`.
- Added `.dockerignore` build-context exclusions and sensitive SQLite/WAL/SHM
  storage guidance. Documented proxy request limits and abuse controls.
- Dependency audit reports no known vulnerabilities; the existing Starlette/AnyIO
  test deprecation warnings remain deferred compatibility work.

**ChainLoop still has no built-in authentication or authorization.** Operators must
protect it with authenticated reverse-proxy, VPN or equivalent network access
controls and prevent backend bypass. This release is not suitable for direct
public Internet exposure and adds no Home Assistant features or RBAC.

## 0.7.0

- Added correct wax treatment/cycle semantics: the first recorded wax starts cycle 1; chains with no treatment show no active cycle instead of cycle 0.
- Added `NEW` chain state for unprepared physical chains.
- Added explicit initial-wax workflow for NEW chains and existing READY chains that lack wax history.
- READY spare counting/installation now requires a recorded wax treatment.
- Added Administration UI for riders, chain specifications, bikes, physical chains and wax products.
- Added physical-chain creation with initial lifetime km, optional initial wear and optional first-used date.
- Added compatible reassignment of NEW/READY chains between bikes that use the same chain specification.
- Added wax-product editing, archiving/reactivation and delete-only-if-unused behaviour.
- Archived wax products remain in historical statistics but are excluded from new wax selections.
- Fresh v0.7 installations start empty instead of seeding example equipment.
- Added optional multiple daily Strava sync times through `STRAVA_SYNC_TIMES`, retaining `STRAVA_SYNC_TIME` compatibility.
- Added `UPGRADE.md` with Git and tarball/manual upgrade, verification and rollback guidance.
- Improved mobile layouts for administration and maintenance forms.

## 0.6.0

- Replaced the browser favicon with the finalized, enlarged green infinity-only design.
- Kept the existing ChainLoop main logo, app icon and Strava application icon unchanged.
- Removed maintainer-specific bike, chain, gear, domain and wax examples from public documentation.
- Replaced fresh-install maintainer-specific seed data with neutral example data; existing databases are unaffected.
- Added `docker-compose.yaml.example` and stopped treating a real deployment compose file as repository content.
- Expanded `.gitignore` to protect `.env`, local Compose configuration, `data/`, SQLite database/WAL files and common Python runtime files.
- Added the Strava API Settings URL (`https://www.strava.com/settings/api`) to the initial setup instructions.
- Added multiple-daily-sync support to the roadmap as a lower-priority enhancement.
- Added concise inline comments and docstrings around migrations, ride accounting, historical corrections, notification deduplication, OAuth, Strava gear lookup and the scheduler.
- Updated release and upgrade documentation for v0.6.0.

## 0.5.0

- Implemented roadmap Priorities 1–4.
- Added per-wax-cycle Pushover threshold deduplication.
- Added ⚠️ 500 km, 🔧 600 km and ⛔️ 800 km notification messages.
- Added no-ready-spare Pushover warning once a bike is change-due.
- Migrates successful v0.4 threshold alerts into the new dedup state and suppresses historical thresholds already passed during backfill.
- Added built-in daily Strava synchronization, defaulting to 21:00 in the configured timezone.
- Added sync-run history: trigger, timestamps, imported/processed count, added km and errors.
- Improved manual sync UI and dashboard sync status.
- Added maintenance status indicators and ready-spare counts.
- Added advisory wear-before-wax check with explicit continue-without-measurement option.
- Enforced one active chain per bike with a SQLite partial unique index.
- Added chain retirement workflow with final wear measurement and retirement reason.
- Added physical-chain history pages.
- Added wax history and wax-product performance statistics.
- Added wear-vs-lifetime-distance and wax-interval graphs.
- Added chain comparison statistics.
- Added activity correction UI for bike/chain reassignment, distance correction and exclusion.
- Activity corrections reverse previous accounting before applying the new state and update the correct historical wax interval when needed.
- Added credited physical-chain tracking to imported activities.
- Manual distance adjustments require a reason and create `DISTANCE_ADJUSTMENT` audit events.
- Added complete web audit log.
- Added database-level duplicate protection for `(source, external_id)`.
- Expanded `/api/bikes`, `/api/activities/recent`, `/api/history/{chain_id}` and `/health`.

## 0.4.0

- Added safe historical tracking initialization/backfill.
- Historical setup can select an already imported first ride, active physical chain, wax product and wax date.
- Backfilled rides remain individual Strava activities and create normal `RIDE_ADDED` audit events.
- Historical backfill suppresses Pushover threshold notifications.
- The active chain's first-used date is set from the selected first ride.
- Initial wax is stored as a proper wax event instead of a distance correction.
- Browser-triggered Strava sync redirects back to the ChainLoop UI with a result summary instead of leaving the user on raw JSON.
- Added latest wax date display beside the current wax product.
- Updated Docker Compose variable handling: deployment-critical values use `:?` validation while harmless defaults retain `:-`.

## 0.3.0

- Finalized ChainLoop naming and branding.
- Added final logo, app icon, favicon and 124×124 Strava tile.
- Fixed/configured Strava API base URL handling.
- Added time-limited OAuth state validation.
- Added Strava gear metadata discovery and mapping UI.
- Added safe tracking start so historical imports do not silently inflate the active chain.
- Added manual distance adjustment events for initial values/corrections.
- Added current wax product display.
- Added recent activity display/API.
- Added Pushover test and distance-threshold notifications.
- Added SQLite filename migration from the first MVP.
- Added Traefik-ready Compose example.

## 0.2.0

- Renamed application to ChainLoop.
- Added initial branding and favicon support.
- Added basic Strava gear mapping UI.
- Made the Strava API base URL configurable.

## 0.1.0

- Initial Chain Tracker MVP.
