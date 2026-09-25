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

| Quotes & approvals (CPQ) | Customer success |
|---|---|
| ![Quote builder](docs/screenshots/quote-builder-light.png) | ![Onboarding](docs/screenshots/success-light.png) |
| **Finance & ERP (A/R aging, credit holds)** | **Partner deal registration** |
| ![Finance](docs/screenshots/finance-light.png) | ![Partners](docs/screenshots/partners-light.png) |
| **RBAC matrix** | **Partner portal** |
| ![RBAC](docs/screenshots/admin-rbac-light.png) | ![Portal](docs/screenshots/portal-light.png) |

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
docker compose --profile voice up -d                  # optional: private Whisper transcription for voice notes
```

Open http://localhost:3000. Every demo user's password is `relate123`:

| Login | Role | What they see |
|---|---|---|
| `admin@relate.demo` | Super Admin | Everything, including Admin (users, RBAC, audit, privacy, dedup, import/export, jobs) and finance approvals |
| `marcus@relate.demo` | Sales Manager | Whole pipeline, approves discounts, customer success, partners, reports |
| `priya@relate.demo`, `diego@relate.demo` | Account Executive | Only their own accounts, deals and quotes (row-level ownership) |
| `sam@relate.demo` | SDR | Own leads and contacts; no quotes, finance or deletes |
| `viewer@relate.demo` | Auditor | Read and export everything, including the immutable audit trail; no writes |
| `partner@northstar-partners.com` | Partner | External partner portal only: deal registration, commissions, collateral |

The stack runs Postgres 16 + pgvector, Redis 7, Ollama, the FastAPI API, a Celery worker with beat (risk scans, SLA
escalation, renewals, ERP sync, nightly re-score and de-duplication), optional Whisper, and the Next.js web app.
Migrations run automatically, and `SEED_DEMO_DATA=true` loads the demo workspace.

### Option C: Kubernetes (Helm)

```bash
helm install relate ./helm/relate -n relate --create-namespace \
  --set image.registry=registry.internal/sdc \
  --set ingress.host=crm.example.com --set publicWebUrl=https://crm.example.com
