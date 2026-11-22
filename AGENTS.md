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

Credentials are capability, not authorization. Possession of a GitHub token,
configured credentials or an authenticated `gh` session never grants permission
to perform a remote write without the explicit owner approval required below.

### Local operations — no approval required

Agents may without approval:

- create and switch non-protected local branches;
- edit files and create local commits;
- amend local work before it has been pushed, when appropriate;
- inspect the repository, branches, PRs, reviews, CI results, issues and other
  read-only GitHub state;
- prepare and review diffs;
- run tests and validation.

### Remote writes — explicit approval required

Any operation that changes remote Git/GitHub state requires explicit approval
from the repository owner immediately before execution. This includes:

- pushing a new branch;
- pushing additional commits to an existing remote branch;
- force-pushing a non-protected/private work branch where otherwise allowed;
- opening a pull request;
- updating PR metadata, body or title;
- closing or reopening PRs or issues;
- deleting remote branches;
- any other GitHub mutation, subject to the high-risk restrictions below.

Required workflow for remote writes:

1. Complete and validate the local work first.
2. Report the current branch, commit(s), working-tree status, tests/validation
   performed and exact remote actions proposed.
3. **STOP** and ask the repository owner for explicit approval.
4. After approval, perform only the specifically approved remote actions.
5. Report the resulting remote branch, PR URL or other changed GitHub state.

Approval is action-specific and one-time. Approval for one push or PR creation
does not grant standing permission for later pushes or other GitHub mutations.
If the proposed commits, target or remote actions change, seek new approval.
Ordinary development-task instructions are not approval for remote writes.

### Prohibited / separate high-risk actions

Agents must not perform the following as part of the ordinary development
workflow:

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
  or other administrative configuration.

These actions require a separate explicit repository-owner instruction where
applicable. Ordinary approval to "push and open the PR" does not authorize them.

### Remote branch cleanup

Never delete a remote branch automatically. Deletion requires explicit approval
for that specific branch immediately before execution; general cleanup approval
does not authorize deleting other branches.

### Owner authority and expected workflow

The repository owner retains sole authority for:

- final review and approval of changes;
- merging pull requests into `main`;
- direct changes to `main`, if ever required;
- release and tag publication;
- repository administration.

The normal expected workflow is:

```text
plan
-> implement locally
-> test
-> review diff
-> commit locally
-> report ready remote actions
-> STOP for owner approval
-> push/open PR after approval
-> owner reviews/merges
```

Pushes and PR creation must not happen automatically when local work is ready.
These restrictions apply even when configured credentials technically permit
an operation. Credentials provide capability, not authorization.

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
