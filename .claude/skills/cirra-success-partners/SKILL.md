---
name: cirra-success-partners
description: Cirra post-sale and channel - Closed-Won hand-off to onboarding workspaces and milestones, churn early warning, renewals from contracts, and partner relationship management (deal registration with conflict checks, 90-day exclusivity, co-sell attribution, commissions, collateral, the partner portal). Use for /success, /partners, /portal, services success.py, prm.py or clm renewals.
---

# Customer success and partners (modules 14, 15)

## Files
- `services/success.py` – `provision_onboarding` (Closed-Won → onboarding workspace with milestones and the
  pre-sales hand-off), `renew_contract_from_deal`, `project_out`. Churn risk lives in `scoring.compute_churn`;
  renewals in `clm.run_renewals` (job `renewals`, 120 days before expiry).
- `services/prm.py` – `find_conflicts` (existing accounts / overlapping registrations), `submit`, `decide`
  (approval grants 90-day exclusivity and creates the deal in the Partner pipeline), `partner_rate`,
  `commission_report`, `can_access_collateral`.
- API: `api/v1/success.py` (`/success/onboarding|milestones|churn|renewals`), `api/v1/partners.py`
  (`/partners`, `/registrations`, `/commissions`, `/collateral`; partner portal router `/portal`).
- Frontend: `app/(app)/success`, `app/(app)/partners`, `app/portal` (partner-only UI), `components/partners.tsx`.

## Rules
- Partner users (`role = partner`) can only use `/portal/*`: `get_principal` rejects them elsewhere; never
  expose internal directories or other partners' data to the portal.
- Registrations create accounts through the normal paths (territory assignment, dedup checks).
- Churn warning combines adoption/utilisation trends, critical/high ticket load and champion turnover.

## Tests
`tests/test_post_sale.py`, `test_scope_regressions.py` (partners kept out of internal endpoints).

## Gotchas
- Not built: partner marketing funds, tier automation, health scorecards/playbooks at dedicated-tool depth.
