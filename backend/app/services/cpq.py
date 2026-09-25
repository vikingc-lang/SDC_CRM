"""Lightweight CPQ (pillar 4): rate cards, volume tiers, TCV and approval routing.

Pricing
    * Rate cards are per product and currency with volume tiers
      ``[{"min_qty": 1, "unit_price": 60}, {"min_qty": 100, "unit_price": 52}]``.
      The whole quantity is priced at the highest tier reached (volume pricing).
    * Recurring lines are priced per month:  line TCV = net unit x qty x term.
      One-time lines: qty x net unit.
    * ACV = recurring monthly net x 12; TCV = sum of line totals; discount
      total = list TCV - net TCV.

Approval routing (``approval_policies``)
    discount_pct   max line discount above threshold -> approver role
    payment_terms  payment days above threshold (e.g. NET60 > 45) -> finance
    credit_hold    account on ERP credit hold -> finance
    tcv            deal size above threshold -> sales manager
Each triggered role must approve; any rejection rejects the quote.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import current_user_id
from app.models import ApprovalPolicy, ApprovalRequest, Deal, PriceBookEntry, Product, Quote, QuoteLine, User
from app.services.notify import emit, notify

STANDARD_TERMS = ("NET15", "NET30", "NET45")
APPROVER_ROLES = {"sales_manager": ("sales_manager", "super_admin"), "finance": ("super_admin",)}
_CENT = Decimal("0.01")


class PricingError(ValueError):
    pass


def tier_price(tiers: list[dict], qty: float) -> Decimal:
    ordered = sorted(tiers, key=lambda t: t["min_qty"])
    if not ordered:
        raise PricingError("Rate card has no tiers")
    price = ordered[0]["unit_price"]
    for t in ordered:
        if qty >= t["min_qty"]:
            price = t["unit_price"]
    return Decimal(str(price))


def payment_days(terms: str) -> int:
    m = re.search(r"(\d+)", terms or "")
    return int(m.group(1)) if m else 0


async def price_line(db: AsyncSession, product_id, currency: str, qty: float, discount_pct: float, term_months: int) -> dict:
    product = await db.get(Product, product_id)
    if product is None or not product.active:
        raise PricingError("Product not found or inactive")
    entry = next((p for p in product.prices if p.currency == currency), None)
    if entry is None:
        raise PricingError(f"No {currency} rate card for {product.sku}")
    if not 0 <= discount_pct <= 100:
        raise PricingError("Discount must be between 0 and 100%")
    q = Decimal(str(qty))
    list_unit = tier_price(entry.tiers, qty)
    net_unit = (list_unit * (Decimal(100) - Decimal(str(discount_pct))) / Decimal(100)).quantize(Decimal("0.0001"), ROUND_HALF_UP)
    periods = Decimal(term_months) if product.billing_type == "recurring" else Decimal(1)
    return {
        "product": product,
        "list_unit_price": list_unit,
        "net_unit_price": net_unit,
        "line_total": (net_unit * q * periods).quantize(_CENT, ROUND_HALF_UP),
        "list_line_total": (list_unit * q * periods).quantize(_CENT, ROUND_HALF_UP),
        "monthly_net": (net_unit * q).quantize(_CENT, ROUND_HALF_UP) if product.billing_type == "recurring" else Decimal(0),
    }


async def next_quote_number(db: AsyncSession) -> str:
    year = date.today().year
    count = (await db.execute(select(func.count()).select_from(Quote).where(Quote.quote_number.like(f"Q-{year}-%")))).scalar_one()
    return f"Q-{year}-{count + 1:04d}"


async def rebuild(db: AsyncSession, quote: Quote, lines: list[dict]) -> Quote:
    """Replace lines and recompute every total. Resets approval state."""
    await db.refresh(quote, ["lines", "approvals"])
    for old in list(quote.lines):
        await db.delete(old)
    await db.flush()
    list_total = tcv = one_time = monthly = Decimal(0)
    max_disc = Decimal(0)
    new_lines = []
    for i, raw in enumerate(lines):
        priced = await price_line(db, raw["product_id"], quote.currency, float(raw["quantity"]), float(raw.get("discount_pct") or 0), quote.term_months)
        p = priced["product"]
        new_lines.append(QuoteLine(
            quote_id=quote.id, product_id=p.id, position=i, description=raw.get("description") or p.name,
            quantity=Decimal(str(raw["quantity"])), list_unit_price=priced["list_unit_price"],
            discount_pct=Decimal(str(raw.get("discount_pct") or 0)), net_unit_price=priced["net_unit_price"],
            billing_type=p.billing_type, line_total=priced["line_total"],
        ))
        list_total += priced["list_line_total"]
        tcv += priced["line_total"]
        monthly += priced["monthly_net"]
        if p.billing_type == "one_time":
            one_time += priced["line_total"]
        max_disc = max(max_disc, Decimal(str(raw.get("discount_pct") or 0)))
    db.add_all(new_lines)
    quote.list_total, quote.tcv, quote.one_time_total = list_total, tcv, one_time
    quote.discount_total = (list_total - tcv).quantize(_CENT)
    quote.acv = (monthly * 12).quantize(_CENT)
    quote.max_discount_pct = max_disc
    if quote.status != "draft":
        for a in quote.approvals:
            if a.status == "pending":
                a.status = "superseded"
        quote.status, quote.approved_at = "draft", None
    await db.flush()
    await db.refresh(quote, ["lines", "approvals"])
    return quote


async def required_approvals(db: AsyncSession, quote: Quote, deal: Deal) -> list[dict]:
    policies = (await db.execute(select(ApprovalPolicy).where(ApprovalPolicy.active.is_(True)))).scalars().all()
    needed: dict[str, list[str]] = {}
    effective = float(quote.discount_total / quote.list_total * 100) if quote.list_total else 0.0
    for p in policies:
        thr = float(p.threshold or 0)
        reason = None
        if p.rule_type == "discount_pct" and float(quote.max_discount_pct) > thr:
            reason = f"Line discount {float(quote.max_discount_pct):g}% exceeds {thr:g}% ({p.name}); effective discount {effective:.1f}%"
        elif p.rule_type == "payment_terms" and payment_days(quote.payment_terms) > thr:
            reason = f"Non-standard payment terms {quote.payment_terms} (> NET{int(thr)})"
        elif p.rule_type == "credit_hold" and deal.account.credit_hold:
            reason = "Account is on ERP credit hold"
        elif p.rule_type == "tcv" and float(quote.tcv) > thr:
            reason = f"TCV {float(quote.tcv):,.0f} {quote.currency} exceeds {thr:,.0f}"
        if reason:
            needed.setdefault(p.approver_role, []).append(reason)
    return [{"required_role": role, "reason": "; ".join(reasons)} for role, reasons in needed.items()]


async def submit(db: AsyncSession, quote: Quote) -> Quote:
    if quote.status not in ("draft", "rejected"):
        raise PricingError(f"Quote is {quote.status}; edit it to resubmit")
    if not quote.lines:
        raise PricingError("Add at least one line item")
    deal = await db.get(Deal, quote.deal_id)
    needed = await required_approvals(db, quote, deal)
    for a in quote.approvals:
        if a.status == "pending":
            a.status = "superseded"
    if not needed:
        quote.status, quote.approved_at = "approved", datetime.now(timezone.utc)
        await _on_approved(db, quote, deal)
    else:
        quote.status = "pending_approval"
        for n in needed:
            db.add(ApprovalRequest(quote_id=quote.id, **n))
            approvers = (await db.execute(select(User.id).where(User.role.in_(APPROVER_ROLES[n["required_role"]]), User.is_active.is_(True)))).scalars().all()
            notify(db, approvers, "approval", f"Approval needed: {quote.quote_number} ({deal.account.name})", n["reason"], f"/quotes/{quote.id}")
    quote.valid_until = quote.valid_until or date.today() + timedelta(days=30)
    await db.flush()
    await db.refresh(quote, ["approvals"])
    return quote


async def decide(db: AsyncSession, request: ApprovalRequest, user: User, approve: bool, comment: str | None) -> Quote:
    if request.status != "pending":
        raise PricingError("This approval was already decided")
    if user.role not in APPROVER_ROLES[request.required_role]:
        raise PermissionError(f"Only {request.required_role.replace('_', ' ')} approvers can decide this request")
    request.status = "approved" if approve else "rejected"
    request.decided_by, request.decided_at, request.comment = user.id, datetime.now(timezone.utc), comment
    quote = await db.get(Quote, request.quote_id)
    await db.refresh(quote, ["approvals"])
    deal = await db.get(Deal, quote.deal_id)
    open_ = [a for a in quote.approvals if a.status == "pending"]
    if not approve:
        quote.status = "rejected"
        for a in open_:
            a.status = "superseded"
        notify(db, [quote.created_by, deal.owner_id], "approval", f"{quote.quote_number} rejected", comment or request.reason, f"/quotes/{quote.id}")
    elif not open_:
        quote.status, quote.approved_at = "approved", datetime.now(timezone.utc)
        await _on_approved(db, quote, deal)
        notify(db, [quote.created_by, deal.owner_id], "approval", f"{quote.quote_number} approved", comment, f"/quotes/{quote.id}")
    await db.flush()
    return quote


async def _on_approved(db: AsyncSession, quote: Quote, deal: Deal) -> None:
    """An approved quote sets the deal's approved amount (TCV) and feeds pricing yield analytics."""
    deal.amount, deal.currency = quote.tcv, quote.currency
    emit(db, "quote.approved", "quote", quote.id, {
        "quote_number": quote.quote_number, "deal_id": str(deal.id), "account_id": str(deal.account_id),
        "currency": quote.currency, "acv": float(quote.acv), "tcv": float(quote.tcv), "discount_total": float(quote.discount_total),
        "lines": [{"sku": l.product.sku, "qty": float(l.quantity), "list": float(l.list_unit_price), "net": float(l.net_unit_price),
                   "discount_pct": float(l.discount_pct)} for l in quote.lines],
        "approved_by": str(current_user_id.get()) if current_user_id.get() else None,
    })


