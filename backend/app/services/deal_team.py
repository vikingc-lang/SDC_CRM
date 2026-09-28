"""Opportunity products, deal teams and splits.

* **Products** on a deal are priced from the account's price books in the deal currency (the same engine as
  quotes); a sales price and discount can be set per line. With ``amount_source = 'lines'`` the deal amount is
  the sum of the line totals and follows every change. "Create quote" turns the lines into a draft quote.
* **Team**: members see the deal and its account even with "own" scope (core/rbac.py); ``edit`` access also
  lets them change it. The owner is always on the team implicitly.
* **Splits**: revenue splits must total exactly 100% and replace the owner's full credit in quota attainment and
  commission; overlay splits are extra credit for specialists and may total anything.
"""
from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Deal, DealLineItem, DealSplit, DealTeamMember, Product, User
from app.models.selling import SPLIT_TYPES, TEAM_ROLES
from app.services import cpq

_CENT = Decimal("0.01")
MAX_LINES = 100


class DealTeamError(ValueError):
    pass


# ---- products ----------------------------------------------------------------------------------------------------

async def set_lines(db: AsyncSession, deal: Deal, lines: list[dict], amount_source: str | None = None) -> list[DealLineItem]:
    if len(lines) > MAX_LINES:
        raise DealTeamError(f"At most {MAX_LINES} products per deal")
    if amount_source is not None:
        if amount_source not in ("manual", "lines"):
            raise DealTeamError("The amount comes from the products or is entered by hand")
        deal.amount_source = amount_source
    books = await cpq.price_books_for(db, deal.account)
    await db.execute(delete(DealLineItem).where(DealLineItem.deal_id == deal.id))
    items = []
    for pos, raw in enumerate(lines):
        qty = Decimal(str(raw.get("quantity") or 0))
        disc = Decimal(str(raw.get("discount_pct") or 0))
        term = int(raw.get("term_months") or 12)
        if qty <= 0:
            raise DealTeamError("Quantity must be more than zero")
        if not 0 <= disc <= 100:
            raise DealTeamError("Discount must be between 0 and 100%")
        if not 1 <= term <= 120:
            raise DealTeamError("Term must be 1 to 120 months")
        product = await db.get(Product, uuid.UUID(str(raw["product_id"])))
        if product is None or not product.active:
            raise DealTeamError("Product not found or inactive")
        if raw.get("unit_price") not in (None, ""):
            unit = Decimal(str(raw["unit_price"]))
            if unit < 0:
                raise DealTeamError("Sales price can't be negative")
        else:
            try:
                unit = (await cpq.price_line(db, product.id, deal.currency, float(qty), 0, term, books))["list_unit_price"]
            except cpq.PricingError as e:
                raise DealTeamError(f"{e}; enter a sales price for {product.name}") from e
        periods = Decimal(term) if product.billing_type == "recurring" else Decimal(1)
        total = (unit * (Decimal(100) - disc) / Decimal(100) * qty * periods).quantize(_CENT, ROUND_HALF_UP)
        item = DealLineItem(id=uuid.uuid4(), deal_id=deal.id, product_id=product.id, position=pos, description=(raw.get("description") or None),
                            quantity=qty, unit_price=unit, discount_pct=disc, term_months=term, total=total, billing_type=product.billing_type)
        db.add(item)
        items.append(item)
    await db.flush()
    if deal.amount_source == "lines":
        deal.amount = sum((i.total for i in items), Decimal(0))
    return items


async def lines_of(db: AsyncSession, deal_id) -> list[DealLineItem]:
    return list((await db.execute(select(DealLineItem).where(DealLineItem.deal_id == deal_id).order_by(DealLineItem.position))).scalars().unique())


def line_out(i: DealLineItem) -> dict:
    return {"id": i.id, "product_id": i.product_id, "sku": i.product.sku, "name": i.product.name, "description": i.description,
            "billing_type": i.billing_type, "unit": i.product.unit, "quantity": float(i.quantity), "unit_price": float(i.unit_price),
            "discount_pct": float(i.discount_pct), "term_months": i.term_months, "total": float(i.total)}


# ---- team --------------------------------------------------------------------------------------------------------

async def set_member(db: AsyncSession, deal: Deal, user_id, role: str, access: str) -> DealTeamMember:
    if role not in TEAM_ROLES:
        raise DealTeamError(f"Role must be one of: {', '.join(TEAM_ROLES)}")
    if access not in ("read", "edit"):
        raise DealTeamError("Access is read or edit")
    user = await db.get(User, uuid.UUID(str(user_id)))
    if user is None or not user.is_active or user.role == "partner":
        raise DealTeamError("Choose an active internal user")
    if user.id == deal.owner_id:
        raise DealTeamError("The owner is always on the deal team")
    member = await db.get(DealTeamMember, (deal.id, user.id))
    if member is None:
        member = DealTeamMember(deal_id=deal.id, user_id=user.id, role=role, access=access)
        db.add(member)
    else:
        member.role, member.access = role, access
    await db.flush()
    return member


