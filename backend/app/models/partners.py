"""Partner relationship management (pillar 9)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

__all__ = ["Partner", "DealRegistration", "DealPartner", "Collateral", "CollateralDownload"]

TIER_ORDER = ["registered", "silver", "gold", "platinum"]


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Partner(Base):
    __tablename__ = "partners"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(255))
    partner_type: Mapped[str] = mapped_column(String(20))
    tier: Mapped[str] = mapped_column(String(20), default="registered")
    domains: Mapped[list] = mapped_column(JSONB, default=list)
    territories: Mapped[list] = mapped_column(JSONB, default=list)
    commission_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=10)
    referral_fee_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=5)
    status: Mapped[str] = mapped_column(String(10), default="active")
    created_at: Mapped[datetime] = _ts()


class DealRegistration(Base):
    __tablename__ = "deal_registrations"

    id: Mapped[uuid.UUID] = _pk()
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("partners.id", ondelete="CASCADE"))
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    company_name: Mapped[str] = mapped_column(String(255))
    domain: Mapped[str] = mapped_column(String(255))
    contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    estimated_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    territory: Mapped[str | None] = mapped_column(String(60))
    product_interest: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="submitted")
    exclusivity_expires_at: Mapped[date | None] = mapped_column(Date)
    conflicts: Mapped[list] = mapped_column(JSONB, default=list)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    deal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deals.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _ts()

    partner: Mapped[Partner] = relationship(lazy="joined")


class DealPartner(Base):
    __tablename__ = "deal_partners"

    id: Mapped[uuid.UUID] = _pk()
    deal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deals.id", ondelete="CASCADE"))
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("partners.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))
    split_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=100)
    commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    registration_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deal_registrations.id", ondelete="SET NULL"))

    partner: Mapped[Partner] = relationship(lazy="joined")
    deal = relationship("Deal", lazy="joined")


class Collateral(Base):
    __tablename__ = "collateral"

    id: Mapped[uuid.UUID] = _pk()
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(20))
    attachment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("attachments.id", ondelete="CASCADE"))
    min_tier: Mapped[str] = mapped_column(String(20), default="registered")
    allowed_domains: Mapped[list] = mapped_column(JSONB, default=list)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts()

    attachment = relationship("Attachment", lazy="joined")


class CollateralDownload(Base):
    __tablename__ = "collateral_downloads"

    id: Mapped[uuid.UUID] = _pk()
    collateral_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("collateral.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    partner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("partners.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _ts()
