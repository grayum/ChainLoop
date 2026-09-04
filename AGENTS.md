# ChainLoop development instructions

ChainLoop is a self-hosted bicycle chain usage, wear and maintenance tracker
for immersive hot waxing.

## Architecture
- Python/FastAPI
- SQLite is authoritative
- Docker deployment
- Strava activity ingestion
- Pushover notifications
- Home Assistant is an integration/display layer

## Data integrity
- Preserve existing SQLite installations.
- Never silently discard or rewrite historical maintenance data.
- Physical chains have permanent identities.
- Lifetime km and km-since-wax are separate.
- Imported activities must be idempotent.
- Corrections must remain auditable.
- Historical corrections must affect the historically correct wax cycle.
- Backfills must not generate maintenance notifications.
- Never infer a physical chain swap.
- Wear measurements are manual.
- Before-wax is the normal wear-measurement point.
- A READY chain must have a wax event.
- First wax treatment is cycle 1; no user-visible cycle 0.

## Security
Review changes against relevant OWASP guidance.
Prioritize:
- input validation
- contextual output encoding
- CSRF protection
- XSS
- auth/authorization boundaries
- SQL injection
- secret handling
- security headers
- SSRF/external API handling
- dependency security
- container hardening

## Repository safety
Never commit:
- .env
- docker-compose.yaml
- data/
- databases
- tokens or credentials
- private deployment details

Public examples must use generic data.

## Workflow
Before substantial changes:
1. Read README.md, ROADMAP.md, CHANGELOG.md and UPGRADE.md.
2. Inspect existing code before proposing changes.
3. Make a plan first.
4. Preserve backward compatibility.
5. Add/update tests.
6. Run tests.
7. Review the final diff.

