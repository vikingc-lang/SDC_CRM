"""Forecast calls: my forecast, team roll-up, submissions, manager adjustments and deal categories."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import Principal, authorize
from app.models import Deal, ForecastAdjustment, ForecastSubmission, User
from app.services import forecasting as svc
from app.services import fx

router = APIRouter(prefix="/forecast", tags=["forecast"])
MANAGERS = ("sales_manager", "super_admin")


class SubmissionIn(BaseModel):
    period: str
    scope: Literal["self", "team"] = "self"
    commit: float = Field(ge=0, le=1e13)
    best_case: float = Field(ge=0, le=1e13)
    note: str | None = Field(default=None, max_length=2000)


class AdjustmentIn(BaseModel):
    period: str
    rep_id: uuid.UUID
    commit: float = Field(ge=0, le=1e13)
    best_case: float = Field(ge=0, le=1e13)
    note: str | None = Field(default=None, max_length=2000)


class CategoryIn(BaseModel):
    category: Literal["commit", "best_case", "pipeline", "omitted"] | None  # None = back to the stage default


def _period(period: str | None) -> str:
    from datetime import date

    p = period or svc.period_of(date.today())
    try:
        svc.period_range(p)
    except svc.ForecastError as e:
        raise HTTPException(422, str(e))
    return p


def _manager(p: Principal) -> None:
    if p.user.role not in MANAGERS:
        raise HTTPException(403, "Only Sales Managers and Super Admins manage team forecasts")


@router.get("/periods")
async def periods(_: Principal = Depends(authorize("deals", "read"))):
    return {"periods": svc.periods(), "categories": [{"key": k, "label": svc.LABELS[k]} for k in svc.CATEGORIES]}


@router.get("/me")
async def me(period: str | None = None, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "read"))):
    return {**await svc.my_view(db, p.user, _period(period)), "is_manager": p.user.role in MANAGERS}


@router.get("/team")
async def team(period: str | None = None, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "read"))):
    _manager(p)
    return await svc.team_view(db, p.user, _period(period))


@router.post("/submissions", status_code=201)
async def submit(body: SubmissionIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "read"))):
    period = _period(body.period)
    if body.best_case < body.commit:
        raise HTTPException(422, "Best case can't be lower than commit")
    if body.scope == "team":
        _manager(p)
        view = await svc.team_view(db, p.user, period)
        snapshot = {k: view["team"][k] for k in ("calculated_commit", "calculated_best_case", "adjusted_commit", "adjusted_best_case", "closed")}
    else:
        rates = await fx.rates(db)
        snapshot = svc.rollup(await svc.deals_in(db, [p.id], period), rates)
    s = ForecastSubmission(user_id=p.id, period=period, scope=body.scope, commit_amount=body.commit, best_case_amount=body.best_case,
                           calculated=snapshot, note=body.note)
    db.add(s)
    await db.commit()
    return {"id": s.id, "period": period, "scope": body.scope, "commit": body.commit, "best_case": body.best_case}


@router.put("/adjustments")
async def adjust(body: AdjustmentIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "read"))):
    _manager(p)
    period = _period(body.period)
    if body.best_case < body.commit:
        raise HTTPException(422, "Best case can't be lower than commit")
    if body.rep_id == p.id:
        raise HTTPException(422, "Adjust your own number through your team call")
    if body.rep_id not in {u.id for u in await svc.team_members(db, p.user)}:
        raise HTTPException(404, "That person isn't on your team")
    adj = (await db.execute(select(ForecastAdjustment).where(ForecastAdjustment.manager_id == p.id, ForecastAdjustment.rep_id == body.rep_id,
                                                             ForecastAdjustment.period == period))).scalar_one_or_none()
    if adj is None:
        adj = ForecastAdjustment(manager_id=p.id, rep_id=body.rep_id, period=period, commit_amount=body.commit, best_case_amount=body.best_case)
        db.add(adj)
    adj.commit_amount, adj.best_case_amount, adj.note = body.commit, body.best_case, body.note
    await db.commit()
    return {"status": "ok"}


@router.delete("/adjustments", status_code=204)
async def clear_adjustment(period: str, rep_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "read"))):
    _manager(p)
    adj = (await db.execute(select(ForecastAdjustment).where(ForecastAdjustment.manager_id == p.id, ForecastAdjustment.rep_id == rep_id,
                                                             ForecastAdjustment.period == _period(period)))).scalar_one_or_none()
    if adj:
        await db.delete(adj)
        await db.commit()


@router.put("/deals/{deal_id}/category")
async def set_category(deal_id: uuid.UUID, body: CategoryIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "update"))):
    deal = (await db.execute(p.scope_deals(select(Deal).where(Deal.id == deal_id)))).scalars().unique().one_or_none()
    if deal is None:
        raise HTTPException(404, "Deal not found")
    if deal.stage.is_closed_won or deal.stage.is_closed_lost:
        raise HTTPException(422, "Closed deals are forecast by their outcome")
    if p.user.role not in MANAGERS and deal.owner_id != p.id:
        raise HTTPException(403, "Only the deal owner or a manager can change its forecast category")
    deal.forecast_category = body.category
    await db.commit()
    return {"id": deal.id, "category": svc.category(deal)}