```

The chart deploys the API (with a migration init container), the worker, the web app, and optionally Postgres/pgvector,
Redis, Ollama and Whisper. Point `externalDatabase` at a managed Postgres to skip the bundled one. Secrets are generated
on first install and kept on upgrade and uninstall. A **zero-egress NetworkPolicy** is on by default: pods can reach only
each other and cluster DNS, plus any CIDRs you list in `networkPolicy.allowEgressCIDRs` (for example an on-prem ERP or
mail relay).

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

The backend suite (53 tests) covers the scoring formulas, the extractor and LLM fallback, and every pillar end to end:
RBAC and row-level scope, the append-only audit trail, crypto-shredding erasure, dedup and merge, hierarchy rollups,
all four pipelines' gates, CPQ pricing and two-level approvals, document generation and e-signature, renewals, email
and calendar ingest, SLA escalation, ERP sync and credit holds, partner registration conflicts and commissions, and
all-or-nothing imports.

---

## Enterprise capabilities (10 pillars)

| # | Pillar | What's built | Where |
|---|---|---|---|
| 1 | **Account 360 & entity graph** | Dossier with firmographics (legal name, tax ID, revenue, employees, locations) and typed JSONB custom fields defined in Admin. Parent/child hierarchies of any depth, with recursive rollups of pipeline, ARR and open tickets and cycle protection. Autonomous de-duplication: Jaro-Winkler + Levenshtein name scores plus registrable-domain match. Pairs are suggested at ≥ 0.88 and auto-merged at ≥ 0.97 (or same domain and ≥ 0.85), with field-level merge rules and a merge log that keeps a snapshot. **Health v2** = 0.30 recency + 0.25 sentiment drift + 0.15 velocity + 0.15 support-ticket load + 0.15 overdue milestones, explained on the account. | Account page tabs · Admin → Duplicates / Custom fields |
| 2 | **Contact & stakeholder intelligence** | Email, direct phone, mobile, LinkedIn, timezone and department. Buying roles (Champion, Economic Buyer, Blocker…). **Relationship Strength Index** = 0.4 reply latency + 0.3 inbound frequency + 0.3 meeting attendance. GDPR/CCPA consent per channel and opt-outs, enforced on send. An append-only consent ledger. **Right to erasure by crypto-shredding**: audit PII is encrypted with a per-contact AES-256-GCM key, and destroying the key leaves an erasure proof (salted hash, fields cleared). | Contact profile · Admin → Privacy & consent |
| 3 | **Opportunity & revenue engine** | Four pipelines with their own stages and probabilities: Enterprise Direct, Inbound Mid-Market, Renewals & Upsells and Partner Channel. Declarative stage-gate rules, editable as JSON (min contacts, role mapped, activity logged, approved amount, signed document…) with a logged manager override. A multi-currency weighted forecast converted to USD and risk-adjusted. Win/loss taxonomy (Competitor, Budget Frozen, Feature Gap, Champion Departed, Price, No Decision…) with a required rep debrief and competitor capture. | Pipeline tabs · Reports · Admin → Pipelines & gates |
| 4 | **CPQ / CLM** | Multi-currency price books with volume tiers, TCV/ACV and discount totals. Approval policies route by discount, payment terms, deal size and credit hold, to Sales Manager and then Finance. NDA, SOW and Order Form generated from sandboxed templates into PDF, with a content hash. **Embedded e-signature**: the customer signs through a public token link on a canvas signature pad, then the company countersigns. The countersigned PDF is attached to the timeline, and a completed Order Form creates the contract. | Deal → Quotes & Documents · Quotes · Approvals · Products · `/sign/[token]` |
| 5 | **Omnichannel activity ledger** | Emails, calls (duration, disposition), meetings (agenda, attendance), notes, stage changes and attachments on one timeline. IMAP/SMTP mailbox sync with encrypted credentials; this works with Microsoft 365, Google Workspace and Exchange through their IMAP/SMTP endpoints. `.eml` drop-in. **Task & SLA engine**: delegation, priority, dependencies with cycle checks, and automatic escalation to the manager and then admins. A personal **iCal feed** that any CalDAV or calendar client subscribes to, plus `.ics` import that logs meetings. | Deal/Account timelines · Log activity · Tasks · Settings |
| 6 | **Ambient AI** | Quick-Log (⌘K) from text **or voice** (local Whisper, audio never stored). **Risk & slippage copilot**: > 14 days stagnant, close-date pushbacks, sentiment drift and champion loss raise alerts. **Hybrid RAG**: Postgres full-text and pgvector results merged with reciprocal-rank fusion over activities and accounts. **Next-best-action generator**: cadence, missing buying roles and suggested messaging you can copy. | ⌘K · Home alerts · Deal page · Ask relate · Copilot |
| 7 | **Post-sale** | Closed-Won provisions an onboarding workspace with milestones and the full pre-sales hand-off (priorities, products, stakeholders, competitors). **Churn early warning** from adoption and utilisation trends, critical/high ticket load and champion turnover. **Renewal opportunities are created 90–120 days before expiry**, carrying the original contract terms. | Customer success · Account → Success |
| 8 | **ERP fabric** | Customer master sync (legal name, tax ID, billing address, credit limit) through `demo`, `file` (CSV drop folder) or `rest` connectors. Invoice-level **A/R aging**; **credit holds** set from 90+ day balances or over-limit exposure block quotes behind finance approval. An outbound event outbox (`quote.approved`, `deal.closed_won`, `contract.created`, `invoice.overdue`…) feeds **promo, Yield, deduct and nexora** through a cursor feed or optional webhooks. | Finance & ERP · Account → Contracts & finance |
| 9 | **PRM** | Partner **deal-registration portal**, where registrations are checked for existing accounts and overlapping registrations. Approval grants **90-day territory exclusivity** and creates the deal in the Partner pipeline. **Co-sell and commission attribution** by partner split and rate. **Collateral repository** gated by partner tier and email domain, with every download logged. | Partners · Partner portal (`/portal`) · Deal → Partners |
| 10 | **Platform** | **RBAC** with CRUD + Export per resource for Super Admin, Sales Manager, Account Executive, SDR, Auditor and Partner, editable in the UI. **Row-level ownership**: out-of-scope records return 404. **Immutable audit trail** (user_id, record_id, field_name, old_value, new_value, timestamp), made append-only by a database trigger that rejects UPDATE, DELETE and TRUNCATE. CSV/JSON **import with auto-mapping, validation and all-or-nothing rollback**; scoped, audited **export**. docker-compose and **Helm with a zero-egress NetworkPolicy**. | Admin · `helm/relate` · `docker-compose.yml` |

---

## What's inside

```
├── docker-compose.yml        # db (pgvector), redis, ollama, api, worker, web (+ whisper "voice" profile)
├── helm/relate/              # Kubernetes chart: api, worker, web, optional pgvector/redis/ollama/whisper, zero-egress policy
├── .env.example              # every setting, documented
├── backend/                  # FastAPI · async SQLAlchemy 2.0 · asyncpg · Pydantic v2 · Alembic · Celery
│   ├── alembic/versions/001_initial_schema.py   # spec §4 DDL + activities/tasks/audit/pgvector
│   ├── alembic/versions/002_enterprise_pillars.py  # RBAC, audit triggers, privacy, CPQ/CLM, success, ERP, PRM
│   └── app/
│       ├── main.py           # app entrypoint + CORS
│       ├── core/             # config, DB sessions, JWT/bcrypt, RBAC + row scope (rbac.py), field-level audit (audit.py)
│       ├── models/           # SQLAlchemy models (mirror the migration)
│       ├── schemas/          # Pydantic v2 contracts (ai.py = spec §9)
│       ├── services/
│       │   ├── ai_extractor.py      # spec §8 system prompt + deterministic extractor
│       │   ├── llm.py               # Ollama / Claude on Bedrock / Claude API gateway with fallback
│       │   ├── embeddings.py        # 1536-d embeddings (hash / Ollama / Titan)
│       │   ├── scoring.py           # spec §6 health & risk engine
│       │   ├── pipeline_service.py  # spec §5 stage gates, §7 forecasting, Kanban
│       │   ├── insights.py          # stage-trigger AI actions, briefing, Copilot, drafts
│       │   ├── dedup.py · hierarchy.py · custom_fields.py · privacy.py   # pillars 1–2
│       │   ├── cpq.py · clm.py · fx.py · pipeline_templates.py          # pillars 3–4
│       │   ├── mail.py · calendar.py · sla.py · voice.py · search.py    # pillars 5–6
│       │   ├── success.py · erp.py · prm.py                             # pillars 7–9
│       │   ├── data_io.py · storage.py · notify.py                      # pillar 10
│       │   └── jobs.py              # Celery or in-process background jobs
│       ├── api/v1/           # accounts, contacts, deals, activities, ai, cpq, success, finance, partners (+portal), admin
│       ├── worker.py         # Celery worker + beat schedule
│       └── seed.py
└── frontend/                 # Next.js 14 App Router · Tailwind · Radix · @dnd-kit · react-query · lucide
    ├── app/                  # home, pipeline, accounts, contacts, tasks, ask, quotes, approvals, products, reports,
    │                         # success, finance, partners, admin, settings, portal (partners), sign/[token] (public)
    ├── components/           # KanbanBoard, DealCard, QuickLogModal, ActivityTimeline, CopilotPanel, charts, indicators, ui/*
    └── lib/                  # axios client, types, query provider, utils
