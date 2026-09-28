"""Sales performance: territories, quotas and commission plans (see services/performance.py)."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import JSONB, UTCDateTime, UUID

__all__ = ["Territory", "Quota", "CommissionPlan"]


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Territory(Base):
    """criteria = {"regions": [], "countries": [], "industries": [], "tiers": [], "min_employees": int, "max_employees": int}"""
    __tablename__ = "territories"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("territories.id", ondelete="SET NULL"))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    member_ids: Mapped[list] = mapped_column(JSONB, default=list)
    criteria: Mapped[dict] = mapped_column(JSONB, default=dict)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class Quota(Base):
    __tablename__ = "quotas"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    period: Mapped[str] = mapped_column(String(7))
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    set_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now())


class CommissionPlan(Base):
    """Pays base_rate % of bookings up to quota; tiers = [{"from_pct": 100, "rate": 8.0}, ...] pay more above each threshold."""
    __tablename__ = "commission_plans"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    base_rate: Mapped[Decimal] = mapped_column(Numeric(6, 3))
    tiers: Mapped[list] = mapped_column(JSONB, default=list)
    roles: Mapped[list] = mapped_column(JSONB, default=list)
    member_ids: Mapped[list] = mapped_column(JSONB, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
