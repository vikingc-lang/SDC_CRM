#!/bin/sh
set -e

# Per-install secrets: when JWT_SECRET is empty (the default in .env.example) generate it once and keep it on the
# data volume, so every install gets its own and restarts keep sessions and encrypted credentials valid. A new
# install gets its own DATA_ENCRYPTION_KEY the same way; existing installs keep theirs (or the JWT-derived key).
SECRETS_DIR="${SECRETS_DIR:-/data/secrets}"
persist_secret() {
  name="$1"; file="$SECRETS_DIR/$2"
  mkdir -p "$SECRETS_DIR" 2>/dev/null || return 0
  [ -w "$SECRETS_DIR" ] || return 0
  if [ ! -s "$file" ]; then
    # create-if-absent (noclobber) so api and worker starting together agree on one value
    ( umask 077; set -C; python -c "import secrets; print(secrets.token_urlsafe(48))" > "$file" ) 2>/dev/null || true
    i=0; while [ ! -s "$file" ] && [ $i -lt 20 ]; do sleep 0.2; i=$((i + 1)); done
  fi
  [ -s "$file" ] && export "$name=$(cat "$file")"
}
if [ -z "${JWT_SECRET:-}" ]; then
  persist_secret JWT_SECRET jwt_secret
  [ -z "${DATA_ENCRYPTION_KEY:-}" ] && persist_secret DATA_ENCRYPTION_KEY data_encryption_key
fi

# Wait for Postgres, apply migrations, optionally seed demo data, then exec the command.
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "Waiting for database..."
  python - <<'PY'
import asyncio, os, sys, time
import asyncpg

async def ping() -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    await conn.close()

for _ in range(30):
    try:
        asyncio.run(ping())
        sys.exit(0)
    except Exception:
        time.sleep(2)
sys.exit("Database not reachable")
PY
  alembic upgrade head
  if [ "${MULTI_TENANT:-false}" = "true" ]; then
    python -m app.tenants migrate  # every tenant workspace's database follows the primary
  fi
  if [ "${SEED_DEMO_DATA:-false}" = "true" ]; then
    python -m app.seed
  fi
fi

exec "$@"
