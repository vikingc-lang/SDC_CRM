---
name: cirra-testing
description: How to test Cirra - the backend pytest suite inside Docker (USE_CELERY=false, throwaway test DB), test helpers and conventions, the Playwright browser journeys (Edge, playwright-core copy in the scratchpad), the screen sweep and lead-to-order run, and the GitHub Actions CI. Use before claiming any Cirra change works, when writing tests, or when CI fails.
---

# Testing Cirra

## Backend suite (always in Docker; the host has no pytest)
```bash
cd backend && docker compose up -d --build api            # the image has no tests: copy them in
MSYS_NO_PATHCONV=1 docker cp tests cirra_api:/app/
MSYS_NO_PATHCONV=1 docker exec -e USE_CELERY=false \
  -e TEST_DATABASE_URL=postgresql+asyncpg://cirra_user:cirra_secure_password@db:5432/cirra_test \
  cirra_api python -m pytest -q -rf [tests/test_x.py]
```
- `USE_CELERY=false` is mandatory (the container defaults to Celery and jobs would hit the live DB).
- The session drops and migrates the test database, then seeds demo data; data persists between tests in
  one run, so use unique names (`uuid.uuid4().hex[:6]`) and clean up rules/objects/queues you create.
- Fixtures: `client` (signed in as marcus, Sales Manager); `tests.helpers.login_as(email)` for other roles;
  `SessionLocal()` for direct DB checks (system session: no validation rules).
- `asyncio_mode = auto`: plain `async def test_…`; no `pytest.mark.asyncio`.
- Await `workflows.drain()` before asserting on workflow effects; fake mail with
  `monkeypatch.setattr(mailer, "_send", …)` / `mail.deliver` (accept `html=None`).
- Rate limiting is off in tests (`RATE_LIMIT_ENABLED=false` in conftest).

## Browser journeys (Playwright, Edge)
Scripts in `e2e/` import `playwright`; locally a `playwright-core` copy lives in the session scratchpad
`e2e/` folder: copy with `sed 's/from "playwright";/from "playwright-core";/'` and run
`E2E_CHANNEL=msedge node <script>.mjs ./out`.
- `screen-sweep.mjs` (every screen × role; 2nd arg = an unsubscribe token from `campaign_members.token`),
  `lead-to-order.mjs`, `wave1-journeys.mjs` … `wave4-journeys.mjs` (wave 4 needs `INBOUND_EMAIL_SECRET`).
- Journeys must clean up what they create and select elements by label/role; wait for the specific row or
  result, not for a toast text that an earlier toast may already show.
- Keep the Windows host awake during long runs (it sleeps and freezes Docker).

## CI
`.github/workflows/ci.yml` on every push/PR: backend suite on pgvector + Redis services, frontend typecheck +
lint + build, then Docker image builds and `helm lint`/`template`. Check a run without `gh` auth via
`https://api.github.com/repos/vikingc-lang/SDC_CRM/actions/runs`. Reproduce the backend job locally in clean
containers (python:3.11 + pgvector/pgvector:pg16 + redis:7 on a docker network).

## Before saying "done"
Backend suite green, frontend typecheck/lint clean, the relevant browser journey passing, and the screen
sweep for UI-wide changes. Report failures honestly with the output.
