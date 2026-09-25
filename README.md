# relate [R]: AI-first, private-cloud CRM

**Intelligent pipeline memory** from the SDC Solutions portfolio (alongside promo [Q], Yield [S], deduct [✔] and nexora [§]).

Sales teams stop typing into forms. Paste meeting notes, emails or dictation into **Quick-Log (⌘K)** and relate extracts
the account, the buying committee, the deal value and timeline, the next steps and the sentiment. You review the preview
and commit it in one click. Health and risk scores update right away and explain themselves. Every note becomes
searchable semantic memory (pgvector) that the Copilot (⌘J) can answer questions from.

![Home dashboard](docs/screenshots/home-light.png)

| Pipeline with stage gates | Account 360 |
|---|---|
| ![Pipeline](docs/screenshots/pipeline-light.png) | ![Account 360](docs/screenshots/account360-light.png) |
| **Quick-Log review (⌘K)** | **Deal: explainable risk & AI insights** |
| ![Quick-Log](docs/screenshots/quicklog-review-light.png) | ![Deal](docs/screenshots/deal-light.png) |

Dark mode, phone-width layouts and keyboard shortcuts are built in (`docs/screenshots/home-dark.png`, `mobile-home-light.png`).

**No setup needed to look around:** open [`docs/relate-ui-preview.html`](docs/relate-ui-preview.html) in any browser. It is a single
self-contained file (works offline) with every screen rendered from the real app and demo data. Links, ⌘K / ⌘J and the
theme toggle work, while live features (drag-and-drop, AI extraction, saving) need the running app.

---

## Quick start

### Option A: Docker (one command)

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec ollama ollama pull llama3.1:8b   # first run only, for the local LLM
```

Open http://localhost:3000 and sign in as **marcus@relate.demo / relate123** (sales manager).
Also available: `priya@relate.demo` (sales rep), `viewer@relate.demo` (read-only), `admin@relate.demo`.

The stack runs Postgres 16 + pgvector, Redis 7, Ollama, the FastAPI API, a Celery worker (with a nightly beat re-score),
and the Next.js web app. Migrations run automatically, and `SEED_DEMO_DATA=true` loads the demo workspace.

### Option B: local development

Prerequisites: Python 3.11, Node 20+, PostgreSQL 16 with the `vector` extension, and optionally Redis.

```bash
# API
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql+asyncpg://relate_user:relate_secure_password@localhost:5432/relate_crm
alembic upgrade head
python -m app.seed                   # --minimal = spec minimum (3 accounts, 6 contacts, 3 deals); --reset wipes first
uvicorn app.main:app --reload        # http://localhost:8000/docs

# Web
cd ../frontend
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev   # http://localhost:3000
```

With no model configured (`LLM_PROVIDER=heuristic`, the default outside Docker), every AI feature runs on relate's
deterministic engine, so the product works fully offline.

### Tests

```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://relate_user:relate_secure_password@localhost:5432/relate_test pytest
cd frontend && npm run typecheck && npm run lint && npm run build
```

The backend suite (19 tests) covers the scoring formulas, the extractor, the LLM fallback path and the API end to end:
auth and roles, Account 360, the Kanban board, stage gates and overrides, Quick-Log preview and commit, semantic search,
Copilot answers and the dashboard.

---

## What's inside

```
├── docker-compose.yml        # db (pgvector), redis, ollama, api, worker, web
├── .env.example              # every setting, documented
├── backend/                  # FastAPI · async SQLAlchemy 2.0 · asyncpg · Pydantic v2 · Alembic · Celery
│   ├── alembic/versions/001_initial_schema.py   # spec §4 DDL + activities/tasks/audit/pgvector
│   └── app/
│       ├── main.py           # app entrypoint + CORS
│       ├── core/             # config, DB session factory, JWT/bcrypt, auth dependencies
│       ├── models/           # SQLAlchemy models (mirror the migration)
│       ├── schemas/          # Pydantic v2 contracts (ai.py = spec §9)
│       ├── services/
│       │   ├── ai_extractor.py      # spec §8 system prompt + deterministic extractor
│       │   ├── llm.py               # Ollama / Claude on Bedrock / Claude API gateway with fallback
│       │   ├── embeddings.py        # 1536-d embeddings (hash / Ollama / Titan)
│       │   ├── scoring.py           # spec §6 health & risk engine
│       │   ├── pipeline_service.py  # spec §5 stage gates, §7 forecasting, Kanban
│       │   ├── insights.py          # stage-trigger AI actions, briefing, Copilot, drafts
│       │   └── jobs.py              # Celery or in-process background jobs
│       ├── api/v1/           # auth, accounts, contacts, deals/pipeline, activities/tasks, ai/search/dashboard
│       ├── worker.py         # Celery worker + nightly re-score
│       └── seed.py
└── frontend/                 # Next.js 14 App Router · Tailwind · Radix · @dnd-kit · react-query · lucide
    ├── app/                  # login, home, pipeline, accounts, accounts/[id] (360), deals/[id], contacts, tasks, ask, settings
    ├── components/           # KanbanBoard, DealCard, QuickLogModal, ActivityTimeline, CopilotPanel, charts, indicators, ui/*
    └── lib/                  # axios client, types, query provider, utils
