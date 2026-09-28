---
name: cirra-admin-security
description: Cirra administration, security and governance - sign-in with lockout, TOTP two-factor and recovery codes, MFA policy, OIDC SSO, JWT sessions, users and the roles/permissions matrix, append-only audit trail, privacy/compliance views, API rate limits, request tracing and security headers, the Admin page tabs. Use for /auth, /admin, core/security.py, core/audit.py, core/ratelimit.py, core/observability.py, services/identity.py, or adding an Admin tab.
---

# Admin, security and governance (module 21)

## Files
- `api/v1/auth.py` – `/auth/login` (per-account lockout from the audit trail: 10 failures / 15 min; MFA 5),
  `/auth/mfa/*`, `/auth/sso/*`, `/users/me` (permissions for the UI), `/auth/methods`.
- `services/identity.py` – TOTP (RFC 6238, Fernet-encrypted secrets), hashed single-use recovery codes, policy
  (`mfa_required_for`, `password_login_allowed`), OIDC with PKCE, state/nonce server-side, JWKS verification.
- `core/security.py` – password hashing, JWT; `core/deps.py` – current user (JWT or API key).
- `core/audit.py` – `before_flush` audit of changes (append-only `audit_log`), `log_action()` for explicit
  events (action ≤ 20 chars).
- `api/v1/admin.py` – users, permission matrix, security policy, audit, compliance, dedup, custom fields,
  import/export, jobs; `api/v1/setup.py` – validation/sharing rules, config transfer (see `cirra-platform-core`).
- `core/ratelimit.py` – Redis fixed windows per minute; buckets `auth` (per IP), `public` (per IP),
  `tracking` (per IP), `api` (per API key, per session token, else per IP); 429 + `Retry-After`,
  `X-RateLimit-*`; fails open; `/health*` exempt; `RATE_LIMIT_*` settings.
- `core/observability.py` – `X-Request-ID`, one access-log line per request, `LOG_FORMAT=json`, security
  headers (no CSP on `/docs`). Middleware order in `main.py`: RequestContext → CORS → RateLimit → app.
- Frontend: `app/(app)/admin/page.tsx` (tab list + `can()` gates), `components/admin/*.tsx`,
  `components/security.tsx`, `app/login`.

## Rules
- **Secrets:** `JWT_SECRET` has no default. `enforce_secure_settings()` (called by `main.py` and `worker.py`)
  refuses to start outside `ENVIRONMENT=development|dev|local|test` when it (or `DATA_ENCRYPTION_KEY`) is missing,
  shorter than 32 characters or a published value (`KNOWN_INSECURE_SECRETS`); development only warns. The container
  entrypoint generates both per install on the data volume when `JWT_SECRET` is empty. Never add a secret default.
- Identity/permission/key management endpoints use `authorize_person` (API keys refused).
- Never log secrets, tokens, server replies from SMTP/webhooks, or response bodies.
- Security fixes ship with a regression test (`test_review_fixes*.py`, `test_scope_regressions.py`).
- Adding an Admin tab: extend the `Tab` union and the `tabs` array (with its `can()` rule) and render the
  panel in `app/(app)/admin/page.tsx`; deep-link with `/admin?tab=<value>`.

## Tests
`tests/test_identity.py`, `test_platform.py` (RBAC, audit), `test_review_fixes_2.py`, `test_wave5.py`.
The suite sets `RATE_LIMIT_ENABLED=false`; rate-limit tests enable it with monkeypatched settings and a
unique client IP (`ASGITransport(app=app, client=(ip, port))`).

## Gotchas
- Not built: field security on standard fields, encryption key management / event monitoring, true sandboxes.
