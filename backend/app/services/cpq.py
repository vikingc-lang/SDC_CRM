"""CPQ and deal desk (pillar 4; lead-to-order step 5).

Pricing
    * Rate cards per product and currency with volume tiers
      ``[{"min_qty": 1, "unit_price": 60}, {"min_qty": 100, "unit_price": 52}]``; the whole
      quantity is priced at the highest tier reached (volume pricing).
    * Price book resolution per line: customer-specific book for the account, then the regional
      book for the account's region, then the list price book (entries with no book).
    * Promotions (``quote.promo_code``) add a pre-approved discount on eligible lines; they do
      not count towards discount approval thresholds.
    * Bundles add their components as included (zero-priced) child lines. Product rules enforce
      dependencies (``requires``) and exclusions (``excludes``) across every line and component.
    * Recurring lines: line TCV = net unit x qty x term; one-time lines: qty x net unit.
      ACV = recurring monthly net x 12.

Approval chain (``approval_policies`` + ``approval_groups``)
    discount_pct   max rep line discount above threshold   -> sales manager / deal desk / VP sales
    tcv            deal size above threshold                -> VP sales (or any level)
    payment_terms  payment days above threshold             -> finance
    credit_hold    account on ERP credit hold               -> finance
    credit_risk    credit-risk score at or above threshold  -> finance
    custom_terms   non-standard legal terms on the quote     -> legal
Triggered levels are decided in order (sales manager -> deal desk -> VP sales -> finance -> legal);
only the current level is notified and may decide. Any rejection rejects the quote.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import current_user_id
from app.models import (
    Account, ApprovalGroup, ApprovalPolicy, ApprovalRequest, BundleComponent, Deal, PriceBook, PriceBookEntry, Product, ProductRule,
    Promotion, Quote, QuoteLine, User,
)
from app.services import app_settings
from app.services.notify import emit, notify

STANDARD_TERMS = ("NET15", "NET30", "NET45")
CHAIN = ("sales_manager", "deal_desk", "vp_sales", "finance", "legal")
LEVEL_LABELS = {"sales_manager": "Sales Manager", "deal_desk": "Deal Desk", "vp_sales": "VP Sales", "finance": "Finance", "legal": "Legal"}
_CENT = Decimal("0.01")
_Q4 = Decimal("0.0001")


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


def _active(obj, today: date) -> bool:
    return obj.active and (obj.valid_from is None or obj.valid_from <= today) and (obj.valid_to is None or obj.valid_to >= today)


async def price_books_for(db: AsyncSession, account: Account | None) -> list[PriceBook]:
    """Applicable books, most specific first: customer, then regional."""
    if account is None:
        return []
    today = date.today()
    conds = [PriceBook.account_id == account.id]
    if account.region:
        conds.append(PriceBook.region == account.region)
    books = (await db.execute(select(PriceBook).where(or_(*conds)))).scalars().unique().all()
    books = [b for b in books if _active(b, today)]
    return sorted(books, key=lambda b: 0 if b.kind == "customer" else 1)


def resolve_entry(product: Product, currency: str, books: list[PriceBook]) -> tuple[PriceBookEntry | None, str]:
    for book in books:
        entry = next((e for e in product.prices if e.price_book_id == book.id and e.currency == currency), None)
        if entry:
            return entry, f"{book.kind}:{book.name}"
    entry = next((e for e in product.prices if e.price_book_id is None and e.currency == currency), None)
    return entry, "list"


async def active_promotion(db: AsyncSession, code: str | None) -> Promotion | None:
    if not code:
        return None
    promo = (await db.execute(select(Promotion).where(func.upper(Promotion.code) == code.strip().upper()))).scalars().first()
    if promo is None or not _active(promo, date.today()):
        raise PricingError(f"Promotion code {code} is not valid")
    return promo


async def price_line(db: AsyncSession, product_id, currency: str, qty: float, discount_pct: float, term_months: int,
                     books: list[PriceBook] | None = None, promo: Promotion | None = None) -> dict:
    product = await db.get(Product, product_id)
    if product is None or not product.active:
        raise PricingError("Product not found or inactive")
    entry, source = resolve_entry(product, currency, books or [])
    if entry is None:
        raise PricingError(f"No {currency} rate card for {product.sku}")
    if not 0 <= discount_pct <= 100:
        raise PricingError("Discount must be between 0 and 100%")
    promo_pct = Decimal(0)
    if promo and (not promo.product_ids or str(product.id) in {str(x) for x in promo.product_ids}) and qty >= float(promo.min_quantity or 0):
        promo_pct = Decimal(str(promo.discount_pct))
    q = Decimal(str(qty))
    list_unit = tier_price(entry.tiers, qty)
    after_rep = list_unit * (Decimal(100) - Decimal(str(discount_pct))) / Decimal(100)
    net_unit = (after_rep * (Decimal(100) - promo_pct) / Decimal(100)).quantize(_Q4, ROUND_HALF_UP)
    periods = Decimal(term_months) if product.billing_type == "recurring" else Decimal(1)
    return {
        "product": product, "price_source": source, "promo_pct": promo_pct,
        "list_unit_price": list_unit,
        "net_unit_price": net_unit,
        "line_total": (net_unit * q * periods).quantize(_CENT, ROUND_HALF_UP),
        "list_line_total": (list_unit * q * periods).quantize(_CENT, ROUND_HALF_UP),
        "promo_amount": ((after_rep - net_unit) * q * periods).quantize(_CENT, ROUND_HALF_UP),
        "monthly_net": (net_unit * q).quantize(_CENT, ROUND_HALF_UP) if product.billing_type == "recurring" else Decimal(0),
    }


async def next_quote_number(db: AsyncSession) -> str:
    year = date.today().year
    count = (await db.execute(select(func.count()).select_from(Quote).where(Quote.quote_number.like(f"Q-{year}-%")))).scalar_one()
    return f"Q-{year}-{count + 1:04d}"


async def check_rules(db: AsyncSession, product_ids: set) -> list[str]:
    rules = (await db.execute(select(ProductRule).where(ProductRule.product_id.in_(product_ids)))).scalars().unique().all()
    problems = []
    for r in rules:
        if r.rule_type == "requires" and r.target_product_id not in product_ids:
            problems.append(r.message or f"{r.product.name} requires {r.target.name}")
        if r.rule_type == "excludes" and r.target_product_id in product_ids:
            problems.append(r.message or f"{r.product.name} cannot be sold with {r.target.name}")
    return sorted(set(problems))


async def rebuild(db: AsyncSession, quote: Quote, lines: list[dict]) -> Quote:
    """Replace lines and recompute every total. Resets approval state."""
    if quote.locked_at:
        raise PricingError("This quote is locked (the deal closed or an order exists); create a new quote to change it")
    await db.refresh(quote, ["lines", "approvals"])
    deal = await db.get(Deal, quote.deal_id)
    books = await price_books_for(db, deal.account if deal else None)
    promo = await active_promotion(db, quote.promo_code)
    for old in list(quote.lines):
        await db.delete(old)
    await db.flush()
    list_total = tcv = one_time = monthly = promo_total = Decimal(0)
    max_disc = Decimal(0)
    new_lines, product_ids, promo_used, position = [], set(), False, 0
    for raw in lines:
        disc = float(raw.get("discount_pct") or 0)
        priced = await price_line(db, raw["product_id"], quote.currency, float(raw["quantity"]), disc, quote.term_months, books, promo)
        p = priced["product"]
        parent = QuoteLine(
            id=uuid.uuid4(), quote_id=quote.id, product_id=p.id, position=position, description=raw.get("description") or p.name,
            quantity=Decimal(str(raw["quantity"])), list_unit_price=priced["list_unit_price"], discount_pct=Decimal(str(disc)),
            net_unit_price=priced["net_unit_price"], billing_type=p.billing_type, line_total=priced["line_total"],
            promo_discount_pct=priced["promo_pct"], price_source=priced["price_source"],
        )
        new_lines.append(parent)
        position += 1
        product_ids.add(p.id)
        list_total += priced["list_line_total"]
        tcv += priced["line_total"]
        monthly += priced["monthly_net"]
        promo_total += priced["promo_amount"]
        promo_used = promo_used or priced["promo_pct"] > 0
        if p.billing_type == "one_time":
            one_time += priced["line_total"]
        max_disc = max(max_disc, Decimal(str(disc)))
        if p.product_type == "bundle":
            comps = (await db.execute(select(BundleComponent).where(BundleComponent.bundle_id == p.id))).scalars().unique().all()
            for c in comps:
                product_ids.add(c.component_id)
                new_lines.append(QuoteLine(
                    id=uuid.uuid4(), quote_id=quote.id, product_id=c.component_id, position=position, parent_line_id=parent.id, is_included=True,
                    description=f"Included in {p.name}", quantity=(Decimal(str(raw["quantity"])) * c.quantity).quantize(_CENT),
                    list_unit_price=Decimal(0), discount_pct=Decimal(0), net_unit_price=Decimal(0), billing_type=c.component.billing_type,
                    line_total=Decimal(0), price_source="bundle",
                ))
                position += 1
    problems = await check_rules(db, product_ids)
    if problems:
        raise PricingError("; ".join(problems))
    if promo and not promo_used:
        raise PricingError(f"Promotion {promo.code} does not apply to any line on this quote")
    db.add_all(new_lines)
    quote.list_total, quote.tcv, quote.one_time_total = list_total, tcv, one_time
    quote.discount_total = (list_total - tcv).quantize(_CENT)
    quote.promo_discount_total = promo_total
    quote.acv = (monthly * 12).quantize(_CENT)
    quote.max_discount_pct = max_disc
    quote.price_book_id = books[0].id if books and any(l.price_source != "list" for l in new_lines if not l.is_included) else None
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
    risk = None
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
            reason = f"TCV {float(quote.tcv):,.0f} {quote.currency} exceeds {thr:,.0f} ({p.name})"
        elif p.rule_type == "custom_terms" and (quote.custom_terms or "").strip():
            reason = "Non-standard legal terms requested"
        elif p.rule_type == "credit_risk":
            if risk is None:
                from app.services import erp, fx
                rates = await fx.rates(db)
                risk = await erp.assess_credit_risk(db, deal.account, fx.to_usd(float(quote.tcv), quote.currency, rates))
            if risk["score"] >= thr:
                reason = f"Credit risk score {risk['score']} ({risk['band']}) at or above {thr:g}"
        if reason:
            needed.setdefault(p.approver_role, []).append(reason)
    order = (await app_settings.get(db, "approval_chain"))["order"]
    roles = sorted(needed, key=lambda r: order.index(r) if r in order else len(order))
    return [{"required_role": role, "level": i + 1, "reason": "; ".join(needed[role])} for i, role in enumerate(roles)]


async def approver_ids(db: AsyncSession, role: str) -> list:
    group = await db.get(ApprovalGroup, role)
    ids = {uuid.UUID(str(x)) for x in (group.member_ids if group else [])}
    roles = ("sales_manager", "super_admin") if role == "sales_manager" else ("super_admin",)
    ids |= set((await db.execute(select(User.id).where(User.role.in_(roles), User.is_active.is_(True)))).scalars().all())
    return list(ids)


async def can_approve(db: AsyncSession, user: User, role: str) -> bool:
    if user.role == "super_admin" or (role == "sales_manager" and user.role == "sales_manager"):
        return True
    group = await db.get(ApprovalGroup, role)
    return bool(group and str(user.id) in {str(x) for x in group.member_ids})


def current_level(quote: Quote) -> int | None:
    pending = [a.level for a in quote.approvals if a.status == "pending"]
    return min(pending) if pending else None


async def _notify_level(db: AsyncSession, quote: Quote, deal: Deal) -> None:
    level = current_level(quote)
    for a in quote.approvals:
        if a.status == "pending" and a.level == level:
            notify(db, await approver_ids(db, a.required_role), "approval",
                   f"{LEVEL_LABELS.get(a.required_role, a.required_role)} approval needed: {quote.quote_number} ({deal.account.name})",
                   a.reason, f"/quotes/{quote.id}")


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
        await db.flush()
        await db.refresh(quote, ["approvals"])
        await _notify_level(db, quote, deal)
    quote.valid_until = quote.valid_until or date.today() + timedelta(days=30)
    await db.flush()
    await db.refresh(quote, ["approvals"])
    return quote


async def decide(db: AsyncSession, request: ApprovalRequest, user: User, approve: bool, comment: str | None) -> Quote:
    if request.status != "pending":
        raise PricingError("This approval was already decided")
    if not await can_approve(db, user, request.required_role):
        raise PermissionError(f"Only {LEVEL_LABELS.get(request.required_role, request.required_role)} approvers can decide this request")
    quote = await db.get(Quote, request.quote_id)
    await db.refresh(quote, ["approvals"])
    level = current_level(quote)
    if level is not None and request.level > level:
        waiting = next(a for a in quote.approvals if a.status == "pending" and a.level == level)
        raise PricingError(f"Waiting for {LEVEL_LABELS.get(waiting.required_role, waiting.required_role)} approval first")
    request.status = "approved" if approve else "rejected"
    request.decided_by, request.decided_at, request.comment = user.id, datetime.now(timezone.utc), comment
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
    else:
        await _notify_level(db, quote, deal)
    await db.flush()
    return quote


async def set_primary(db: AsyncSession, quote: Quote) -> Quote:
    others = (await db.execute(select(Quote).where(Quote.deal_id == quote.deal_id, Quote.id != quote.id, Quote.is_primary.is_(True)))).scalars().all()
    if any(o.locked_at for o in others):
        raise PricingError("The primary quote is locked for this deal")
    for o in others:
        o.is_primary = False
    await db.flush()
    quote.is_primary = True
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
        "approved_at": q.approved_at, "created_at": q.created_at, "promo_code": q.promo_code,
        "promo_discount_total": float(q.promo_discount_total or 0), "is_primary": q.is_primary, "locked_at": q.locked_at,
        "custom_terms": q.custom_terms, "billing_frequency": q.billing_frequency, "price_book_id": q.price_book_id,
        "current_level": current_level(q),
        "deal": {"id": q.deal.id, "title": q.deal.title, "account": {"id": q.deal.account.id, "name": q.deal.account.name,
                 "credit_hold": q.deal.account.credit_hold}} if q.deal else None,
        "lines": [{
            "id": l.id, "product_id": l.product_id, "sku": l.product.sku, "name": l.product.name, "description": l.description,
            "billing_type": l.billing_type, "unit": l.product.unit, "quantity": float(l.quantity), "list_unit_price": float(l.list_unit_price),
            "discount_pct": float(l.discount_pct), "net_unit_price": float(l.net_unit_price), "line_total": float(l.line_total),
            "parent_line_id": l.parent_line_id, "is_included": l.is_included, "promo_discount_pct": float(l.promo_discount_pct or 0),
            "price_source": l.price_source,
        } for l in q.lines],
        "approvals": [{
            "id": a.id, "required_role": a.required_role, "level": a.level, "label": LEVEL_LABELS.get(a.required_role, a.required_role),
            "reason": a.reason, "status": a.status, "comment": a.comment,
            "decided_by": {"id": a.decider.id, "full_name": a.decider.full_name} if a.decider else None, "decided_at": a.decided_at,
            "created_at": a.created_at,
        } for a in q.approvals],
    }


def product_out(p: Product) -> dict:
    return {"id": p.id, "sku": p.sku, "name": p.name, "description": p.description, "family": p.family,
            "billing_type": p.billing_type, "unit": p.unit, "active": p.active, "product_type": p.product_type,
            "prices": [{"currency": e.currency, "tiers": sorted(e.tiers, key=lambda t: t["min_qty"])} for e in p.prices if e.price_book_id is None]}


def price_book(entry: PriceBookEntry) -> dict:
    return {"currency": entry.currency, "tiers": entry.tiers}
