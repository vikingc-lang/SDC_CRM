import uuid
from datetime import date
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.config import settings
from app.core.database import get_db
from app.core.dialect import json_array_has
from app.core.rbac import Principal, authorize, authorize_person
from app.models import Account, ErpSyncRun, FxRateHistory, IntegrationEvent, Quote, TaxRate
from app.services import app_settings, erp, fx, tax

router = APIRouter(tags=["finance & integrations"])


@router.get("/finance/accounts/{account_id}/ar")
async def account_ar(account_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("finance", "read"))):
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    await p.ensure_account(db, account_id, "finance")
    return await erp.ar_summary(db, account)


@router.get("/finance/accounts/{account_id}/credit-risk")
async def credit_risk(account_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("finance", "read"))):
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    await p.ensure_account(db, account_id, "finance")
    risk = await erp.assess_credit_risk(db, account)
    await db.commit()
    return risk


@router.get("/finance/ar-aging")
async def ar_aging(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("finance", "read"))):
    accounts = (await db.execute(p.scope_accounts(select(Account), "finance").where(Account.erp_customer_id.is_not(None)))).scalars().unique().all()
    rows, totals = [], {b[0]: 0.0 for b in erp.AGING_BUCKETS}
    rates = await fx.rates(db)
    for a in accounts:
        s = await erp.ar_summary(db, a, rates=rates)
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
    stmt = select(IntegrationEvent).where(IntegrationEvent.id > after_id).order_by(IntegrationEvent.id).limit(min(max(limit, 1), 500))
    if target:  # filter in SQL so the cursor always advances past other modules' events
        stmt = stmt.where(json_array_has(IntegrationEvent.targets, target))
    rows = (await db.execute(stmt)).scalars().all()
    return {"next_after_id": rows[-1].id if rows else after_id,
            "events": [{"id": e.id, "type": e.event_type, "entity": e.entity_type, "entity_id": e.entity_id, "payload": e.payload,
                        "targets": e.targets, "created_at": e.created_at, "delivered_at": e.delivered_at} for e in rows]}


@router.post("/integrations/events/deliver")
async def deliver(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    return await erp.deliver_events(db)


# ---- exchange rates ---------------------------------------------------------------------------------------------

class FxIn(BaseModel):
    currency: str = Field(min_length=3, max_length=3)
    rate_to_usd: float = Field(gt=0)
    effective_date: date | None = None


@router.get("/finance/fx")
async def fx_rates(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("finance", "read"))):
    table = await fx.rates(db)
    history = (await db.execute(select(FxRateHistory).order_by(FxRateHistory.currency, FxRateHistory.effective_date.desc()))).scalars().all()
    by: dict[str, list] = {}
    for h in history:
        by.setdefault(h.currency, []).append({"effective_date": h.effective_date, "rate_to_usd": float(h.rate_to_usd), "source": h.source})
    return {"current": [{"currency": c, "rate_to_usd": r, "history": by.get(c, [])} for c, r in sorted(table.items())],
            "feed": {"configured": bool(settings.fx_feed_url)}, "can_edit": p.can("finance", "update")}


@router.post("/finance/fx", status_code=201)
async def set_fx(body: FxIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize_person("finance", "update"))):
    try:
        row = await fx.set_rate(db, body.currency, body.rate_to_usd, body.effective_date)
    except fx.FxError as e:
        raise HTTPException(422, str(e)) from e
    log_action(db, "fx_rate", "fx_rate", None, f"{row.currency} = {row.rate_to_usd} USD from {row.effective_date}")
    await db.commit()
    return {"currency": row.currency, "effective_date": row.effective_date, "rate_to_usd": float(row.rate_to_usd)}


@router.post("/finance/fx/import")
async def import_fx(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize_person("finance", "update"))):
    if not settings.fx_feed_url:
        raise HTTPException(409, "No exchange-rate feed is configured (FX_FEED_URL)")
    try:
        out = await fx.import_feed(db, settings.fx_feed_url)
    except (fx.FxError, httpx.HTTPError) as e:
        raise HTTPException(502, f"The rate feed couldn't be loaded: {e.__class__.__name__}") from e
    await db.commit()
    return out


# ---- tax --------------------------------------------------------------------------------------------------------

class TaxPolicyIn(BaseModel):
    policy: dict


class TaxRateIn(BaseModel):
    country: str = Field(min_length=2, max_length=2)
    region: str | None = Field(default=None, max_length=10)
    tax_code: str | None = Field(default=None, max_length=20)
    name: str = Field(min_length=1, max_length=60)
    rate: float = Field(ge=0, le=100)


class TaxPreviewIn(BaseModel):
    amount: float = Field(gt=0)
    country: str
    region: str | None = None
    city: str | None = None
    postal_code: str | None = None
    tax_code: str | None = None
    currency: str = "USD"


def _rate_out(r: TaxRate) -> dict:
    return {"id": r.id, "country": r.country, "region": r.region, "tax_code": r.tax_code, "name": r.name, "rate": float(r.rate), "active": r.active}


@router.get("/finance/tax")
async def tax_settings(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("finance", "read"))):
    rows = (await db.execute(select(TaxRate).order_by(TaxRate.country, TaxRate.region, TaxRate.tax_code))).scalars().all()
    return {"policy": await tax.policy(db), "engines": tax.ENGINES, "rates": [_rate_out(r) for r in rows],
            "avalara": {"configured": bool(settings.avalara_account_id and settings.avalara_license_key), "environment": settings.avalara_environment},
            "can_edit": p.can("finance", "update")}


@router.put("/finance/tax")
async def save_tax(body: TaxPolicyIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize_person("finance", "update"))):
    try:
        clean = tax.validate_policy(body.policy)
    except tax.TaxError as e:
        raise HTTPException(422, str(e)) from e
    await app_settings.put(db, tax.POLICY_KEY, clean)
    log_action(db, "tax_policy", "setting", None, f"Tax engine: {clean['engine']}")
    await db.commit()
    return await tax.policy(db)


@router.post("/finance/tax/rates", status_code=201)
async def add_tax_rate(body: TaxRateIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize_person("finance", "update"))):
    row = TaxRate(country=body.country.upper(), region=(body.region or "").strip().upper() or None, tax_code=(body.tax_code or "").strip() or None,
                  name=body.name.strip(), rate=body.rate)
    db.add(row)
    await db.commit()
    return _rate_out(row)


@router.delete("/finance/tax/rates/{rate_id}", status_code=204)
async def delete_tax_rate(rate_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize_person("finance", "update"))):
    row = await db.get(TaxRate, rate_id)
    if row is None:
        raise HTTPException(404, "Rate not found")
    await db.delete(row)
    await db.commit()


@router.post("/finance/tax/preview")
async def preview_tax(body: TaxPreviewIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("finance", "read"))):
    res = await tax.calculate(db, [tax.Line(number="1", amount=Decimal(str(body.amount)), tax_code=body.tax_code)],
                              {"country": body.country, "region": body.region, "city": body.city, "postal_code": body.postal_code},
                              currency=body.currency.upper())
    return res.out()


@router.post("/quotes/{quote_id}/tax")
async def recalculate_quote_tax(quote_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("quotes", "update"))):
    from app.api.v1.cpq import _quote
    from app.services import cpq

    quote = await _quote(db, p, quote_id)
    if quote.locked_at:
        raise HTTPException(409, "This quote is locked")
    await tax.apply_to_quote(db, quote)
    await db.commit()
    return cpq.quote_out(await db.get(Quote, quote.id))
