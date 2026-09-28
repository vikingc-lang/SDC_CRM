"""Database-specific SQL in one place.

Services use these constructs instead of Postgres-only syntax, so every query has a path on other databases.
Each construct compiles to the native SQL of the database running it (SQLAlchemy compiler extensions), with
PostgreSQL as the reference implementation - its SQL is exactly what Cirra has always emitted.

| Construct / helper            | PostgreSQL                         | Elsewhere                                        |
|-------------------------------|------------------------------------|--------------------------------------------------|
| ``date_bucket(unit, expr)``   | ``date_trunc(unit, x)::date``      | MySQL ``DATE_FORMAT``/``MAKEDATE``, SQL Server ``DATETRUNC`` (2022+), Oracle ``TRUNC``, SQLite ``date()`` |
| ``seconds_between(a, b)``     | ``EXTRACT(EPOCH FROM a - b)``      | ``TIMESTAMPDIFF``, ``DATEDIFF_BIG``, day arithmetic |
| ``looks_numeric`` / ``looks_iso_date`` | ``~`` regex               | ``REGEXP`` / ``REGEXP_LIKE`` / ``TRY_CAST`` / ``GLOB`` |
| ``concat_words(*exprs)``      | ``concat_ws(' ', ...)``            | ``||`` or ``CONCAT`` with ``COALESCE``           |
| ``json_array_has(col, v)``    | ``col @> '["v"]'``                 | ``JSON_CONTAINS`` / ``json_each`` / ``OPENJSON`` |
| ``insert_ignore(...)``        | ``ON CONFLICT DO NOTHING``         | ``INSERT IGNORE`` / ``OR IGNORE`` / per-row      |
| ``try_lock(db, name)``        | ``pg_try_advisory_xact_lock``      | a SKIP LOCKED row lock on ``number_sequences``   |
| ``next_number(db, key)``      | row-locked counter (all databases) | same                                             |
| ``nulls_last(x, descending)`` | ``x DESC NULLS LAST``              | ``CASE WHEN x IS NULL ...`` key on MySQL / SQL Server |
| full-text / trigram / vector  | ``tsvector``, ``pg_trgm``, pgvector | ``is_postgres(db)`` lets callers fall back to LIKE |
"""
from __future__ import annotations

from sqlalchemy import Date, String, Text, and_, case, func, literal, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.elements import Label
from sqlalchemy.sql.expression import FunctionElement
from sqlalchemy.sql.visitors import InternalTraversal
from sqlalchemy.types import Boolean, Float


def _arg(el):
    return list(el.clauses)[0]


def dialect_of(db: AsyncSession) -> str:
    return db.get_bind().dialect.name


def is_postgres(db: AsyncSession) -> bool:
    return dialect_of(db) == "postgresql"


# ---- date and time ---------------------------------------------------------------------------------------------

class date_bucket(FunctionElement):  # noqa: N801 - reads like an SQL function
    """The first day of the day / week / month / quarter / year containing ``expr`` (a date)."""
    type = Date()
    inherit_cache = True
    _traverse_internals = FunctionElement._traverse_internals + [("unit", InternalTraversal.dp_string)]

    def __init__(self, unit: str, expr):
        if unit not in ("day", "week", "month", "quarter", "year"):
            raise ValueError(f"unknown date bucket {unit!r}")
        self.unit = unit
        super().__init__(expr)


@compiles(date_bucket)
def _date_bucket_default(el, compiler, **kw):  # SQL Server 2022+, standard DATETRUNC
    return f"CAST(DATETRUNC({el.unit}, {compiler.process(list(el.clauses)[0], **kw)}) AS DATE)"


@compiles(date_bucket, "postgresql")
def _date_bucket_pg(el, compiler, **kw):
    return f"CAST(date_trunc('{el.unit}', {compiler.process(list(el.clauses)[0], **kw)}) AS DATE)"


@compiles(date_bucket, "mysql")
@compiles(date_bucket, "mariadb")
def _date_bucket_mysql(el, compiler, **kw):
    x = compiler.process(list(el.clauses)[0], **kw)
    return {
        "day": f"DATE({x})",
        "week": f"DATE(DATE_SUB({x}, INTERVAL WEEKDAY({x}) DAY))",
        "month": f"DATE(DATE_FORMAT({x}, '%%Y-%%m-01'))",
        "quarter": f"MAKEDATE(YEAR({x}), 1) + INTERVAL (QUARTER({x}) - 1) QUARTER",
        "year": f"MAKEDATE(YEAR({x}), 1)",
    }[el.unit]


