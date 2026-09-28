"""AI governance: usage and cost, budgets, the trust layer's settings and log, agent policies, and the review
inbox for changes AI agents propose."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.database import get_db
from app.core.rbac import Principal, authorize, authorize_person
from app.models import AiAction, AiUsage, Deal, User
from app.services import agents, ai_governance as gov, app_settings

router = APIRouter(prefix="/ai", tags=["ai governance"])


class PolicyIn(BaseModel):
    policy: dict


@router.get("/governance")
async def governance(days: int = 30, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("admin", "read"))):
    out = await gov.summary(db, max(1, min(days, 365)))
    out["agents"] = {"policy": await agents.policy(db), "catalog": agents.AGENTS, "actions": agents.ACTIONS}
    out["can_edit"] = p.can("admin", "update")
    return out


@router.put("/governance")
async def save_governance(body: PolicyIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize_person("admin", "update"))):
    try:
        clean = gov.validate_policy(body.policy)
    except (ValueError, TypeError) as e:
        raise HTTPException(422, str(e)) from e
    await app_settings.put(db, gov.POLICY_KEY, clean)
    log_action(db, "ai_policy", "setting", None, "AI budgets and trust settings changed")
    await db.commit()
    return await gov.policy()


@router.put("/agents/policy")
async def save_agent_policy(body: PolicyIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize_person("admin", "update"))):
    try:
        clean = agents.validate_policy(body.policy)
    except agents.AgentError as e:
        raise HTTPException(422, str(e)) from e
    await app_settings.put(db, agents.POLICY_KEY, clean)
    log_action(db, "agent_policy", "setting", None, ", ".join(f"{k}: {'on' if v['enabled'] else 'off'}/{v['mode']}" for k, v in clean.items()))
    await db.commit()
    return await agents.policy(db)


@router.get("/usage/log")
async def usage_log(status: str | None = None, feature: str | None = None, flagged: bool = False, limit: int = 100,
                    db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "read"))):
    stmt = select(AiUsage).order_by(AiUsage.id.desc()).limit(max(1, min(limit, 500)))
    if status:
        stmt = stmt.where(AiUsage.status == status)
    if feature:
        stmt = stmt.where(AiUsage.feature == feature)
    rows = (await db.execute(stmt)).scalars().all()
    if flagged:
        rows = [r for r in rows if r.injection_flags]
    names = dict((await db.execute(select(User.id, User.full_name).where(User.id.in_({r.user_id for r in rows if r.user_id})))).all())
    return [{"id": r.id, "created_at": r.created_at, "user": names.get(r.user_id, "System") if r.user_id else "System",
             "feature": gov.FEATURES.get(r.feature, r.feature), "provider": r.provider, "model": r.model, "status": r.status,
             "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "cost_usd": float(r.cost_usd), "latency_ms": r.latency_ms,
             "pii_masked": r.pii_masked, "injection_flags": r.injection_flags, "detail": r.detail,
             "prompt_excerpt": r.prompt_excerpt, "response_excerpt": r.response_excerpt} for r in rows]


@router.get("/usage/me")
async def my_usage(p: Principal = Depends(authorize("activities", "read"))):
    pol = await gov.policy()
    s = await gov.spend(p.user.id)
    return {"month_usd": round(s["user_month_usd"], 4), "calls_today": s["user_calls_today"],
            "limits": {"monthly_usd": pol["user_monthly_budget_usd"] or None, "daily_calls": pol["user_daily_calls"] or None},
            "blocked": gov.over_budget(pol, s)}


# ---- agent suggestions -------------------------------------------------------------------------------------------

async def _visible_actions(db: AsyncSession, p: Principal, stmt):
    if p.is_own_scope("deals"):
        managed = select(User.id).where(User.manager_id == p.user.id)
        stmt = stmt.where((AiAction.owner_id == p.user.id) | AiAction.owner_id.in_(managed))
    return stmt


@router.get("/actions")
async def list_actions(status: str | None = "pending", db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "read"))):
    stmt = select(AiAction).order_by(AiAction.created_at.desc()).limit(200)
    if status:
        stmt = stmt.where(AiAction.status == status)
    rows = (await db.execute(await _visible_actions(db, p, stmt))).scalars().all()
    deals = {d.id: d for d in (await db.execute(select(Deal).where(Deal.id.in_({a.entity_id for a in rows})))).scalars().unique().all()}
    names = dict((await db.execute(select(User.id, User.full_name).where(
        User.id.in_({x for a in rows for x in (a.owner_id, a.decided_by) if x})))).all())
    counts = dict((await db.execute(await _visible_actions(db, p, select(AiAction.status, func.count()).group_by(AiAction.status)))).all())
    out = []
    for a in rows:
        item = agents.action_out(a, names, deals.get(a.entity_id))
        item["can_decide"] = a.status == "pending" and await agents.can_decide(db, p, a)
        out.append(item)
    return {"actions": out, "counts": counts}


class DecisionIn(BaseModel):
    approve: bool
    note: str | None = None


@router.post("/actions/{action_id}/decide")
async def decide(action_id: uuid.UUID, body: DecisionIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize_person("deals", "update"))):
    action = (await db.execute(await _visible_actions(db, p, select(AiAction).where(AiAction.id == action_id)))).scalar_one_or_none()
    if action is None:
        raise HTTPException(404, "Not found")
    try:
        await agents.decide(db, p, action, body.approve, body.note)
    except agents.AgentError as e:
        raise HTTPException(409, str(e)) from e
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    log_action(db, "ai_approve" if body.approve else "ai_reject", "ai_action", action.id, action.title[:200])
    await db.commit()
    deal = await db.get(Deal, action.entity_id)
    return agents.action_out(action, {p.user.id: p.user.full_name}, deal) | {"decided_at": action.decided_at or datetime.now(timezone.utc)}
