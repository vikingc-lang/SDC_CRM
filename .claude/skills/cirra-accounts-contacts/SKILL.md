---
name: cirra-accounts-contacts
description: Cirra accounts, contacts and the customer 360 - account 360 view, hierarchies and roll-ups, health/churn/relationship scoring, dedup and merge, enrichment, privacy/consent and cryptographic erasure, related custom-object records. Use for work on /accounts, /contacts, scoring.py, dedup.py, hierarchy.py, enrichment.py or privacy.py.
---

# Accounts, contacts and customer 360 (modules 2, 3, 16)

## Files
- API: `api/v1/accounts.py` (`/accounts`, `/accounts/{id}/360`, tickets, hierarchy), `api/v1/contacts.py`.
- Services: `scoring.py` (account health, deal risk, relationship strength index, churn),
  `hierarchy.py` (parent/child trees, roll-ups, `would_create_cycle`), `dedup.py` (Jaro-Winkler + Levenshtein name
  match, registrable domain, merge rules, `auto_merge`), `enrichment.py` (`internal` | `rest` | `disabled`),
  `privacy.py` (`can_contact`, `record_consent`, `erase_contact`), `serializers.py` (`contact_out`, `deal_card`).
- Frontend: `app/(app)/accounts`, `app/(app)/contacts`, `components/panels.tsx` (`CustomFieldsEditor`),
  `components/objects.tsx` (`RelatedObjectRecords` on the account page), `components/indicators.tsx`.

## Rules
- Merges re-point every foreign key in the schema at the survivor (`dedup.reparent`), so orders, price books,
  campaign history, cases, converted leads and custom records all move; on a unique-key collision the survivor's
  row is kept. New tables need nothing extra, provided their link is a real foreign key.
- Health = 0.30 recency + 0.25 sentiment + 0.15 velocity + 0.15 support + 0.15 milestones; rescore with
  `scoring.rescore_account(db, account_id)` after changes that affect it (activities, cases, milestones).
- Contacts belong to exactly one account; emails are unique CRM-wide (`contacts_email_key`), so never create a
  contact with an email that exists elsewhere (quick-log drops the clashing email).
- Consent: `can_contact(contact, channel)` before any outreach; opt-outs are most-restrictive on merge;
  erasure anonymises PII, redacts notes and destroys the per-person key (audit values become unreadable).
- Account custom fields live in `custom_metadata` (`health_breakdown`, `domain_unverified`, `source` are reserved
  keys); contacts/deals/leads use `custom_fields`. Always output through `custom_fields.redact()`.
- New accounts get a territory via `performance.assign(db, account)`.
- Duplicate review (`/admin/dedup`) only lists and acts on pairs where both records are in the caller's scope
  (404 otherwise, also for dismiss); merging deletes a record, so it needs update **and delete** on the resource
  (AEs can merge their contacts, not accounts). `can_merge` tells the UI.

## Tests
`tests/test_platform.py` (dedup, hierarchy, privacy, custom fields, import/export), `test_scoring.py`,
`test_wave3.py` (field security, sharing on accounts).

## Gotchas
- `/accounts` list returns a slim item without region; use the report engine (`/analytics/run` with `with_ids`)
  when you need filtered account sets in tests.
- Account search indexes custom values; hidden custom keys are excluded per role (`search.py`).
