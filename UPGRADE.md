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

Review changes to the example configuration files and copy any new variables you actually want into your local files:

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

Never replace `data/chainloop.db` with an empty database during an application upgrade.

## v0.6.x -> v0.7.0 notes

v0.7.0 adds wax-product archiving and proper wax-cycle numbering. The first recorded wax treatment is **cycle 1**.

Existing chains that are marked `READY` but have no wax event are not assigned an invented treatment/date. ChainLoop flags them as missing their initial wax. Open the chain page and use **Record initial wax** before installing that chain. This preserves accurate wax history.

v0.7.0 also adds optional multiple daily Strava sync times:

```dotenv
STRAVA_SYNC_TIMES=07:00,13:00,21:00
```

When `STRAVA_SYNC_TIMES` is empty or unset, the existing `STRAVA_SYNC_TIME` value remains in use.

## Rollback

If the new container cannot start, stop it and restore the previous application files. Restore the database from backup only if release notes explicitly say a schema migration cannot be used by the previous release.

Avoid casually rolling an older application version against a database that has already received newer schema migrations.
