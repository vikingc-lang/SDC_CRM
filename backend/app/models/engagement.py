"""Service and marketing depth: inbound support email, agent presence, nurture journeys and email tracking
(see services/email_to_case.py, services/routing.py, services/journeys.py and services/tracking.py)."""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import JSONB, UTCDateTime, UUID

__all__ = ["InboundEmail", "AgentPresence", "Journey", "JourneyEnrollment", "EmailSend", "EmailEvent"]


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class InboundEmail(Base):
    """Every message received on a support address and what became of it."""
    __tablename__ = "inbound_emails"

    id: Mapped[uuid.UUID] = _pk()
    message_id: Mapped[str] = mapped_column(String(500), unique=True)
    from_email: Mapped[str] = mapped_column(String(255))
    from_name: Mapped[str | None] = mapped_column(String(200))
    to_email: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12))  # case_created | appended | unmatched | ignored | converted | dismissed
    detail: Mapped[str | None] = mapped_column(String(300))
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_tickets.id", ondelete="SET NULL"))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class AgentPresence(Base):
    """A service agent's availability and how many open cases they take at once."""
    __tablename__ = "agent_presence"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(10), default="offline")  # available | busy | away | offline
    capacity: Mapped[int] = mapped_column(SmallInteger, default=5)
    last_assigned_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now())


class Journey(Base):
    """A multi-step nurture for a campaign's members: emails and waits, optionally conditional on engagement."""
    __tablename__ = "journeys"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | active | paused | archived
    steps: Mapped[list] = mapped_column(JSONB, default=list)
    sender_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now())


class JourneyEnrollment(Base):
    __tablename__ = "journey_enrollments"

    id: Mapped[uuid.UUID] = _pk()
    journey_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"))
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_members.id", ondelete="CASCADE"))
    step: Mapped[int] = mapped_column(Integer, default=0)
    next_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | completed | exited
    exit_reason: Mapped[str | None] = mapped_column(String(80))
    enrolled_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now())


class EmailSend(Base):
    """One tracked marketing email to one person (campaign blast or journey step)."""
    __tablename__ = "email_sends"

    id: Mapped[uuid.UUID] = _pk()
    token: Mapped[str] = mapped_column(String(40), unique=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    journey_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("journeys.id", ondelete="SET NULL"))
    step_index: Mapped[int | None] = mapped_column(Integer)
    member_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("campaign_members.id", ondelete="SET NULL"))
    enrollment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("journey_enrollments.id", ondelete="SET NULL"))
    email: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(300))
    message_id: Mapped[str | None] = mapped_column(String(500))
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    open_count: Mapped[int] = mapped_column(Integer, default=0)
    clicked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    click_count: Mapped[int] = mapped_column(Integer, default=0)


class EmailEvent(Base):
    __tablename__ = "email_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    send_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("email_sends.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(6))  # open | click
    url: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
