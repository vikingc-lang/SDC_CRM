"""Async SQLAlchemy engine and session factory."""
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.pool import NullPool

from app.core.config import settings

engine = (
    create_async_engine(settings.database_url, poolclass=NullPool)
    if settings.db_null_pool
    else create_async_engine(settings.database_url, pool_pre_ping=True)
)
class CirraSession(AsyncSession):
    """Checks admin validation rules before committing changes a person made (sessions from ``get_db``).
    Background jobs and automation open sessions without the flag and act as the system."""

    async def commit(self) -> None:
        if self.info.get("validate"):
            from app.services import validation

            await self.flush()
            await validation.check(self)
        await super().commit()


class RoutingSession(Session):
    """Binds every statement to the current tenant's database (core/tenancy.py); the primary one by default."""

    def get_bind(self, mapper=None, clause=None, **kw):
        from app.core import tenancy

        return tenancy.sync_bind()


SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=CirraSession, sync_session_class=RoutingSession)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        session.info["validate"] = True
        yield session
