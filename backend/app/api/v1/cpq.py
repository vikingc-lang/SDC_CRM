import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.rbac import Principal, authorize
from app.core.config import settings
from app.models import (
    Account, ApprovalGroup, ApprovalPolicy, ApprovalRequest, Attachment, BundleComponent, Contract, Deal, Document, DocumentComment,
    DocumentVersion, PriceBook, PriceBookEntry, Product, ProductRule, Promotion, Quote, SignatureRequest, User,
)
from app.services import clm, contracting, cpq, storage

router = APIRouter(tags=["cpq"])
public = APIRouter(tags=["e-signature (public)"])


class Tier(BaseModel):
    min_qty: float = Field(ge=0)
    unit_price: float = Field(ge=0)


class PriceIn(BaseModel):
    currency: str = Field(min_length=3, max_length=3)
    tiers: list[Tier] = Field(min_length=1)


class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    family: str | None = None
    billing_type: Literal["recurring", "one_time"] = "recurring"
    unit: str = "user / month"
    active: bool = True
    product_type: Literal["standard", "bundle"] = "standard"
    prices: list[PriceIn] = []


class LineIn(BaseModel):
    product_id: uuid.UUID
    quantity: float = Field(gt=0)
    discount_pct: float = Field(default=0, ge=0, le=100)
    description: str | None = None


class QuoteIn(BaseModel):
    name: str | None = None
    currency: str = Field(default="USD", min_length=3, max_length=3)
    term_months: int = Field(default=12, ge=1, le=120)
    payment_terms: str = "NET30"
    notes: str | None = None
    promo_code: str | None = Field(default=None, max_length=40)
    custom_terms: str | None = Field(default=None, max_length=4000)
    billing_frequency: Literal["annual", "quarterly", "monthly"] = "annual"
    lines: list[LineIn] = []


class DecisionIn(BaseModel):
    approve: bool
    comment: str | None = None


class DocumentIn(BaseModel):
    doc_type: Literal["nda", "sow", "order_form", "proposal", "msa", "sla", "dpa"]
    deal_id: uuid.UUID
    quote_id: uuid.UUID | None = None


class Signer(BaseModel):
    name: str = Field(min_length=2)
    email: str = Field(min_length=3)
    party: Literal["customer", "company"]


class SendIn(BaseModel):
    signers: list[Signer] = Field(min_length=2)
    provider: Literal["builtin", "docusign", "adobe_sign"] | None = None


class SignIn(BaseModel):
    signature_text: str = ""
    signature_image: str | None = None
    agree: bool = False
    decline: bool = False


class PolicyIn(BaseModel):
    name: str
    rule_type: Literal["discount_pct", "payment_terms", "credit_hold", "tcv", "custom_terms", "credit_risk"]
    threshold: float | None = None
    approver_role: Literal["sales_manager", "deal_desk", "vp_sales", "finance", "legal"]
    active: bool = True


# ---- catalog --------------------------------------------------------------------------
@router.get("/products")
async def list_products(include_inactive: bool = False, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("products", "read"))):
    stmt = select(Product).order_by(Product.family, Product.name)
    if not include_inactive:
        stmt = stmt.where(Product.active.is_(True))
    return [cpq.product_out(p) for p in (await db.execute(stmt)).scalars().all()]


async def _apply_product(db: AsyncSession, product: Product, body: ProductIn) -> None:
    for k, v in body.model_dump(exclude={"prices"}).items():
        setattr(product, k, v)
    for price in body.prices:
        tiers = sorted([t.model_dump() for t in price.tiers], key=lambda t: t["min_qty"])
        if tiers[0]["min_qty"] > 1:
            raise HTTPException(422, "The first tier must start at quantity 0 or 1")
        entry = next((e for e in product.prices if e.currency == price.currency.upper()), None)
        if entry:
            entry.tiers = tiers
        else:
            product.prices.append(PriceBookEntry(currency=price.currency.upper(), tiers=tiers))


