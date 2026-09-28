---
name: cirra-performance
description: Cirra sales performance - territories (criteria matching, priority, realignment, coverage gaps), quotas, attainment and tiered commission plans and statements. Use for /performance, services/performance.py, territory assignment of accounts or commission maths.
---

# Territories, quotas and commission (module 11)

## Files
- `services/performance.py` – `match`/`assign` (territory for an account: lower priority first, then the more
  specific; empty criteria = catch-all), `realign` (nightly job `territories`; stamps accounts, never changes the
  owner), `coverage_gaps`, commission plans (`clean_tiers`, `plan_for`, `commission`, `statement`),
  `quota_of`, `scorecard`, `my_view`, `team_view`.
- API: `api/v1/performance.py` (`/performance/territories|quotas|plans|me|team|periods`).
- Frontend: `app/(app)/performance`, `components/performance.tsx`, Admin → Territories & incentives
  (`components/admin/performance.tsx`).

## Rules
- Criteria: region, country, industry, tier, employee range; every non-empty criterion must match.
- Attainment = closed-won bookings in the quarter (USD) vs quota. Commission is bracketed like tax: base rate up
  to the first tier threshold, each tier's rate on the slice above it; each deal's commission is the increase it
  causes, so statement lines add up to the total.
- Every code path that creates an account must call `performance.assign(db, account)` (UI, import, partner
  registration, quick-log, upsert already do).
- Account sharing rules (`cirra-platform-core`) are the way to give territory-based visibility to own-scope roles.

## Tests
`tests/test_performance.py`, `test_review_fixes.py`.

## Gotchas
- No territory hierarchies or territory models (planning scenarios) yet.
