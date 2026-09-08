# ChainLoop Roadmap

## Completed in v0.5.0 — Priorities 1–4

### Priority 1 — Core reliability

- Pushover notification deduplication per wax cycle.
- Distinct ⚠️ / 🔧 / ⛔️ threshold alerts.
- Automatic Strava sync once daily, 21:00 by default.
- Persistent sync status and improved sync-result UI.

### Priority 2 — Maintenance workflow

- Clear maintenance states and chain-change workflow.
- Wear-before-wax advisory prompt.
- `IN_USE`, `READY`, `NEEDS_WAX`, `RETIRED` chain states.
- Protection against multiple active chains on one bike.
- Pushover no-ready-spare warning.
- Chain retirement with final wear and reason.

### Priority 3 — History and statistics

- Per-chain history pages.
- Wax history.
- Wax performance statistics.
- Wear vs lifetime-km graph.
- Wax interval graph.
- Chain comparison table.

### Priority 4 — Corrections and data integrity

- Activity correction UI.
- Manual distance adjustments with required reasons.
- Complete audit log.
- Application- and database-level duplicate protection.

## Completed in v0.7.0 — Priority 5 and setup polish

- Improved dashboard progress/wax-cycle visibility.
- Rider, bike and chain-specification administration.
- Physical-chain administration with initial km/wear, NEW/READY preparation state and compatible reassignment.
- Wax-product add/edit/archive/reactivate/delete-unused workflow.
- Explicit initial-wax workflow: first treatment starts cycle 1.
- Mobile-friendly maintenance/administration improvements.
- Multiple daily Strava sync times (`STRAVA_SYNC_TIMES`) with `STRAVA_SYNC_TIME` backwards compatibility.
- Dedicated `UPGRADE.md`.

## Security baseline — before further integration work

- Phase 4A (unreleased): external-access-control documentation; CSRF and signed
  browser sessions; OAuth correlation; mandatory secrets; secure cookies; canonical
  URL/Host validation; CSP/headers; health/error redaction; security dependency updates.
- Phase 4B (unreleased): bounded input/external-payload validation, outbound URL
  constraints, non-root/read-only container hardening and abuse-control guidance.
- Native authentication/authorization is not provided; access policy remains an
  explicit deployment responsibility.

## Priority 6 — Home Assistant

- Expanded REST API for current state, maintenance, next chain, km remaining, wear, last wax and sync state.
- Example HA REST sensors.
- Example compact HA dashboard card.
- Selected safe HA actions; destructive/correction operations remain in ChainLoop.

## Priority 7 — Future expansion / lower priority

- Public repository polish: choose an open-source license, add CONTRIBUTING guidance and issue/release templates.
- Multiple bikes with different chain specifications and rotation sizes.
- Multiple people and, where needed, separate Strava accounts.
- Intervals.icu activity adapter.
- Carefully scoped Strava webhook support.
- CSV export for analysis/portability.
- Proper versioned database migrations (for example Alembic) as the project matures.
