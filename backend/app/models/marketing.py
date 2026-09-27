"""Marketing campaigns and their members (see services/campaigns.py)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

__all__ = ["Campaign", "CampaignMember"]


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(150))
    code: Mapped[str] = mapped_column(String(80), unique=True)
    campaign_type: Mapped[str] = mapped_column(String(20), default="email")
    status: Mapped[str] = mapped_column(String(12), default="planned")
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    budget: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    actual_cost: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    expected_revenue: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    email_subject: Mapped[str | None] = mapped_column(String(200))
    email_body: Mapped[str | None] = mapped_column(Text)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CampaignMember(Base):
    """Exactly one of lead_id / contact_id is set."""
    __tablename__ = "campaign_members"

    id: Mapped[uuid.UUID] = _pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(12), default="targeted")
    source: Mapped[str] = mapped_column(String(10), default="manual")
    token: Mapped[str] = mapped_column(String(48), unique=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