@router.post("/products", status_code=201)
async def create_product(body: ProductIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("products", "create"))):
    if (await db.execute(select(Product.id).where(Product.sku == body.sku))).first():
        raise HTTPException(409, f"SKU {body.sku} already exists")
    product = Product(sku=body.sku, name=body.name, prices=[])
    await _apply_product(db, product, body)
    db.add(product)
    await db.commit()
    await db.refresh(product, ["prices"])
    return cpq.product_out(product)


@router.put("/products/{product_id}")
async def update_product(product_id: uuid.UUID, body: ProductIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("products", "update"))):
    product = await db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    await _apply_product(db, product, body)
    await db.commit()
    await db.refresh(product, ["prices"])
    return cpq.product_out(product)


@router.get("/approval-policies")
async def list_policies(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("quotes", "read"))):
    return [{"id": p.id, "name": p.name, "rule_type": p.rule_type, "threshold": float(p.threshold) if p.threshold is not None else None,
             "approver_role": p.approver_role, "active": p.active} for p in (await db.execute(select(ApprovalPolicy))).scalars().all()]


@router.put("/approval-policies/{policy_id}")
async def update_policy(policy_id: uuid.UUID, body: PolicyIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    policy = await db.get(ApprovalPolicy, policy_id)
    if policy is None:
        raise HTTPException(404, "Policy not found")
    for k, v in body.model_dump().items():
        setattr(policy, k, v)
    await db.commit()
    return {"status": "ok"}


# ---- quotes ---------------------------------------------------------------------------------
async def _deal(db: AsyncSession, p: Principal, deal_id: uuid.UUID, resource: str = "quotes") -> Deal:
    deal = await db.get(Deal, deal_id)
    if deal is None:
        raise HTTPException(404, "Deal not found")
    if p.is_own_scope(resource) and deal.owner_id != p.id:
        await p.ensure_account(db, deal.account_id, resource)
    return deal


async def _quote(db: AsyncSession, p: Principal, quote_id: uuid.UUID) -> Quote:
    quote = await db.get(Quote, quote_id)
    if quote is None:
        raise HTTPException(404, "Quote not found")
    await _deal(db, p, quote.deal_id)
    return quote


@router.get("/quotes")
async def list_quotes(status: str | None = None, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("quotes", "read"))):
    stmt = select(Quote).join(Deal, Quote.deal_id == Deal.id).order_by(Quote.created_at.desc()).limit(200)
    stmt = p.scope_deals(stmt) if p.is_own_scope("quotes") else stmt
    if status:
        stmt = stmt.where(Quote.status.in_(status.split(",")))
    return [cpq.quote_out(q) for q in (await db.execute(stmt)).scalars().unique().all()]


@router.post("/deals/{deal_id}/quotes", status_code=201)
async def create_quote(deal_id: uuid.UUID, body: QuoteIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("quotes", "create"))):
    deal = await _deal(db, p, deal_id)
    has_primary = (await db.execute(select(Quote.id).where(Quote.deal_id == deal.id, Quote.is_primary.is_(True)))).first()
    quote = Quote(deal_id=deal.id, quote_number=await cpq.next_quote_number(db), name=body.name or f"{deal.title} quote",
                  currency=body.currency.upper(), term_months=body.term_months, payment_terms=body.payment_terms.upper(), notes=body.notes,
                  promo_code=(body.promo_code or "").strip().upper() or None, custom_terms=body.custom_terms,
                  billing_frequency=body.billing_frequency, created_by=p.id, status="draft", is_primary=not has_primary)
    db.add(quote)
    await db.flush()
    try:
        await cpq.rebuild(db, quote, [l.model_dump() for l in body.lines])
    except cpq.PricingError as exc:
        await db.rollback()
        raise HTTPException(422, str(exc))
    await db.commit()
    return cpq.quote_out(await db.get(Quote, quote.id))


@router.get("/quotes/{quote_id}")
async def get_quote(quote_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("quotes", "read"))):
    quote = await _quote(db, p, quote_id)
    out = cpq.quote_out(quote)
    out["required_approvals"] = await cpq.required_approvals(db, quote, await db.get(Deal, quote.deal_id))
    out["documents"] = [clm.document_out(d, include_body=False) for d in (await db.execute(select(Document).where(Document.quote_id == quote_id))).scalars().unique().all()]
    return out


