---
name: cirra-integration
description: Cirra integration platform - API keys (ck_ prefix, acts as a user, read-only option), signed webhooks with retries from the event outbox, integration events feed, upsert by external id (single and batch), bulk actions, inbound email webhook, CSV/JSON import/export, ERP/ecosystem event delivery, API rate limits. Use for /developer, /upsert, /bulk, /integrations, /admin/import|export, services developer.py, sync.py, bulk.py, data_io.py, notify.emit.
---

# Integration, API and events (module 20)

## Files
- `services/developer.py` – keys (`new_key`, `authenticate`, SHA-256 hash only), webhooks (`clean_url`,
  `clean_patterns`, `sign`/`verify`: `X-Cirra-Signature: t=..,v1=HMAC`), fan-out with a settled cursor and
  advisory lock, retries 1 m / 5 m / 30 m / 2 h / 12 h, auto-off after 20 failures (job `webhooks`, every minute).
- `core/deps.py` – bearer `ck_…` or `X-API-Key` → user; `request.state.api_key_id`; read-only keys GET/HEAD only.
- `services/notify.py` `emit()` – outbox events (`EVENT_TARGETS` maps event → SDC modules); ERP/ecosystem
  delivery in `erp.deliver_events`.
- `services/sync.py` + `api/v1/sync.py` – `PUT /upsert/{entity}/{external_id}`, `POST /upsert/{entity}`
  (≤500 records, per-row result); match external_id → natural key (domain / email); never re-keys a record
  linked to another external id; related records by their external id or natural key.
- `services/bulk.py` – `POST /bulk/{leads|accounts|contacts|cases}` `{ids, action, value, note}`; scope-checked,
  side effects through services; `INLINE` map for list-view editing.
- `services/data_io.py` – import (auto-map headers, validate all rows, one transaction) and export (scoped, audited).
- `api/v1/inbound.py` – `/inbound/email` (see `cirra-service`), `/t/*` tracking (see `cirra-marketing`).
- `core/ratelimit.py` – see `cirra-admin-security`.
- Frontend: Admin → API & webhooks (`components/admin/developer.tsx`, `IntegrationReference`), Admin →
  Import / export (`components/admin/data.tsx`).

## Rules
- Keys act as their user: role permissions and row-level scope apply unchanged; keys can never manage users,
  permissions, sign-in security or keys (`authorize_person`).
- Webhook deliveries are at-least-once and signed; consumers must verify signatures and dedupe on the delivery id.
- Upsert follows the same rules as the UI (scope, custom-field validation, territory placement, lead capture,
  outbox events); deal stage only at creation.

## Tests
`tests/test_developer.py`, `test_wave1.py` / `test_wave1_review.py` (upsert, bulk), `test_platform.py`
(import/export), `test_review_fixes.py` (webhook fan-out), `test_wave5.py` (rate limits).

## Gotchas
- Webhook tests: count only your own listener path; old test subscriptions may still exist in the test DB.
- Not built: streaming / change-data-capture APIs, a bulk async job API, an integration hub.
