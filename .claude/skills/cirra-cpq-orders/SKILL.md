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
- Load quotes only through `_quote(db, p, id)` (scope via the quote's deal), and a document's quote must belong to
  the document's deal (422 otherwise), so one deal's pricing can never land in another deal's contract.
- Approval levels come from `approval_policies` + `approval_groups` (discount %, payment terms, deal size,
  credit hold, credit risk) and are decided in chain order `cpq.CHAIN`: sales manager → deal desk → VP sales →
  finance → legal (`app_settings` `approval_chain`).
- Documents: the body is always the latest version; the SHA-256 changes per version so parties sign the exact text.
- Public signing links are for customer signers only and expire after `ESIGN_LINK_DAYS` (410 after that);
  `POST /documents/{id}/signers/{sid}/resend` issues a new token (the old link stops working). The company
  countersigns inside Cirra (`POST /documents/{id}/countersign`, only the named signer, signed in); company
  signers must be active Cirra users.
- Order creation and ERP push are asynchronous; UI shows `submitted` until acknowledged.
- Tax (`services/tax.py`, engine in `app_settings` key `tax`: none | builtin | india_gst | avalara) is
  recalculated at the end of every `rebuild` and via `POST /quotes/{id}/tax`, on each line's term total, from
  the deal ship-to → bill-to → account billing address; exempt deals are never taxed; engine failures leave
  tax at 0 with `tax_detail.error` and never block the quote. Orders copy `tax_total` / `tax_detail`; the ERP
  payload carries `tax_total` and `tax_lines`. Products carry an optional `tax_code` (HSN/SAC, Avalara code).
- Exchange rates: `services/fx.py` `Rates` (a dict of today's rates plus dated history); change rates only via
  `fx.set_rate` (history row + today's rate). Admin → Tax & currency (`components/admin/taxfx.tsx`).

## Tests
`tests/test_deal_desk.py`, `test_revenue.py`, `test_orders.py` (full lead-to-order), `test_post_sale.py` (ERP).
Browser: `e2e/lead-to-order.mjs` (62 checks; parameterised with `E2E_FIRST`, `E2E_LAST`, `E2E_COMPANY`, …).

## Gotchas
- Not built yet (don't assume): guided selling, amendments, co-termed renewals, order orchestration.
- Validation rules don't cover quotes/orders (only accounts, contacts, deals, leads, cases, custom objects).
