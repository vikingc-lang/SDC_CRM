"""Database portability: the models describe the migrated schema exactly, the schema builds on other databases,
data copies across, and every construct in app.core.dialect works (see docs/database-portability.md)."""
import asyncio
import datetime as dt
import io
import re
import uuid

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import JSON, CheckConstraint, Column, Integer, MetaData, String, Table, create_engine, create_mock_engine, literal, select, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app import dbtools
from app.core import dialect as d
from app.core.config import settings
from app.core.database import Base, SessionLocal
from app.models import IntegrationEvent, NumberSequence, WebhookDelivery, WebhookSubscription
from app.models.schema_rules import POSTGRES_INDEXES

DIALECTS = {
    "postgresql": "postgresql+psycopg2://",
    "mysql": "mysql+pymysql://",
    "mssql": "mssql+pyodbc://",
    "oracle": "oracle+oracledb://",
    "sqlite": "sqlite://",
}


# Postgres spells these with regexes / NULL tests that have no literal-for-literal portable form (schema_rules.py)
HAND_WRITTEN_CHECKS = {"price_books_check", "price_books_check1", "custom_field_definitions_entity_check"}


def _flat(diffs):
    for x in diffs:
        if isinstance(x, list):
            yield from _flat(x)
        else:
            yield x


def _expected(diff) -> bool:
    kind = diff[0]
    if kind == "add_index":  # SQL Server's filtered stand-ins for nullable uniques
        declared = {i.name: i for i in Base.metadata.tables[diff[1].table.name].indexes}
        return declared[diff[1].name].info.get("mssql_only", False)
    if kind == "remove_index" and diff[1].name in {name for _, name, _ in POSTGRES_INDEXES}:  # raw DDL, Postgres only
        return True
    return False