@router.put("/quotes/{quote_id}")
async def update_quote(quote_id: uuid.UUID, body: QuoteIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("quotes", "update"))):
    quote = await _quote(db, p, quote_id)
    if quote.status in ("sent", "accepted"):
        raise HTTPException(409, f"Quote is {quote.status}; create a new version instead")
    if quote.locked_at:
        raise HTTPException(409, "Quote is locked; create a new quote instead")
    quote.name = body.name or quote.name
    quote.currency, quote.term_months, quote.payment_terms, quote.notes = body.currency.upper(), body.term_months, body.payment_terms.upper(), body.notes
    quote.promo_code, quote.custom_terms = (body.promo_code or "").strip().upper() or None, body.custom_terms
    quote.billing_frequency = body.billing_frequency
    try:
        await cpq.rebuild(db, quote, [l.model_dump() for l in body.lines])
    except cpq.PricingError as exc:
        await db.rollback()
        raise HTTPException(422, str(exc))
    await db.commit()
    return cpq.quote_out(await db.get(Quote, quote_id))


@router.post("/quotes/{quote_id}/submit")
async def submit_quote(quote_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("quotes", "update"))):
    quote = await _quote(db, p, quote_id)
    try:
        await cpq.submit(db, quote)
    except cpq.PricingError as exc:
        raise HTTPException(422, str(exc))
    await db.commit()
    return cpq.quote_out(await db.get(Quote, quote_id))


@router.post("/quotes/{quote_id}/primary")
async def make_primary(quote_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("quotes", "update"))):
    quote = await _quote(db, p, quote_id)
    try:
        await cpq.set_primary(db, quote)
    except cpq.PricingError as exc:
        raise HTTPException(409, str(exc))
    await db.commit()
    return cpq.quote_out(await db.get(Quote, quote_id))


@router.get("/approvals")
async def approvals_inbox(status: Literal["pending", "decided", "all"] = "pending", db: AsyncSession = Depends(get_db),
                          p: Principal = Depends(authorize("approvals", "read"))):
    stmt = (select(ApprovalRequest).options(selectinload(ApprovalRequest.quote).joinedload(Quote.deal).joinedload(Deal.account),
                                            selectinload(ApprovalRequest.quote).selectinload(Quote.approvals))
            .order_by(ApprovalRequest.created_at.desc()).limit(200))
    if p.is_own_scope("approvals"):
        stmt = stmt.where(ApprovalRequest.quote_id.in_(select(Quote.id).join(Deal, Quote.deal_id == Deal.id).where(
            (Deal.owner_id == p.id) | Deal.account_id.in_(p.owned_account_ids()))))
    if status == "pending":
        stmt = stmt.where(ApprovalRequest.status == "pending")
    elif status == "decided":
        stmt = stmt.where(ApprovalRequest.status.in_(("approved", "rejected")))
    rows = (await db.execute(stmt)).scalars().unique().all()
    out = []
    for a in rows:
        q = a.quote
        out.append({"id": a.id, "required_role": a.required_role, "reason": a.reason, "status": a.status, "comment": a.comment, "created_at": a.created_at,
                    "decided_at": a.decided_at, "decided_by": {"full_name": a.decider.full_name} if a.decider else None,
                    "level": a.level, "label": cpq.LEVEL_LABELS.get(a.required_role, a.required_role),
                    "waiting": a.status == "pending" and any(o.status == "pending" and o.level < a.level for o in q.approvals),
                    "can_decide": a.status == "pending" and await cpq.can_approve(db, p.user, a.required_role)
                    and not any(o.status == "pending" and o.level < a.level for o in q.approvals),
                    "quote": {"id": q.id, "quote_number": q.quote_number, "name": q.name, "currency": q.currency, "tcv": float(q.tcv),
                              "acv": float(q.acv), "max_discount_pct": float(q.max_discount_pct), "payment_terms": q.payment_terms,
                              "deal": {"id": q.deal.id, "title": q.deal.title, "account": q.deal.account.name}}})
    return out