```

### Business logic (from the specification)

| Spec | Implementation |
|---|---|
| §5 Stage-gate machine | Discovery 10% → Pain Fit 25% → Solution Demo 50% → Proposal/InfoSec 75% → Closed-Won 100% / Closed-Lost 0%. Forward moves check each gate's entry criteria against logged activity. Unmet criteria return a checklist (HTTP 409), and the user can knowingly override, which is recorded in the audit trail. Closed-Lost always requires a `loss_reason`. Every transition writes `deal_stage_history` with the forecast delta. |
| §5 AI actions per stage | Discovery: pain-point extraction · Pain Fit: competitor scan · Solution Demo: recap email draft + action items · Proposal/InfoSec: stagnation check against the 14-day velocity benchmark · Closed-Won: onboarding event, health set to 100 · Closed-Lost: post-mortem written to vector memory. Runs as background jobs. |
| §6 Health | `H = 0.40·Recency + 0.35·Sentiment + 0.25·Velocity`. The breakdown is stored and shown on Account 360. |
| §6 Deal risk | `R = 30·Stale + 30·SentimentDrop + 40·NoChampion`. The factors drive the deal page's "Next best actions". |
| §7 Forecast | `Σ amount × probability × (1 − risk/200)` on the dashboard, Kanban columns and every deal card. |
| §8 Ambient Quick-Log | ⌘K modal: type to search, or paste notes to extract. You get an editable review (account, deal, people with buying roles, activity, action items, competitor and pain signals) before anything is written. |
| §9 Contracts | `QuickLogResponse`, `ContactExtracted`, `DealExtracted`, `ActionItem` validate every model response strictly before any database write. |
| §11 Endpoints | All spec routes, plus CRUD, `/ai/ask`, `/ai/briefing`, `/ai/deals/{id}/draft-email`, `/ai/accounts/{id}/brief`, `/search/global`, `/dashboard/summary`. Interactive OpenAPI docs are at `/docs`. |
| §12 Account 360 | `GET /api/v1/accounts/{id}/360` returns the spec payload shape, plus tasks, stage history and pipeline summary. |

### Intelligence layer

`LLM_PROVIDER` selects the model. **Any failure (unreachable, refusal, invalid JSON) falls back to the deterministic
engine**, so logging never breaks.

| Provider | Data stays | Notes |
|---|---|---|
| `ollama` | On your hardware | JSON-schema-constrained output (`format`), default `llama3.1:8b` |
| `aws_bedrock` | In your AWS account | Claude via the Bedrock Mantle endpoint (`anthropic.claude-opus-5`), structured outputs |
| `anthropic` | Anthropic API | Claude (`claude-opus-5`), structured outputs |
| `heuristic` | In-process | Deterministic extractor, keyword signals, templated drafts. No model needed. |

`EMBEDDING_PROVIDER` (`hash` | `ollama` | `aws_bedrock`) fills the 1536-dimension pgvector column behind semantic search
and the Copilot. Vectors from different providers aren't comparable, so re-embed if you switch.

### Roles

`super_admin`, `sales_manager`, `sales_rep` and `read_only`. Read-only users can browse but every write returns 403.
Only managers and admins can run the full re-score.

---

## Deviations from the specification (and why)

- **`contacts.email` is nullable (still unique).** Ambient notes often name people without an email. Requiring one
  would force placeholder data.
- **Extra tables:** `activities` (with the `vector(1536)` column and an HNSW index), `tasks`, and `deal_stage_history`.
  The spec's features (timeline, action items, audit records, semantic search) need them, but its DDL section is
  truncated before defining them.
- **Seed:** `python -m app.seed --minimal` produces exactly the spec's 3 accounts, 6 contacts and 3 active deals. The
  default seed adds 5 more accounts so the dashboard, risk and win/loss views have something to show. The pipeline has
  the spec's 4 open stage gates plus the two terminal stages.
- **Added to the spec's compose file:** the `ollama` service (the spec's comment lists it), a `worker` for Celery, and
  health checks.
- **The frontend uses Radix primitives styled in the shadcn/ui pattern**, written directly into `components/ui` instead
  of generated by the CLI.
