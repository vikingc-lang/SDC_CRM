---
name: cirra-overview
description: Map of the Cirra CRM codebase (FastAPI + Next.js + Postgres/pgvector + Celery) and which cirra-* skill covers each of the 21 modules. Use at the start of any Cirra task to find the right module skill, the files involved, and the house rules every change must follow.
---

# Cirra CRM: overview and module map

Cirra is a private-cloud CRM: FastAPI (async SQLAlchemy) API in `backend/app`, Next.js 14 app in `frontend`,
Postgres 16 + pgvector, Redis, Celery workers plus a separate beat scheduler, optional Ollama/Whisper. Everything
runs with `docker compose up -d --build`. Demo logins use password `cirra123` (marcus = Sales Manager,
admin = Super Admin, priya/diego = AEs, sam = SDR, sofia = Support Agent, nina = Marketing,
partner@northstar-partners.com = partner portal).

## Which skill to load

| # | Module (priority) | Skill |
|---|---|---|
| 1 | CRM platform foundation (P0) | `cirra-platform-core` |
| 2, 3, 16 | Accounts, contacts, customer data / 360 (P0) | `cirra-accounts-contacts` |
| 4 | Leads (P0) | `cirra-leads` |
| 5, 10 | Opportunities / pipeline, forecasting | `cirra-pipeline-forecasting` |
| 6 | Activities, email, calendar, tasks (P0) | `cirra-activities-mail` |
| 7, 8, 9 | Products, CPQ / quotes, documents, e-sign, orders, contracts, ERP | `cirra-cpq-orders` |
| 11 | Territory / quota / commission | `cirra-performance` |
| 12 | Marketing, campaigns, nurture journeys, email tracking | `cirra-marketing` |
| 13 | Customer service: cases, routing, email-to-case, SLA, knowledge, CSAT | `cirra-service` |
| 14, 15 | Customer success, renewals, partners / PRM | `cirra-success-partners` |
| 17 | Workflow automation | `cirra-workflows` |
| 18 | Analytics: reports, dashboards, list views, subscriptions | `cirra-analytics` |
| 19 | AI: quick-log, Aiden, RAG search, LLM gateway, voice | `cirra-ai` |
| 20 | Integration: API keys, webhooks, upsert, bulk, inbound email, import/export | `cirra-integration` |
| 21 | Admin, security, governance: auth, MFA/SSO, RBAC, audit, privacy, rate limits | `cirra-admin-security` |
| — | Frontend conventions (UI kit, data fetching, pages) | `cirra-frontend` |
| — | Tests: backend suite, browser journeys, CI | `cirra-testing` |
| — | Deployment and operations: compose, Helm, migrations, jobs, health | `cirra-ops` |

## Layout

```
backend/app/
  api/v1/*.py        routers (all mounted under /api/v1 in main.py)
  services/*.py      business logic, one module per domain; routers stay thin
  models/*.py        SQLAlchemy models (platform, revenue, success, leads, orders, marketing, performance,
                     partners, developer, engagement); re-exported from app.models
  core/              config, database (CirraSession), rbac (Principal), deps, audit, context,
                     ratelimit, observability, security
  worker.py          Celery app + beat schedule;  services/jobs.py = the job registry
  seed.py, demo_volume.py   demo data
backend/alembic/versions/   001..016 raw-SQL migrations
backend/tests/              pytest suite (runs against a throwaway database)
frontend/app/(app)/         pages;  frontend/components/  feature components;  components/ui/  kit
e2e/                        Playwright browser journeys
helm/cirra/                 Kubernetes chart;  .github/workflows/ci.yml  CI
```

## House rules for every change

1. **Permissions and scope always.** Endpoints depend on `authorize(resource, action)` (or `get_principal`)
   and every query that returns records goes through the principal's scope (`p.scope_accounts`,
   `p.scope_deals`, `cases.scope`, …). Return 404 (not 403) for records outside scope. See `cirra-platform-core`.
2. **Keep routers thin.** Put logic in `services/`, give it a module docstring explaining the rules.
3. **Schema changes = a new Alembic migration** (`0NN_name.py`, raw SQL in `UPGRADE_SQL`/`DOWNGRADE_SQL`,
   `down_revision` = previous file). Never edit a migration that has been applied anywhere.
4. **Server-set columns** (`updated_at` with `onupdate`) expire after flush: call `await db.refresh(obj)` after
   `commit()` before serialising, or you get a 500 (MissingGreenlet).
5. **Every feature ships with tests** (`backend/tests/test_*.py`) and, for UI flows, a browser journey in `e2e/`.
   Run tests in Docker (see `cirra-testing`); the host has no pytest/node_modules.
6. **Write for business users**: UI copy is plain, error messages say what to do next.
7. Commit with the attribution line the session asks for; push only when the user asks.
