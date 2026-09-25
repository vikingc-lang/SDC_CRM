import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rbac import Principal, authorize
from app.models import Account, ErpSyncRun, IntegrationEvent
from app.services import erp

router = APIRouter(tags=["finance & integrations"])


@router.get("/finance/accounts/{account_id}/ar")
async def account_ar(account_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("finance", "read"))):
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    await p.ensure_account(db, account_id, "finance")
    return await erp.ar_summary(db, account)


@router.get("/finance/ar-aging")
async def ar_aging(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("finance", "read"))):
    accounts = (await db.execute(p.scope_accounts(select(Account), "finance").where(Account.erp_customer_id.is_not(None)))).scalars().unique().all()
    rows, totals = [], {b[0]: 0.0 for b in erp.AGING_BUCKETS}
    for a in accounts:
        s = await erp.ar_summary(db, a)
        for k, v in s["buckets"].items():
            totals[k] += v
        rows.append({"account": {"id": a.id, "name": a.name}, **{k: v for k, v in s.items() if k != "invoices"}})
    rows.sort(key=lambda r: -r["overdue_balance"])
    return {"totals": {k: round(v, 2) for k, v in totals.items()}, "open_balance": round(sum(totals.values()), 2),
            "credit_holds": sum(1 for r in rows if r["credit_hold"]), "accounts": rows}


@router.post("/integrations/erp/sync")
async def erp_sync(direction: str = "inbound", db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("finance", "update"))):
    run = await (erp.sync_inbound(db) if direction == "inbound" else erp.sync_outbound(db))
    await db.commit()
    return {"id": run.id, "connector": run.connector, "direction": run.direction, "status": run.status, "stats": run.stats, "error": run.error}


@router.get("/integrations/erp/runs")
async def erp_runs(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("finance", "read"))):
    runs = (await db.execute(select(ErpSyncRun).order_by(ErpSyncRun.started_at.desc()).limit(20))).scalars().all()
    return {"connector": settings.erp_connector, "runs": [{"id": r.id, "connector": r.connector, "direction": r.direction, "status": r.status,
            "stats": r.stats, "error": r.error, "started_at": r.started_at, "finished_at": r.finished_at} for r in runs]}


@router.get("/integrations/events")
async def events_feed(after_id: int = 0, target: str | None = None, limit: int = 100, db: AsyncSession = Depends(get_db),
                      _: Principal = Depends(authorize("finance", "read"))):
    """Cursor-based feed for neighbouring SDC modules (promo, Yield, deduct, nexora)."""
    stmt = select(IntegrationEvent).where(IntegrationEvent.id > after_id).order_by(IntegrationEvent.id).limit(min(limit, 500))
    rows = (await db.execute(stmt)).scalars().all()
    if target:
        rows = [e for e in rows if target in (e.targets or [])]
    return {"next_after_id": rows[-1].id if rows else after_id,
            "events": [{"id": e.id, "type": e.event_type, "entity": e.entity_type, "entity_id": e.entity_id, "payload": e.payload,
                        "targets": e.targets, "created_at": e.created_at, "delivered_at": e.delivered_at} for e in rows]}


@router.post("/integrations/events/deliver")
async def deliver(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    return await erp.deliver_events(db)