@compiles(date_bucket, "sqlite")
def _date_bucket_sqlite(el, compiler, **kw):
    x = compiler.process(list(el.clauses)[0], **kw)
    return {
        "day": f"date({x})",
        "week": f"date({x}, '-' || ((CAST(strftime('%w', {x}) AS INTEGER) + 6) % 7) || ' days')",
        "month": f"date({x}, 'start of month')",
        "quarter": f"date({x}, 'start of month', '-' || ((CAST(strftime('%m', {x}) AS INTEGER) - 1) % 3) || ' months')",
        "year": f"date({x}, 'start of year')",
    }[el.unit]


@compiles(date_bucket, "oracle")
def _date_bucket_oracle(el, compiler, **kw):
    x = compiler.process(list(el.clauses)[0], **kw)
    return f"TRUNC({x}, '{ {'day': 'DD', 'week': 'IW', 'month': 'MM', 'quarter': 'Q', 'year': 'YYYY'}[el.unit] }')"


class seconds_between(FunctionElement):  # noqa: N801
    """Seconds from ``start`` to ``end`` (both timestamps); NULL if either is NULL."""
    type = Float()
    inherit_cache = True

    def __init__(self, end, start):
        super().__init__(end, start)


def _pair(el, compiler, **kw):
    end, start = list(el.clauses)
    return compiler.process(end, **kw), compiler.process(start, **kw)


@compiles(seconds_between)
def _secs_default(el, compiler, **kw):  # SQL Server
    end, start = _pair(el, compiler, **kw)
    return f"DATEDIFF_BIG(second, {start}, {end})"


@compiles(seconds_between, "postgresql")
def _secs_pg(el, compiler, **kw):
    end, start = _pair(el, compiler, **kw)
    return f"EXTRACT(EPOCH FROM {end} - {start})"


@compiles(seconds_between, "mysql")
@compiles(seconds_between, "mariadb")
def _secs_mysql(el, compiler, **kw):
    end, start = _pair(el, compiler, **kw)
    return f"TIMESTAMPDIFF(SECOND, {start}, {end})"


@compiles(seconds_between, "sqlite")
def _secs_sqlite(el, compiler, **kw):
    end, start = _pair(el, compiler, **kw)
    return f"((julianday({end}) - julianday({start})) * 86400.0)"


@compiles(seconds_between, "oracle")
def _secs_oracle(el, compiler, **kw):
    end, start = _pair(el, compiler, **kw)
    return f"((CAST({end} AS DATE) - CAST({start} AS DATE)) * 86400)"


# ---- text checks used to type schemaless (custom-field) values ---------------------------------------------------

class looks_numeric(FunctionElement):  # noqa: N801
    """True when a text value is a plain decimal number (so it can be cast safely)."""
    type = Boolean()
    inherit_cache = True


class looks_iso_date(FunctionElement):  # noqa: N801
    """True when a text value starts with an ISO date (YYYY-MM-DD)."""
    type = Boolean()
    inherit_cache = True


NUMBER_RE = r"^\s*-?[0-9]+(\.[0-9]+)?\s*$"
DATE_RE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}"


def _regex(compiler, expr, pattern, op, **kw):
    return f"({compiler.process(expr, **kw)} {op} {compiler.process(literal(pattern), **kw)})"


@compiles(looks_numeric, "postgresql")
def _num_pg(el, compiler, **kw):
    return _regex(compiler, _arg(el), NUMBER_RE, "~", **kw)


@compiles(looks_iso_date, "postgresql")
def _date_pg(el, compiler, **kw):
    return _regex(compiler, _arg(el), DATE_RE, "~", **kw)


@compiles(looks_numeric, "mysql")
@compiles(looks_numeric, "mariadb")
def _num_mysql(el, compiler, **kw):
    return _regex(compiler, _arg(el), NUMBER_RE, "REGEXP", **kw)


