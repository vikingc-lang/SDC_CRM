"""Control plane for multi-tenancy (core/tenancy.py): the registry of tenant workspaces. It lives in the primary
database only; each tenant's own records live in that tenant's database."""
from datetime import datetime

from sqlalchemy import CheckConstraint, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import JSONB, UTCDateTime

__all__ = ["Tenant"]


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (CheckConstraint("status IN ('active', 'suspended')", name="ck_tenants_status"),)

    slug: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    db_name: Mapped[str] = mapped_column(String(63), unique=True)
    hosts: Mapped[list] = mapped_column(JSONB, default=list)  # host names that route to this tenant
    status: Mapped[str] = mapped_column(String(10), default="active", server_default="active")
    max_users: Mapped[int | None] = mapped_column(Integer)  # plan limit on active users; None = unlimited
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
