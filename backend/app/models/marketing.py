"""Marketing campaigns and their members (see services/campaigns.py)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import JSONB, UTCDateTime, UUID

__all__ = ["Campaign", "CampaignMember", "BehaviorEvent", "Segment", "SegmentMember"]


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
    last_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # the email send runs in the background: queued -> sending -> done | failed, with its outcome
    send_status: Mapped[str | None] = mapped_column(String(10))
    send_result: Mapped[dict | None] = mapped_column(JSONB)
    send_requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    send_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now())


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
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    responded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class BehaviorEvent(Base):
    """One thing a person did: a page view, form submit, email open or click, or a product-usage event sent over the
    API (services/cdp.py). Anonymous visits carry only ``anonymous_id`` until the visitor identifies; then every
    earlier event of that visitor is linked to the lead or contact."""
    __tablename__ = "behavior_events"
    __table_args__ = (Index("ix_behavior_events_contact", "contact_id", "occurred_at"),
                      Index("ix_behavior_events_lead", "lead_id", "occurred_at"),
                      Index("ix_behavior_events_anon", "anonymous_id"),
                      Index("ix_behavior_events_event", "event", "occurred_at"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event: Mapped[str] = mapped_column(String(60))
    anonymous_id: Mapped[str | None] = mapped_column(String(64))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    url: Mapped[str | None] = mapped_column(String(1000))
    properties: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    source: Mapped[str] = mapped_column(String(20), default="web", server_default="web")  # web | email | api | form
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)


class Segment(Base):
    """A dynamic audience: contacts or leads matching attribute and behaviour rules, recomputed on demand and by
    the ``segments`` job, which records who entered and left (services/cdp.py)."""
    __tablename__ = "segments"
    __table_args__ = (CheckConstraint("object IN ('contact', 'lead')", name="ck_segments_object"),)

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(500))
    object: Mapped[str] = mapped_column(String(10), default="contact")
    rules: Mapped[dict] = mapped_column(JSONB, default=dict)
    member_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    refreshed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)


class SegmentMember(Base):
    __tablename__ = "segment_members"

    segment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("segments.id", ondelete="CASCADE"), primary_key=True)
    record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)  # the contact or lead
    entered_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
