# ChainLoop development instructions

ChainLoop is a self-hosted bicycle chain usage, wear and maintenance tracker
for immersive hot waxing.

## Architecture
- Python/FastAPI
- SQLite is authoritative
- Docker deployment
- Strava activity ingestion
- Pushover notifications

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

## GitHub write boundaries

Sandbox agents may use configured GitHub credentials to support the review
workflow, but possession of those credentials does not authorize final
repository actions.

Agents may:

- create and work on non-protected feature, fix, chore, and release branches;
- create commits on those working branches;
- push those working branches to the remote repository;
- open and update pull requests;
- read pull-request reviews, CI results, checks, issues, and repository
  information required for development.

Agents must not:

- push commits directly to `main`;
- merge, squash-merge, or rebase-merge any pull request;
- enable auto-merge or add a pull request to a merge queue;
- approve their own pull requests;
- force-push `main` or another protected/shared branch;
- create, move, delete, or push release tags;
- create or publish GitHub releases;
- change repository visibility;
- change branch protection or repository rulesets;
- change repository settings, secrets, deploy keys, collaborators, permissions,
  or other administrative configuration;
- delete remote branches unless explicitly instructed by the repository owner
  as part of an already approved cleanup.

The repository owner retains sole authority for:

- final review and approval of changes;
- merging pull requests into `main`;
- direct changes to `main`, if ever required;
- release and tag publication;
- repository administration.

These restrictions apply even when configured GitHub credentials technically
permit an operation. GitHub credentials provide capability, not authorization.

Do not interpret an ordinary development-task instruction as overriding these
boundaries. Changing this policy requires an explicit request from the
repository owner to modify this section.

## Workflow
Before substantial changes:
1. Read README.md, ROADMAP.md, CHANGELOG.md and UPGRADE.md.
2. Inspect existing code before proposing changes.
3. Make a plan first.
4. Preserve backward compatibility.
5. Add/update tests.
6. Run tests.
7. Review the final diff.

## Docker Sandbox manual testing

When preparing a disposable Docker candidate for manual testing inside Docker
Sandboxes:

- ChainLoop must listen on `0.0.0.0:8080` inside its container.
- The candidate container must publish:
  `sandbox 0.0.0.0:8080 -> container 8080`.
- Do not mistakenly publish sandbox port 18080 from the nested Docker container.
- The outer Docker Sandbox mapping is responsible for:
  `host 127.0.0.1:18080 -> sandbox 8080`.
- Therefore the expected path is:

  host 127.0.0.1:18080
      -> sandbox 8080
      -> ChainLoop container 8080

- Verify both:
  - from the sandbox VM: `curl http://127.0.0.1:8080/health`
  - from the host: `curl http://127.0.0.1:18080/health`

- Do not change the public/production Compose example merely to support
  Sandbox testing. The nested Docker port publishing is disposable
  development infrastructure only.

## Manual-test sample data

Whenever a disposable candidate is prepared for manual browser testing:

- Populate it with realistic synthetic data unless the task specifically
  requires an empty database.
- Do not use real production data, credentials, tokens, domains, Strava IDs,
  Pushover credentials, or personally identifying information.
- Synthetic data should exercise the workflows relevant to the change.

Where useful, include realistic examples such as:
- multiple riders;
- multiple bikes;
- compatible and incompatible chain specifications;
- several physical chains in NEW, READY, IN_USE, NEEDS_WAX and RETIRED states;
- multiple wax products;
- several wax cycles;
- wear measurements;
- realistic ride distances and dates;
- historical activities;
- enough activity history for statistics and charts;
- notification threshold states;
- historical corrections where relevant.

The sample dataset should make the UI look like a realistically used ChainLoop
installation rather than an empty demonstration database.

Synthetic data and disposable test configuration must never be committed unless
they are deliberately added as generic test fixtures.
