# Upgrading ChainLoop

Read `CHANGELOG.md` before upgrading. ChainLoop keeps its authoritative history in SQLite, so upgrades should preserve your local configuration and `data/` directory.

## Files that are local to an installation

Do not replace or commit these files/directories:

```text
.env
docker-compose.yaml
data/
```

The repository provides `.env.example` and `docker-compose.yaml.example` as references only.

## v0.7.x -> v0.8.0

**BREAKING / OPERATOR ACTION:** v0.8.0 runs as **UID 10001, GID 10001**.
An existing `./data` directory/database created by the previous root-running
container may need a one-time ownership correction. ChainLoop does not repair
ownership with a root entrypoint.

1. Read this document, [CHANGELOG.md](CHANGELOG.md) and [SECURITY.md](SECURITY.md)
   before upgrading. ChainLoop has no built-in authentication or authorization;
   verify your authenticated proxy, VPN or equivalent operator-controlled access
   policy and prevent direct backend access. Do not expose it to the public Internet.
2. Create and verify a recoverable backup of the database and local configuration.
   Use a SQLite-consistent backup while running, or a stopped copy including any
   journal/WAL/SHM files; do not copy only a live database file. Protect backups as
   credentials. ChainLoop does not make an automatic migration backup.
3. Stop ChainLoop with `docker compose down` before ownership changes or file refresh.
4. Inspect the actual host directory mounted at container `/data` in your local
   Compose file and inspect its numeric ownership. Confirm `./data` resolves to
   that exact ChainLoop directory; do not assume your current directory is correct.
5. If required, change ownership **only on that confirmed data directory and its
   contents** to `10001:10001`. For a normal checkout using `./data`, after backup:

   ```bash
   docker compose down
   ls -lan ./data
   sudo chown -R 10001:10001 ./data
   ```

   **Back up first and ensure ChainLoop is stopped. Confirm `./data` really is
   ChainLoop's data directory before running chown. Never recursively chown `/`,
   a home directory, `/opt`, `/opt/docker`, the repository root or any arbitrary
   parent directory.** Substitute the verified exact mount path if different.
   Do not use `chmod 777` as a workaround. SQLite needs directory write access for
   its database, journal and WAL/SHM files, not just write access to the database.
6. Refresh repository-managed files using the Git or tarball procedure below.
   Review changes in `.env.example` and `docker-compose.yaml.example` against your
   local files. Apply the required security settings described below, preserving
   local secrets, volume paths and access policy. **Do not overwrite `.env` or
   your local `docker-compose.yaml` with examples.**
7. Validate local Compose configuration and build the v0.8.0 image with
   `docker compose config --quiet` and `docker compose build`. If using a separately
   supplied image, pull your verified v0.8.0 image instead.
8. Start ChainLoop with `docker compose up -d`; check `docker compose ps`.
9. Check `docker compose logs --tail=50 chainloop` for successful startup and no
   database, permission or migration failures. Do not retry a refused schema blindly.
10. Check `/health` using the command below. Expect
    `{"status":"ok","app":"ChainLoop","version":"0.8.0"}`.
11. Verify migration/startup: the database must have schema version **2** and the
    two ledger entries documented below. Restart once and verify startup succeeds
    without duplicate ledger entries or changed historical totals.
12. Verify the UI: riders, bikes, physical chain identities, lifetime km and
    km-since-wax, wax history, wear, activity attribution and corrections. Reload
    forms after upgrade. Verify Strava connection and configured sync schedule;
    perform a manual sync with your own integration and check its result, mapping
    and duplicate protection. Existing OAuth tokens should remain in SQLite.

### Required v0.8.0 runtime settings

The image sets `USER 10001:10001`; remove any local override that runs it as root.
Retain the following settings in your local Compose service (see the full example):

```yaml
read_only: true
init: true
cap_drop:
  - ALL
security_opt:
  - no-new-privileges:true
tmpfs:
  - /tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777
volumes:
  - ./data:/data:rw
```