@compiles(looks_iso_date, "mysql")
@compiles(looks_iso_date, "mariadb")
def _date_mysql(el, compiler, **kw):
    return _regex(compiler, _arg(el), DATE_RE, "REGEXP", **kw)


@compiles(looks_numeric, "oracle")
def _num_oracle(el, compiler, **kw):
    return f"REGEXP_LIKE({compiler.process(_arg(el), **kw)}, {compiler.process(literal(NUMBER_RE), **kw)})"


@compiles(looks_iso_date, "oracle")
def _date_oracle(el, compiler, **kw):
    return f"REGEXP_LIKE({compiler.process(_arg(el), **kw)}, {compiler.process(literal(DATE_RE), **kw)})"


@compiles(looks_numeric)
def _num_default(el, compiler, **kw):  # SQL Server
    return f"(TRY_CAST({compiler.process(_arg(el), **kw)} AS DECIMAL(38, 10)) IS NOT NULL)"


@compiles(looks_iso_date)
def _date_default(el, compiler, **kw):  # SQL Server
    return f"({compiler.process(_arg(el), **kw)} LIKE '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]%')"


@compiles(looks_numeric, "sqlite")
def _num_sqlite(el, compiler, **kw):
    x = compiler.process(_arg(el), **kw)
    return (f"(trim({x}) <> '' AND trim({x}) NOT GLOB '*[^0-9.-]*' AND trim({x}) GLOB '*[0-9]*' "
            f"AND trim({x}) NOT GLOB '*.*.*' AND trim({x}) NOT GLOB '?*-*')")


@compiles(looks_iso_date, "sqlite")
def _date_sqlite(el, compiler, **kw):
    return f"({compiler.process(_arg(el), **kw)} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*')"


# ---- strings ------------------------------------------------------------------------------------------------------

class concat_words(FunctionElement):  # noqa: N801
    """Non-null values joined with single spaces (``concat_ws(' ', ...)``)."""
    type = String()
    inherit_cache = True


@compiles(concat_words)
def _concat_ws(el, compiler, **kw):  # PostgreSQL, MySQL, MariaDB, SQL Server 2017+
    return f"concat_ws(' ', {compiler.process(el.clauses, **kw)})"


@compiles(concat_words, "sqlite")
def _concat_sqlite(el, compiler, **kw):  # ' ' || NULL is NULL, so NULLs drop out with their separator
    return "SUBSTR(" + " || ".join(f"COALESCE(' ' || {compiler.process(c, **kw)}, '')" for c in el.clauses) + ", 2)"


@compiles(concat_words, "oracle")
def _concat_oracle(el, compiler, **kw):  # Oracle concatenates NULL as ''
    return "SUBSTR(" + " || ".join(f"NVL2({compiler.process(c, **kw)}, ' ' || {compiler.process(c, **kw)}, NULL)" for c in el.clauses) + ", 2)"


# ---- JSON ---------------------------------------------------------------------------------------------------------

class json_array_has(FunctionElement):  # noqa: N801
    """True when a JSON array column contains the string ``value``."""
    type = Boolean()
    inherit_cache = True

    def __init__(self, column, value: str):
        super().__init__(column, literal(str(value), String()))


def _col_val(el, compiler, **kw):
    column, value = list(el.clauses)
    return compiler.process(column, **kw), compiler.process(value, **kw)


@compiles(json_array_has, "postgresql")
def _json_has_pg(el, compiler, **kw):
    col, val = _col_val(el, compiler, **kw)
    return f"({col} @> jsonb_build_array(CAST({val} AS TEXT)))"


@compiles(json_array_has, "mysql")
@compiles(json_array_has, "mariadb")
def _json_has_mysql(el, compiler, **kw):
    col, val = _col_val(el, compiler, **kw)
    return f"JSON_CONTAINS({col}, JSON_QUOTE({val}))"


@compiles(json_array_has, "sqlite")
def _json_has_sqlite(el, compiler, **kw):
    col, val = _col_val(el, compiler, **kw)
    return f"EXISTS (SELECT 1 FROM json_each({col}) WHERE json_each.value = {val})"


