"""AI agents act only through proposals, under an admin policy.

Each agent has a fixed list of the kinds of change it may ever make (its permissions). The policy switches an
agent on or off, narrows what it may do, and sets its mode:

* ``auto``    – the change is applied at once and kept as an applied proposal (a reviewable record);
* ``approve`` – the change waits as ``pending`` until the record owner, their manager or someone with
  organisation-wide edit rights approves (then it's applied) or rejects it. Pending proposals expire after
  EXPIRE_DAYS.

Nothing an agent does bypasses this module: the stage assistant (tasks, notes, email drafts on stage changes)
and the pipeline monitor (proposes a new close date for overdue deals) both call ``propose``.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, AiAction, AppSetting, Deal, Task, User
from app.services.notify import notify

log = logging.getLogger(__name__)

POLICY_KEY = "ai_agents"
EXPIRE_DAYS = 14
ACTIONS = {
    "create_task": "Create follow-up tasks",
    "log_note": "Log notes on the timeline",
    "save_draft": "Save email drafts on the deal",
    "update_deal": "Change deal fields (close date, forecast category)",
}
AGENTS = {
    "stage_assistant": {"label": "Stage assistant", "may": ["create_task", "log_note", "save_draft"], "default_mode": "auto",
                        "description": "When a deal changes stage: follow-up tasks, the demo recap draft and the loss post-mortem note."},
    "pipeline_monitor": {"label": "Pipeline monitor", "may": ["update_deal"], "default_mode": "approve",
                         "description": "Finds open deals whose close date has passed and proposes a realistic new date."},
}
UPDATABLE = {"target_close_date": "date", "forecast_category": ("commit", "best_case", "pipeline", "omitted")}


class AgentError(ValueError):
    pass


async def policy(db: AsyncSession) -> dict:
    row = await db.get(AppSetting, POLICY_KEY)
    saved = (row.value if row else {}) or {}
    out = {}
    for key, a in AGENTS.items():
        p = saved.get(key) or {}
        out[key] = {"enabled": bool(p.get("enabled", True)), "mode": p.get("mode") if p.get("mode") in ("auto", "approve") else a["default_mode"],
                    "actions": [x for x in (p.get("actions") or a["may"]) if x in a["may"]]}
    return out


def validate_policy(body: dict) -> dict:
    clean = {}
    for key, p in (body or {}).items():
        if key not in AGENTS:
            raise AgentError(f"Unknown agent {key}")
        if p.get("mode") not in ("auto", "approve"):
            raise AgentError("Mode must be automatic or needs approval")
        extra = [x for x in p.get("actions") or [] if x not in AGENTS[key]["may"]]
        if extra:
            raise AgentError(f"{AGENTS[key]['label']} is never allowed to: {', '.join(ACTIONS.get(x, x) for x in extra)}")
        clean[key] = {"enabled": bool(p.get("enabled", True)), "mode": p["mode"], "actions": list(p.get("actions") or [])}
    return clean


async def propose(db: AsyncSession, agent: str, action_type: str, deal: Deal, title: str, payload: dict,
                  rationale: str | None = None) -> AiAction | None:
    """Record an agent's intended change on a deal and apply it now or queue it for review, per the policy.
    Returns None when the policy doesn't let this agent do this at all."""
    if action_type not in AGENTS[agent]["may"]:
        raise AgentError(f"{agent} may not {action_type}")  # a programming error, not a policy choice
    pol = (await policy(db))[agent]
    if not pol["enabled"] or action_type not in pol["actions"]:
        return None
    action = AiAction(id=uuid.uuid4(), agent=agent, action_type=action_type, entity_type="deal", entity_id=deal.id, title=title[:300],
                      payload=payload, rationale=rationale, mode=pol["mode"], owner_id=deal.owner_id)
    db.add(action)
    if pol["mode"] == "auto":
        await _apply(db, action, None)
    else:
        notify(db, [deal.owner_id], "ai", f"Aiden suggests: {title}"[:300], rationale, "/approvals?tab=ai")
    return action


