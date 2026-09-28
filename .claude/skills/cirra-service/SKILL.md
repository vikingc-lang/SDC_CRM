---
name: cirra-service
description: Cirra customer service - cases (numbering, SLA clocks, breaches), queues and routing (least-loaded or presence/capacity with push of waiting cases), agent presence and the routing console, email-to-case (webhook + IMAP, threading, reopen, follow-ups, unmatched inbox, emailed replies), knowledge base, CSAT. Use for /cases, /service, /knowledge, /inbound/email, services cases.py, routing.py, email_to_case.py.
---

# Customer service (module 13)

## Files
- `services/cases.py` – `create` (number `CS-00001`, queue, auto-assign via routing, SLA, `case.created` event),
  `add_comment` (first public reply stops the response clock; public reply → pending), `update` (re-times SLA on
  priority change, resolve/reopen, CSAT token, then `routing.assign_waiting`), `clocks`, `scan_breaches` (job
  `case_sla`), `suggest_articles`, `submit_csat`, `scope`, constants `PRIORITIES`, `STATUSES`, `CHANNELS`.
- `services/routing.py` – per-queue `routing`: `least_loaded` or `presence`; `pick()` (available, under
  capacity, fewest open cases, longest idle), `assign_waiting()` (priority then age; job `case_routing` every
  minute), `set_presence()`, `console()`.
- `services/email_to_case.py` – `Inbound`, `parse_raw()`, `ingest()` (idempotent by Message-ID; loop,
  auto-reply and burst protection; thread by `[CS-xxxxx]` or In-Reply-To/References; contact → account, else
  domain match excluding free mail; queue by support address), `file_unmatched()`, `email_reply()` (system
  mailer, threading headers), `poll_support_mailbox()` (job `support_mail`, `SUPPORT_IMAP_*`).
- API: `api/v1/cases.py` (`/cases`, `/cases/routing`, `/cases/presence/me|{user}`, `/cases/inbound`, `/service/
  queues|sla`, `/knowledge`, public `/public/csat`), `api/v1/inbound.py` (`POST /inbound/email`, header
  `X-Cirra-Inbound-Secret`).
- Frontend: `app/(app)/cases` (+ `/routing`, `/inbox`, `[id]`), `components/service.tsx`,
  `components/serviceops.tsx` (PresenceControl, RoutingConsole, SupportInbox), Admin → Service
  (`components/admin/service.tsx`: queues, support address, routing mode, SLA targets), `app/csat`, `app/(app)/knowledge`.

## Rules
- Only users whose role can update cases can own cases or join queues (`_check_owner`).
- Presence routing never assigns beyond capacity; with nobody free the case waits unassigned and is pushed later.
- Inbound email is system work: the endpoint disables validation rules for its session; unknown senders go to
  the unmatched tray instead of creating accounts.
- Customer replies are comments with `author_id = NULL` and `from_email`; our emailed replies store
  `message_id` and `emailed = true`.

## Tests
`tests/test_cases.py`, `test_wave4.py` (email-to-case, routing). Browser: `e2e/wave4-journeys.mjs`
(needs `INBOUND_EMAIL_SECRET`, local default `local-dev-inbound-secret`).

## Gotchas
- After routing touches `agent_presence`, refresh the row before returning it (server-set `updated_at`).
- Not built: live chat, customer self-service portal, entitlements, macros.
