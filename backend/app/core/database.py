"""Async SQLAlchemy engine and session factory."""
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
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


SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=CirraSession)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        session.info["validate"] = True
        yield session
