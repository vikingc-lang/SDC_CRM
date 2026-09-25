#!/bin/sh
set -e

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
  if [ "${SEED_DEMO_DATA:-false}" = "true" ]; then
    python -m app.seed
  fi
fi

exec "$@"