@router.post("/approvals/{request_id}/decide")
async def decide(request_id: uuid.UUID, body: DecisionIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("approvals", "read"))):
    req = await db.get(ApprovalRequest, request_id)
    if req is None:
        raise HTTPException(404, "Approval request not found")
    try:
        quote = await cpq.decide(db, req, p.user, body.approve, body.comment)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except cpq.PricingError as exc:
        raise HTTPException(409, str(exc))
    await db.commit()
    return cpq.quote_out(await db.get(Quote, quote.id))


# ---- documents & e-signature ---------------------------------------------------------------------
@router.post("/documents", status_code=201)
async def generate_document(body: DocumentIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("documents", "create"))):
    deal = await _deal(db, p, body.deal_id, "documents")
    quote = await db.get(Quote, body.quote_id) if body.quote_id else None
    if body.doc_type == "order_form" and quote is None:
        quote = (await db.execute(select(Quote).where(Quote.deal_id == deal.id, Quote.status.in_(("approved", "sent", "accepted")))
                                  .order_by(Quote.created_at.desc()))).scalars().first()
    try:
        doc = await clm.generate(db, body.doc_type, deal.account, deal, quote, p.id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    await db.commit()
    return clm.document_out(await db.get(Document, doc.id))


@router.get("/documents/{document_id}")
async def get_document(document_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("documents", "read"))):
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    await p.ensure_account(db, doc.account_id, "documents")
    return clm.document_out(doc)


@router.post("/documents/{document_id}/send")
async def send_document(document_id: uuid.UUID, body: SendIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("documents", "update"))):
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    await p.ensure_account(db, doc.account_id, "documents")
    try:
        await clm.send_for_signature(db, doc, [s.model_dump(exclude={"provider"}) for s in body.signers], body.provider)
    except contracting.CreditReviewRequired as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    await db.commit()
    return clm.document_out(await db.get(Document, document_id))


@router.get("/documents/{document_id}/pdf")
async def document_pdf(document_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("documents", "read"))):
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    await p.ensure_account(db, doc.account_id, "documents")
    if doc.pdf_attachment_id:
        att = await db.get(Attachment, doc.pdf_attachment_id)
        data = storage.read(att)
    else:  # draft preview
        data = clm.render_pdf(doc, [])
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{doc.title}.pdf"'})


@public.get("/sign/{token}")
async def signing_view(token: str, db: AsyncSession = Depends(get_db)):
    req = (await db.execute(select(SignatureRequest).where(SignatureRequest.token == token))).scalars().first()
    if req is None:
        raise HTTPException(404, "This signing link is invalid")
    doc = await db.get(Document, req.document_id)
    await db.refresh(doc, ["signers"])
    comments = (await db.execute(select(DocumentComment).where(DocumentComment.document_id == doc.id).order_by(DocumentComment.created_at))).scalars().all()
    return {"document": {"title": doc.title, "status": doc.status, "body_html": clm.to_html(doc.body), "content_sha256": doc.content_sha256,
                         "version": doc.current_version},
            "comments": [contracting.comment_out(c) for c in comments],
            "can_comment": req.signer_party == "customer" and doc.status in ("sent", "partially_signed", "in_negotiation"),
            "signer": {"name": req.signer_name, "email": req.signer_email, "party": req.signer_party, "status": req.status},
            "your_turn": req.status == "pending" and clm.is_turn(req, doc) and doc.status not in ("completed", "voided", "in_negotiation"),
            "signers": [{"name": s.signer_name, "party": s.signer_party, "status": s.status, "signed_at": s.signed_at} for s in doc.signers]}