@compiles(json_array_has)
def _json_has_default(el, compiler, **kw):  # SQL Server
    col, val = _col_val(el, compiler, **kw)
    return f"EXISTS (SELECT 1 FROM OPENJSON({col}) WHERE value = {val})"


@compiles(json_array_has, "oracle")
def _json_has_oracle(el, compiler, **kw):
    col, val = _col_val(el, compiler, **kw)
    return f"JSON_EXISTS({col}, '$[*]?(@ == $v)' PASSING {val} AS \"v\")"


# ---- ordering ----------------------------------------------------------------------------------------------------

class nulls_last(FunctionElement):  # noqa: N801
    """``ORDER BY expr [DESC] NULLS LAST``; MySQL and SQL Server get the equivalent ``CASE WHEN expr IS NULL`` key."""
    inherit_cache = True
    _nulls = "LAST"
    _traverse_internals = FunctionElement._traverse_internals + [("descending", InternalTraversal.dp_boolean)]

    def __init__(self, expr, descending: bool = False):
        self.descending = descending
        super().__init__(expr)


class nulls_first(nulls_last):  # noqa: N801
    """``ORDER BY expr [DESC] NULLS FIRST``."""
    inherit_cache = True
    _nulls = "FIRST"


def _sort_key(el, compiler, **kw) -> str:
    """A labelled column sorts by its label (``ORDER BY total``), as SQLAlchemy renders a plain ``.desc()``."""
    arg = _arg(el)
    if isinstance(arg, Label):
        return compiler.process(arg, render_label_as_label=arg, **kw)
    return compiler.process(arg, **kw)


@compiles(nulls_last)
def _nulls_standard(el, compiler, **kw):  # PostgreSQL, Oracle, SQLite 3.30+
    return f"{_sort_key(el, compiler, **kw)}{' DESC' if el.descending else ''} NULLS {el._nulls}"


@compiles(nulls_last, "mysql")
@compiles(nulls_last, "mariadb")
@compiles(nulls_last, "mssql")
def _nulls_emulated(el, compiler, **kw):  # SQL Server won't take a column alias inside an expression
    arg = _arg(el)
    x = compiler.process(arg.element if isinstance(arg, Label) else arg, **kw)
    first, last = (0, 1) if el._nulls == "LAST" else (1, 0)
    return f"CASE WHEN {x} IS NULL THEN {last} ELSE {first} END, {x}{' DESC' if el.descending else ''}"


# ---- search ------------------------------------------------------------------------------------------------------

def search_words(query: str) -> list[str]:
    return [w for w in "".join(ch if ch.isalnum() else " " for ch in (query or "")).split() if len(w) > 1]


def full_text(db: AsyncSession, query: str, fields, *, vector=None, any_word: bool = False):
    """``(condition, rank)`` for a keyword search over ``fields``.

    PostgreSQL: ``websearch_to_tsquery`` against ``vector`` (a stored tsvector) or ``to_tsvector`` of the fields,
    ranked by ``ts_rank``. Elsewhere: every word (or any word) must appear in one of the fields (case-insensitive
    LIKE), ranked by how many words matched."""
    if is_postgres(db):
        tsq = func.websearch_to_tsquery("english", query)
        doc = vector if vector is not None else func.to_tsvector("english", concat_words(*fields))
        return doc.op("@@")(tsq), func.ts_rank(doc, tsq)
    words = [w for w in search_words(query) if w.upper() != "OR"]
    if not words:
        return literal(False), literal(0)
    hits = [or_(*[func.lower(f).like(f"%{w.lower()}%") for f in fields]) for w in words]
    rank = sum((case((h, 1), else_=0) for h in hits), literal(0))
    return (or_(*hits) if any_word else and_(*hits)), rank


def json_without_keys(db: AsyncSession, column, keys):
    """The JSON object ``column`` minus ``keys`` (PostgreSQL ``jsonb - text[]``); None where that isn't available,
    so callers leave the document out rather than expose hidden keys."""
    if not keys:
        return column
    if is_postgres(db):
        from sqlalchemy.dialects.postgresql import ARRAY

        return column.op("-")(literal(list(keys), ARRAY(Text)))
    return None


