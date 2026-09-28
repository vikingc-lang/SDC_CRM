---
name: cirra-marketing
description: Cirra marketing - campaigns, members (leads/contacts), list building from report filters, consent-checked campaign email, unsubscribe, attribution and ROI, nurture journeys (email/wait steps with opened/clicked branching), open-pixel and signed click tracking feeding lead engagement. Use for /campaigns, /journeys, /t tracking endpoints, services campaigns.py, journeys.py or tracking.py.
---

# Marketing, journeys and email tracking (module 12)

## Files
- `services/campaigns.py` – members (`add_members`, `add_from_filter`, `set_status`: responded/registered/
  attended add lead engagement), `person_for()` (who a member is + why they can't be emailed), `recipients()`,
  `render()` / `render_subject()`, `send()` (commits per recipient; bounced on refused address), `unsubscribe()`,
  `metrics()` (funnel, sourced/influenced pipeline, ROI, `email` stats), `email_stats(db, *where)`.
- `services/tracking.py` – `deliver_tracked()` (records an `EmailSend`, sends text + HTML through the sender's
  mailbox), `tracked_text/html` (links → `/api/v1/t/c/<token>?u=&s=<HMAC>`; pixel `/api/v1/t/o/<token>.gif`;
  unsubscribe links stay direct), `record_open`, `record_click` (verifies signature; a click implies an open;
  first click marks the member responded).
- `services/journeys.py` – `validate_steps` (email `{subject, body, send_if}` / wait `{days}`; `send_if`:
  always | opened | not_opened | clicked | not_clicked vs the previous email), `enroll`, `advance`, `run`
  (job `journeys` every 5 min; commits per person), `stats`.
- API: `api/v1/campaigns.py` (+ public `/public/unsubscribe/{token}`), `api/v1/journeys.py`
  (`/journeys`, `/{id}/status?status=active|paused|archived`, `/{id}/test`, `/run`), `api/v1/inbound.py` (`/t/...`).
- Models: `models/marketing.py` (Campaign, CampaignMember), `models/engagement.py` (Journey,
  JourneyEnrollment, EmailSend, EmailEvent).
- Frontend: `app/(app)/campaigns`, `app/(app)/journeys/[id]`, `components/campaigns.tsx`, `components/journeys.tsx`.

## Rules
- Consent first: leads need no denial and, under GDPR, granted consent; contacts go through `can_contact`.
- Journeys exit on unsubscribe, bounce, lost consent ("Can't email: …"), lead converted/disqualified.
  Edits only when draft/paused; a journey that has run can only be archived.
- Tracking links must be signed with `tracking.sign(token, url)`; never add an unsigned redirect.
- Opens are indicative (image pre-loading); present them that way in UI copy.
- Public tracking endpoints use their own `SessionLocal` session so validation rules never block them;
  `PUBLIC_API_URL` must be reachable by recipients.

## Tests
`tests/test_campaigns.py`, `test_wave4.py` (tracking + journeys), `test_review_fixes.py` (partial SMTP failure).
Browser: `e2e/wave4-journeys.mjs`.

## Gotchas
- Subjects must be single-line: always use `render_subject` (a raw `render` leaves a trailing newline).
- Not built: A/B tests, landing pages, segment builder, bounce processing from the mail server, SMS.
