import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.rbac import Principal, authorize
from app.models import (
    ApprovalPolicy, ApprovalRequest, Attachment, Contract, Deal, Document, PriceBookEntry, Product, Quote, SignatureRequest,
)
from app.services import clm, cpq, storage

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
    lines: list[LineIn] = []


class DecisionIn(BaseModel):
    approve: bool
    comment: str | None = None


class DocumentIn(BaseModel):
    doc_type: Literal["nda", "sow", "order_form"]
    deal_id: uuid.UUID
    quote_id: uuid.UUID | None = None


class Signer(BaseModel):
    name: str = Field(min_length=2)
    email: str = Field(min_length=3)
    party: Literal["customer", "company"]


class SendIn(BaseModel):
    signers: list[Signer] = Field(min_length=2)


class SignIn(BaseModel):
    signature_text: str = ""
    signature_image: str | None = None
    agree: bool = False
    decline: bool = False


class PolicyIn(BaseModel):
    name: str
    rule_type: Literal["discount_pct", "payment_terms", "credit_hold", "tcv"]
    threshold: float | None = None
    approver_role: Literal["sales_manager", "finance"]
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
    quote = Quote(deal_id=deal.id, quote_number=await cpq.next_quote_number(db), name=body.name or f"{deal.title} quote",
                  currency=body.currency.upper(), term_months=body.term_months, payment_terms=body.payment_terms.upper(), notes=body.notes,
                  created_by=p.id, status="draft")
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
    quote.name = body.name or quote.name
    quote.currency, quote.term_months, quote.payment_terms, quote.notes = body.currency.upper(), body.term_months, body.payment_terms.upper(), body.notes
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


@router.get("/approvals")
async def approvals_inbox(status: Literal["pending", "decided", "all"] = "pending", db: AsyncSession = Depends(get_db),
                          p: Principal = Depends(authorize("approvals", "read"))):
    stmt = (select(ApprovalRequest).options(selectinload(ApprovalRequest.quote).joinedload(Quote.deal).joinedload(Deal.account))
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
                    "can_decide": a.status == "pending" and p.user.role in cpq.APPROVER_ROLES[a.required_role],
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
        await clm.send_for_signature(db, doc, [s.model_dump() for s in body.signers])
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
    return {"document": {"title": doc.title, "status": doc.status, "body_html": clm.to_html(doc.body), "content_sha256": doc.content_sha256},
            "signer": {"name": req.signer_name, "email": req.signer_email, "party": req.signer_party, "status": req.status},
            "your_turn": req.status == "pending" and clm.is_turn(req, doc) and doc.status not in ("completed", "voided"),
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
