"""Post-sale: onboarding workspaces, support and adoption signals, invoices (pillars 7, 8)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

__all__ = ["OnboardingProject", "OnboardingMilestone", "SupportTicket", "ProductUsage", "Invoice"]


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

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
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[OnboardingProject] = relationship(back_populates="milestones")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[uuid.UUID] = _pk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    external_id: Mapped[str | None] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), default="open")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
