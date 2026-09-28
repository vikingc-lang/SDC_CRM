"""Post-sale: onboarding workspaces, support and adoption signals, invoices (pillars 7, 8)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Computed, Date, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.types import JSONB, POSTGRES_ONLY, SearchVector, UTCDateTime, UUID

__all__ = ["OnboardingProject", "OnboardingMilestone", "SupportTicket", "SupportQueue", "CaseComment", "KbArticle", "ProductUsage", "Invoice"]


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class OnboardingProject(Base):
    __tablename__ = "onboarding_projects"

    id: Mapped[uuid.UUID] = _pk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    deal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deals.id", ondelete="SET NULL"), unique=True)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="not_started")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kickoff_date: Mapped[date | None] = mapped_column(Date)
    target_go_live: Mapped[date | None] = mapped_column(Date)
    scope: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)

    milestones: Mapped[list["OnboardingMilestone"]] = relationship(back_populates="project", lazy="selectin", cascade="all, delete-orphan", order_by="OnboardingMilestone.position")
    account = relationship("Account", lazy="joined")
    owner = relationship("User", lazy="joined")


class OnboardingMilestone(Base):
    __tablename__ = "onboarding_milestones"

    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("onboarding_projects.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    position: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    project: Mapped[OnboardingProject] = relationship(back_populates="milestones")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[uuid.UUID] = _pk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    external_id: Mapped[str | None] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), default="open")
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # case management (services/cases.py)
    case_number: Mapped[str | None] = mapped_column(String(20), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    queue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_queues.id", ondelete="SET NULL"))
    channel: Mapped[str] = mapped_column(String(10), default="web")
    category: Mapped[str | None] = mapped_column(String(60))
    first_response_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    resolve_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    first_responded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sla_breached: Mapped[bool] = mapped_column(Boolean, default=False)
    breach_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    csat_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    csat_score: Mapped[int | None] = mapped_column(Integer)
    csat_comment: Mapped[str | None] = mapped_column(Text)
    supplied_email: Mapped[str | None] = mapped_column(String(255))  # email-to-case sender when no contact matched
    supplied_name: Mapped[str | None] = mapped_column(String(200))
    csat_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now())


class SupportQueue(Base):
    __tablename__ = "support_queues"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    member_ids: Mapped[list] = mapped_column(JSONB, default=list)
    auto_assign: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    email_address: Mapped[str | None] = mapped_column(String(255), unique=True)  # email-to-case: mail to this address opens cases here
    routing: Mapped[str] = mapped_column(String(12), default="least_loaded")  # least_loaded | presence (see services/routing.py)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class CaseComment(Base):
    """A public reply (visible to the customer) or an internal note on a case."""
    __tablename__ = "case_comments"

    id: Mapped[uuid.UUID] = _pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id", ondelete="CASCADE"))
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text)
    internal: Mapped[bool] = mapped_column(Boolean, default=False)
    message_id: Mapped[str | None] = mapped_column(String(500))  # email thread id (inbound message or the reply we sent)
    from_email: Mapped[str | None] = mapped_column(String(255))  # a customer's emailed reply (author_id is null)
    emailed: Mapped[bool] = mapped_column(Boolean, default=False)  # a public reply that went out by email
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class KbArticle(Base):
    __tablename__ = "kb_articles"

    id: Mapped[uuid.UUID] = _pk()
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(10), default="draft")
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    views: Mapped[int] = mapped_column(Integer, default=0)
    helpful: Mapped[int] = mapped_column(Integer, default=0)
    not_helpful: Mapped[int] = mapped_column(Integer, default=0)
    search_tsv = mapped_column(  # Postgres full-text vector; other databases search with LIKE
        SearchVector,
        Computed("setweight(to_tsvector('english', coalesce(title, '')), 'A') || setweight(to_tsvector('english', coalesce(body, '')), 'B')", persisted=True),
        deferred=True,
        info={POSTGRES_ONLY: True},
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now())


class ProductUsage(Base):
    __tablename__ = "product_usage"

    id: Mapped[uuid.UUID] = _pk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    metric_date: Mapped[date] = mapped_column(Date)
    active_users: Mapped[int] = mapped_column(Integer)
    licensed_users: Mapped[int] = mapped_column(Integer)
    feature_adoption: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = _pk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    erp_invoice_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    invoice_number: Mapped[str] = mapped_column(String(64))
    issue_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    balance: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    status: Mapped[str] = mapped_column(String(10), default="open")
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
