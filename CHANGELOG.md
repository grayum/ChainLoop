# Changelog

## Unreleased

- Phase 4A: documented external access control as an operator responsibility;
  ChainLoop has no authentication/authorization or proxy identity handling.
- Added session-bound CSRF checks before browser mutations, signed eight-hour
  browser sessions, mandatory random session secrets and session-correlated
  Strava OAuth state with its existing ten-minute expiry.
- Added strict canonical URL/Host validation, secure production cookies and an
  explicit loopback-only HTTP development exception for Docker Sandbox.
- Added restrictive CSP and browser security headers; moved chart JavaScript and
  inline styling into static assets while preserving contextual output encoding.
- Reduced health output, redacted integration errors and callback access logs,
  disabled interactive API docs and prevented validation responses echoing input.
- Updated Jinja2 to 3.1.6, python-multipart to 0.0.31, Starlette to 1.3.1 and
  FastAPI to 0.136.0 for concrete security advisories/framework compatibility.
- Preserved accounting, wax cycles, migration execution and notification rules;
  broader validation/outbound URL/container hardening remain Phase 4B.

- Added an ordered, append-only SQLite schema migration ledger with explicit
  current-version and postcondition validation.
- Added safe adoption of supported pre-ledger ChainLoop databases while
  preserving the historical `migration_markers` table and its data.
- Moved legacy filename handling and all database migrations out of module
  import and into controlled application startup before scheduler launch.
- Added explicit rejection of newer, contradictory and unsupported database
  states instead of attempting speculative repair.
- Serialized concurrent SQLite startup migrations with a write lock and busy
  timeout.

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