def quote_out(q: Quote) -> dict:
    return {
        "id": q.id, "deal_id": q.deal_id, "quote_number": q.quote_number, "name": q.name, "currency": q.currency,
        "term_months": q.term_months, "payment_terms": q.payment_terms, "status": q.status, "valid_until": q.valid_until,
        "list_total": float(q.list_total), "discount_total": float(q.discount_total), "max_discount_pct": float(q.max_discount_pct),
        "one_time_total": float(q.one_time_total), "acv": float(q.acv), "tcv": float(q.tcv), "notes": q.notes,
        "approved_at": q.approved_at, "created_at": q.created_at,
        "deal": {"id": q.deal.id, "title": q.deal.title, "account": {"id": q.deal.account.id, "name": q.deal.account.name,
                 "credit_hold": q.deal.account.credit_hold}} if q.deal else None,
        "lines": [{
            "id": l.id, "product_id": l.product_id, "sku": l.product.sku, "name": l.product.name, "description": l.description,
            "billing_type": l.billing_type, "unit": l.product.unit, "quantity": float(l.quantity), "list_unit_price": float(l.list_unit_price),
            "discount_pct": float(l.discount_pct), "net_unit_price": float(l.net_unit_price), "line_total": float(l.line_total),
        } for l in q.lines],
        "approvals": [{
            "id": a.id, "required_role": a.required_role, "reason": a.reason, "status": a.status, "comment": a.comment,
            "decided_by": {"id": a.decider.id, "full_name": a.decider.full_name} if a.decider else None, "decided_at": a.decided_at,
            "created_at": a.created_at,
        } for a in q.approvals],
    }


def product_out(p: Product) -> dict:
    return {"id": p.id, "sku": p.sku, "name": p.name, "description": p.description, "family": p.family,
            "billing_type": p.billing_type, "unit": p.unit, "active": p.active,
            "prices": [{"currency": e.currency, "tiers": sorted(e.tiers, key=lambda t: t["min_qty"])} for e in p.prices]}


def price_book(entry: PriceBookEntry) -> dict:
    return {"currency": entry.currency, "tiers": entry.tiers}