@public.post("/sign/{token}")
async def signing_submit(token: str, body: SignIn, request: Request, db: AsyncSession = Depends(get_db)):
    req = (await db.execute(select(SignatureRequest).where(SignatureRequest.token == token))).scalars().first()
    if req is None:
        raise HTTPException(404, "This signing link is invalid")
    if not body.decline and not body.agree:
        raise HTTPException(422, "Please agree to sign electronically")
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "").split(",")[0].strip()
    try:
        doc = await clm.sign(db, req, body.signature_text, body.signature_image, ip, request.headers.get("user-agent"), body.decline)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    await db.commit()
    return {"status": req.status, "document_status": doc.status}


# ---- contracts ---------------------------------------------------------------------------------------
@router.get("/contracts")
async def list_contracts(status: str | None = None, expiring_within_days: int | None = None, db: AsyncSession = Depends(get_db),
                         p: Principal = Depends(authorize("contracts", "read"))):
    stmt = p.scope_accounts(select(Contract), "contracts", Contract.account_id).order_by(Contract.end_date)
    if status:
        stmt = stmt.where(Contract.status.in_(status.split(",")))
    if expiring_within_days is not None:
        from datetime import timedelta
        stmt = stmt.where(Contract.end_date <= date.today() + timedelta(days=expiring_within_days))
    return [clm.contract_out(c) for c in (await db.execute(stmt)).scalars().unique().all()]


@router.post("/quotes/{quote_id}/contract", status_code=201)
async def contract_from_quote(quote_id: uuid.UUID, start_date: date | None = None, db: AsyncSession = Depends(get_db),
                              p: Principal = Depends(authorize("contracts", "create"))):
    """Record a contract executed outside Cirra (e.g. wet ink) from an approved quote."""
    quote = await _quote(db, p, quote_id)
    if quote.status not in ("approved", "sent", "accepted"):
        raise HTTPException(422, "Quote must be approved")
    if (await db.execute(select(Contract.id).where(Contract.quote_id == quote_id))).first():
        raise HTTPException(409, "A contract already exists for this quote")
    contract = await clm.create_contract_from_quote(db, quote, start=start_date)
    quote.status = "accepted"
    await db.commit()
    return clm.contract_out(await db.get(Contract, contract.id))


# ---- deal desk administration: price books, promotions, bundles, rules, approval chain ---------------------
class BookEntryIn(BaseModel):
    product_id: uuid.UUID
    currency: str = Field(min_length=3, max_length=3)
    tiers: list[Tier] = Field(min_length=1)


class PriceBookIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    kind: Literal["customer", "regional"]
    account_id: uuid.UUID | None = None
    region: str | None = None
    active: bool = True
    valid_from: date | None = None
    valid_to: date | None = None
    entries: list[BookEntryIn] = []


class PromotionIn(BaseModel):
    code: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=120)
    discount_pct: float = Field(gt=0, lt=100)
    product_ids: list[uuid.UUID] = []
    min_quantity: float = Field(default=0, ge=0)
    valid_from: date | None = None
    valid_to: date | None = None
    active: bool = True


class ComponentIn(BaseModel):
    component_id: uuid.UUID
    quantity: float = Field(default=1, gt=0)


class RuleIn(BaseModel):
    product_id: uuid.UUID
    rule_type: Literal["requires", "excludes"]
    target_product_id: uuid.UUID
    message: str | None = Field(default=None, max_length=300)


class GroupIn(BaseModel):
    member_ids: list[uuid.UUID]


def _book_out(b: PriceBook) -> dict:
    return {"id": b.id, "name": b.name, "kind": b.kind, "account_id": b.account_id, "region": b.region, "active": b.active,
            "valid_from": b.valid_from, "valid_to": b.valid_to,
            "entries": [{"product_id": e.product_id, "currency": e.currency, "tiers": e.tiers} for e in b.entries]}


@router.get("/price-books")
async def list_books(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("products", "read"))):
    books = (await db.execute(select(PriceBook).order_by(PriceBook.kind, PriceBook.name))).scalars().unique().all()
    accounts = {a.id: a.name for a in (await db.execute(select(Account).where(Account.id.in_([b.account_id for b in books if b.account_id])))).scalars().unique().all()}
    return [{**_book_out(b), "account_name": accounts.get(b.account_id)} for b in books]


