"""Closed-Won validation, order generation and CRM -> ERP sales-order hand-off (lead-to-order, step 7).

Readiness   the same checks as the Enterprise Solution Sale Closed-Won gate: signed Order Form, primary
            quote approved, PO number, bill-to and ship-to addresses, tax-exempt certificate when exempt.
Order       built from the primary quote: header (customer master link, PO, payment terms, billing
            frequency, requested delivery date, incoterms, addresses) and lines (SKU, quantity, agreed
            unit price, discounts, billing schedule). Creating the order locks the primary quote.
Hand-off    orders are queued (status ``submitted``) and pushed asynchronously by the ``erp_orders`` job
            through the ERP connector:
              demo  acknowledges immediately with a sales-order number
              file  writes ``outbound/sales-order-<no>.json``; ERP acks arrive in ``inbound/order-acks.json``
              rest  ``POST {ERP_REST_URL}/sales-orders`` -> {erp_order_id, status}
            Failures are retried up to 5 times; acknowledgements emit ``order.acknowledged`` to the
            SDC ecosystem and update the timeline.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Account, Activity, Contract, Deal, Order, OrderLine, PipelineStage, Quote, User
from app.services.notify import emit, notify

MAX_ATTEMPTS = 5
PERIOD_MONTHS = {"annual": 12, "quarterly": 3, "monthly": 1}
_CENT = Decimal("0.01")


class OrderError(ValueError):
    pass


async def readiness(db: AsyncSession, deal: Deal) -> list[dict]:
    from app.services.pipeline_service import _check, _Ctx
    from app.services.pipeline_templates import ORDER_READY

    ctx = _Ctx(db, deal, None, None)
    return [{"criterion": r["label"], "type": r["type"], "met": await _check(r, ctx)} for r in ORDER_READY]


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    last = (date(year + (month // 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(d.day, last))


def billing_schedule(billing_type: str, net_unit: Decimal, qty: Decimal, term_months: int, frequency: str, start: date) -> list[dict]:
    if billing_type == "one_time":
        return [{"invoice_date": start.isoformat(), "period": "one-time", "amount": float((net_unit * qty).quantize(_CENT, ROUND_HALF_UP))}]
    step = PERIOD_MONTHS.get(frequency, 12)
    out, month = [], 0
    for _ in range(math.ceil(term_months / step)):
        months = min(step, term_months - month)
        begin = _add_months(start, month)
        end = _add_months(start, month + months) - timedelta(days=1)
        out.append({"invoice_date": begin.isoformat(), "period": f"{begin.isoformat()} to {end.isoformat()}",
                    "amount": float((net_unit * qty * months).quantize(_CENT, ROUND_HALF_UP))})
        month += months
    return out


async def next_order_number(db: AsyncSession) -> str:
    year = date.today().year
    count = (await db.execute(select(func.count()).select_from(Order).where(Order.order_number.like(f"ORD-{year}-%")))).scalar_one()
    return f"ORD-{year}-{count + 1:04d}"


async def primary_quote(db: AsyncSession, deal: Deal) -> Quote | None:
    return (await db.execute(select(Quote).where(Quote.deal_id == deal.id, Quote.is_primary.is_(True)))).scalars().first()


async def create_order(db: AsyncSession, deal: Deal, user: User | None, *, enforce: bool = True) -> Order:
    existing = (await db.execute(select(Order).where(Order.deal_id == deal.id, Order.status != "cancelled"))).scalars().first()
    if existing:
        raise OrderError(f"Order {existing.order_number} already exists for this deal")
    checks = await readiness(db, deal)
    missing = [c["criterion"] for c in checks if not c["met"]]
    if enforce and missing:
        raise OrderError("Not ready to order: " + "; ".join(missing))
    quote = await primary_quote(db, deal)
    if quote is None or quote.status not in ("approved", "sent", "accepted"):
        raise OrderError("The deal needs an approved primary quote")
    await db.refresh(quote, ["lines"])
    contract = (await db.execute(select(Contract).where(Contract.quote_id == quote.id))).scalars().first()
    start = contract.start_date if contract else (deal.requested_delivery_date or date.today())
    account = await db.get(Account, deal.account_id)
    order = Order(order_number=await next_order_number(db), account_id=deal.account_id, deal_id=deal.id, quote_id=quote.id,
                  contract_id=contract.id if contract else None, status="submitted", currency=quote.currency, po_number=deal.po_number,
                  payment_terms=quote.payment_terms, billing_frequency=quote.billing_frequency, term_months=quote.term_months, start_date=start,
                  requested_delivery_date=deal.requested_delivery_date, incoterms=deal.incoterms,
                  bill_to=deal.bill_to or account.billing_address or {}, ship_to=deal.ship_to or {}, tax_exempt=deal.tax_exempt,
                  total=quote.tcv, submitted_at=datetime.now(timezone.utc), created_by=user.id if user else None)
    db.add(order)
    await db.flush()
    line_no = {}
    for i, l in enumerate(sorted(quote.lines, key=lambda l: l.position), start=1):
        line_no[l.id] = i * 10
        effective = (1 - (1 - float(l.discount_pct or 0) / 100) * (1 - float(l.promo_discount_pct or 0) / 100)) * 100
        db.add(OrderLine(order_id=order.id, line_no=i * 10, parent_line_no=line_no.get(l.parent_line_id), product_id=l.product_id,
                         sku=l.product.sku, name=l.description or l.product.name, quantity=l.quantity, unit_list_price=l.list_unit_price,
                         discount_pct=Decimal(str(round(effective, 2))), net_unit_price=l.net_unit_price, line_total=l.line_total,
                         billing_type=l.billing_type,
                         billing_schedule=[] if l.is_included else billing_schedule(l.billing_type, l.net_unit_price, l.quantity,
                                                                                    quote.term_months, quote.billing_frequency, start)))
    now = datetime.now(timezone.utc)
    quote.locked_at = quote.locked_at or now
    db.add(Activity(account_id=deal.account_id, deal_id=deal.id, user_id=user.id if user else None, activity_type="system", source="system",
                    sentiment="positive", summary=f"Order {order.order_number} created from {quote.quote_number} "
                                                  f"({quote.currency} {float(quote.tcv):,.2f}); primary quote locked; queued for ERP"))
    emit(db, "order.created", "order", order.id, {"order_number": order.order_number, "account_id": str(deal.account_id),
                                                  "deal_id": str(deal.id), "quote_number": quote.quote_number, "currency": order.currency,
                                                  "total": float(order.total), "po_number": order.po_number})
    await db.flush()
    await db.refresh(order, ["lines"])
    return order


def sales_order_payload(order: Order, account: Account) -> dict:
    return {
        "order_number": order.order_number, "crm_order_id": str(order.id),
        "sold_to": {"erp_customer_id": account.erp_customer_id, "crm_account_id": str(account.id), "legal_name": account.legal_name or account.name,
                    "tax_id": account.tax_id, "domain": account.domain},
        "po_number": order.po_number, "currency": order.currency, "payment_terms": order.payment_terms,
        "billing_frequency": order.billing_frequency, "term_months": order.term_months,
        "start_date": order.start_date.isoformat() if order.start_date else None,
        "requested_delivery_date": order.requested_delivery_date.isoformat() if order.requested_delivery_date else None,
        "incoterms": order.incoterms, "bill_to": order.bill_to, "ship_to": order.ship_to, "tax_exempt": order.tax_exempt,
        "total": float(order.total),
        "lines": [{"line_no": l.line_no, "parent_line_no": l.parent_line_no, "sku": l.sku, "description": l.name, "quantity": float(l.quantity),
                   "unit_list_price": float(l.unit_list_price), "discount_pct": float(l.discount_pct), "net_unit_price": float(l.net_unit_price),
                   "line_total": float(l.line_total), "billing_type": l.billing_type, "billing_schedule": l.billing_schedule} for l in order.lines],
    }


async def _send(order: Order, payload: dict) -> dict:
    connector = settings.erp_connector
    if connector == "demo":
        n = int(hashlib.sha256(order.order_number.encode()).hexdigest()[:6], 16) % 1_000_000
        return {"status": "acknowledged", "erp_order_id": f"SO-45{n:06d}", "message": "Sales order created (demo ERP)"}
    if connector == "file":
        out = Path(settings.erp_exchange_dir) / "outbound"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"sales-order-{order.order_number}.json").write_text(json.dumps(payload, indent=2, default=str))
        return {"status": "sent_to_erp", "message": f"Written to outbound/sales-order-{order.order_number}.json"}
    if connector == "rest":
        if not settings.erp_rest_url:
            raise RuntimeError("ERP_REST_URL is not configured")
        headers = {"Authorization": f"Bearer {settings.erp_rest_token}"} if settings.erp_rest_token else {}
        async with httpx.AsyncClient(timeout=60, headers=headers) as c:
            r = await c.post(f"{settings.erp_rest_url.rstrip('/')}/sales-orders", json=payload)
            r.raise_for_status()
            data = r.json() or {}
        ack = data.get("erp_order_id") or data.get("sales_order") or data.get("id")
        return {"status": "acknowledged" if ack else "sent_to_erp", "erp_order_id": ack, "message": data.get("message")}
    raise RuntimeError("ERP connector is disabled")


async def _acknowledge(db: AsyncSession, order: Order, erp_order_id: str, message: str | None = None) -> None:
    order.status, order.erp_order_id, order.erp_status = "acknowledged", erp_order_id, "created"
    order.erp_message, order.erp_acknowledged_at = message, datetime.now(timezone.utc)
    deal = await db.get(Deal, order.deal_id) if order.deal_id else None
    db.add(Activity(account_id=order.account_id, deal_id=order.deal_id, activity_type="system", source="system", sentiment="positive",
                    summary=f"ERP sales order {erp_order_id} created for {order.order_number}: billing, fulfilment and revenue recognition started"))
    notify(db, [deal.owner_id if deal else None, order.created_by], "order", f"{order.order_number} accepted by ERP as {erp_order_id}", None,
           f"/orders/{order.id}")
    emit(db, "order.acknowledged", "order", order.id, {"order_number": order.order_number, "erp_order_id": erp_order_id,
                                                       "account_id": str(order.account_id), "total": float(order.total), "currency": order.currency})


async def push(db: AsyncSession, order: Order) -> Order:
    if order.status not in ("submitted", "failed"):
        return order
    account = await db.get(Account, order.account_id)
    if not account.erp_customer_id and settings.erp_connector == "demo":  # new customer: demo ERP creates the customer master
        account.erp_customer_id = f"C{int(hashlib.sha256(str(account.id).encode()).hexdigest()[:6], 16) % 1_000_000:06d}"
        account.erp_synced_at = datetime.now(timezone.utc)
    await db.refresh(order, ["lines"])
    order.erp_attempts += 1
    order.erp_sent_at = datetime.now(timezone.utc)
    try:
        result = await _send(order, sales_order_payload(order, account))
    except Exception as exc:
        order.status, order.erp_message = "failed", str(exc)[:1000]
        if order.erp_attempts >= MAX_ATTEMPTS:
            deal = await db.get(Deal, order.deal_id) if order.deal_id else None
            notify(db, [deal.owner_id if deal else None, order.created_by], "order", f"{order.order_number} could not reach the ERP",
                   order.erp_message, f"/orders/{order.id}")
        await db.flush()
        return order
    if result["status"] == "acknowledged" and result.get("erp_order_id"):
        await _acknowledge(db, order, result["erp_order_id"], result.get("message"))
    else:
        order.status, order.erp_status, order.erp_message = "sent_to_erp", "pending_ack", result.get("message")
    await db.flush()
    return order


async def read_file_acks(db: AsyncSession) -> int:
    path = Path(settings.erp_exchange_dir) / "inbound" / "order-acks.json"
    if settings.erp_connector != "file" or not path.exists():
        return 0
    done = 0
    for ack in json.loads(path.read_text()):
        order = (await db.execute(select(Order).where(Order.order_number == ack.get("order_number")))).scalars().first()
        if order is None or order.status == "acknowledged":
            continue
        if ack.get("status") in ("error", "rejected"):
            order.status, order.erp_status, order.erp_message = "failed", ack.get("status"), ack.get("message")
        elif ack.get("erp_order_id"):
            await _acknowledge(db, order, ack["erp_order_id"], ack.get("message"))
            done += 1
    await db.flush()
    return done


async def process_queue(db: AsyncSession) -> dict:
    stats = {"pushed": 0, "acknowledged": 0, "failed": 0}
    queue = (await db.execute(select(Order).where(Order.status.in_(("submitted", "failed")), Order.erp_attempts < MAX_ATTEMPTS)
                              .order_by(Order.created_at))).scalars().unique().all()
    for order in queue:
        await push(db, order)
        stats["pushed"] += 1
        if order.status == "acknowledged":
            stats["acknowledged"] += 1
        elif order.status == "failed":
            stats["failed"] += 1
    stats["acknowledged"] += await read_file_acks(db)
    await db.commit()
    return stats


async def on_closed_won(db: AsyncSession, deal: Deal, user: User | None) -> Order | None:
    """Lock the primary quote and raise the order automatically when everything the ERP needs is present."""
    quote = await primary_quote(db, deal)
    if quote and not quote.locked_at:
        quote.locked_at = datetime.now(timezone.utc)
    if not all(c["met"] for c in await readiness(db, deal)):
        return None
    try:
        return await create_order(db, deal, user)
    except OrderError:
        return None


def order_out(o: Order) -> dict:
    return {
        "id": o.id, "order_number": o.order_number, "status": o.status, "account": {"id": o.account.id, "name": o.account.name,
                                                                                 "erp_customer_id": o.account.erp_customer_id},
        "deal_id": o.deal_id, "quote_id": o.quote_id, "contract_id": o.contract_id, "currency": o.currency, "po_number": o.po_number,
        "payment_terms": o.payment_terms, "billing_frequency": o.billing_frequency, "term_months": o.term_months, "start_date": o.start_date,
        "requested_delivery_date": o.requested_delivery_date, "incoterms": o.incoterms, "bill_to": o.bill_to, "ship_to": o.ship_to,
        "tax_exempt": o.tax_exempt, "total": float(o.total), "erp_order_id": o.erp_order_id, "erp_status": o.erp_status,
        "erp_message": o.erp_message, "erp_attempts": o.erp_attempts, "submitted_at": o.submitted_at, "erp_sent_at": o.erp_sent_at,
        "erp_acknowledged_at": o.erp_acknowledged_at, "created_at": o.created_at,
        "lines": [{"line_no": l.line_no, "parent_line_no": l.parent_line_no, "sku": l.sku, "name": l.name, "quantity": float(l.quantity),
                   "unit_list_price": float(l.unit_list_price), "discount_pct": float(l.discount_pct), "net_unit_price": float(l.net_unit_price),
                   "line_total": float(l.line_total), "billing_type": l.billing_type, "billing_schedule": l.billing_schedule} for l in o.lines],
    }
