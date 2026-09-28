---
name: cirra-pipeline-forecasting
description: Cirra opportunities and pipeline - multiple pipelines with declarative stage gates, stage changes, kanban, loss taxonomy, win/loss, weighted pipeline, deal risk, and forecast calls (categories, quarterly roll-ups, rep submissions, manager adjustments). Use for /deals, /pipelines, /forecast, pipeline_service.py, pipeline_templates.py or forecasting.py.
---

# Pipeline and forecasting (modules 5, 10)

## Files
- `services/pipeline_service.py` – stage-gate engine: `evaluate_gates`, `change_stage`, `kanban`, `forecast`,
  `win_loss`; gate rule types: min_contacts, domain_verified, role_mapped, activity_logged, field_present
  (`custom:<key>` for custom fields), amount_approved, keyword, pain_identified, signed_document, any_of,
  loss_reason, qualification, primary_quote, tax_exempt_cert. `LOSS_TAXONOMY`, `OVERRIDE_ROLES`.
- `services/pipeline_templates.py` – the four default pipelines (Enterprise Direct, Inbound Mid-Market,
  Renewals & Upsells, Partner Channel) and their stages/gates.
- `services/forecasting.py` – categories (pipeline, best_case, commit; won = closed, lost = omitted),
  `rollup`, `my_view`, `team_view`, submissions and adjustments. Commit call = Closed + Commit; Best case =
  Closed + Commit + Best Case.
- `services/fx.py` – `rates()`, `to_usd()`; all cross-currency totals are USD.
- API: `api/v1/deals.py` (`/deals`, `/deals/{id}/stage`, `/pipelines`, `/pipeline`, reports),
  `api/v1/forecasting.py` (`/forecast/...`).
- Frontend: `app/(app)/pipeline` (kanban), `app/(app)/deals/[id]`, `components/KanbanBoard.tsx`,
  `components/forecast.tsx`, Admin → Stages / Stage gates (`components/admin/config.tsx`, `leadtoorder.tsx`).

## Rules
- Stage moves must go through `change_stage` (gates, history rows, triggers, Closed-Won order readiness);
  never set `stage_id` directly. The upsert API only sets a stage at creation.
- Closed-Lost needs a loss reason from the taxonomy + debrief; Closed-Won on the Enterprise pipeline needs the
  order readiness checks (see `cirra-cpq-orders`).
- Weighted pipeline = Σ amount_usd × probability × (1 − risk/200). Deal risk = 30 stale + 30 sentiment drop +
  40 no champion.
- Own-scope roles see deals they own or on accounts they own/sell into (`p.scope_deals`).

## Tests
`tests/test_revenue.py` (gates, loss taxonomy, FX forecast), `test_forecasting.py`, `test_orders.py`.

## Gotchas
- Deals have no list-view page yet (the pipeline page is a kanban); the report source `deals` supports list views.
