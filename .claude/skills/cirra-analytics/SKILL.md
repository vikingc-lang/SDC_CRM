---
name: cirra-analytics
description: Cirra analytics - the report engine (whitelisted field catalogue per source, filters, groupings, measures, custom fields, custom-object sources, field security), saved reports, dashboards with period/owner filters, drill-down, pivot, period-over-period comparison, CSV export, saved list views with inline edit, scheduled report subscriptions. Use for /analytics, /views, services reporting.py or subscriptions.py, report builder or dashboard UI.
---

# Analytics, list views and subscriptions (module 18)

## Files
- `services/reporting.py` – `Source`/`F` catalogue (`SOURCES`: deals, accounts, contacts, leads, activities,
  tasks, quotes, orders, campaigns, cases + `obj_<key>` custom objects), `refresh_custom_fields`, `src_of`,
  `src_for(key, p)` (role-hidden fields removed), `catalogue(p)`, `validate`, `run(db, p, defn)`
  (list or summary, `with_ids`, `compare: "previous_period"`), `OPS`, `RELATIVE`, `_period`,
  `previous_range`, `with_dashboard_filters`, `drill_definition`, `match_ids`, `criteria_ids`, `record_values`,
  `to_csv`, `link_for`, `STARTER_REPORTS`.
- `services/subscriptions.py` – schedule slots (daily/weekly/monthly at an hour, UTC), `deliver()` per
  recipient with their own principal (`principal_for`), notification + email with CSV via `mailer`;
  hourly job `report_subscriptions`.
- API: `api/v1/analytics.py` (`/analytics/sources|run|reports|dashboards|drill|subscriptions`),
  `api/v1/views.py` (`/views?source=`, `/views/run`, CRUD). Inline edits go through `/bulk/{entity}` with one id
  (`bulk.INLINE` lists editable columns).
- Frontend: `app/(app)/reports` (+ builder, dashboards), `components/reportviz.tsx` (charts, matrix,
  `Headline` delta, `ResultTable` compare columns), `components/drill.tsx`, `components/analytics.tsx`,
  `components/listviews.tsx` (`useListView`, `ListViewPicker`, `ViewGrid`), `components/subscribe.tsx`,
  `components/filters.tsx` (`FilterRow`).

## Rules
- Definitions never carry SQL; every field comes from the catalogue. Every run applies the viewer's scope and
  requires read permission on the source's resource, so shared reports show each viewer only their rows.
- Filters/groupings/measures on a field hidden from the viewer → "uses a field your role can't see";
  list columns that are hidden are dropped silently.
- Comparison shifts the last `within` filter (a dashboard's period comes last); not allowed with date groupings.
- Postgres has no `round(double, int)`: cast to `Numeric` before rounding.

## Recipes
- New source: add a `_name()` builder returning `Source(key, label, resource, description, fields, base, scope,
  default_columns, id_col)`, register in `SOURCES`, `DASH_DATE`, `LINKS`.
- New field on a source: add an `F(label, type, expr, options, groupable)`; types text|enum|number|money|date|bool.

## Tests
`tests/test_analytics.py`, `test_wave1.py`, `test_wave2.py` (views, compare, subscriptions), `test_wave3.py`
(FLS, custom-object sources). Browser: `e2e/wave1-journeys.mjs`, `e2e/wave2-journeys.mjs`.
