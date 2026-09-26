"""Forecast calls: categories, quarterly roll-ups, rep submissions and manager adjustments.

A deal's forecast category is the rep's override if set, else its stage's category; won deals are
'closed' and lost deals 'omitted'. A quarter counts open deals whose target close date falls in it and
won deals closed in it. The two calls are cumulative, as in most CRMs:
    Commit call    = Closed + Commit
    Best case call = Closed + Commit + Best Case
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Deal, ForecastAdjustment, ForecastSubmission, PipelineStage, User
from app.services import fx

CATEGORIES = ("closed", "commit", "best_case", "pipeline", "omitted")
REP_CATEGORIES = ("commit", "best_case", "pipeline", "omitted")  # a rep can move an open deal into these
LABELS = {"closed": "Closed", "commit": "Commit", "best_case": "Best case", "pipeline": "Pipeline", "omitted": "Omitted"}
SELLER_ROLES = ("account_executive", "sdr", "sales_manager")
_PERIOD = re.compile(r"^(\d{4})-Q([1-4])$")


class ForecastError(ValueError):
    pass


def period_of(d: date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def period_range(period: str) -> tuple[date, date]:
    m = _PERIOD.match(period or "")
    if not m:
        raise ForecastError("Periods look like 2026-Q4")
    y, q = int(m.group(1)), int(m.group(2))
    start = date(y, 3 * (q - 1) + 1, 1)
    end = date(y + (q == 4), 1 if q == 4 else 3 * q + 1, 1) - timedelta(days=1)
    return start, end


def periods(today: date | None = None) -> list[dict]:
    today = today or date.today()
    y, q = today.year, (today.month - 1) // 3 + 1
    out = []
    for off in (-1, 0, 1, 2):
        qq, yy = q + off, y
        while qq < 1:
            qq, yy = qq + 4, yy - 1
        while qq > 4:
            qq, yy = qq - 4, yy + 1
        p = f"{yy}-Q{qq}"
        s, e = period_range(p)
        out.append({"period": p, "label": f"Q{qq} {yy}", "start": s, "end": e, "current": off == 0})
    return out


def category(deal: Deal) -> str:
    if deal.stage.is_closed_won:
        return "closed"
    if deal.stage.is_closed_lost:
        return "omitted"
    return deal.forecast_category or deal.stage.forecast_category or "pipeline"


async def deals_in(db: AsyncSession, owner_ids, period: str) -> list[Deal]:
    start, end = period_range(period)
    stmt = (select(Deal).join(PipelineStage, PipelineStage.id == Deal.stage_id).where(Deal.owner_id.in_(list(owner_ids)))
            .where(or_(and_(PipelineStage.is_closed_won.is_(False), PipelineStage.is_closed_lost.is_(False),
                            Deal.target_close_date.between(start, end)),
                       and_(PipelineStage.is_closed_won.is_(True), func.date(Deal.closed_at).between(start, end))))
            .order_by(Deal.amount.desc()))
    return list((await db.execute(stmt)).scalars().unique().all())


def rollup(deals: list[Deal], rates: dict) -> dict:
    by = {c: 0.0 for c in CATEGORIES}
    counts = {c: 0 for c in CATEGORIES}
    for d in deals:
        c = category(d)
        by[c] += fx.to_usd(float(d.amount or 0), d.currency, rates)
        counts[c] += 1
    by = {k: round(v, 2) for k, v in by.items()}
    return {**by, "counts": counts, "commit_call": round(by["closed"] + by["commit"], 2),
            "best_case_call": round(by["closed"] + by["commit"] + by["best_case"], 2)}


def deal_out(d: Deal, rates: dict) -> dict:
    return {"id": d.id, "title": d.title, "account": d.account.name, "stage": d.stage.name, "owner_id": d.owner_id,
            "amount_usd": fx.to_usd(float(d.amount or 0), d.currency, rates), "close_date": d.target_close_date,
            "closed_at": d.closed_at, "category": category(d), "overridden": bool(d.forecast_category) and not (d.stage.is_closed_won or d.stage.is_closed_lost),
            "stage_category": d.stage.forecast_category, "risk_score": d.risk_score, "is_closed": d.stage.is_closed_won or d.stage.is_closed_lost}


def _sub_out(s: ForecastSubmission | None) -> dict | None:
    if s is None:
        return None
    return {"id": s.id, "commit": float(s.commit_amount), "best_case": float(s.best_case_amount), "note": s.note,
            "calculated": s.calculated, "created_at": s.created_at}


async def latest_submission(db: AsyncSession, user_id, period: str, scope: str) -> ForecastSubmission | None:
    return (await db.execute(select(ForecastSubmission).where(ForecastSubmission.user_id == user_id, ForecastSubmission.period == period,
                                                              ForecastSubmission.scope == scope)
                             .order_by(ForecastSubmission.created_at.desc()).limit(1))).scalar_one_or_none()


async def history(db: AsyncSession, user_id, period: str, scope: str) -> list[dict]:
    rows = (await db.execute(select(ForecastSubmission).where(ForecastSubmission.user_id == user_id, ForecastSubmission.period == period,
                                                              ForecastSubmission.scope == scope)
                             .order_by(ForecastSubmission.created_at.desc()).limit(20))).scalars().all()
    return [_sub_out(s) for s in rows]


async def team_members(db: AsyncSession, manager: User) -> list[User]:
    stmt = select(User).where(User.is_active.is_(True), User.role.in_(SELLER_ROLES), User.id != manager.id)
    if manager.role != "super_admin":
        stmt = stmt.where(User.manager_id == manager.id)
    return list((await db.execute(stmt.order_by(User.full_name))).scalars().all())


async def my_view(db: AsyncSession, user: User, period: str) -> dict:
    rates = await fx.rates(db)
    deals = await deals_in(db, [user.id], period)
    return {"period": period, "totals": rollup(deals, rates), "deals": [deal_out(d, rates) for d in deals],
            "submission": _sub_out(await latest_submission(db, user.id, period, "self")),
            "history": await history(db, user.id, period, "self")}


async def team_view(db: AsyncSession, manager: User, period: str) -> dict:
    rates = await fx.rates(db)
    members = await team_members(db, manager)
    people = [manager, *members]
    all_deals = await deals_in(db, [u.id for u in people], period)
    adjustments = {a.rep_id: a for a in (await db.execute(select(ForecastAdjustment).where(
        ForecastAdjustment.manager_id == manager.id, ForecastAdjustment.period == period))).scalars()}
    rows, team = [], {"calculated_commit": 0.0, "calculated_best_case": 0.0, "submitted_commit": 0.0, "submitted_best_case": 0.0,
                      "adjusted_commit": 0.0, "adjusted_best_case": 0.0, "closed": 0.0, "not_submitted": 0}
    for u in people:
        mine = [d for d in all_deals if d.owner_id == u.id]
        calc = rollup(mine, rates)
        sub = await latest_submission(db, u.id, period, "self")
        adj = adjustments.get(u.id) if u.id != manager.id else None
        submitted = (float(sub.commit_amount), float(sub.best_case_amount)) if sub else (calc["commit_call"], calc["best_case_call"])
        final = (float(adj.commit_amount), float(adj.best_case_amount)) if adj else submitted
        team["calculated_commit"] += calc["commit_call"]
        team["calculated_best_case"] += calc["best_case_call"]
        team["submitted_commit"] += submitted[0]
        team["submitted_best_case"] += submitted[1]
        team["adjusted_commit"] += final[0]
        team["adjusted_best_case"] += final[1]
        team["closed"] += calc["closed"]
        team["not_submitted"] += sub is None
        rows.append({"user": {"id": u.id, "name": u.full_name, "role": u.role}, "is_self": u.id == manager.id, "calculated": calc,
                     "submission": _sub_out(sub),
                     "adjustment": {"commit": float(adj.commit_amount), "best_case": float(adj.best_case_amount), "note": adj.note,
                                    "updated_at": adj.updated_at} if adj else None,
                     "final": {"commit": final[0], "best_case": final[1],
                               "source": "adjusted" if adj else "submitted" if sub else "calculated"}})
    return {"period": period, "rows": rows, "team": {k: round(v, 2) if isinstance(v, float) else v for k, v in team.items()},
            "submission": _sub_out(await latest_submission(db, manager.id, period, "team")),
            "history": await history(db, manager.id, period, "team")}
