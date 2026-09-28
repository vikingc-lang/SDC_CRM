---
name: cirra-ops
description: Deploying and operating Cirra - docker compose services (db, redis, ollama, api, worker, scheduler, web, optional whisper), the Helm chart, Alembic migrations, the Celery job registry and beat schedule, health/readiness endpoints, environment settings, demo data seeding and local gotchas on Windows. Use for docker-compose.yml, helm/, worker.py, services/jobs.py, alembic migrations, .env settings or production readiness questions.
---

# Operations and deployment

## Services
`docker-compose.yml`: `db` (pgvector/pgvector:pg16), `redis`, `ollama`, `api` (runs migrations on start via
`docker-entrypoint.sh`, `SEED_DEMO_DATA=true` seeds), `worker` (Celery, no beat), `scheduler` (Celery beat,
exactly one), `web` (Next.js, `NEXT_PUBLIC_API_URL` build arg), `whisper` (profile `voice`).
Rebuild after code changes: `docker compose up -d --build api worker scheduler web` (images have no source mounts).

Helm (`helm/cirra`): API (migration init container, probes `/health/ready` and `/health/live`), worker
(scalable), scheduler (1 replica, Recreate), web, optional bundled Postgres/Redis/Ollama/Whisper, zero-egress
NetworkPolicy (`networkPolicy.allowEgressCIDRs`). Validate with
`docker run --rm -v "$(pwd -W)/helm:/helm" alpine/helm:3.16.2 lint /helm/cirra` (and `template`).

## Jobs
- Register a job in `services/jobs.py` `JOBS` (async function opening its own `SessionLocal`), schedule it in
  `worker.py` `beat_schedule` with `_every(name, crontab(...))`, and show it in Admin → Jobs if user-visible.
- Jobs are system work (no principal): no validation rules, no field security; commit per unit of work so a
  failure doesn't redo finished work.
- On-demand from requests: `enqueue(background, "job_name", *args)` (Celery when `USE_CELERY=true`, otherwise
  FastAPI background task).

## Migrations
`backend/alembic/versions/0NN_description.py`, raw SQL, `revision`/`down_revision` chain (latest: 016).
Never edit an applied migration; add a new one (e.g. 015 widened 002's check constraint). `/health/ready`
reports `migrations` not at head.

## Health and observability
`/health` (DB ping), `/health/live` (no deps), `/health/ready` (DB, migrations at head, Redis → 503 if not);
`X-Request-ID` on every response; `LOG_FORMAT=json`; rate limits via `RATE_LIMIT_*`.

## Secrets
`JWT_SECRET` / `DATA_ENCRYPTION_KEY`: leave empty in compose and `docker-entrypoint.sh` generates them once into
`/data/secrets` (api, worker and scheduler share the volume). Helm generates them in its Secret and sets
`ENVIRONMENT=production`, which makes the API refuse weak or published secrets at startup.

## Settings
All in `core/config.py` (pydantic-settings from env / `.env`, documented in `.env.example`): database/redis,
JWT, CORS, `PUBLIC_WEB_URL`, `PUBLIC_API_URL`, LLM/embedding/transcription providers, ERP/enrichment/e-sign
connectors, `SMTP_*`, `INBOUND_EMAIL_SECRET`, `SUPPORT_IMAP_*`, rate limits. `.env` is local and git-ignored.

## Demo data
`python -m app.seed [--reset]`; volume data `docker compose exec api python -m app.demo_volume [--scale 3]`.

## Windows host gotchas
- Git Bash rewrites paths in `docker exec`: prefix with `MSYS_NO_PATHCONV=1`.
- Complex heredocs with backslashes break: write edit scripts to the scratchpad and run them with python.
- Git converts LF→CRLF on checkout; binaries are marked in `.gitattributes`.
