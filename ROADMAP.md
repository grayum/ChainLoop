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

## Completed in v0.8.0 — reliability, migrations and security

- Automated disposable-database test harness and historical accounting/READY fixes.
- Controlled startup migrations, append-only schema ledger, safe pre-ledger adoption
  and refusal of newer schemas; database-side-effect-free imports.
- Phase 4A: external-access-control documentation; CSRF and signed
  browser sessions; OAuth correlation; mandatory secrets; secure cookies; canonical
  URL/Host validation; CSP/headers; health/error redaction; security dependency updates.
- Phase 4B: bounded input/external-payload validation, outbound URL
  constraints, non-root/read-only container hardening and abuse-control guidance.
- Native authentication/authorization is not provided; access policy remains an
  explicit deployment responsibility.

## Completed in v0.9.0 — Strava compatibility and UI polish

- Strava Activity/Athlete `id_str` support, exact signed-64-bit identity handling
  and pre-approved future official API host (existing default retained).
- System / Light / Dark themes with display/sun/moon selector icons, browser-local
  preference and live OS following.
- Theme-aware charts, accessible appearance controls, mobile/readability polish
  and a header version link to GitHub.
- Canonical branding assets and preserved logo artwork/maintainer attribution.

## Priority 6 — Future expansion / lower priority

- Public repository polish: choose an open-source license, add CONTRIBUTING guidance and issue/release templates.
- Multiple bikes with different chain specifications and rotation sizes.
- Multiple people and, where needed, separate Strava accounts.
- Intervals.icu activity adapter.
- Carefully scoped Strava webhook support.
- CSV export for analysis/portability.
- Evaluate a migration toolkit such as Alembic if needs outgrow the versioned
  SQLite migration framework introduced in v0.8.0.