The root filesystem stays read-only; `/data` stays writable and persistent, while
`/tmp` is a bounded disposable tmpfs. The tmpfs mode is unrelated to data-directory
permissions. Drop all capabilities and enable `no-new-privileges`. Updating the
tracked Compose example does not apply these requirements to your local deployment.

## Before every upgrade

1. Make or confirm a recent backup of the ChainLoop directory/database before
   installing a release that can migrate the database.
   Do not rely on ChainLoop to create an automatic migration backup.
2. Check `CHANGELOG.md` for release-specific notes.
3. Stop ChainLoop before replacing application files:

```bash
docker compose down
```

A short local rollback copy is optional but convenient:

```bash
cp -a /opt/docker/chainloop /opt/docker/chainloop-pre-upgrade
```

## Git-based installations

For a normal Git checkout:

```bash
git status
git pull --ff-only
```

Review the example configuration changes and apply the required v0.8.0 settings
to your local files without replacing deployment values:

```bash
git diff HEAD@{1} -- .env.example docker-compose.yaml.example
```

Then validate, rebuild and start:

```bash
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
```

## Tarball/manual installations

Extract the new release into a temporary directory and copy only repository-managed files:

```bash
rsync -av \
  --exclude='.env' \
  --exclude='docker-compose.yaml' \
  --exclude='data/' \
  /tmp/ChainLoop/ \
  /opt/docker/chainloop/
```

Then:

```bash
cd /opt/docker/chainloop
docker compose config --quiet
docker compose build --no-cache
docker compose up -d
docker compose ps
```

## Post-upgrade checks

Verify the application from inside the container:

```bash
docker compose exec -T chainloop python - <<'PY'
import urllib.request
print(urllib.request.urlopen(
    "http://127.0.0.1:8080/health"
).read().decode())
PY
```

Also check:

```bash
docker compose logs --tail=50 chainloop
```

In the UI verify:

- the expected application version;
- existing bike/chain totals;
- Strava connection and scheduler state;
- current wax product/cycle;
- recent activities.

## Database migrations

ChainLoop runs ordered, additive SQLite migrations during application startup,
before the scheduler begins. Successful migrations are recorded in the
`schema_migrations` ledger. The older `migration_markers` table is retained as
historical evidence and is not replaced or repurposed.

Fresh empty databases are created at the current schema. Supported older
databases without a ledger are structurally validated before they are adopted.
Databases with a ledger are migrated in version order. ChainLoop refuses to
start rather than guess when it finds a newer schema, a contradictory ledger or
marker state, or an unsupported legacy shape.

Migration versions are append-only: released version/name pairs keep their
original meaning, and later schema work receives a new version.

The v0.8.0 schema version is **2**, independently of the application version.
The expected `schema_migrations` rows (with recorded `applied_at` timestamps) are:

| version | name |
|---|---|
| 1 | `v0.5.0-schema-and-data` |
| 2 | `v0.7.0-schema` |

These are permanent historical identifiers; `v0.7.0-schema` is correct in v0.8.0.
The legacy `v0.5.0-data-backfill` marker remains in `migration_markers` along with
any existing supported marker history. To inspect the ledger after startup:

```bash
docker compose exec -T chainloop python - <<'PYTHON'
import os
import sqlite3
from sqlalchemy.engine import make_url
path = make_url(os.environ.get("DATABASE_URL", "sqlite:////data/chainloop.db")).database
with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
    print(db.execute(
        "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
    ).fetchall())
    print(db.execute("SELECT key, applied_at FROM migration_markers ORDER BY key").fetchall())
PYTHON
```

Never replace `data/chainloop.db` with an empty database during an application upgrade.

## v0.8.0 browser security and configuration

Before rebuilding, read [SECURITY.md](SECURITY.md). ChainLoop provides no
access control: verify your authenticated reverse proxy, VPN or equivalent policy
and prevent direct backend access. The production example retains no published port.

- Generate and set a mandatory `SESSION_SECRET` (at least 32 random bytes):
  `python -c 'import secrets; print(secrets.token_urlsafe(32))'`.
  Placeholder secrets no longer work. Rotation invalidates browser sessions and
  pending OAuth flows, not stored Strava OAuth tokens.