def test_models_match_the_migrated_schema(database):
    engine = create_engine(settings.sync_database_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True, "compare_server_default": False})
        drift = [x for x in _flat(compare_metadata(ctx, Base.metadata)) if not _expected(x)]
        db_checks = {name: sql for name, sql in conn.execute(text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE contype = 'c' AND connamespace = 'public'::regnamespace"))}
    engine.dispose()
    assert drift == []
    # check constraints: same names, and the same allowed values / bounds (the SQL spelling differs by design)
    model_checks = {c.name: str(c.sqltext) for t in Base.metadata.tables.values() for c in t.constraints if isinstance(c, CheckConstraint)}
    assert sorted(set(db_checks) - set(model_checks)) == []
    assert sorted(set(model_checks) - set(db_checks)) == []
    literals = lambda sql: sorted(re.findall(r"'([^']*)'|(?<![\w.])(\d+(?:\.\d+)?)", sql))  # noqa: E731
    rewritten = {n for n, s in model_checks.items() if literals(s) != literals(db_checks[n])}
    assert rewritten == HAND_WRITTEN_CHECKS


@pytest.mark.parametrize("name", list(DIALECTS))
def test_schema_compiles_for_every_database(name):
    statements: list[str] = []
    engine = create_mock_engine(DIALECTS[name], lambda sql, *a, **kw: statements.append(str(sql.compile(dialect=engine.dialect))))
    Base.metadata.create_all(engine, checkfirst=False)
    ddl = "\n".join(statements)
    assert ddl.count("CREATE TABLE") == len(Base.metadata.tables)
    if name == "postgresql":
        assert "TSVECTOR" in ddl and "VECTOR(" in ddl and "hnsw" in ddl and "WHERE" in ddl
    else:
        assert "TSVECTOR" not in ddl and "tsvector" not in ddl and "hnsw" not in ddl and "gin_trgm_ops" not in ddl
    if name == "oracle":
        assert "TIMESTAMP WITH TIME ZONE" in ddl and " DATE DEFAULT" not in ddl
    if name == "mysql":
        assert "DATETIME(6)" in ddl
    if name == "mssql":  # nullable uniques become filtered indexes (SQL Server treats NULLs as equal)
        assert "uqn_" in ddl and "IS NOT NULL" in ddl


def test_whole_database_copies_to_sqlite(seeded, tmp_path):
    source = create_engine(settings.sync_database_url)
    target = create_engine(f"sqlite:///{tmp_path / 'cirra.db'}")
    dbtools.create_schema(target)
    counts = dbtools.copy_data(source, target, batch=250, out=io.StringIO())
    assert sum(counts.values()) > 500
    assert dbtools.verify(source, target) == []
    with source.connect() as a, target.connect() as b:  # values survive the trip: JSON, UUIDs, timestamps, numbers
        acc = Base.metadata.tables["accounts"]
        q = select(acc.c.id, acc.c.name, acc.c.custom_metadata, acc.c.alt_domains, acc.c.created_at, acc.c.annual_revenue)
        assert sorted(map(tuple, a.execute(q)), key=str) == sorted(map(tuple, b.execute(q)), key=str)
    source.dispose()
    target.dispose()


# ---- dialect constructs, compiled for each database and run on SQLite ------------------------------------------

@pytest.mark.parametrize("name", list(DIALECTS))
def test_constructs_compile_everywhere(name):
    engine = create_mock_engine(DIALECTS[name], lambda *a, **kw: None)
    t = Table("t", MetaData(), Column("a", String), Column("b", String), Column("j", JSON), Column("ts", String))
    for expr in [d.date_bucket(u, t.c.ts) for u in ("day", "week", "month", "quarter", "year")] + [
        d.seconds_between(t.c.ts, t.c.ts), d.looks_numeric(t.c.a), d.looks_iso_date(t.c.a), d.concat_words(t.c.a, t.c.b),
        d.json_array_has(t.c.j, "x"),
    ]:
        assert str(select(expr).compile(dialect=engine.dialect))


def test_nulls_ordering():
    t = Table("t3", MetaData(), Column("a", Integer), Column("b", String))
    n = t.c.a.label("n")
    q = select(t.c.b, n).order_by(d.nulls_last(n, descending=True), d.nulls_first(t.c.b))
    compile_for = lambda name: str(q.compile(dialect=create_mock_engine(DIALECTS[name], lambda *a, **kw: None).dialect))  # noqa: E731
    assert compile_for("postgresql").endswith("ORDER BY n DESC NULLS LAST, t3.b NULLS FIRST")
    assert "CASE WHEN t3.a IS NULL THEN 1 ELSE 0 END, t3.a DESC" in compile_for("mssql")
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        t.create(conn)
        conn.execute(t.insert(), [{"a": 1, "b": "x"}, {"a": None, "b": None}, {"a": 3, "b": "y"}])
        assert [r.a for r in conn.execute(select(t.c.a).order_by(d.nulls_last(t.c.a, descending=True)))] == [3, 1, None]
        assert [r.b for r in conn.execute(select(t.c.b).order_by(d.nulls_first(t.c.b)))] == [None, "x", "y"]


def test_date_bucket_units_do_not_share_a_cached_statement():
    t = Table("t2", MetaData(), Column("ts", String))
    day, week = (select(d.date_bucket(u, t.c.ts))._generate_cache_key() for u in ("day", "week"))
    assert day != week
    assert day == select(d.date_bucket("day", t.c.ts))._generate_cache_key()


@pytest.fixture
def lite():
    engine = create_engine("sqlite://")
    with Session(engine) as s:
        yield s


def _one(s, expr):
    return s.execute(select(expr)).scalar()


def test_sqlite_dates_and_durations(lite):
    day = literal("2026-08-13")  # a Thursday
    assert [_one(lite, d.date_bucket(u, day)) for u in ("day", "week", "month", "quarter", "year")] == [
        dt.date(2026, 8, 13), dt.date(2026, 8, 10), dt.date(2026, 8, 1), dt.date(2026, 7, 1), dt.date(2026, 1, 1)]
    assert _one(lite, d.seconds_between(literal("2026-08-13 12:00:00"), literal("2026-08-13 10:30:00"))) == pytest.approx(5400, abs=0.01)


@pytest.mark.parametrize("value,numeric,iso", [
    ("42", True, False), (" -3.50 ", True, False), ("1.2.3", False, False), ("12-3", False, False), ("abc", False, False),
    ("", False, False), ("2026-01-31", False, True), ("2026-01-31T10:00", False, True), ("31/01/2026", False, False),
])
def test_sqlite_value_checks(lite, value, numeric, iso):
    assert bool(_one(lite, d.looks_numeric(literal(value)))) is numeric
    assert bool(_one(lite, d.looks_iso_date(literal(value)))) is iso


def test_sqlite_strings_and_json(lite):
    assert _one(lite, d.concat_words(literal("Ada"), literal(None, String), literal("Lovelace"))) == "Ada Lovelace"
    assert _one(lite, d.concat_words(literal(None, String), literal("Lovelace"))) == "Lovelace"
    assert _one(lite, d.concat_words(literal("Ada"), literal("Lovelace"))) == "Ada Lovelace"
    t = Table("docs", MetaData(), Column("id", Integer, primary_key=True), Column("tags", JSON))
    t.create(lite.get_bind())
    lite.execute(t.insert(), [{"id": 1, "tags": ["acme.com", "acme.io"]}, {"id": 2, "tags": []}, {"id": 3, "tags": ["acme.com.au"]}])
    assert lite.execute(select(t.c.id).where(d.json_array_has(t.c.tags, "acme.com"))).scalars().all() == [1]


def test_sqlite_full_text_and_trigrams(lite):
    t = Table("kb", MetaData(), Column("id", Integer, primary_key=True), Column("title", String), Column("body", String))
    t.create(lite.get_bind())
    lite.execute(t.insert(), [{"id": 1, "title": "Reset your password", "body": "Use the sign-in page"},
                              {"id": 2, "title": "Invoices", "body": "Download a PDF invoice"}])
    cond, rank = d.full_text(lite, "password sign", [t.c.title, t.c.body])
    assert lite.execute(select(t.c.id).where(cond)).scalars().all() == [1]
    cond, rank = d.full_text(lite, "password OR invoice", [t.c.title, t.c.body], any_word=True)
    assert lite.execute(select(t.c.id).where(cond).order_by(rank.desc(), t.c.id)).scalars().all() == [1, 2]
    assert d.trigram_similarity("Acme Corp", "ACME Corporation") > 0.35 > d.trigram_similarity("Acme", "Globex")


# ---- counters, locks and insert-ignore on the reference database -------------------------------------------------

async def test_next_number_never_hands_out_the_same_number_twice(database):
    key = f"test:{uuid.uuid4().hex[:8]}"

    async def take():
        async with SessionLocal() as db:
            n = await d.next_number(db, key, start=1001)
            await asyncio.sleep(0.01)  # hold the row lock a moment so the others queue behind it
            await db.commit()
            return n

    numbers = await asyncio.gather(*(take() for _ in range(12)))
    assert sorted(numbers) == list(range(1001, 1013))


async def test_try_lock_admits_one_holder_at_a_time(database, monkeypatch):
    for portable in (False, True):  # the Postgres advisory lock, then the row-lock path other databases use
        if portable:
            monkeypatch.setattr(d, "dialect_of", lambda db: "mysql")
        name = f"test-{uuid.uuid4().hex[:8]}"
        async with SessionLocal() as a, SessionLocal() as b:
            assert await d.try_lock(a, name) is True
            assert await d.try_lock(b, name) is False
            await a.commit()
            assert await d.try_lock(b, name) is True
            await b.commit()


async def test_insert_ignore_skips_duplicates_on_every_path(seeded, monkeypatch):
    async with SessionLocal() as db:
        sub = WebhookSubscription(name="portability", url="https://example.com/hook", secret_enc="x", active=False)
        events = [IntegrationEvent(event_type="test", entity_type="test", payload={}, targets=["erp"]) for _ in range(3)]
        db.add_all([sub, *events])
        await db.flush()
        ids = [e.id for e in events]
        rows = [{"subscription_id": sub.id, "event_id": i, "event_type": "test", "next_attempt_at": dt.datetime.now(dt.timezone.utc)} for i in ids[:2]]
        assert await d.insert_ignore(db, WebhookDelivery.__table__, rows, ["subscription_id", "event_id"]) == 2
        assert await d.insert_ignore(db, WebhookDelivery.__table__, rows, ["subscription_id", "event_id"]) == 0
        monkeypatch.setattr(d, "dialect_of", lambda db: "oracle")  # the savepoint-per-row path
        more = rows + [{**rows[0], "event_id": ids[2]}]
        assert await d.insert_ignore(db, WebhookDelivery.__table__, more, ["subscription_id", "event_id"]) == 1
        await db.rollback()
    async with SessionLocal() as db:
        assert (await db.execute(select(NumberSequence).where(NumberSequence.name == "x-never"))).first() is None
        assert (await db.execute(text("SELECT count(*) FROM pg_class WHERE relname = 'case_number_seq'"))).scalar() == 0