@router.post("/price-books", status_code=201)
async def create_book(body: PriceBookIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("products", "update"))):
    if (body.kind == "customer") != bool(body.account_id) or (body.kind == "regional") != bool(body.region):
        raise HTTPException(422, "Customer books need an account; regional books need a region")
    book = PriceBook(**body.model_dump(exclude={"entries"}))
    db.add(book)
    await db.flush()
    for e in body.entries:
        tiers = sorted([t.model_dump() for t in e.tiers], key=lambda t: t["min_qty"])
        db.add(PriceBookEntry(product_id=e.product_id, currency=e.currency.upper(), tiers=tiers, price_book_id=book.id))
    await db.commit()
    fresh = (await db.execute(select(PriceBook).where(PriceBook.id == book.id).options(selectinload(PriceBook.entries))
                              .execution_options(populate_existing=True))).scalars().one()
    return _book_out(fresh)


@router.delete("/price-books/{book_id}", status_code=204)
async def delete_book(book_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("products", "update"))):
    book = await db.get(PriceBook, book_id)
    if book:
        await db.delete(book)
        await db.commit()


def _promo_out(p: Promotion) -> dict:
    return {"id": p.id, "code": p.code, "name": p.name, "discount_pct": float(p.discount_pct), "product_ids": p.product_ids,
            "min_quantity": float(p.min_quantity or 0), "valid_from": p.valid_from, "valid_to": p.valid_to, "active": p.active}


@router.get("/promotions")
async def list_promotions(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("quotes", "read"))):
    return [_promo_out(p) for p in (await db.execute(select(Promotion).order_by(Promotion.created_at.desc()))).scalars().all()]


@router.post("/promotions", status_code=201)
async def create_promotion(body: PromotionIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("products", "update"))):
    if (await db.execute(select(Promotion.id).where(Promotion.code == body.code.upper()))).first():
        raise HTTPException(409, "Promotion code already exists")
    promo = Promotion(**{**body.model_dump(), "code": body.code.upper(), "product_ids": [str(x) for x in body.product_ids]})
    db.add(promo)
    await db.commit()
    return _promo_out(promo)


@router.patch("/promotions/{promo_id}")
async def toggle_promotion(promo_id: uuid.UUID, active: bool, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("products", "update"))):
    promo = await db.get(Promotion, promo_id)
    if promo is None:
        raise HTTPException(404, "Promotion not found")
    promo.active = active
    await db.commit()
    return _promo_out(promo)


@router.get("/products/{product_id}/configuration")
async def product_configuration(product_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("products", "read"))):
    comps = (await db.execute(select(BundleComponent).where(BundleComponent.bundle_id == product_id))).scalars().unique().all()
    rules = (await db.execute(select(ProductRule).where(ProductRule.product_id == product_id))).scalars().unique().all()
    return {"components": [{"component_id": c.component_id, "sku": c.component.sku, "name": c.component.name, "quantity": float(c.quantity)} for c in comps],
            "rules": [{"id": r.id, "rule_type": r.rule_type, "target_product_id": r.target_product_id, "target": r.target.name, "message": r.message} for r in rules]}


@router.put("/products/{product_id}/components")
async def set_components(product_id: uuid.UUID, body: list[ComponentIn], db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("products", "update"))):
    product = await db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    if any(c.component_id == product_id for c in body):
        raise HTTPException(422, "A bundle cannot contain itself")
    for old in (await db.execute(select(BundleComponent).where(BundleComponent.bundle_id == product_id))).scalars().all():
        await db.delete(old)
    await db.flush()
    for c in body:
        db.add(BundleComponent(bundle_id=product_id, component_id=c.component_id, quantity=c.quantity))
    product.product_type = "bundle" if body else "standard"
    await db.commit()
    return await product_configuration(product_id, db)


