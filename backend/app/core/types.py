"""Portable column types: exactly the Postgres storage Cirra has always used, standard types elsewhere.

Models import their special types from here instead of ``sqlalchemy.dialects.postgresql``, so the same models
create a working schema on any SQLAlchemy-supported database (see docs/database-portability.md):

| Type            | PostgreSQL (reference)           | Other databases                                   |
|-----------------|----------------------------------|---------------------------------------------------|
| ``UUID``        | ``uuid``                         | ``CHAR(32)`` / native UUID where one exists        |
| ``JSONB``       | ``jsonb``                        | ``JSON`` (MySQL, SQLite) / ``NVARCHAR(max)`` (SQL Server) / ``CLOB`` (Oracle) |
| ``Embedding``   | pgvector ``vector(n)``           | ``JSON`` array of floats (similarity search is Postgres-only) |
| ``SearchVector``| ``tsvector`` (generated column)  | not created: full-text search falls back to LIKE   |
| ``UTCDateTime`` | ``timestamptz``                  | ``DATETIMEOFFSET`` / ``TIMESTAMP WITH TIME ZONE``; naive UTC ``DATETIME(6)`` on MySQL and SQLite, read back as aware UTC |

JSON columns expose SQLAlchemy's *generic* JSON operators (``col["key"].as_string()``). Postgres-only operators
(``@>``, ``?``, ``-``, ``->>`` via ``.astext``) must go through ``app.core.dialect`` so every database has a path.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Text, Uuid
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import CreateColumn
from sqlalchemy.types import TypeDecorator

# ``UUID(as_uuid=True)`` keeps working: the generic Uuid is native ``uuid`` on Postgres.
UUID = Uuid
POSTGRES_ONLY = "postgres_only"  # Column.info flag: the column exists only on PostgreSQL


NAIVE_TIME_DIALECTS = ("sqlite", "mysql", "mariadb")  # no time zone in their timestamp types


class UTCDateTime(TypeDecorator):
    """A moment in time, always handled as timezone-aware UTC in Python. PostgreSQL, SQL Server and Oracle store
    the offset (``timestamptz`` / ``DATETIMEOFFSET`` / ``TIMESTAMP WITH TIME ZONE``); MySQL and SQLite store naive
    UTC, converted on the way in and marked UTC on the way out, so comparisons with ``datetime.now(timezone.utc)``
    keep working."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "oracle":  # the generic type would be DATE (no fractions, no zone)
            from sqlalchemy.dialects import oracle

            return dialect.type_descriptor(oracle.TIMESTAMP(timezone=True))
        if dialect.name in ("mysql", "mariadb"):  # keep microseconds
            from sqlalchemy.dialects import mysql

            return dialect.type_descriptor(mysql.DATETIME(fsp=6))
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value, dialect):
        if isinstance(value, datetime) and value.tzinfo is not None and dialect.name in NAIVE_TIME_DIALECTS:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if isinstance(value, datetime) and value.tzinfo is None and dialect.name in NAIVE_TIME_DIALECTS:
            return value.replace(tzinfo=timezone.utc)
        return value


class _JsonText:
    """Oracle: SQLAlchemy 2.0 has no generic JSON type there, so documents are stored as JSON text in a CLOB."""

    def _oracle(self, dialect) -> bool:
        return dialect.name == "oracle"

    def process_bind_param(self, value, dialect):
        return json.dumps(value) if self._oracle(dialect) and value is not None else value

    def process_result_value(self, value, dialect):
        return json.loads(value) if self._oracle(dialect) and isinstance(value, str) else value


class JSONB(_JsonText, TypeDecorator):
    """JSON document: ``jsonb`` on Postgres, the database's JSON type elsewhere."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.JSONB())
        if dialect.name == "oracle":
            return dialect.type_descriptor(Text())
        return dialect.type_descriptor(JSON())

    @property
    def comparator_factory(self):  # generic JSON indexing: col["key"].as_string(), .as_integer(), ...
        return JSON.Comparator


class Embedding(_JsonText, TypeDecorator):
    """A semantic embedding: pgvector ``vector(n)`` on Postgres, a JSON array of floats elsewhere."""

    impl = JSON
    cache_ok = True

    def __init__(self, dim: int):
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(Text() if dialect.name == "oracle" else JSON())

    def process_bind_param(self, value, dialect):
        if value is None or dialect.name == "postgresql":
            return value
        return super().process_bind_param([float(x) for x in value], dialect)  # numpy arrays / lists -> plain JSON

    @property
    def comparator_factory(self):  # keeps .cosine_distance() etc. for the Postgres-only query paths
        from pgvector.sqlalchemy import Vector

        return Vector.comparator_factory


class SearchVector(TypeDecorator):
    """Postgres full-text vector, maintained by the database as a generated column. Not created elsewhere."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.TSVECTOR())
        return dialect.type_descriptor(Text())


@compiles(CreateColumn)
def _skip_postgres_only_columns(element, compiler, **kw):
    """Leave Postgres-only columns (generated tsvector, ...) out of CREATE TABLE on other databases."""
    column = element.element
    if column.info.get(POSTGRES_ONLY) and compiler.dialect.name != "postgresql":
        return None
    return compiler.visit_create_column(element, **kw)