async def _apply(db: AsyncSession, action: AiAction, user: User | None) -> None:
    deal = await db.get(Deal, action.entity_id)
    now = datetime.now(timezone.utc)
    action.decided_by, action.decided_at = (user.id if user else None), now
    if deal is None:
        action.status, action.result = "failed", "The deal no longer exists"
        return
    p = action.payload or {}
    try:
        if action.action_type == "create_task":
            db.add(Task(title=p["title"][:255], due_date=date.today() + timedelta(days=int(p.get("due_in_days", 2))), account_id=deal.account_id,
                        deal_id=deal.id, owner_id=deal.owner_id, assignee_id=deal.owner_id, source="ai", priority=p.get("priority", "normal")))
            action.result = "Task created"
        elif action.action_type == "log_note":
            from app.services import embeddings

            note = Activity(account_id=deal.account_id, deal_id=deal.id, user_id=user.id if user else (uuid.UUID(p["user_id"]) if p.get("user_id") else None), activity_type="note",
                            summary=p["summary"], sentiment=p.get("sentiment", "neutral"), source="ai")
            note.embedding = await embeddings.embed(p["summary"])
            db.add(note)
            action.result = "Note logged"
        elif action.action_type == "save_draft":
            deal.ai_insights = {**(deal.ai_insights or {}), p["key"]: p["text"]}
            action.result = "Draft saved on the deal"
        elif action.action_type == "update_deal":
            field, value = p["field"], p["value"]
            kind = UPDATABLE.get(field)
            if kind is None:
                raise AgentError(f"{field} can't be changed by an agent")
            if kind == "date":
                new = date.fromisoformat(value)
                if deal.target_close_date and new > deal.target_close_date:
                    deal.close_date_pushes = (deal.close_date_pushes or 0) + 1
                deal.target_close_date = new
            elif value not in kind:
                raise AgentError(f"Unknown {field} {value}")
            else:
                setattr(deal, field, value)
            action.result = f"{field.replace('_', ' ').capitalize()} set to {value}"
        else:
            raise AgentError(f"Unknown action {action.action_type}")
        action.status = "applied"
    except (KeyError, ValueError) as e:
        action.status, action.result = "failed", str(e)[:300]


async def can_decide(db: AsyncSession, p, action: AiAction) -> bool:
    if not p.can("deals", "update"):
        return False
    if not p.is_own_scope("deals") or action.owner_id == p.user.id:
        return True
    manager = (await db.execute(select(User.manager_id).where(User.id == action.owner_id))).scalar() if action.owner_id else None
    return manager == p.user.id


async def decide(db: AsyncSession, p, action: AiAction, approve: bool, note: str | None = None) -> AiAction:
    if action.status != "pending":
        raise AgentError(f"This suggestion was already {action.status}")
    if not await can_decide(db, p, action):
        raise PermissionError("Only the deal owner, their manager or a manager with company-wide access can decide this")
    if approve:
        await _apply(db, action, p.user)
    else:
        action.status, action.decided_by, action.decided_at = "rejected", p.user.id, datetime.now(timezone.utc)
        action.result = (note or "Rejected")[:300]
    return action


async def expire(db: AsyncSession) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=EXPIRE_DAYS)
    rows = (await db.execute(select(AiAction).where(AiAction.status == "pending", AiAction.created_at < cutoff))).scalars().all()
    for a in rows:
        a.status, a.result = "expired", f"Not decided within {EXPIRE_DAYS} days"
    await db.commit()
    return {"expired": len(rows)}


async def pipeline_monitor(db: AsyncSession, deals: list[Deal]) -> int:
    """Propose a new close date for open deals whose date has passed (one open suggestion per deal)."""
    today, proposed = date.today(), 0
    overdue = [d for d in deals if d.target_close_date and d.target_close_date < today]
    if not overdue:
        return 0
    waiting = set((await db.execute(select(AiAction.entity_id).where(
        AiAction.agent == "pipeline_monitor", AiAction.status == "pending", AiAction.entity_id.in_([d.id for d in overdue])))).scalars())
    for d in overdue:
        if d.id in waiting:
            continue
        late = (today - d.target_close_date).days
        new = today + timedelta(days=max(14, 7 * (d.close_date_pushes or 0) + 14))
        if await propose(db, "pipeline_monitor", "update_deal", d, f"Move the close date of {d.title} to {new.isoformat()}",
                         {"field": "target_close_date", "value": new.isoformat(), "previous": d.target_close_date.isoformat()},
                         f"The target close date passed {late} days ago and the deal is still in {d.stage.name}. "
                         f"{'It has already slipped ' + str(d.close_date_pushes) + ' times. ' if d.close_date_pushes else ''}"
                         "Approving keeps the forecast honest; reject if the deal will still close this week."):
            proposed += 1
    return proposed


def action_out(a: AiAction, names: dict | None = None, deal: Deal | None = None) -> dict:
    names = names or {}
    return {"id": a.id, "agent": a.agent, "agent_label": AGENTS.get(a.agent, {}).get("label", a.agent), "action_type": a.action_type,
            "action_label": ACTIONS.get(a.action_type, a.action_type), "title": a.title, "payload": a.payload, "rationale": a.rationale,
            "status": a.status, "mode": a.mode, "result": a.result, "created_at": a.created_at, "decided_at": a.decided_at,
            "owner": {"id": a.owner_id, "full_name": names.get(a.owner_id)} if a.owner_id else None,
            "decided_by": {"id": a.decided_by, "full_name": names.get(a.decided_by)} if a.decided_by else None,
            "deal": {"id": deal.id, "title": deal.title, "account": deal.account.name} if deal else {"id": a.entity_id}}