async def team_of(db: AsyncSession, deal_id) -> list[DealTeamMember]:
    return list((await db.execute(select(DealTeamMember).where(DealTeamMember.deal_id == deal_id).order_by(DealTeamMember.added_at))).scalars().unique())


def member_out(m: DealTeamMember) -> dict:
    return {"user": {"id": m.user.id, "full_name": m.user.full_name, "role": m.user.role}, "role": m.role, "access": m.access, "added_at": m.added_at}


# ---- splits ------------------------------------------------------------------------------------------------------

async def set_splits(db: AsyncSession, deal: Deal, splits: list[dict]) -> list[DealSplit]:
    """Replace all splits. Revenue splits must total 100% (or be absent: the owner gets full credit)."""
    seen, clean = set(), []
    for s in splits:
        kind = s.get("split_type", "revenue")
        if kind not in SPLIT_TYPES:
            raise DealTeamError("Split type is revenue or overlay")
        pct = Decimal(str(s.get("percent") or 0))
        if not 0 < pct <= 100:
            raise DealTeamError("Each split is more than 0% and at most 100%")
        uid = uuid.UUID(str(s["user_id"]))
        if (uid, kind) in seen:
            raise DealTeamError("Each person can have one split of each type")
        seen.add((uid, kind))
        user = await db.get(User, uid)
        if user is None or not user.is_active:
            raise DealTeamError("Splits go to active users")
        clean.append((uid, kind, pct))
    revenue = sum((p for _, k, p in clean if k == "revenue"), Decimal(0))
    if clean and any(k == "revenue" for _, k, _ in clean) and revenue != 100:
        raise DealTeamError(f"Revenue splits must add up to 100% (now {revenue.normalize():f}%)")
    await db.execute(delete(DealSplit).where(DealSplit.deal_id == deal.id))
    rows = [DealSplit(id=uuid.uuid4(), deal_id=deal.id, user_id=u, split_type=k, percent=p) for u, k, p in clean]
    db.add_all(rows)
    # people with a split can see the deal
    for u, _, _ in clean:
        if u != deal.owner_id and await db.get(DealTeamMember, (deal.id, u)) is None:
            db.add(DealTeamMember(deal_id=deal.id, user_id=u, role="Overlay Specialist" if (u, "overlay") in seen and (u, "revenue") not in seen
                                  else "Other", access="read"))
    await db.flush()
    return rows


async def splits_of(db: AsyncSession, deal_id) -> list[DealSplit]:
    return list((await db.execute(select(DealSplit).where(DealSplit.deal_id == deal_id).order_by(DealSplit.split_type, DealSplit.percent.desc()))).scalars().unique())


def split_out(s: DealSplit, amount: float) -> dict:
    return {"id": s.id, "user": {"id": s.user.id, "full_name": s.user.full_name}, "split_type": s.split_type, "percent": float(s.percent),
            "amount": round(amount * float(s.percent) / 100, 2)}


async def revenue_shares(db: AsyncSession, deal_ids) -> dict:
    """{deal_id: {user_id: share 0..1}} for deals with revenue splits."""
    out: dict = {}
    if not deal_ids:
        return out
    for s in (await db.execute(select(DealSplit).where(DealSplit.deal_id.in_(list(deal_ids)), DealSplit.split_type == "revenue"))).scalars().unique():
        out.setdefault(s.deal_id, {})[s.user_id] = float(s.percent) / 100
    return out


def credit(deal: Deal, user_id, shares: dict) -> float:
    """The share of a deal's amount credited to ``user_id``: its revenue split, else all of it for the owner."""
    s = shares.get(deal.id)
    if s:
        return s.get(user_id, 0.0)
    return 1.0 if deal.owner_id == user_id else 0.0


async def ensure_editable(db: AsyncSession, p, deal: Deal) -> None:
    """Own-scope users who see a deal only through its team need edit access to change it."""
    from fastapi import HTTPException

    from app.models import Account

    if not p.is_own_scope("deals") or deal.owner_id == p.user.id:
        return
    base = p.owned_account_ids(include_team=False).where(Account.id == deal.account_id)
    if (await db.execute(base)).first():
        return
    member = await db.get(DealTeamMember, (deal.id, p.user.id))
    if member is None or member.access != "edit":
        raise HTTPException(403, "You're on this deal's team with read-only access")
