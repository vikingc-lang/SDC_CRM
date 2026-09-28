---
name: cirra-cpq-orders
description: Cirra quote-to-order - products, price books, volume tiers, promotions, bundles and product rules, quotes and the approval chain, document generation, redlining, built-in and external e-signature, credit checks, orders and the ERP sales-order hand-off, contracts and renewals, ERP customer/invoice sync. Use for /products, /quotes, /approvals, /documents, /sign, /orders, /contracts, /finance or services cpq.py, contracting.py, clm.py, orders.py, erp.py.
---

# Products, CPQ, documents, orders, contracts, ERP (modules 7, 8, 9)

## Files
- `services/cpq.py` – pricing (`tier_price`: the whole quantity at the highest tier reached; price book order:
  customer book → regional book → list), promotions (don't count toward approval thresholds), bundles and
  `requires`/`excludes` rules, TCV/ACV, `rebuild()`, approval chain (`required_approvals`, `submit`, `decide`,
  `can_approve`), `set_primary`, `quote_out`.
- `services/contracting.py` – document versions and redlines (editing a sent doc voids signatures), clause
  comments (must be resolved before sending), `credit_check` (Finance approval for credit hold / high risk),
  external e-sign (`send_external`, `handle_provider_event`).
- `services/clm.py` – Jinja2 (sandboxed) templates → Markdown/HTML/PDF, built-in e-signature with single-use
  links (customer first, then company), signature certificate PDF, contract from the Order Form, renewals
  (`run_renewals`: renewal deals 120 days before expiry).
- `services/orders.py` – Closed-Won `readiness` (signed Order Form, approved primary quote, PO, bill-to/ship-to,
  tax-exempt certificate), `create_order` (locks the primary quote), queue + `process_queue` (job `erp_orders`
  every 2 min, 5 retries, `order.acknowledged` event).
- `services/erp.py` – connectors `demo` | `file` | `rest`: customer master + invoices in, credit holds, A/R aging.
- API: `api/v1/cpq.py` (+ public `/sign/{token}`, `/esign/webhook/{provider}`), `api/v1/orders.py`,
  `api/v1/finance.py`. Frontend: `app/(app)/quotes|approvals|orders|products|documents|finance`,
  `components/dealdesk.tsx`, `negotiation.tsx`, `orders.tsx`, Admin → Approval chain.

## Rules
- Prices are always computed server-side (`rebuild`); never trust client totals.
- Approval levels come from `approval_policies` + `approval_groups` (discount %, payment terms, deal size,
  credit hold, credit risk) and are decided in chain order `cpq.CHAIN`: sales manager → deal desk → VP sales →
  finance → legal (`app_settings` `approval_chain`).
- Documents: the body is always the latest version; the SHA-256 changes per version so parties sign the exact text.
- Order creation and ERP push are asynchronous; UI shows `submitted` until acknowledged.

## Tests
`tests/test_deal_desk.py`, `test_revenue.py`, `test_orders.py` (full lead-to-order), `test_post_sale.py` (ERP).
Browser: `e2e/lead-to-order.mjs` (62 checks; parameterised with `E2E_FIRST`, `E2E_LAST`, `E2E_COMPANY`, …).

## Gotchas
- Not built yet (don't assume): guided selling, amendments, co-termed renewals, order orchestration.
- Validation rules don't cover quotes/orders (only accounts, contacts, deals, leads, cases, custom objects).
