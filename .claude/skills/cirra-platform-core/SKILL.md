---
name: cirra-platform-core
description: Cirra platform foundation - RBAC and row-level scope (Principal), account sharing rules, the request context, CirraSession and validation rules, custom fields with field-level security, custom objects, config export/import and app settings. Use when adding endpoints, permissions, resources, custom fields/objects, validation or sharing rules, or anything touching core/rbac.py, core/database.py or services/custom_fields.py.
---

# Platform core (module 1)

## Files
- `core/rbac.py` – `RESOURCES`, `ROLES`, `DEFAULT_MATRIX` (role × resource × CRUDE + scope all/own),
  `Principal`, `authorize()`, `authorize_person()` (refuses API keys), `get_principal()`, `principal_for()`,
  `load_matrix()` / `load_shares()` (30 s caches, `invalidate_cache()` / `invalidate_shares()`).
- `core/context.py` – `current_principal` contextvar, set by `get_principal`; `context.role()` for code
  with no principal parameter (serializers, custom-field validation). Unset in background jobs = system.
- `core/database.py` – `CirraSession.commit()` flushes and runs validation rules when `session.info["validate"]`
  (set by `get_db` only); `SessionLocal` sessions (jobs, workflows, tracking) are exempt.
- `services/validation.py` – rule capture (`after_flush`) and `check()`; `ValidationRuleError` → 422 via the
  handler in `main.py`. Rules use report filter syntax against report sources.
- `services/custom_fields.py` – `validate()` (types + write protection), `redact()`, `definitions_out()`,
  `level(defn, role)`; cache filled by `reporting.refresh_custom_fields`.
- `api/v1/objects.py` – custom objects and records; `api/v1/setup.py` – validation rules, sharing rules,
  config export/import; `services/config_bundle.py` – bundle format, dry-run plan, all-or-nothing apply.
- `api/v1/admin.py` – users, permissions matrix, custom-field CRUD (incl. `access`), audit, dedup, import/export.
- `services/app_settings.py` – key/value settings with `DEFAULTS` (`get`, `put`).
- `core/types.py` – portable column types (`UUID`, `JSONB`, `UTCDateTime`, `Embedding`, `SearchVector`);
  `core/dialect.py` – every database-specific construct (see "Database portability" below);
  `models/schema_rules.py` – checks, uniques and indexes the migrations create, declared on `Base.metadata`.

## Invariants
- **Scope**: `own` roles see accounts they own or sell into (`owned_account_ids()`), plus accounts matching an
  active sharing rule for their role, plus everything hanging off those accounts. All list/get queries must go
  through `p.scope_accounts(stmt, resource, column)` / `p.scope_deals(stmt)` or the module's own scope helper.
- **Field security** only covers custom fields: `access = {role: "read"|"hidden"}`; super_admin always edits.
  Hidden values are removed by `custom_fields.redact()` in serializers, from report sources (`reporting.src_for`),
  and from search; saves by restricted roles keep hidden values. Standard fields are not field-secured.
- **Validation rules** apply to people's saves only; a rule whose field was deleted is skipped (logged), never blocking.
- **Custom objects**: fields are custom field definitions with `entity = "object:<key>"`; records in
  `custom_records`; report source `obj_<key>`; permission resource `custom_objects`. Deleting an object deletes
  its fields, rules, views, reports (and dashboard tiles). Workflows don't run on custom objects yet.
- The live field catalogue refreshes by a signature (counts + max `updated_at` of field definitions and objects),
  so every replica sees changes on its next request (`principal_for` refreshes it).

## Database portability (docs/database-portability.md)
- Models import types from `app.core.types` only (never `sqlalchemy.dialects.postgresql`); timestamps are
  `UTCDateTime()`. Postgres stays the reference: its SQL is unchanged.
- Queries use generic JSON (`col["k"].as_string()`), `dialect.json_array_has`, `date_bucket`, `seconds_between`,
  `looks_numeric`/`looks_iso_date`, `concat_words`, `nulls_last`/`nulls_first`, `full_text`,
  `insert_ignore`, `try_lock`. Raw Postgres SQL only behind `dialect.is_postgres(db)` with a fallback.
- Running numbers: `await dialect.next_number(db, "quote:2026")` (row-locked counter in `number_sequences`),
  never `count(*) + 1` or a database sequence.
- A migration adding a check / unique / index the model doesn't declare also adds it to `schema_rules.py`;
  `tests/test_portability.py` (drift, DDL for 5 databases, copy to SQLite) fails otherwise.

## Recipes
- **New resource**: add to `RESOURCES`, give each role a spec in `DEFAULT_MATRIX` (missing rows fall back to
  defaults, so no migration is needed for existing tenants), guard endpoints with `authorize("x", action)`.
- **New endpoint**: `p: Principal = Depends(authorize(...))`, scope every query, 404 outside scope, audit
  sensitive actions with `log_action(db, action≤20 chars, entity, id, detail)`.
- **New validated entity**: add the model → source key to `validation.MODELS` and make sure a report source exists.
- **New thing in config bundles**: extend `config_bundle.export` and `apply` (natural key, `_upsert`, a check
  function), keep matching stable for repeated names (`_Occurrences`).

## Tests
`tests/test_platform.py`, `test_wave3.py` (rules, FLS, sharing, objects, config), `test_scope_regressions.py`.

## Gotchas
- Pydantic `Literal` enums on entity fields were widened to patterns for custom objects; keep the DB check
  constraint in sync (migration 015).
- Tests that create rules/objects must use unique names and clean up: the test database persists across runs.