def trigrams(value: str) -> set[str]:
    """pg_trgm's trigram set: lower-cased words padded with two leading spaces and one trailing space."""
    out: set[str] = set()
    for word in "".join(ch if ch.isalnum() else " " for ch in (value or "").lower()).split():
        padded = f"  {word} "
        out.update(padded[i:i + 3] for i in range(len(padded) - 2))
    return out


def trigram_similarity(a: str, b: str) -> float:
    """Same measure as pg_trgm ``similarity()``: shared trigrams / all distinct trigrams."""
    ta, tb = trigrams(a), trigrams(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


# ---- writes, locks and numbering ----------------------------------------------------------------------------------

async def insert_ignore(db: AsyncSession, table, rows: list[dict], keys: list[str]) -> int:
    """Insert rows, silently skipping any that collide on the unique ``keys``. Returns how many were inserted."""
    if not rows:
        return 0
    name = dialect_of(db)
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
        res = await db.execute(insert(table).values(rows).on_conflict_do_nothing(index_elements=keys))
        return max(res.rowcount or 0, 0)
    if name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
        res = await db.execute(insert(table).values(rows).on_conflict_do_nothing(index_elements=keys))
        return max(res.rowcount or 0, 0)
    if name in ("mysql", "mariadb"):
        from sqlalchemy import insert
        res = await db.execute(insert(table).prefix_with("IGNORE").values(rows))
        return max(res.rowcount or 0, 0)
    inserted = 0  # SQL Server, Oracle, others: one savepoint per row
    from sqlalchemy import insert
    for row in rows:
        try:
            async with db.begin_nested():
                await db.execute(insert(table).values(**row))
            inserted += 1
        except IntegrityError:
            pass
    return inserted


async def _counter(db: AsyncSession, key: str, start: int = 1):
    from app.models import NumberSequence

    stmt = select(NumberSequence).where(NumberSequence.name == key).with_for_update()
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        try:
            async with db.begin_nested():
                db.add(NumberSequence(name=key, value=start - 1))
                await db.flush()
        except IntegrityError:
            pass  # created concurrently
        row = (await db.execute(stmt)).scalar_one_or_none()
    return row


async def next_number(db: AsyncSession, key: str, start: int = 1) -> int:
    """The next value of a named counter (case numbers, quote numbers per year, ...), ``start`` for a new counter.
    The counter row stays locked until the transaction ends, so concurrent creators get distinct numbers on every
    database."""
    row = await _counter(db, key, start=start)
    row.value += 1
    await db.flush()
    return int(row.value)


async def try_lock(db: AsyncSession, name: str) -> bool:
    """A transaction-scoped lock that doesn't wait: True if this transaction now holds ``name``.

    PostgreSQL uses an advisory lock. Other databases lock a ``lock:<name>`` row in ``number_sequences`` with
    SKIP LOCKED; the row is created once in its own short transaction, so a second caller skips instead of
    waiting on the first one's uncommitted insert. SQLite has a single writer, so overlapping runs serialise anyway."""
    name_ = dialect_of(db)
    if name_ == "postgresql":
        return bool((await db.execute(text("SELECT pg_try_advisory_xact_lock(hashtext(:n))"), {"n": name})).scalar())
    if name_ == "sqlite":
        return True
    from app.models import NumberSequence

    key = f"lock:{name}"
    try:
        async with db.bind.begin() as conn:
            if not (await conn.execute(select(NumberSequence.name).where(NumberSequence.name == key))).first():
                await conn.execute(NumberSequence.__table__.insert().values(name=key, value=0))
    except IntegrityError:
        pass  # created concurrently
    stmt = select(NumberSequence.name).where(NumberSequence.name == key).with_for_update(skip_locked=True)
    return (await db.execute(stmt)).first() is not None


async def delete_all_rows(db: AsyncSession, metadata) -> None:
    """Empty every table (demo reset), children first."""
    from sqlalchemy import delete

    for table in reversed(metadata.sorted_tables):
        await db.execute(delete(table))


__all__ = ["nulls_last", "nulls_first", "full_text", "search_words", "json_without_keys", "trigrams", "trigram_similarity", "date_bucket", "seconds_between", "looks_numeric", "looks_iso_date", "concat_words", "json_array_has",
           "insert_ignore", "next_number", "try_lock", "delete_all_rows", "dialect_of", "is_postgres"]