- Set `APP_BASE_URL` to the canonical external HTTPS origin. If explicitly set,
  `STRAVA_REDIRECT_URI` must match its `/auth/strava/callback` URL. Trailing base
  slashes and default URL ports are normalized. Subpath deployment is unsupported.
- Add `CHAINLOOP_ALLOWED_HOSTS=127.0.0.1,localhost` for the supplied healthcheck.
  The canonical hostname is automatically allowed. Other alternate hosts need
  explicit configuration; wildcard hosts and arbitrary forwarded hosts are rejected.
- HTTP development requires `CHAINLOOP_DEV_ALLOW_HTTP=true` and a loopback
  canonical URL. Do not enable this for production. See Sandbox instructions.
- Reload open forms after upgrade. All browser POSTs require a signed session and
  CSRF field; scripts submitting POSTs need the same flow. GET application APIs
  retain their functionality, with historical integration-error notes redacted.
- `/health` now exposes only `status`, `app`, `version`; update consumers relying
  on its former configuration/sync fields. Interactive API documentation is disabled.
- CSP blocks inline scripts/styles and framing. Use existing JSON APIs rather
  than embedding the UI in an iframe. Static assets still support caching.
- Startup errors omit raw database diagnostics; back up and consult migration
  guidance before retrying an incompatible database. Existing raw integration errors
  remain in SQLite but are hidden from ordinary responses; protect the database and backups as credentials.

Dependency updates include the security-affected Jinja2, python-multipart,
Starlette and the FastAPI version needed for compatibility. The non-root ownership
and runtime requirements above are part of the same v0.8.0 upgrade.

## v0.8.0 input and integration compatibility

Validation does not rewrite historical wear or maintenance records. The startup
framework adopts supported pre-ledger databases as described above. New submissions
must satisfy the bounds in
[SECURITY.md](SECURITY.md), including 0–2% wear and dates no later than today in
`CHAINLOOP_TIMEZONE`. Reload browser forms after upgrading. Scripts should handle
controlled 400/404/409/422 responses; the recent-activity API limit must be 1–100.

Custom Strava API origins are no longer accepted in production. Explicit
loopback development mocks require `CHAINLOOP_DEV_ALLOW_OUTBOUND_MOCKS=true` and
development/test mode. Credential-bearing requests do not follow redirects,
disable certificate verification or honor ambient HTTP proxy settings. Unexpected
compressed Strava responses are rejected. Sync results add `skipped`; malformed
activities remain unimported but are not guaranteed retries beyond the normal
synchronization window. Copy the runtime hardening options into your local Compose
configuration; the tracked example does not update local deployments automatically.

## v0.6.x -> v0.7.0 notes

v0.7.0 adds wax-product archiving and proper wax-cycle numbering. The first recorded wax treatment is **cycle 1**.

Existing chains that are marked `READY` but have no wax event are not assigned an invented treatment/date. ChainLoop flags them as missing their initial wax. Open the chain page and use **Record initial wax** before installing that chain. This preserves accurate wax history.

v0.7.0 also adds optional multiple daily Strava sync times:

```dotenv
STRAVA_SYNC_TIMES=07:00,13:00,21:00
```

When `STRAVA_SYNC_TIMES` is empty or unset, the existing `STRAVA_SYNC_TIME` value remains in use.

## Rollback

**Do not casually start an older application against a database that a newer
version has migrated.** Older code may lack the newer-schema refusal and can
perform its own import/startup mutations. The ledger is not permission to downgrade.

Stop the candidate first. Keep a protected copy of its database and all journal/WAL/SHM
files for recovery. Restore the matching pre-upgrade application, configuration
and verified pre-upgrade database backup when compatibility has not been explicitly
established. Restoring a backup loses changes made after that backup; account for
those changes before rollback. Never delete ledger rows or rename migrations to
force an older version to start. Review data ownership again before a later upgrade
if a restored root-running container has created root-owned files.
