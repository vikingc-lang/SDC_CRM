<p>
  <img src="docs/brand/cirra-logo.svg" alt="Cirra" height="56">
</p>

# Cirra: Connect what matters.

[![CI](https://github.com/vikingc-lang/SDC_CRM/actions/workflows/ci.yml/badge.svg)](https://github.com/vikingc-lang/SDC_CRM/actions/workflows/ci.yml)

**A CRM built around relationships, not just records.** Cirra is the AI CRM in the SDC Solutions portfolio of
distinguished products (alongside promo [Q], Yield [S], deduct [✔] and nexora [§]). It brings every account, contact, deal
and conversation into one place, and uses AI to surface the customers, signals and next steps that move revenue.

**The problem.** Reps log activity late or never. The pipeline looks healthy until a deal goes quiet, and managers find out
on the forecast call, when it's too late to act.

**Why it's different.** The CRM does the data entry and flags risk while there's still time to act:

- **Aiden's briefing every morning:** overdue follow-ups, deals at risk and deals closing soon, in one view.
- **Quick-Log from meeting notes (⌘K):** paste notes, dictate or log a conversation, and Cirra files it against the right
  account and deal. You review the extraction (people and buying roles, deal value and timeline, next steps, sentiment)
  and commit it in one click.
- **Deal risk you can see:** every deal gets a risk score with the reason: stale, no champion, sentiment drop.
- **Aiden, your AI assistant (⌘J):** Aiden answers questions about accounts, deals and pipeline in plain English, grounded in
  hybrid keyword + semantic search (pgvector).
- **A forecast you can explain:** weighted by stage probability and deal risk, shown by stage and by close month.
- **Account 360 and stage gates:** the full picture of every account, and clear criteria for moving a deal forward.

**Partner fit.** Suits partners serving mid-market clients who need a CRM that runs cleanly next to SAP (customer master,
A/R aging and credit holds sync through the ERP fabric).

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

**No setup needed to look around:** open [`docs/cirra-ui-preview.html`](docs/cirra-ui-preview.html) in any browser. It is a single
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

Open http://localhost:3000. Every demo user's password is `cirra123`:

| Login | Role | What they see |
|---|---|---|
| `admin@cirra.demo` | Super Admin | Everything, including Admin (users, RBAC, audit, privacy, dedup, import/export, jobs) and finance approvals |
| `marcus@cirra.demo` | Sales Manager | Whole pipeline, approves discounts, customer success, partners, reports |
| `priya@cirra.demo`, `diego@cirra.demo` | Account Executive | Only their own accounts, deals and quotes (row-level ownership) |
| `sam@cirra.demo` | SDR | Own leads and contacts; no quotes, finance or deletes |
| `sofia@cirra.demo` | Support Agent | Every case and the knowledge base; read-only accounts; lands on Cases |
| `nina@cirra.demo` | Marketing | Campaigns, every lead, list building and campaign email; read-only customers and pipeline |
| `viewer@cirra.demo` | Auditor | Read and export everything, including the immutable audit trail; no writes |
| `partner@northstar-partners.com` | Partner | External partner portal only: deal registration, commissions, collateral |

The stack runs Postgres 16 + pgvector, Redis 7, Ollama, the FastAPI API, Celery workers for background jobs, a separate
**scheduler** (Celery beat, exactly one) that triggers the scheduled ones (risk scans, SLA escalation, renewals, ERP sync,
nightly re-score and de-duplication, scheduled workflows, case SLA scans and routing, territory realignment, webhook
delivery, report subscriptions, nurture journeys and the support mailbox), optional Whisper, and the Next.js web app.
Workers can be scaled (`docker compose up -d --scale worker=3` after removing `container_name`) without doubling scheduled
jobs.
Migrations run automatically, and `SEED_DEMO_DATA=true` loads the demo workspace. For a larger dataset across every object and
transaction type (all lead sources and statuses, every pipeline stage, won/lost with each loss reason, quotes through the approval
chain, documents in every signing state, orders, contracts and renewals, cases with SLA outcomes and CSAT, campaigns, partner
registrations, invoices across aging buckets, forecasts, quotas, webhooks and more), run
`docker compose exec api python -m app.demo_volume` (add `--scale 3` for more, or `--batch v2` to add another batch).

### Option C: Kubernetes (Helm)

```bash
helm install cirra ./helm/cirra -n cirra --create-namespace \
  --set image.registry=registry.internal/sdc \
  --set ingress.host=crm.example.com --set publicWebUrl=https://crm.example.com
```

The chart deploys the API (with a migration init container), scalable workers, a single-replica scheduler, the web app,
and optionally Postgres/pgvector,
Redis, Ollama and Whisper. Point `externalDatabase` at a managed Postgres to skip the bundled one. Secrets are generated
on first install and kept on upgrade and uninstall. A **zero-egress NetworkPolicy** is on by default: pods can reach only
each other and cluster DNS, plus any CIDRs you list in `networkPolicy.allowEgressCIDRs` (for example an on-prem ERP or
mail relay).

### Operations

- **Health checks:** `/health/live` (the process answers; used for liveness, so a database blip never restarts pods) and
  `/health/ready` (database reachable and migrated to this build's schema, Redis reachable; 503 otherwise). `/health`
  remains for simple uptime checks.
- **Rate limits** (per minute, shared across API replicas through Redis): 600 per signed-in user, 300 per API key, 120
  per IP for anonymous API calls and for public endpoints (forms, e-signature, CSAT, unsubscribe, inbound email), 60
  sign-in attempts per IP (on top of the per-account lockout), 1,200 tracking hits per IP. Over the limit: HTTP 429 with
  `Retry-After`; every response carries `X-RateLimit-Limit` and `X-RateLimit-Remaining`. Tune with `RATE_LIMIT_*`. If
  Redis is down the limiter lets requests through rather than taking the CRM down.
- **Tracing and logs:** every response carries an `X-Request-ID` (yours if you send one), written on a one-line access
  log with status and duration. `LOG_FORMAT=json` switches the whole API to one JSON object per line for log shippers.
- **Response hardening:** `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and a deny-all CSP on API
  responses (the API only serves JSON; the interactive docs are exempt).
- **CI** (`.github/workflows/ci.yml`) on every push and pull request: the backend suite against real Postgres/pgvector
  and Redis, the frontend typecheck, lint and production build, then both Docker images and a Helm lint and render.

### Option B: local development

Prerequisites: Python 3.11, Node 20+, PostgreSQL 16 with the `vector` extension, and optionally Redis.

```bash
# API
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql+asyncpg://cirra_user:cirra_secure_password@localhost:5432/cirra_crm
alembic upgrade head
python -m app.seed                   # --minimal = spec minimum (3 accounts, 6 contacts, 3 deals); --reset wipes first
uvicorn app.main:app --reload        # http://localhost:8000/docs

# Web
cd ../frontend
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev   # http://localhost:3000
```

With no model configured (`LLM_PROVIDER=heuristic`, the default outside Docker), every AI feature runs on Cirra's
deterministic engine, so the product works fully offline.

### Tests

```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://cirra_user:cirra_secure_password@localhost:5432/cirra_test pytest
cd frontend && npm run typecheck && npm run lint && npm run build
```

Browser end-to-end test of the lead-to-order flow (62 checks across 12 journeys: web form → lead → conversion → stage
gates → CPQ and approvals → redlines and e-signature → Closed-Won → order → ERP, plus admin, role access, an error sweep
of every screen for five roles, and mobile layout). Run it against freshly seeded demo data with the API and web app up:

```bash
npm i playwright && node e2e/lead-to-order.mjs ./e2e-output   # results.json + 28 evidence screenshots in e2e-output/evidence
node e2e/wave1-journeys.mjs ./w1   # drill-down, pivot, dashboard filters, bulk actions (10 checks)
node e2e/wave2-journeys.mjs ./w2   # list views, in-place edits, period comparison, report subscriptions (10 checks)
node e2e/wave3-journeys.mjs ./w3   # custom objects, field security, validation and sharing rules, config transfer (9 checks)
node e2e/wave4-journeys.mjs ./w4   # email-to-case, presence routing, support inbox, nurture journeys and tracking (10 checks; needs INBOUND_EMAIL_SECRET)
```

The backend suite (176 tests) covers the scoring formulas, the extractor and LLM fallback, and every pillar end to end:
RBAC and row-level scope, the append-only audit trail, crypto-shredding erasure, dedup and merge, hierarchy rollups,
all pipelines' gates, CPQ pricing, price books, promotions, bundles and the sequential approval chain, document generation,
redlining and e-signature (built-in and provider webhooks), lead capture, scoring, routing and conversion, order generation
and the ERP sales-order hand-off, renewals, email
and calendar ingest, SLA escalation, ERP sync and credit holds, partner registration conflicts and commissions, SSO and sign-in policy, the report builder and dashboards, workflow
rules, forecast calls, case SLAs, routing and CSAT, territories, quotas and tiered commission, campaign attribution, list
building, consent-checked email and unsubscribe, API keys (scope, read-only, revocation), signed webhooks with retries, and
all-or-nothing imports, saved list views and in-place edits, previous-period comparison, and scheduled report delivery
(schedule slots and catch-up, per-recipient access, mail failures recorded without the server's reply), and the platform
layer: validation rules (people vs system, create/edit scope, broken rules skipped), field-level security across record pages,
API, reports and search, account sharing rules, custom objects end to end, and all-or-nothing configuration import;
email-to-case (threading, reopen and follow-up, domain matching, loop and burst protection, emailed replies), presence routing
(waiting, priority order, capacity), signed click tracking, and journeys branching on opens and clicks with exits;
quick-log scope and permissions, rate limits per identity and IP (with fail-open), request tracing, security headers and
health checks.

---

## Enterprise capabilities (10 pillars)

| # | Pillar | What's built | Where |
|---|---|---|---|
| 1 | **Account 360 & entity graph** | Dossier with firmographics (legal name, tax ID, revenue, employees, locations) and typed JSONB custom fields defined in Admin. Parent/child hierarchies of any depth, with recursive rollups of pipeline, ARR and open tickets and cycle protection. Autonomous de-duplication: Jaro-Winkler + Levenshtein name scores plus registrable-domain match. Pairs are suggested at ≥ 0.88 and auto-merged at ≥ 0.97 (or same domain and ≥ 0.85), with field-level merge rules and a merge log that keeps a snapshot. **Health v2** = 0.30 recency + 0.25 sentiment drift + 0.15 velocity + 0.15 support-ticket load + 0.15 overdue milestones, explained on the account. | Account page tabs · Admin → Duplicates / Custom fields |
| 2 | **Contact & stakeholder intelligence** | Email, direct phone, mobile, LinkedIn, timezone and department. Buying roles (Champion, Economic Buyer, Blocker…). **Relationship Strength Index** = 0.4 reply latency + 0.3 inbound frequency + 0.3 meeting attendance. GDPR/CCPA consent per channel and opt-outs, enforced on send. An append-only consent ledger. **Right to erasure by crypto-shredding**: audit PII is encrypted with a per-contact AES-256-GCM key, and destroying the key leaves an erasure proof (salted hash, fields cleared). | Contact profile · Admin → Privacy & consent |
| 3a | **Self-service analytics** | **Report builder** over eight data sources (opportunities, accounts, contacts, leads, activities, tasks, quotes, orders): list or summary reports with up to two groupings (dates bucketed by week, month, quarter or year), count/total/average/min/max, typed filters with relative periods ("this quarter", "last 90 days") and live preview as a bar, column, line, stacked bar, headline number or table. Definitions are compiled from a whitelisted field catalogue, never SQL. **Dashboards** arrange saved reports in thirds, halves or full width. Sales Managers and Super Admins share reports and dashboards with the team, and **every viewer sees only the records their role can access**. CSV export is audited. A shared *Sales overview* dashboard ships with nine starter reports. **Custom fields are reportable** (filter, group, sum, chart) as soon as an admin defines them. **Drill-down**: click any bar, point, stacked segment, matrix cell or summary row to see the records behind it, each linked to its record. **Matrix (pivot)** view with row and column totals. **Dashboard filters** (period and owner) re-run every tile at once, and a tile says when a filter doesn't apply to its data. **Period-over-period**: tick *Compare with the previous period* on a summary with an "in period" date filter and headline numbers show the change (▲ 12% vs last quarter), tables gain *previous* and *change* columns, and a dashboard's period drives the comparison. **Scheduled delivery**: subscribe to any saved report daily, weekly or monthly at a chosen hour (UTC), optionally for colleagues on a shared report; everyone receives the report run with *their own* access, as a notification and, when system email (`SMTP_*`) is configured, an email with the data as CSV. *Send now* sends a check copy to yourself only. | Reports → Dashboards / Saved reports · `/reports/builder` |
| 3a+ | **Saved list views** | On Leads, Accounts, Contacts and Cases, pick **Standard list** or a saved view: your own columns (any report field, custom fields included, up to 12, reorderable), filters and sort, kept private or shared with the team by managers. Click a header to re-sort. **Edit in place**: owner, status, priority and tier change straight from the grid (permission- and scope-checked, with the same side effects as a normal edit, e.g. case SLAs re-timed). Row selection and bulk actions work in views too. The last view you used is remembered per list. | Leads / Accounts / Contacts / Service → *List view* |
| 3g | **Platform flexibility** | **Custom objects**: admins define new record types (e.g. *Site survey*, *Installed asset*) with typed fields, optionally linked to an account. Each gets a list page with saved views, a record page, related records on the account, reports and dashboards, validation rules and REST access (`/api/v1/objects/{key}/records`); access follows the *Custom objects* permission and account scope. **Validation rules**: *block saving when…* conditions (report-builder filters) with your own message, on accounts, contacts, opportunities, leads, cases and custom objects; enforced centrally at save time for every screen, import and API call made by a person (automation and background jobs are exempt), with a *check existing records* preview; a rule whose field is later deleted is skipped, never blocking. **Field-level security**: each custom field is editable, read-only or hidden per role; hidden values are left out of record pages, the API, search, reports, list views, exports and subscriptions, and are kept when others save. **Account sharing rules**: accounts matching criteria (e.g. region is EMEA) become visible, with everything on them, to chosen own-scope roles, with a match preview. **Configuration transfer**: export objects, fields and their security, validation and sharing rules, workflows, role permissions and team-shared reports, dashboards and list views as JSON; import previews what would be created, changed or left alone and applies all or nothing (Super Admin only). | Admin → Custom objects / Custom fields / Validation rules / Sharing rules / Configuration · sidebar → *Custom objects* |
| 3b | **No-code workflows** | *When / If / Then* rules over opportunities, leads, accounts, contacts, activities, tasks, quotes and orders. **Triggers**: a record is created, chosen fields change (stage, owner, amount, status…), or an hourly check that acts once per matching record (or again after N days). **Conditions** use the report builder's typed filters. **Actions**: create a task (for the owner, their manager or a named user), notify people or whole roles, update a field (reassign owner, set tier or priority), or emit an event to the integration outbox. `{{field}}` placeholders fill in record values. **Call a webhook** (the record as JSON to any https endpoint) and **post to Slack or Microsoft Teams** (templated message to an incoming webhook); these go out after the change commits, retry once, never target loopback or cloud-metadata addresses, and log only the status. Conditions and `{{placeholders}}` can use custom fields. Rules run only after the triggering change commits, never re-trigger themselves, and stop cascading after three levels. A **test run** previews matches and outcomes without changing anything, and every run is logged. Three templates included. | Admin → Workflows |
| 3c | **Forecast calls** | Every stage maps to a **forecast category** (Pipeline, Best Case, Commit; won is Closed, lost is Omitted), set per stage in Admin and overridable per deal by its owner. Each quarter shows Closed, the **Commit call** (closed + commit) and the **Best case call** (+ best case) from the rep's deals. Reps **submit** their call with a note; submissions keep a snapshot of what the CRM showed. Managers see a **team roll-up**, **adjust** any rep's number with a reason, and submit the **team call**; the final number is the adjustment, else the submission, else the calculated figure. The effective category is available to reports and workflows. | Reports → Forecast call · Admin → Stages |
| 3d | **Customer service** | **Cases** numbered `CS-00001` onward, on an account and optionally a contact, with a channel (email, phone, web, portal, chat) and category. New cases go to a **queue** and are **auto-assigned** to its least-loaded active member. **SLA clocks** per priority (Critical 1 h response / 8 h resolve, High 4 h / 24 h, Medium 8 h / 72 h, Low 24 h / 120 h, editable in Admin): the first public reply stops the response clock and resolving stops the other; changing priority re-times both. A scan every 10 minutes flags breaches once and alerts the owner, their manager (or the queue). Conversations mix **public replies** and **internal notes**. Resolving issues a **CSAT** link (1–5 rating and comment). A **knowledge base** with full-text search suggests published articles on each case. New **Support Agent** role. | Cases · Knowledge · Admin → Service · `/csat/[token]` |
| 3d+ | **Email-to-case and omnichannel routing** | **Email-to-case**: mail to a queue's support address (via the inbound webhook `POST /api/v1/inbound/email` with `INBOUND_EMAIL_SECRET`, or a support mailbox polled over IMAP) opens a case on the sender's account: the matching contact, else the account whose domain matches (free-mail domains never match). Replies thread back onto their case by the `[CS-00012]` tag or the email's In-Reply-To/References; a customer reply reopens a pending or resolved case, and mail to a closed case opens a follow-up. Agents' public replies are emailed with threading headers, and new cases are acknowledged, once system email is configured. Unmatched mail waits in the **Email inbox** to be filed or dismissed; auto-replies, mail from Cirra's own addresses and bursts from one sender are ignored. **Presence routing** (per queue): agents set Available / Busy / Away / Offline and a capacity; new cases go only to available agents under capacity (fewest open cases, then longest idle), otherwise they wait and are pushed, by priority then age, as soon as an agent becomes available or frees capacity. A **routing console** shows each agent's status and load and what waits in every queue. | Service → Routing / Email inbox · Admin → Service (support address, routing) |
| 3e | **Territories, quotas & incentives** | **Territories** match accounts by region, country, industry, tier and employee range, checked in priority order (a territory with no rules is a catch-all); new accounts are placed on creation and a nightly realignment (or a previewed one in Admin) keeps them current without changing owners. **Quotas** per seller per quarter, set by managers for their team. **Attainment** is closed-won bookings in USD, shown with the commit-call projection and pipeline coverage of the remaining gap. **Commission plans** pay a base rate to quota and accelerated rates above tier thresholds, assigned by role or person; each rep gets a per-deal **commission statement**, and managers get a team **leaderboard**. | Quotas & commission · Admin → Territories & incentives · Account header |
| 3f+ | **Nurture journeys and email tracking** | Campaign and journey emails are **tracked**: an open pixel and signed click-through links (never an open redirect; the unsubscribe link stays direct). First opens and clicks become lead engagement (`email_open` / `email_click` scoring), and a click marks the member as responded. Campaigns show sent, open rate, click rate and click-to-open. **Journeys**: sequences of emails and waits for a campaign's members, where an email can go to everyone or only to people who opened, didn't open, clicked or didn't click the previous one. Activating enrolls the members (later members join automatically); people leave when they unsubscribe, bounce, lose consent, or their lead converts or is disqualified, and the reason is shown. Per-step sent / open / click results, test sends to yourself, pause to edit, archive. | Campaigns → a campaign → Nurture journeys |
| 3f | **Marketing campaigns** | Campaigns with type, dates, budget, actual cost and expected revenue. **Members** are leads or contacts, added from any report-builder filter or automatically when a lead is captured with the campaign's code or name as its campaign / utm_campaign. Responses (responded, registered, attended) log engagement on the lead and feed its score. **Campaign email** with personalisation goes only to people who allow it (opt-outs, GDPR basis), once each, with a personal **unsubscribe** link that opts them out everywhere. **Attribution**: sourced pipeline from member leads' deals, influenced revenue from member contacts' accounts, cost per lead and **ROI**. A Marketing role and a Campaigns report source. | Campaigns · `/unsubscribe/[token]` |
| 3 | **Opportunity & revenue engine** | Four pipelines with their own stages and probabilities: Enterprise Direct, Inbound Mid-Market, Renewals & Upsells and Partner Channel. Declarative stage-gate rules, editable as JSON (min contacts, role mapped, activity logged, approved amount, signed document…) with a logged manager override. A multi-currency weighted forecast converted to USD and risk-adjusted. Win/loss taxonomy (Competitor, Budget Frozen, Feature Gap, Champion Departed, Price, No Decision…) with a required rep debrief and competitor capture. | Pipeline tabs · Reports · Admin → Pipelines & gates |
| 4 | **CPQ / CLM** | Multi-currency price books with volume tiers, TCV/ACV and discount totals. Approval policies route by discount, payment terms, deal size and credit hold, to Sales Manager and then Finance. NDA, SOW and Order Form generated from sandboxed templates into PDF, with a content hash. **Embedded e-signature**: the customer signs through a public token link on a canvas signature pad, then the company countersigns. The countersigned PDF is attached to the timeline, and a completed Order Form creates the contract. | Deal → Quotes & Documents · Quotes · Approvals · Products · `/sign/[token]` |
| 5 | **Omnichannel activity ledger** | Emails, calls (duration, disposition), meetings (agenda, attendance), notes, stage changes and attachments on one timeline. IMAP/SMTP mailbox sync with encrypted credentials; this works with Microsoft 365, Google Workspace and Exchange through their IMAP/SMTP endpoints. `.eml` drop-in. **Task & SLA engine**: delegation, priority, dependencies with cycle checks, and automatic escalation to the manager and then admins. A personal **iCal feed** that any CalDAV or calendar client subscribes to, plus `.ics` import that logs meetings. | Deal/Account timelines · Log activity · Tasks · Settings |
| 6 | **Ambient AI** | Quick-Log (⌘K) from text **or voice** (local Whisper, audio never stored). **Aiden's risk & slippage alerts**: > 14 days stagnant, close-date pushbacks, sentiment drift and champion loss raise alerts. **Hybrid RAG**: Postgres full-text and pgvector results merged with reciprocal-rank fusion over activities and accounts. **Next-best-action generator**: cadence, missing buying roles and suggested messaging you can copy. | ⌘K · Home alerts · Deal page · Ask Aiden · Aiden panel (⌘J) |
| 7 | **Post-sale** | Closed-Won provisions an onboarding workspace with milestones and the full pre-sales hand-off (priorities, products, stakeholders, competitors). **Churn early warning** from adoption and utilisation trends, critical/high ticket load and champion turnover. **Renewal opportunities are created 90–120 days before expiry**, carrying the original contract terms. | Customer success · Account → Success |
| 8 | **ERP fabric** | Customer master sync (legal name, tax ID, billing address, credit limit) through `demo`, `file` (JSON drop folder) or `rest` connectors. Invoice-level **A/R aging**; **credit holds** set from 90+ day balances or over-limit exposure block quotes behind finance approval. An outbound event outbox (`quote.approved`, `deal.closed_won`, `contract.created`, `invoice.overdue`…) feeds **promo, Yield, deduct and nexora** through a cursor feed or optional webhooks. | Finance & ERP · Account → Contracts & finance |
| 9 | **PRM** | Partner **deal-registration portal**, where registrations are checked for existing accounts and overlapping registrations. Approval grants **90-day territory exclusivity** and creates the deal in the Partner pipeline. **Co-sell and commission attribution** by partner split and rate. **Collateral repository** gated by partner tier and email domain, with every download logged. | Partners · Partner portal (`/portal`) · Deal → Partners |
| 10 | **Platform** | **Sign-in security**: two-factor authentication with any authenticator app (TOTP, replay-protected) and single-use recovery codes, required per role by policy; admins reset a lost device, which signs the user out everywhere. **Single sign-on** over OpenID Connect (authorization code + PKCE, ID token verified against the IdP's keys) with Microsoft Entra ID, Okta, Google Workspace, Keycloak or ADFS, optional just-in-time provisioning and domain allow-list, and an enforce mode that keeps password sign-in only for Super Admins as break-glass. **RBAC** with CRUD + Export per resource for Super Admin, Sales Manager, Account Executive, SDR, Auditor and Partner, editable in the UI. **Row-level ownership**: out-of-scope records return 404. **Immutable audit trail** (user_id, record_id, field_name, old_value, new_value, timestamp), made append-only by a database trigger that rejects UPDATE, DELETE and TRUNCATE. **Bulk actions** on the Leads, Accounts, Contacts and Cases lists (reassign, status, priority, tier, queue, add to campaign) change only records the user may edit and report what was skipped. CSV/JSON **import with auto-mapping, validation and all-or-nothing rollback**; scoped, audited **export**. docker-compose and **Helm with a zero-egress NetworkPolicy**. | Admin → Sign-in security · Settings · Admin · `helm/cirra` · `docker-compose.yml` |
| 10b | **Developer platform** | **API keys** that act as a chosen user (so role permissions and row-level scope apply), optionally read-only or expiring, stored only as a hash, revocable, and never able to manage keys or sign-in security. **Webhooks**: admins subscribe endpoints to events by name or pattern (`deal.*`, `case.created`, `*`), including custom workflow events. Deliveries are signed (`X-Cirra-Signature`, HMAC-SHA256 of timestamp and body), retried after 1 min, 5 min, 30 min, 2 h and 12 h, logged with response codes and timings, retryable by hand, and a subscription that fails 20 times in a row is switched off. Core records now publish `account.created`, `contact.created`, `deal.created`, `deal.stage_changed`, `case.created`, `case.resolved`, `campaign.launched` and `campaign.member_responded`. **Upsert by external id** (`PUT /api/v1/upsert/{accounts|contacts|deals|leads}/{external_id}`, or up to 500 per call with a per-row result): outside systems keep records in sync by their own ids, referencing related records by *their* ids or natural keys, with the same scope, validation and events as the UI; records already linked to another system's id are never silently re-keyed, and stage moves stay behind stage gates. Interactive API reference at `/docs`. | Admin → API & webhooks |

---

## Lead-to-order: the end-to-end flow

Cirra runs the full commercial process, from first touch to an ERP sales order. Every step is in the product and
covered by `tests/test_leads.py`, `tests/test_deal_desk.py` and `tests/test_orders.py`. The seed walks one deal
(**Harborline Freight**) through the whole flow.

| # | Step | What Cirra does | Where |
|---|---|---|---|
| 1 | **Lead capture & hygiene** | Hosted web forms (`/forms/<key>`), HTML form posts, marketing-automation webhooks (engagement events), manual entry and import, authenticated by hashed intake keys and rate limited, with a spam honeypot. Input is normalised. Duplicates are checked against open leads (merged), contacts and accounts (flagged, and linked on conversion). Enrichment runs through the `internal` provider (existing accounts and peer leads) or `rest`. Region comes from country, the privacy regime from region, and consent is recorded with its source and timestamp. | Leads · Admin → Lead management → Web forms & webhooks |
| 2 | **Qualification & scoring** | **Fit** against the ICP (industry 30, size 25, revenue 20, geography 15, seniority 10), plus **engagement** points that halve every 30 days. Score = 50/50 blend (configurable); at 60 or above the lead becomes an **MQL**. BANT or MEDDPICC with evidence per criterion; reaching the minimum (3/4 or 5/8) makes it an **SQL**. Priority-ordered **assignment rules** use round robin, a named owner or the existing account owner, with a round-robin fallback across SDRs. A nightly job applies the decay. | Lead page · Admin → Lead management |
| 3 | **Conversion** | One step creates or links the **Account**, **Contact** (role, consent and engagement carried over) and **Opportunity** in the chosen pipeline and owner. The qualification gate can be overridden only by a manager. | Lead → Convert |
| 4 | **Opportunity management** | The **Enterprise Solution Sale** pipeline runs Discovery → Solution Design / Demo → Technical Evaluation / PoC → Business Case Validation → Negotiation & Legal → Closed-Won, with evidence gates at each stage. The buying committee includes **Legal Counsel** and **Procurement**. Admins can add, rename, reorder and remove stages. | Deal page · Admin → Stages / Stage gates |
| 5 | **CPQ & deal desk** | Price books resolve **customer → regional → list** price. **Promotions** are pre-approved discounts. **Bundles** expand into included lines, and **requires/excludes** rules are enforced. Billing frequency, non-standard terms and one **primary quote** per deal. A **sequential approval chain** runs Sales Manager → Deal Desk → VP Sales → Finance → Legal, driven by policies and approval groups. **Proposal/SOW, MSA, SLA and DPA** templates. | Quote builder · Approvals · Products → Price books / Promotions · Admin → Approval chain |
| 6 | **Negotiation & e-signature** | Numbered document **versions** with line-level **redlines**. Clause **comments** from both sides; customers comment from their signing link, which pauses signing. A new version voids outstanding signatures, and open comments block sending. A **credit & risk check** runs before an Order Form goes out: credit hold or a high risk score needs Finance approval. Signing uses the built-in e-sign, **DocuSign** or **Adobe Sign**, with a completion webhook. | Document page · `/sign/<token>` |
| 7 | **Closed-Won → Order → ERP** | Closed-Won validation requires a signed Order Form, an approved primary quote, a PO number, bill-to and ship-to addresses, and a tax-exemption certificate when the customer is exempt. Closing **locks the primary quote** and raises the **order**: a header plus lines with **billing schedules**. The `erp_orders` job pushes it **asynchronously** as a sales order (demo, file drop or REST), retries up to 5 times, reads acknowledgements, and emits `order.created` / `order.acknowledged`. | Deal → Order readiness · Orders |

Demo intake keys are printed by the seed: the hosted form is `/forms/cf_demo_webform_cirra`, and the webhook key is
`ck_demo_webhook_cirra`.

---

## What's inside

```
├── .github/workflows/ci.yml  # tests, frontend checks, image builds and Helm lint on every push
├── docker-compose.yml        # db (pgvector), redis, ollama, api, worker, scheduler, web (+ whisper "voice" profile)
├── helm/cirra/               # Kubernetes chart: api, worker, scheduler, web, optional pgvector/redis/ollama/whisper, zero-egress policy
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
│       │   ├── insights.py          # Aiden: stage-trigger AI actions, briefing, Q&A, drafts
│       │   ├── dedup.py · hierarchy.py · custom_fields.py · privacy.py   # pillars 1–2
│       │   ├── cpq.py · clm.py · fx.py · pipeline_templates.py          # pillars 3–4
│       │   ├── mail.py · calendar.py · sla.py · voice.py · search.py    # pillars 5–6
│       │   ├── success.py · erp.py · prm.py                             # pillars 7–9
│       │   ├── data_io.py · storage.py · notify.py                      # pillar 10
│       │   └── jobs.py              # Celery or in-process background jobs
│       ├── api/v1/           # accounts, contacts, deals, activities, ai, cpq, success, finance, partners (+portal), admin
│       ├── worker.py         # Celery tasks and the beat schedule (run by the scheduler container)
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
and Aiden. Vectors from different providers aren't comparable, so re-embed if you switch.

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

- **Renamed from "relate [R]" to Cirra.** A few internal database identifiers created by the migrations keep their original
  prefix (the `relate_append_only()` trigger function and the `relate.allow_ledger_reset` setting). They are never shown to
  users, and renaming them would need a migration on existing databases.
