"""SQLAlchemy 2.0 declarative models for Cirra.

The physical schema is owned by alembic/versions/001_initial_schema.py; these
models mirror it one-to-one.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.core.database import Base

USER_ROLES = ("super_admin", "sales_manager", "account_executive", "sdr", "auditor", "partner")
ACCOUNT_TIERS = ("SMB", "Mid-Market", "Enterprise")
BUYING_ROLES = ("Champion", "Decision Maker", "Economic Buyer", "Blocker", "Evaluator", "Influencer")
ACTIVITY_TYPES = ("meeting", "call", "note", "email", "system", "file", "document")
SENTIMENTS = ("positive", "neutral", "negative")
LOSS_REASONS = ("competitor", "budget_frozen", "feature_gap", "champion_departed", "price", "no_decision", "timing", "other")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(f"role IN {USER_ROLES}", name="ck_users_role"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(150))
    role: Mapped[str] = mapped_column(String(50), default="account_executive")
    manager_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    partner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("partners.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    ical_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(f"tier IN {ACCOUNT_TIERS}", name="ck_accounts_tier"),
        CheckConstraint("health_score BETWEEN 0 AND 100", name="ck_accounts_health"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255))
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    industry: Mapped[str | None] = mapped_column(String(100))
    tier: Mapped[str] = mapped_column(String(50), default="Mid-Market")
    health_score: Mapped[int] = mapped_column(Integer, default=100)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    custom_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    # firmographics & hierarchy (pillar 1)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), index=True)
    industry_code: Mapped[str | None] = mapped_column(String(20))
    annual_revenue: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    employee_count: Mapped[int | None] = mapped_column(Integer)
    locations: Mapped[list] = mapped_column(JSONB, default=list)
    alt_domains: Mapped[list] = mapped_column(JSONB, default=list)
    lifecycle_stage: Mapped[str] = mapped_column(String(20), default="prospect")
    # customer master (pillar 8)
    legal_name: Mapped[str | None] = mapped_column(String(255))
    tax_id: Mapped[str | None] = mapped_column(String(64))
    billing_address: Mapped[dict] = mapped_column(JSONB, default=dict)
    credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    credit_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    payment_terms: Mapped[str] = mapped_column(String(20), default="NET30")
    erp_customer_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    erp_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # retention & relationship signals (pillars 2, 7)
    churn_risk: Mapped[int] = mapped_column(Integer, default=0)
    churn_factors: Mapped[dict] = mapped_column(JSONB, default=dict)
    relationship_strength: Mapped[int | None] = mapped_column(Integer)
    country: Mapped[str | None] = mapped_column(String(64))
    region: Mapped[str | None] = mapped_column(String(40))
    credit_risk_score: Mapped[int | None] = mapped_column(Integer)
    credit_risk_band: Mapped[str | None] = mapped_column(String(10))
    credit_risk_factors: Mapped[dict] = mapped_column(JSONB, default=dict)
    embedding = mapped_column(Vector(settings.embedding_dim), nullable=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owner: Mapped[User | None] = relationship(lazy="joined")
    contacts: Mapped[list["Contact"]] = relationship(back_populates="account", lazy="selectin", cascade="all, delete-orphan")


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (CheckConstraint(f"buying_role IN {BUYING_ROLES}", name="ck_contacts_buying_role"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    phone: Mapped[str | None] = mapped_column(String(50))
    job_title: Mapped[str | None] = mapped_column(String(150))
    buying_role: Mapped[str] = mapped_column(String(50), default="Evaluator")
    mobile: Mapped[str | None] = mapped_column(String(50))
    linkedin_url: Mapped[str | None] = mapped_column(String(255))
    timezone: Mapped[str | None] = mapped_column(String(64))
    department: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="active")
    departed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_email: Mapped[str] = mapped_column(String(20), default="unknown")
    consent_basis: Mapped[str | None] = mapped_column(String(40))
    privacy_regime: Mapped[str | None] = mapped_column(String(10))
    consent_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    do_not_sell: Mapped[bool] = mapped_column(Boolean, default=False)
    opt_out_email: Mapped[bool] = mapped_column(Boolean, default=False)
    opt_out_phone: Mapped[bool] = mapped_column(Boolean, default=False)
    opt_out_sms: Mapped[bool] = mapped_column(Boolean, default=False)
    relationship_strength: Mapped[int | None] = mapped_column(Integer)
    rsi_factors: Mapped[dict] = mapped_column(JSONB, default=dict)
    custom_fields: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    account: Mapped[Account] = relationship(back_populates="contacts")

    PII_FIELDS = ("first_name", "last_name", "email", "phone", "mobile", "linkedin_url")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(100))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(20), default="direct")
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()

    stages: Mapped[list["PipelineStage"]] = relationship(
        back_populates="pipeline", lazy="selectin", order_by="PipelineStage.stage_order"
    )


class PipelineStage(Base):
    __tablename__ = "pipeline_stages"
    __table_args__ = (
        UniqueConstraint("pipeline_id", "stage_order"),
        CheckConstraint("default_probability BETWEEN 0 AND 100", name="ck_stage_probability"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    pipeline_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipelines.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    stage_order: Mapped[int] = mapped_column(Integer)
    default_probability: Mapped[int] = mapped_column(Integer)
    is_closed_won: Mapped[bool] = mapped_column(Boolean, default=False)
    is_closed_lost: Mapped[bool] = mapped_column(Boolean, default=False)
    gate_rules: Mapped[list] = mapped_column(JSONB, default=list)

    pipeline: Mapped[Pipeline] = relationship(back_populates="stages")

    @property
    def is_closed(self) -> bool:
        return self.is_closed_won or self.is_closed_lost


class Deal(Base):
    __tablename__ = "deals"
    __table_args__ = (
        CheckConstraint("risk_score BETWEEN 0 AND 100", name="ck_deals_risk"),
        CheckConstraint(f"loss_reason IS NULL OR loss_reason IN {LOSS_REASONS}", name="ck_deals_loss_reason"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(String(255))
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), index=True)
    primary_contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    pipeline_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipelines.id", ondelete="RESTRICT"))
    stage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_stages.id", ondelete="RESTRICT"), index=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    target_close_date: Mapped[date | None] = mapped_column(Date)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    risk_factors: Mapped[dict] = mapped_column(JSONB, default=dict)
    ai_insights: Mapped[dict] = mapped_column(JSONB, default=dict)
    loss_reason: Mapped[str | None] = mapped_column(String(50))
    loss_debrief: Mapped[str | None] = mapped_column(Text)
    loss_competitor: Mapped[str | None] = mapped_column(String(100))
    win_debrief: Mapped[str | None] = mapped_column(Text)
    deal_type: Mapped[str] = mapped_column(String(20), default="new_business")
    source: Mapped[str] = mapped_column(String(20), default="direct")
    contract_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL", use_alter=True))
    original_close_date: Mapped[date | None] = mapped_column(Date)
    close_date_pushes: Mapped[int] = mapped_column(Integer, default=0)
    custom_fields: Mapped[dict] = mapped_column(JSONB, default=dict)
    po_number: Mapped[str | None] = mapped_column(String(64))
    bill_to: Mapped[dict] = mapped_column(JSONB, default=dict)
    ship_to: Mapped[dict] = mapped_column(JSONB, default=dict)
    tax_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    tax_exempt_cert_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("attachments.id", ondelete="SET NULL", use_alter=True))
    requested_delivery_date: Mapped[date | None] = mapped_column(Date)
    incoterms: Mapped[str | None] = mapped_column(String(10))
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL", use_alter=True))
    stage_entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    account: Mapped[Account] = relationship(lazy="joined")
    stage: Mapped[PipelineStage] = relationship(lazy="joined")
    owner: Mapped[User | None] = relationship(lazy="joined")
    primary_contact: Mapped[Contact | None] = relationship(lazy="joined")


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (
        CheckConstraint(f"activity_type IN {ACTIVITY_TYPES}", name="ck_activities_type"),
        CheckConstraint(f"sentiment IN {SENTIMENTS}", name="ck_activities_sentiment"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    deal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deals.id", ondelete="SET NULL"), index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    activity_type: Mapped[str] = mapped_column(String(20), default="note")
    summary: Mapped[str] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    sentiment: Mapped[str] = mapped_column(String(10), default="neutral")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    embedding = mapped_column(Vector(settings.embedding_dim), nullable=True)
    # omnichannel ledger (pillar 5)
    direction: Mapped[str | None] = mapped_column(String(10))
    subject: Mapped[str | None] = mapped_column(String(500))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    disposition: Mapped[str | None] = mapped_column(String(20))
    agenda: Mapped[str | None] = mapped_column(Text)
    attendance: Mapped[str | None] = mapped_column(String(12))
    external_id: Mapped[str | None] = mapped_column(String(500), unique=True)
    thread_id: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(20), default="manual")
    search_tsv = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(subject, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(raw_text, ''))", persisted=True),
        deferred=True,
    )
    created_at: Mapped[datetime] = _created()

    user: Mapped[User | None] = relationship(lazy="joined")
    account: Mapped[Account] = relationship(lazy="joined")
    deal: Mapped[Deal | None] = relationship(lazy="joined")
    contact: Mapped[Contact | None] = relationship(lazy="joined")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(String(500))
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    deal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deals.id", ondelete="SET NULL"))
    activity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("activities.id", ondelete="SET NULL"))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | ai | system
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(10), default="normal")
    depends_on_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    milestone_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("onboarding_milestones.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()

    account: Mapped[Account | None] = relationship(lazy="joined")
    deal: Mapped[Deal | None] = relationship(lazy="joined")
    owner: Mapped[User | None] = relationship(foreign_keys=[owner_id], lazy="joined")
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id], lazy="joined")
    depends_on: Mapped["Task | None"] = relationship(remote_side="Task.id", lazy="joined", join_depth=1)


class DealStageHistory(Base):
    """Audit trail written on every stage-gate transition."""

    __tablename__ = "deal_stage_history"

    id: Mapped[uuid.UUID] = _uuid_pk()
    deal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deals.id", ondelete="CASCADE"), index=True)
    from_stage_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pipeline_stages.id", ondelete="SET NULL"))
    to_stage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_stages.id", ondelete="CASCADE"))
    changed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    forecast_delta: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    gate_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    changed_at: Mapped[datetime] = _created()

    from_stage: Mapped[PipelineStage | None] = relationship(foreign_keys=[from_stage_id], lazy="joined")
    to_stage: Mapped[PipelineStage] = relationship(foreign_keys=[to_stage_id], lazy="joined")
    user: Mapped[User | None] = relationship(lazy="joined")


# Models for the enterprise pillars live in sibling modules; importing them here
# registers every table on Base.metadata.
from app.models.platform import *  # noqa: E402,F401,F403
from app.models.revenue import *  # noqa: E402,F401,F403
from app.models.success import *  # noqa: E402,F401,F403
from app.models.partners import *  # noqa: E402,F401,F403
from app.models.leads import *  # noqa: E402,F401,F403
from app.models.orders import *  # noqa: E402,F401,F403

import app.core.audit  # noqa: E402,F401  (registers the audit-trail flush listener)