@router.post("/product-rules", status_code=201)
async def create_rule(body: RuleIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("products", "update"))):
    if body.product_id == body.target_product_id:
        raise HTTPException(422, "A product cannot reference itself")
    rule = ProductRule(**body.model_dump())
    db.add(rule)
    await db.commit()
    return {"id": rule.id}


@router.delete("/product-rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("products", "update"))):
    rule = await db.get(ProductRule, rule_id)
    if rule:
        await db.delete(rule)
        await db.commit()


@router.post("/approval-policies", status_code=201)
async def create_policy(body: PolicyIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    policy = ApprovalPolicy(**body.model_dump())
    db.add(policy)
    await db.commit()
    return {"id": policy.id}


@router.delete("/approval-policies/{policy_id}", status_code=204)
async def delete_policy(policy_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    policy = await db.get(ApprovalPolicy, policy_id)
    if policy:
        await db.delete(policy)
        await db.commit()


@router.get("/approval-groups")
async def approval_groups(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("approvals", "read"))):
    groups = {g.key: g for g in (await db.execute(select(ApprovalGroup))).scalars().all()}
    users = {u.id: u for u in (await db.execute(select(User))).scalars().all()}
    out = []
    for level, key in enumerate(cpq.CHAIN, start=1):
        members = [users[uuid.UUID(str(m))] for m in (groups[key].member_ids if key in groups else []) if uuid.UUID(str(m)) in users]
        implicit = [u for u in users.values() if u.is_active and (u.role == "super_admin" or (key == "sales_manager" and u.role == "sales_manager"))]
        out.append({"key": key, "label": cpq.LEVEL_LABELS[key], "level": level,
                    "members": [{"id": u.id, "full_name": u.full_name, "role": u.role} for u in members],
                    "implicit": [{"id": u.id, "full_name": u.full_name, "role": u.role} for u in implicit]})
    return out


@router.put("/approval-groups/{key}")
async def set_group(key: Literal["sales_manager", "deal_desk", "vp_sales", "finance", "legal"], body: GroupIn, db: AsyncSession = Depends(get_db),
                    _: Principal = Depends(authorize("admin", "update"))):
    group = await db.get(ApprovalGroup, key)
    ids = [str(x) for x in body.member_ids]
    if group is None:
        db.add(ApprovalGroup(key=key, name=cpq.LEVEL_LABELS[key], member_ids=ids))
    else:
        group.member_ids = ids
    await db.commit()
    return {"key": key, "member_ids": ids}


# ---- negotiation: versions, redlines, comments ---------------------------------------------------------------
class VersionIn(BaseModel):
    body: str = Field(min_length=20, max_length=200_000)
    note: str | None = Field(default=None, max_length=500)


class CommentIn(BaseModel):
    body: str = Field(min_length=3, max_length=4000)
    clause: str | None = Field(default=None, max_length=200)


async def _doc(db: AsyncSession, p: Principal, document_id: uuid.UUID) -> Document:
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    await p.ensure_account(db, doc.account_id, "documents")
    return doc


@router.get("/documents/{document_id}/negotiation")
async def negotiation(document_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("documents", "read"))):
    doc = await _doc(db, p, document_id)
    comments = (await db.execute(select(DocumentComment).where(DocumentComment.document_id == doc.id).order_by(DocumentComment.created_at))).scalars().all()
    return {"current_version": doc.current_version, "status": doc.status,
            "versions": [contracting.version_out(v) for v in await contracting.versions(db, doc)],
            "comments": [contracting.comment_out(c) for c in comments],
            "open_comments": sum(1 for c in comments if not c.resolved)}


@router.get("/documents/{document_id}/diff")
async def document_diff(document_id: uuid.UUID, from_version: int, to_version: int | None = None, db: AsyncSession = Depends(get_db),
                        p: Principal = Depends(authorize("documents", "read"))):
    doc = await _doc(db, p, document_id)
    by_no = {v.version: v for v in await contracting.versions(db, doc)}
    to_version = to_version or doc.current_version
    if from_version not in by_no or to_version not in by_no:
        raise HTTPException(404, "Version not found")
    lines = contracting.diff(by_no[from_version].body, by_no[to_version].body)
    return {"from": from_version, "to": to_version, "lines": lines,
            "stats": {"inserted": sum(l["op"] == "insert" for l in lines), "deleted": sum(l["op"] == "delete" for l in lines)}}


@router.post("/documents/{document_id}/versions", status_code=201)
async def revise(document_id: uuid.UUID, body: VersionIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("documents", "update"))):
    doc = await _doc(db, p, document_id)
    try:
        v = await contracting.new_version(db, doc, body.body, body.note, "internal", p.user)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    await db.commit()
    return contracting.version_out(v)


