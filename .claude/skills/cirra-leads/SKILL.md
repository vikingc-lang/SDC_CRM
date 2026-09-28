---
name: cirra-leads
description: Cirra lead management - capture from forms/webhooks/partners/SDRs, dedup, enrichment, consent, fit + engagement scoring, MQL threshold, routing rules, qualification frameworks (BANT/MEDDPICC) and conversion to account/contact/opportunity. Use for /leads, /intake, services/leads.py, lead scoring or routing changes.
---

# Leads (module 4, lead-to-order steps 1-3)

## Files
- `services/leads.py` – `capture()` (normalise → dedup → enrichment → consent → score → route), `rescore()`,
  `add_event()` (engagement events: form_submit, content_download, pricing_page_visit, webinar_registered,
  webinar_attended, email_open, email_click, trade_show_scan, meeting_booked, web_visit), `route()`,
  `convert()`, `qualification_summary()`, `lead_out()`; constants `FRAMEWORKS`, `SOURCES`, `OPEN_STATUSES`,
  `DISQUALIFY_REASONS`, `ALIASES`.
- `api/v1/leads.py` – `/leads` (list, detail, create, patch, convert, events, admin rules) and the public
  `/intake` router (hosted web forms, webhooks).
- `services/app_settings.py` `DEFAULTS["lead_scoring"]` – ICP weights, `event_points`, half-life, MQL threshold.
- Frontend: `app/(app)/leads`, `components/leads.tsx`, Admin → Lead management (`components/admin/leadtoorder.tsx`).

## Rules
- Score = weighted blend of fit (explicit ICP match) and engagement (event points decayed by a half-life);
  crossing the MQL threshold marks the lead MQL and notifies the owner. Nightly `lead_rescore` job decays scores.
- Capture never duplicates an open lead with the same email: it updates it (`merged: true`).
- Conversion is one transaction creating/linking Account, Contact and Opportunity and needs the framework minimum
  (BANT 3/4, MEDDPICC 5/8) unless a manager overrides; converted leads are read-only.
- Own-scope roles see leads they own or unassigned leads.
- Email engagement from marketing (`tracking.py`) calls `add_event` + `rescore` on first open / click.

## Tests
`tests/test_leads.py`, `test_wave1*.py` (lead upsert), `test_wave4.py` (engagement from tracking).

## Gotchas
- GDPR leads (EU country) need `consent: "granted"` before marketing email; tests creating emailable leads
  should pass `consent="granted"` and a non-EU country.
- Lead custom fields can be defined and reported on but the lead page has no custom-field editor yet.
