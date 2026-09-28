# Database portability

PostgreSQL (16, with `pgvector` and `pg_trgm`) is Cirra's reference database: the migrations target it, CI runs
on it, and its behaviour is unchanged. The data model and queries are written so that Cirra can move to another
database without rewriting the application. This page covers what that rests on, what changes per database, and
how to move.

## What makes the model portable

| Layer | Where | What it does |
|---|---|---|
| Column types | `backend/app/core/types.py` | `UUID`, `JSONB`, `UTCDateTime`, `Embedding`, `SearchVector`: exact Postgres storage on Postgres, standard types elsewhere. Models import types only from here, never from `sqlalchemy.dialects.postgresql`. |
| Complete schema in the models | `backend/app/models/schema_rules.py` | The check constraints, unique constraints and indexes the migrations create (114 checks, 13 uniques, 61 indexes) are declared on `Base.metadata` in portable SQL, so `create_all` builds the full schema on any database. |
| Postgres-only SQL behind one module | `backend/app/core/dialect.py` | Date buckets, durations, regex checks, JSON membership, `concat_ws`, `NULLS LAST`, full-text search, trigram similarity, insert-or-ignore, locks and document numbers. Each construct compiles to the running database's own SQL. |
| Counters instead of sequences | `number_sequences` table, `dialect.next_number()` | Case, quote, order and contract numbers come from a row-locked counter (migration 017). This also fixes the `count(*) + 1` numbering that could give two concurrent creators the same number. |
| Tooling | `backend/app/dbtools.py` | `schema`, `copy` and `verify` commands to build a target and move the data across. |
| Guard rails | `backend/tests/test_portability.py` | Zero drift between models and the migrated schema (columns, types, nullability, indexes, constraint values). DDL compiles for PostgreSQL, MySQL, SQL Server, Oracle and SQLite. The whole test database copies to SQLite with identical values, and every adapter construct is exercised. |

### Rules for new code

- Import column types from `app.core.types`; timestamps are `UTCDateTime()`.
- Use the generic JSON operators (`col["key"].as_string()`) and `dialect.json_array_has()`. Postgres-only
  operators such as `@>`, `?`, `-` and `.astext` go through `app.core.dialect`.
- Use `dialect.next_number(db, key)` for any human-facing running number; never `count(*) + 1`.
- Use `dialect.nulls_last()` / `nulls_first()` rather than `.nulls_last()`.
- Raw `text()` SQL only for Postgres-only paths, behind `dialect.is_postgres(db)`, with a portable fallback.
- A migration that adds a check, unique constraint or index not declared on the model adds it to
  `schema_rules.py` too. `test_models_match_the_migrated_schema` fails until you do.

## What each database gets

| Capability | PostgreSQL | MySQL 8 / MariaDB | SQL Server 2022 | Oracle 19c+ | SQLite |
|---|---|---|---|---|---|
| UUID keys | `uuid` | `CHAR(32)` | `UNIQUEIDENTIFIER` | `CHAR(32)` | `CHAR(32)` |
| JSON documents | `jsonb` | `JSON` | `NVARCHAR(max)` | `CLOB` (JSON text) | `JSON` |
| Timestamps | `timestamptz` | `DATETIME(6)`, naive UTC, read back as aware UTC | `DATETIMEOFFSET` | `TIMESTAMP WITH TIME ZONE` | naive UTC, read back as aware UTC |
| Check constraints | yes | yes (8.0.16+) | yes | yes | yes |
| Partial unique indexes ("where not null") | yes | plain unique index (NULLs never collide) | filtered unique index | plain unique index | yes |
| Nullable unique columns | unique constraint | unique constraint | filtered unique index `uqn_*` instead (SQL Server treats NULLs as equal) | unique constraint | unique constraint |
| Case-insensitive `lower()` indexes | yes | functional index (8.0.13+) | not created (default collation is case-insensitive) | function-based index | yes |
| Keyword search | `tsvector` + `ts_rank` | `LIKE` on each word, ranked by words matched | same as MySQL | same as MySQL | same as MySQL |
| Semantic search (embeddings) | pgvector HNSW | skipped; keyword search only | skipped | skipped | skipped |
| Duplicate blocking by name | `pg_trgm` in the database | same trigram measure in Python | same | same | same |
| Webhook run lock | advisory lock | `SELECT … FOR UPDATE SKIP LOCKED` on a lock row | same | same | not needed (one writer) |
| Insert-or-ignore | `ON CONFLICT DO NOTHING` | `INSERT IGNORE` | savepoint per row | savepoint per row | `ON CONFLICT DO NOTHING` |
| Append-only ledgers (`audit_log`, `consent_events`, `erasure_log`) | database triggers | recreate as triggers (see runbook) | recreate as triggers | recreate as triggers | recreate as triggers |

## Moving to another database

1. **Install the driver** in the backend image: `pymysql`/`aiomysql`, `pyodbc`/`aioodbc`, `oracledb` or
   `aiosqlite`. Use the async driver for `DATABASE_URL` and the sync one for the tools.
2. **Build the schema** on the empty target:
   `python -m app.dbtools schema --target mysql+pymysql://user:pass@host/cirra`.
   For a PostgreSQL target, run `alembic upgrade head` instead; that also creates the extensions and triggers.
3. **Stop writers**: scale the API, workers and scheduler to zero so the source is quiet.
4. **Copy**: `python -m app.dbtools copy --target … [--source …] [--batch 1000]`.
   - Tables go parents first, with foreign-key checks off where the target allows.
   - Identity counters are reset past the copied ids.
   - Generated columns (`search_tsv`) are skipped because the target has none.
5. **Verify**: `python -m app.dbtools verify --target …` compares row counts table by table and exits
   non-zero on any difference.
6. **Recreate the ledger guards.** Add BEFORE UPDATE / BEFORE DELETE triggers that raise an error on
   `audit_log`, `consent_events` and `erasure_log` in the target's trigger language.
7. **Point Cirra at it**: set `DATABASE_URL` and stamp the schema version with `alembic stamp head`, so
   `/health/ready` reports the schema as current.
   - Future schema changes on a non-Postgres install are applied with `alembic` autogenerate against
     `Base.metadata`. The hand-written migrations are Postgres SQL.
8. **Run the test suite against the target** before switching traffic.

## Known caveats

- **SQL Server** rejects multiple cascade paths to one table. A few child tables are reachable from two
  cascading parents. Change those foreign keys to `NO ACTION` in the target schema and let the application
  delete the children.
- **Oracle** needs 19c or later: identity columns, and 128-character names on 12.2+. SQLAlchemy 2.0 has no JSON
  path operators for Oracle, so custom-field report columns aren't available there.
- **MySQL** stores timestamps without a zone. Keep the server and session time zone at UTC, and `UTCDateTime`
  handles the rest.
- **Keyword search** off Postgres is `LIKE`-based: no stemming or language ranking. Plan a search service
  (for example OpenSearch) if that matters.
- **Semantic search** needs pgvector. Off Postgres, embeddings are still stored as JSON arrays, so they move back
  or to a vector service without being recomputed.
- The **demo reset** (`python -m app.seed --reset`) on other databases deletes rows table by table instead of
  `TRUNCATE`.
