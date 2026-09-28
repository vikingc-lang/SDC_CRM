---
name: cirra-activities-mail
description: Cirra activity ledger and engagement - activities timeline, tasks with dependencies and SLA escalation, notifications, files/attachments, user mailbox IMAP/SMTP sync and sending, iCal feeds and .ics import. Use for /activities, /tasks, /email, /calendar, /files, /notifications, mail.py, sla.py, calendar.py, storage.py or notify.py.
---

# Activities, email, calendar and tasks (module 6)

## Files
- `api/v1/activities.py` – activities, tasks, email send, calendar feed/import, files, notifications.
- `services/mail.py` – per-user `MailboxConnection` (encrypted credentials), `ingest_message()` (RFC 822 →
  email activity matched to contacts), `sync_mailbox()` (IMAP, job `mail_sync` every 5 min),
  `deliver()` (user's SMTP; returns `(message_id, delivered)`, recorded only without SMTP; optional `html`),
  `send_email()` (consent-checked).
- `services/mailer.py` – **system** SMTP (`SMTP_*` settings) for report deliveries, case replies and
  acknowledgements; returns the Message-ID; raises `MailError` (never includes the server's reply text).
- `services/sla.py` – overdue task escalation ladder (remind → manager → reassign), dependency cycles.
- `services/calendar.py`, `services/storage.py` (content-addressed files on the volume), `services/notify.py`
  (`notify()` in-app notifications, `emit()` integration outbox events with `EVENT_TARGETS`).
- Frontend: `components/ActivityTimeline.tsx`, `LogActivityDialog.tsx`, `TaskList.tsx`, `app/(app)/tasks`.

## Rules
- Two mail paths: **user mailbox** (`mail.deliver`, sales email, campaign and journey sends) vs **system
  mailer** (`mailer.send`, service and reports). Don't mix them.
- Every outbound email to a contact checks `privacy.can_contact`.
- New activities should rescore the account and be embedded for search (`enqueue(background, "embed_activity", id)`).
- `notify()` titles are truncated to 300 chars; notification `kind` ≤ 30 chars.

## Tests
`tests/test_ops.py` (ledger, files, tasks/SLA, email & calendar sync, notifications).

## Gotchas
- Tests that fake `mail.deliver` must accept `html=None` (added for tracked marketing emails).
- Two-way calendar sync: `services/calendar_sync.py` (Google Calendar, Microsoft Graph; OAuth + PKCE with the
  state in `sso_login_states`, encrypted tokens, incremental cursors). Pull creates meetings only for events with
  a CRM contact attendee; push mirrors the user's upcoming meetings; `calendar_links` hold etags and a hash of the
  synced fields (echo and conflict detection; on a two-sided edit the calendar wins). Tests swap providers via
  `PROVIDERS` or `calendar_sync.http_transport`. Settings → Calendar sync (`components/preferences.tsx`).
- There are no Outlook/Gmail add-ins; don't assume them.
