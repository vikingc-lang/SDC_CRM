"""Orders generated from the primary quote and pushed to the ERP (lead-to-order, step 7)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

__all__ = ["Order", "OrderLine"]


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_number: Mapped[str] = mapped_column(String(30), unique=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    deal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deals.id", ondelete="SET NULL"))
    quote_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("quotes.id", ondelete="SET NULL"))
    contract_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    currency: Mapped[str] = mapped_column(String(3))
    po_number: Mapped[str | None] = mapped_column(String(64))
    payment_terms: Mapped[str] = mapped_column(String(20))
    billing_frequency: Mapped[str] = mapped_column(String(10), default="annual")
    term_months: Mapped[int] = mapped_column(Integer, default=12)
    start_date: Mapped[date | None] = mapped_column(Date)
    requested_delivery_date: Mapped[date | None] = mapped_column(Date)
    incoterms: Mapped[str | None] = mapped_column(String(10))
    bill_to: Mapped[dict] = mapped_column(JSONB, default=dict)
    ship_to: Mapped[dict] = mapped_column(JSONB, default=dict)
    tax_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    total: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    erp_order_id: Mapped[str | None] = mapped_column(String(64))
    erp_status: Mapped[str | None] = mapped_column(String(40))
    erp_message: Mapped[str | None] = mapped_column(Text)
    erp_attempts: Mapped[int] = mapped_column(Integer, default=0)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    erp_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    erp_acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    lines: Mapped[list["OrderLine"]] = relationship(back_populates="order", lazy="selectin", cascade="all, delete-orphan", order_by="OrderLine.line_no")
    account = relationship("Account", lazy="joined")


class OrderLine(Base):
    __tablename__ = "order_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)
    parent_line_no: Mapped[int | None] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    unit_list_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    net_unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    line_total: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    billing_type: Mapped[str] = mapped_column(String(20))
    billing_schedule: Mapped[list] = mapped_column(JSONB, default=list)

    order: Mapped[Order] = relationship(back_populates="lines")
