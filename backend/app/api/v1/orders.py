"""Orders (lead-to-order, step 7): Closed-Won readiness, order generation and the ERP sales-order hand-off."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import Principal, authorize
from app.models import Activity, Deal, Order
from app.services import orders as svc

router = APIRouter(tags=["orders"])

Status = Literal["submitted", "sent_to_erp", "acknowledged", "failed", "cancelled"]


async def _order(db: AsyncSession, p: Principal, order_id: uuid.UUID) -> Order:
    order = (await db.execute(select(Order).where(Order.id == order_id).execution_options(populate_existing=True))).scalars().first()
    if order is None:
        raise HTTPException(404, "Order not found")
    await p.ensure_account(db, order.account_id, "orders")
    return order


async def _deal(db: AsyncSession, p: Principal, deal_id: uuid.UUID) -> Deal:
    deal = await db.get(Deal, deal_id)
    if deal is None:
        raise HTTPException(404, "Deal not found")
    if p.is_own_scope("deals") and deal.owner_id != p.id:
        await p.ensure_account(db, deal.account_id, "deals")
    return deal


@router.get("/orders")
async def list_orders(status: Status | None = None, account_id: uuid.UUID | None = None, limit: int = Query(100, le=500),
                      db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("orders", "read"))):
    stmt = select(Order).order_by(Order.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Order.status == status)
    if account_id:
        stmt = stmt.where(Order.account_id == account_id)
    stmt = p.scope_accounts(stmt, "orders", Order.account_id)
    return [svc.order_out(o) for o in (await db.execute(stmt)).scalars().unique().all()]


@router.get("/orders/{order_id}")
async def get_order(order_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("orders", "read"))):
    order = await _order(db, p, order_id)
    return {**svc.order_out(order), "erp_payload": svc.sales_order_payload(order, order.account)}


@router.get("/deals/{deal_id}/order-readiness")
async def order_readiness(deal_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "read"))):
    deal = await _deal(db, p, deal_id)
    checks = await svc.readiness(db, deal)
    return {"ready": all(c["met"] for c in checks), "checks": checks}


@router.post("/deals/{deal_id}/orders", status_code=201)
async def create_order(deal_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("orders", "create"))):
    deal = await _deal(db, p, deal_id)
    try:
        order = await svc.create_order(db, deal, p.user)
    except svc.OrderError as exc:
        raise HTTPException(422, str(exc))
    await db.commit()
    return svc.order_out(await _order(db, p, order.id))


@router.post("/orders/{order_id}/submit")
async def submit_order(order_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("orders", "update"))):
    """Push now instead of waiting for the ``erp_orders`` job (also resets the retry budget of a failed order)."""
    order = await _order(db, p, order_id)
    if order.status not in ("submitted", "failed"):
        raise HTTPException(422, f"Order is {order.status.replace('_', ' ')}")
    if order.status == "failed" and order.erp_attempts >= svc.MAX_ATTEMPTS:
        order.erp_attempts = 0
    await svc.push(db, order)
    await db.commit()
    return svc.order_out(await _order(db, p, order.id))


@router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("orders", "update"))):
    order = await _order(db, p, order_id)
    if order.status in ("acknowledged", "sent_to_erp"):
        raise HTTPException(422, "The ERP already has this order; cancel it there so billing and fulfilment stay consistent")
    if order.status == "cancelled":
        raise HTTPException(422, "Order is already cancelled")
    order.status = "cancelled"
    db.add(Activity(account_id=order.account_id, deal_id=order.deal_id, user_id=p.id, activity_type="system", source="system",
                    sentiment="neutral", summary=f"Order {order.order_number} cancelled by {p.user.full_name} before reaching the ERP"))
    await db.commit()
    return svc.order_out(await _order(db, p, order.id))