@router.post("/documents/{document_id}/comments", status_code=201)
async def comment(document_id: uuid.UUID, body: CommentIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("documents", "update"))):
    doc = await _doc(db, p, document_id)
    try:
        c = await contracting.add_comment(db, doc, body.body, "company", p.user.full_name, p.user.email, body.clause, p.user)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    await db.commit()
    return contracting.comment_out(c)


@router.post("/documents/{document_id}/comments/{comment_id}/resolve")
async def resolve_comment(document_id: uuid.UUID, comment_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                          p: Principal = Depends(authorize("documents", "update"))):
    await _doc(db, p, document_id)
    c = await db.get(DocumentComment, comment_id)
    if c is None or c.document_id != document_id:
        raise HTTPException(404, "Comment not found")
    c.resolved, c.resolved_by = True, p.id
    await db.commit()
    return contracting.comment_out(c)


# ---- external e-signature provider callbacks ------------------------------------------------------------
@public.post("/esign/webhook/{provider}")
async def esign_webhook(provider: Literal["docusign", "adobe_sign"], payload: dict, x_cirra_esign_secret: str | None = Header(default=None),
                        db: AsyncSession = Depends(get_db)):
    if not settings.esign_webhook_secret or x_cirra_esign_secret != settings.esign_webhook_secret:
        raise HTTPException(401, "Invalid webhook secret")
    event = {  # accept normalised events or the providers' common field names
        "envelope_id": payload.get("envelope_id") or payload.get("envelopeId") or (payload.get("agreement") or {}).get("id"),
        "status": {"completed": "completed", "AGREEMENT_WORKFLOW_COMPLETED": "completed", "declined": "declined",
                   "AGREEMENT_REJECTED": "declined", "recipient-completed": "signer_completed", "AGREEMENT_ACTION_COMPLETED": "signer_completed",
                   "signer_completed": "signer_completed"}.get(payload.get("status") or payload.get("event") or ""),
        "signer_email": payload.get("signer_email") or payload.get("recipientEmail") or (payload.get("participantUserEmail")),
        "ip": payload.get("ip"),
    }
    if not event["envelope_id"] or not event["status"]:
        raise HTTPException(422, "Unrecognised provider event")
    try:
        doc = await contracting.handle_provider_event(db, provider, event)
    except LookupError:
        raise HTTPException(404, "Unknown envelope")
    await db.commit()
    return {"document_id": doc.id, "status": doc.status}


# ---- customer side of the negotiation (signing link) ------------------------------------------------------
class CustomerCommentIn(BaseModel):
    body: str = Field(min_length=3, max_length=4000)
    clause: str | None = Field(default=None, max_length=200)


async def _signer(db: AsyncSession, token: str) -> tuple[SignatureRequest, Document]:
    req = (await db.execute(select(SignatureRequest).where(SignatureRequest.token == token))).scalars().first()
    if req is None:
        raise HTTPException(404, "This signing link is invalid")
    return req, await db.get(Document, req.document_id)


@public.post("/sign/{token}/comments", status_code=201)
async def customer_comment(token: str, body: CustomerCommentIn, db: AsyncSession = Depends(get_db)):
    req, doc = await _signer(db, token)
    if req.signer_party != "customer":
        raise HTTPException(403, "Company signers comment inside Cirra")
    try:
        c = await contracting.add_comment(db, doc, body.body, "customer", req.signer_name, req.signer_email, body.clause)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    await db.commit()
    return contracting.comment_out(c)
