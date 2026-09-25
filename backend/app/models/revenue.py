"""CPQ, approvals, documents, e-signature and contracts (pillar 4)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

__all__ = [
    "Product", "PriceBookEntry", "ApprovalPolicy", "Quote", "QuoteLine", "ApprovalRequest", "DocumentTemplate",
    "Document", "SignatureRequest", "Contract",
]


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = _pk()
    sku: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    family: Mapped[str | None] = mapped_column(String(100))
    billing_type: Mapped[str] = mapped_column(String(20), default="recurring")
    unit: Mapped[str] = mapped_column(String(40), default="user / month")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts()

    prices: Mapped[list["PriceBookEntry"]] = relationship(back_populates="product", lazy="selectin", cascade="all, delete-orphan")


class PriceBookEntry(Base):
    """Rate card for one currency: tiers = [{"min_qty": 1, "unit_price": 50.0}, ...] ascending."""

    __tablename__ = "price_book_entries"

    id: Mapped[uuid.UUID] = _pk()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    currency: Mapped[str] = mapped_column(String(3))
    tiers: Mapped[list] = mapped_column(JSONB)

    product: Mapped[Product] = relationship(back_populates="prices")


class ApprovalPolicy(Base):
    __tablename__ = "approval_policies"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(120))
    rule_type: Mapped[str] = mapped_column(String(30))
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    approver_role: Mapped[str] = mapped_column(String(30))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[uuid.UUID] = _pk()
    deal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deals.id", ondelete="CASCADE"), index=True)
    quote_number: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    term_months: Mapped[int] = mapped_column(Integer, default=12)
    payment_terms: Mapped[str] = mapped_column(String(20), default="NET30")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    valid_until: Mapped[date | None] = mapped_column(Date)
    list_total: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    discount_total: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    max_discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    one_time_total: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    acv: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    tcv: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    lines: Mapped[list["QuoteLine"]] = relationship(back_populates="quote", lazy="selectin", cascade="all, delete-orphan", order_by="QuoteLine.position")
    approvals: Mapped[list["ApprovalRequest"]] = relationship(back_populates="quote", lazy="selectin", cascade="all, delete-orphan", order_by="ApprovalRequest.created_at")
    deal = relationship("Deal", lazy="joined")


class QuoteLine(Base):
    __tablename__ = "quote_lines"

    id: Mapped[uuid.UUID] = _pk()
    quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotes.id", ondelete="CASCADE"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str | None] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    list_unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    net_unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    billing_type: Mapped[str] = mapped_column(String(20))
    line_total: Mapped[Decimal] = mapped_column(Numeric(16, 2))

    quote: Mapped[Quote] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship(lazy="joined")


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[uuid.UUID] = _pk()
    quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotes.id", ondelete="CASCADE"))
    required_role: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()

    quote: Mapped[Quote] = relationship(back_populates="approvals")
    decider = relationship("User", lazy="joined")


class DocumentTemplate(Base):
    __tablename__ = "document_templates"

    id: Mapped[uuid.UUID] = _pk()
    doc_type: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts()


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _pk()
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_templates.id", ondelete="SET NULL"))
    doc_type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(255))
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    deal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deals.id", ondelete="SET NULL"))
    quote_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("quotes.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    pdf_attachment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("attachments.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _ts()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    signers: Mapped[list["SignatureRequest"]] = relationship(back_populates="document", lazy="selectin", cascade="all, delete-orphan", order_by="SignatureRequest.sign_order")
    account = relationship("Account", lazy="joined")


class SignatureRequest(Base):
    __tablename__ = "signature_requests"

    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    signer_name: Mapped[str] = mapped_column(String(200))
    signer_email: Mapped[str] = mapped_column(String(255))
    signer_party: Mapped[str] = mapped_column(String(20))
    sign_order: Mapped[int] = mapped_column(Integer, default=1)
    token: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    signature_text: Mapped[str | None] = mapped_column(String(200))
    signature_image: Mapped[str | None] = mapped_column(Text)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _ts()

    document: Mapped[Document] = relationship(back_populates="signers")


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[uuid.UUID] = _pk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    deal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deals.id", ondelete="SET NULL"))
    quote_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("quotes.id", ondelete="SET NULL"))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    contract_number: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    acv: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    tcv: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    payment_terms: Mapped[str] = mapped_column(String(20), default="NET30")
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="active")
    terms: Mapped[dict] = mapped_column(JSONB, default=dict)
    renewal_deal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deals.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _ts()

    account = relationship("Account", lazy="joined")
