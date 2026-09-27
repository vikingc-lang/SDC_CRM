"""Sales performance: my scorecard, team leaderboard, quotas, territories and commission plans."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.database import get_db
from app.core.rbac import ROLES, Principal, authorize
from app.models import Account, CommissionPlan, Quota, Territory, User
from app.services import forecasting
from app.services import performance as svc

router = APIRouter(prefix="/performance", tags=["performance"])
MANAGERS = ("sales_manager", "super_admin")


class QuotaIn(BaseModel):
    period: str
    user_id: uuid.UUID
    amount: float = Field(ge=0, le=1e13)


class TerritoryIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    parent_id: uuid.UUID | None = None
    manager_id: uuid.UUID | None = None
    member_ids: list[uuid.UUID] = []
    criteria: dict = {}
    priority: int = Field(default=100, ge=0, le=10000)


class PlanIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    base_rate: float = Field(ge=0, le=100)
    tiers: list[dict] = []
    roles: list[str] = []
    member_ids: list[uuid.UUID] = []
    active: bool = True


def _period(period: str | None) -> str:
    from datetime import date

    p = period or forecasting.period_of(date.today())
    try:
        forecasting.period_range(p)
    except forecasting.ForecastError as e:
        raise HTTPException(422, str(e))
    return p


def _manager(p: Principal) -> None:
    if p.user.role not in MANAGERS:
        raise HTTPException(403, "Only Sales Managers and Super Admins manage quotas and team performance")


async def _users_exist(db: AsyncSession, ids) -> None:
    ids = {uuid.UUID(str(i)) for i in ids if i}
    if ids and len((await db.execute(select(User.id).where(User.id.in_(ids), User.is_active.is_(True)))).all()) != len(ids):
        raise HTTPException(422, "Every person must be an active user")


# ---- scorecards ----------------------------------------------------------------------------------

@router.get("/periods")
async def periods(_: Principal = Depends(authorize("deals", "read"))):
    return {"periods": forecasting.periods()}


@router.get("/me")
async def me(period: str | None = None, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "read"))):
    return {**await svc.my_view(db, p.user, _period(period)), "is_manager": p.user.role in MANAGERS}


@router.get("/team")
async def team(period: str | None = None, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "read"))):
    _manager(p)
    return await svc.team_view(db, p.user, _period(period))


# ---- quotas ----------------------------------------------------------------------------------------

@router.get("/quotas")
async def list_quotas(period: str | None = None, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "read"))):
    _manager(p)
    period = _period(period)
    people = await forecasting.team_members(db, p.user)
    amounts = dict((await db.execute(select(Quota.user_id, Quota.amount).where(Quota.period == period,
                                                                               Quota.user_id.in_([u.id for u in people])))).all()) if people else {}
    return {"period": period, "rows": [{"user": {"id": u.id, "name": u.full_name, "role": u.role},
                                        "amount": float(amounts[u.id]) if u.id in amounts else None} for u in people]}


@router.put("/quotas")
async def set_quotas(body: list[QuotaIn], db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "read"))):
    _manager(p)
    team_ids = {u.id for u in await forecasting.team_members(db, p.user)}
    for q in body:
        period = _period(q.period)
        if q.user_id not in team_ids:
            raise HTTPException(404, "That person isn't on your team")
        row = (await db.execute(select(Quota).where(Quota.user_id == q.user_id, Quota.period == period))).scalar_one_or_none()
        if row is None:
            db.add(Quota(user_id=q.user_id, period=period, amount=q.amount, set_by=p.id))
        else:
            row.amount, row.set_by = q.amount, p.id
        log_action(db, "quota_set", "quotas", q.user_id, f"{period}: {q.amount:,.0f}")
    await db.commit()
    return {"status": "ok", "saved": len(body)}


# ---- territories -----------------------------------------------------------------------------------

def _territory_out(t: Territory, counts: dict, names: dict) -> dict:
    return {"id": t.id, "name": t.name, "description": t.description, "parent_id": t.parent_id, "parent": names.get(t.parent_id),
            "manager_id": t.manager_id, "member_ids": [str(m) for m in t.member_ids or []], "criteria": t.criteria or {},
            "priority": t.priority, "accounts": counts.get(t.id, 0)}


@router.get("/territories")
async def list_territories(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("accounts", "read"))):
    ts = svc.ordered(await svc.all_territories(db))
    counts = dict((await db.execute(select(Account.territory_id, func.count()).where(Account.territory_id.is_not(None))
                                    .group_by(Account.territory_id))).all())
    unassigned = (await db.execute(select(func.count()).select_from(Account).where(Account.territory_id.is_(None)))).scalar()
    names = {t.id: t.name for t in ts}
    return {"territories": [_territory_out(t, counts, names) for t in ts], "unassigned": unassigned}


async def _save_territory(db: AsyncSession, t: Territory, body: TerritoryIn) -> None:
    try:
        criteria = svc.clean_criteria(body.criteria)
    except svc.PerformanceError as e:
        raise HTTPException(422, str(e))
    await _users_exist(db, [*body.member_ids, body.manager_id])
    if body.parent_id:
        parent = await db.get(Territory, body.parent_id)
        if parent is None:
            raise HTTPException(422, "Parent territory not found")
        seen, cur = {t.id}, parent  # no cycles
        while cur is not None:
            if cur.id in seen:
                raise HTTPException(422, "A territory can't sit under itself")
            seen.add(cur.id)
            cur = await db.get(Territory, cur.parent_id) if cur.parent_id else None
    clash = (await db.execute(select(Territory.id).where(func.lower(Territory.name) == body.name.strip().lower(), Territory.id != t.id))).first()
    if clash:
        raise HTTPException(409, "A territory with this name exists")
    t.name, t.description, t.parent_id, t.manager_id = body.name.strip(), body.description, body.parent_id, body.manager_id
    t.member_ids, t.criteria, t.priority = [str(m) for m in dict.fromkeys(body.member_ids)], criteria, body.priority


@router.post("/territories", status_code=201)
async def create_territory(body: TerritoryIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    t = Territory(id=uuid.uuid4(), name=body.name)
    await _save_territory(db, t, body)
    db.add(t)
    await db.commit()
    return {"id": t.id}


@router.put("/territories/{territory_id}")
async def update_territory(territory_id: uuid.UUID, body: TerritoryIn, db: AsyncSession = Depends(get_db),
                           _: Principal = Depends(authorize("admin", "update"))):
    t = await db.get(Territory, territory_id)
    if t is None:
        raise HTTPException(404, "Territory not found")
    await _save_territory(db, t, body)
    await db.commit()
    return {"status": "ok"}


@router.delete("/territories/{territory_id}", status_code=204)
async def delete_territory(territory_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    t = await db.get(Territory, territory_id)
    if t:
        await svc.detach(db, t.id)
        await db.delete(t)
        await db.commit()


@router.post("/territories/realign")
async def realign(apply: bool = False, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    """Preview (default) or apply the territory each account falls into under the current rules."""
    out = await svc.realign(db, apply)
    if apply:
        log_action(db, "territory_realign", "accounts", None, f"{len(out['changes'])} accounts moved")
        await db.commit()
    return out


@router.get("/territories/{territory_id}/gaps")
async def gaps(territory_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("accounts", "read"))):
    t = await db.get(Territory, territory_id)
    if t is None:
        raise HTTPException(404, "Territory not found")
    return await svc.coverage_gaps(db, t)


# ---- commission plans -----------------------------------------------------------------------------

@router.get("/plans")
async def list_plans(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "read"))):
    return [svc._plan_out(pl) for pl in (await db.execute(select(CommissionPlan).order_by(CommissionPlan.name))).scalars()]


async def _save_plan(db: AsyncSession, pl: CommissionPlan, body: PlanIn) -> None:
    try:
        tiers = svc.clean_tiers(body.tiers, body.base_rate)
    except svc.PerformanceError as e:
        raise HTTPException(422, str(e))
    if any(r not in ROLES for r in body.roles):
        raise HTTPException(422, "Unknown role")
    await _users_exist(db, body.member_ids)
    clash = (await db.execute(select(CommissionPlan.id).where(func.lower(CommissionPlan.name) == body.name.strip().lower(),
                                                              CommissionPlan.id != pl.id))).first()
    if clash:
        raise HTTPException(409, "A plan with this name exists")
    pl.name, pl.description, pl.base_rate, pl.tiers = body.name.strip(), body.description, body.base_rate, tiers
    pl.roles, pl.member_ids, pl.active = list(dict.fromkeys(body.roles)), [str(m) for m in dict.fromkeys(body.member_ids)], body.active


@router.post("/plans", status_code=201)
async def create_plan(body: PlanIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    pl = CommissionPlan(id=uuid.uuid4())
    await _save_plan(db, pl, body)
    db.add(pl)
    await db.commit()
    return {"id": pl.id}


@router.put("/plans/{plan_id}")
async def update_plan(plan_id: uuid.UUID, body: PlanIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    pl = await db.get(CommissionPlan, plan_id)
    if pl is None:
        raise HTTPException(404, "Plan not found")
    await _save_plan(db, pl, body)
    await db.commit()
    return {"status": "ok"}


@router.delete("/plans/{plan_id}", status_code=204)
async def delete_plan(plan_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    pl = await db.get(CommissionPlan, plan_id)
    if pl:
        await db.delete(pl)
        await db.commit()


@router.post("/plans/preview")
async def preview(body: dict, _: Principal = Depends(authorize("deals", "read"))):
    """What a plan pays at given bookings and quota, for the plan editor."""
    try:
        base = float(body.get("base_rate") or 0)
        tiers = svc.clean_tiers(body.get("tiers"), base)
        return svc.commission(float(body.get("bookings") or 0), float(body.get("quota") or 0), base, tiers)
    except (svc.PerformanceError, TypeError, ValueError) as e:
        raise HTTPException(422, str(e))
