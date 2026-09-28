---
name: cirra-workflows
description: Cirra no-code workflow engine - rules with created / field-changed / scheduled triggers, conditions in report filter syntax, actions (create task, notify, update field, emit event, HTTP webhook, Slack/Teams message), after-commit dispatch, loop protection, dry runs and run logs. Use for /workflows, services/workflows.py, adding trigger fields, record types or action types.
---

# Workflow automation (module 17)

## Files
- `services/workflows.py` – `ENTITIES` (record types: deals, leads, accounts, contacts, activities, tasks,
  quotes, orders, cases; each with watched fields and settable fields), `validate()`, `_capture`
  (`after_flush`), `_dispatch` (`after_commit` → `_process` in a fresh session), `drain()` (await in tests and
  jobs), `run_scheduled` (job `workflows`, hourly), actions incl. `http_request` / `post_message`
  (queued in `db.info["wf_http"]`, sent by `send_pending()` after commit, retry once, SSRF guard
  `_blocked_host`, response bodies never logged), `render()` placeholders `{{field}}` (incl. `cf_` keys).
- API: `api/v1/workflows.py` (`/workflows`, `/meta`, `/{id}/toggle|test|runs`).
- Frontend: Admin → Workflows (`components/admin/workflows.tsx`, `app/(app)/admin/workflows`).

## Rules
- Rules evaluate after the transaction commits, so a rolled-back change never triggers anything.
- Workflow-made changes may trigger other rules up to `MAX_DEPTH`; a rule never re-triggers itself.
- Workflow sessions are system sessions: validation rules and field security don't apply.
- Outbound URLs must be https to public hosts (`developer.clean_url` + `_blocked_host`).
- Per-rule failures roll back that rule only and are recorded in `workflow_runs`.

## Recipes
- New record type: add an `Entity` to `ENTITIES` (model, label, watch map, owner, link, account/deal getters,
  settable fields), map it in `ENTITY_TYPE`, make sure a report source exists for conditions.
- New action: validate it in `validate()`, run it in `execute()` (which also serves dry runs via `dry_run()`),
  and add its form to the workflow editor.

## Tests
`tests/test_workflows.py`, `test_wave1.py`, `test_wave1_review.py` (after-commit HTTP, SSRF, no body logging).
Always `await workflows.drain()` before asserting on workflow effects.

## Gotchas
- A workflow update/toggle must `db.refresh(rule)` after commit (server-set `updated_at`).
- Not built: visual flow builder, screen flows, workflows on custom objects.
