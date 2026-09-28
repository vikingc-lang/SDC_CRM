"""Opportunity products, deal teams and splits; dated exchange rates and tax rates
(see services/deal_team.py, services/fx.py and services/tax.py)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.types import UTCDateTime, UUID

__all__ = ["DealLineItem", "DealTeamMember", "DealSplit", "FxRateHistory", "TaxRate", "DealContact", "TEAM_ROLES", "SPLIT_TYPES",
           "COMMITTEE_ROLES"]

TEAM_ROLES = ("Sales Engineer", "Solution Architect", "Executive Sponsor", "Partner Manager", "Customer Success", "Overlay Specialist",
              "Sales Manager", "Other")
SPLIT_TYPES = ("revenue", "overlay")
# A contact's part in one deal's decision (the contact's own buying_role is their usual part across deals)
COMMITTEE_ROLES = ("Economic Buyer", "Decision Maker", "Champion", "Technical Buyer", "Influencer", "Evaluator", "User",
                   "Legal Counsel", "Procurement", "Blocker")


class DealLineItem(Base):
    """A product on an opportunity (before any quote): quantity, sales price and discount. With
    ``deals.amount_source = 'lines'`` the deal amount is the sum of its line totals."""
    __tablename__ = "deal_line_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_deal_line_items_qty"),
                      CheckConstraint("discount_pct >= 0 AND discount_pct <= 100", name="ck_deal_line_items_discount"),
                      Index("ix_deal_line_items_deal", "deal_id"))

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deals.id", ondelete="CASCADE"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    description: Mapped[str | None] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0, server_default="0")
    term_months: Mapped[int] = mapped_column(Integer, default=12, server_default="12")
    total: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    billing_type: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)

    product = relationship("Product", lazy="joined")


class DealTeamMember(Base):
    """Someone besides the owner working the deal. Members see the deal (and its account) even with "own" scope;
    ``access = 'edit'`` also lets them change it."""
    __tablename__ = "deal_team_members"
    __table_args__ = (CheckConstraint("access IN ('read', 'edit')", name="ck_deal_team_access"),
                      Index("ix_deal_team_user", "user_id"))

    deal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deals.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(30))
    access: Mapped[str] = mapped_column(String(5), default="read", server_default="read")
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)

    user = relationship("User", lazy="joined")


class DealSplit(Base):
    """Credit for a deal. Revenue splits add up to 100% and drive quota attainment; overlay splits are extra credit
    for specialists and may add up to anything."""
    __tablename__ = "deal_splits"
    __table_args__ = (CheckConstraint("split_type IN ('revenue', 'overlay')", name="ck_deal_splits_type"),
                      CheckConstraint("percent > 0 AND percent <= 100", name="ck_deal_splits_percent"),
                      UniqueConstraint("deal_id", "user_id", "split_type", name="uq_deal_splits"),
                      Index("ix_deal_splits_user", "user_id"))

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deals.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    split_type: Mapped[str] = mapped_column(String(8))
    percent: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)

    user = relationship("User", lazy="joined")


class FxRateHistory(Base):
    """Exchange rate to USD in effect from ``effective_date`` until the next row for the currency."""
    __tablename__ = "fx_rate_history"

    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    effective_date: Mapped[date] = mapped_column(Date, primary_key=True)
    rate_to_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6))
    source: Mapped[str] = mapped_column(String(20), default="manual", server_default="manual")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)


class TaxRate(Base):
    """A rate for the built-in tax engine: country, optional region (state / province), optional product tax code."""
    __tablename__ = "tax_rates"
    __table_args__ = (CheckConstraint("rate >= 0 AND rate <= 100", name="ck_tax_rates_rate"),
                      Index("ix_tax_rates_country", "country"))

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    country: Mapped[str] = mapped_column(String(2))
    region: Mapped[str | None] = mapped_column(String(10))
    tax_code: Mapped[str | None] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(60))
    rate: Mapped[Decimal] = mapped_column(Numeric(6, 3))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class DealContact(Base):
    """A contact on a deal's buying committee: their role in this decision, how much weight they carry and where
    they stand. Drives the stakeholder map, coverage gaps and the missing-roles risk signal."""
    __tablename__ = "deal_contacts"
    __table_args__ = (CheckConstraint(f"role IN {COMMITTEE_ROLES}", name="ck_deal_contacts_role"),
                      CheckConstraint("influence IN ('high', 'medium', 'low')", name="ck_deal_contacts_influence"),
                      CheckConstraint("stance IN ('champion', 'supporter', 'neutral', 'skeptic', 'blocker')", name="ck_deal_contacts_stance"),
                      Index("ix_deal_contacts_contact", "contact_id"))

    deal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deals.id", ondelete="CASCADE"), primary_key=True)
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))
    influence: Mapped[str] = mapped_column(String(10), default="medium", server_default="medium")
    stance: Mapped[str] = mapped_column(String(12), default="neutral", server_default="neutral")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    notes: Mapped[str | None] = mapped_column(String(500))
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)

    contact = relationship("Contact", lazy="joined")