```

### Business logic (from the specification)

| Spec | Implementation |
|---|---|
| §5 Stage-gate machine | Discovery 10% → Pain Fit 25% → Solution Demo 50% → Proposal/InfoSec 75% → Closed-Won 100% / Closed-Lost 0%. Forward moves check each gate's entry criteria against logged activity. Unmet criteria return a checklist (HTTP 409), and the user can knowingly override, which is recorded in the audit trail. Closed-Lost always requires a `loss_reason`. Every transition writes `deal_stage_history` with the forecast delta. |
| §5 AI actions per stage | Discovery: pain-point extraction · Pain Fit: competitor scan · Solution Demo: recap email draft + action items · Proposal/InfoSec: stagnation check against the 14-day velocity benchmark · Closed-Won: onboarding event, health set to 100 · Closed-Lost: post-mortem written to vector memory. Runs as background jobs. |
| §6 Health | The spec's `0.40·Recency + 0.35·Sentiment + 0.25·Velocity`, extended in v2 to `0.30·R + 0.25·S + 0.15·V + 0.15·Support + 0.15·Milestones` (pillar 1). The breakdown is stored and shown on Account 360. |
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

`super_admin`, `sales_manager`, `account_executive`, `sdr`, `auditor` and `partner`. Each role has Create / Read /
Update / Delete / Export per resource, with an `all` or `own` row scope. The defaults live in `core/rbac.py`, and
overrides are stored in `role_permissions` and edited in Admin → Roles & permissions. Super Admin cannot remove its own
admin access (lock-out protection).

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
- **Added to the spec's compose file:** the `ollama` service (the spec's comment lists it), a `worker` for Celery, an
  optional `whisper` service, a shared file volume, and health checks.
- **Roles were extended** from the spec's four to the six the enterprise pillars need. Migration 002 maps
  `sales_rep` → `account_executive` and `read_only` → `auditor`.
- **Microsoft Graph / Gmail API sync is done over IMAP/SMTP** (both providers expose it). This keeps the deployment
  free of cloud OAuth apps and outbound calls; a Graph connector can be added behind the same `mail.py` interface.
- **The frontend uses Radix primitives styled in the shadcn/ui pattern**, written directly into `components/ui` instead
  of generated by the CLI.
