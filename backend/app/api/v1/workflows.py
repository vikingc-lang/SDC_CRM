"""Admin: no-code workflow rules, their run history and dry runs."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.database import get_db
from app.core.rbac import Principal, authorize
from app.models import User, WorkflowRule, WorkflowRun
from app.services import reporting, workflows
from app.services.workflows import WorkflowError

router = APIRouter(prefix="/workflows", tags=["workflows"])


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool = False
    source: str
    trigger: dict
    conditions: list[dict] = Field(default_factory=list, max_length=20)
    actions: list[dict] = Field(default_factory=list)


class TestIn(BaseModel):
    record_id: uuid.UUID | None = None


def _out(r: WorkflowRule, names: dict | None = None) -> dict:
    return {"id": r.id, "name": r.name, "description": r.description, "enabled": r.enabled, "source": r.source, "trigger": r.trigger,
            "conditions": r.conditions, "actions": r.actions, "created_by": (names or {}).get(r.created_by),
            "last_run_at": r.last_run_at, "run_count": r.run_count, "updated_at": r.updated_at}


async def _rule(db: AsyncSession, rule_id: uuid.UUID) -> WorkflowRule:
    r = await db.get(WorkflowRule, rule_id)
    if r is None:
        raise HTTPException(404, "Workflow not found")
    return r


def _check(body: RuleIn) -> None:
    try:
        workflows.validate(body.source, body.trigger, body.conditions, body.actions)
    except WorkflowError as e:
        raise HTTPException(422, str(e))


@router.get("/meta")
async def meta(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "read"))):
    await reporting.refresh_custom_fields(db)
    users = (await db.execute(select(User).where(User.is_active.is_(True), User.role != "partner").order_by(User.full_name))).scalars().all()
    return {**workflows.meta(), "users": [{"id": u.id, "name": u.full_name, "role": u.role} for u in users]}


@router.get("")
async def list_rules(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "read"))):
    rows = (await db.execute(select(WorkflowRule).order_by(WorkflowRule.name))).scalars().all()
    ids = {r.created_by for r in rows if r.created_by}
    names = {u.id: u.full_name for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars()} if ids else {}
    return [_out(r, names) for r in rows]


@router.post("", status_code=201)
async def create_rule(body: RuleIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("admin", "update"))):
    await reporting.refresh_custom_fields(db)  # conditions may use custom fields
    _check(body)
    r = WorkflowRule(**body.model_dump(), created_by=p.id)
    db.add(r)
    await db.commit()
    await db.refresh(r)  # server-set timestamps are expired after commit
    workflows.invalidate_cache()
    return _out(r, {p.id: p.user.full_name})


@router.get("/{rule_id}")
async def get_rule(rule_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "read"))):
    return _out(await _rule(db, rule_id))


@router.put("/{rule_id}")
async def update_rule(rule_id: uuid.UUID, body: RuleIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("admin", "update"))):
    r = await _rule(db, rule_id)
    await reporting.refresh_custom_fields(db)  # conditions may use custom fields
    _check(body)
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    await db.commit()
    await db.refresh(r)  # updated_at is set by the database on update
    workflows.invalidate_cache()
    return _out(r)


@router.post("/{rule_id}/toggle")
async def toggle(rule_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("admin", "update"))):
    r = await _rule(db, rule_id)
    r.enabled = not r.enabled
    log_action(db, "workflow_on" if r.enabled else "workflow_off", "workflow_rules", r.id, r.name)
    await db.commit()
    await db.refresh(r)  # updated_at is set by the database on update
    workflows.invalidate_cache()
    return _out(r)


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("admin", "update"))):
    await db.delete(await _rule(db, rule_id))
    await db.commit()
    workflows.invalidate_cache()


@router.get("/{rule_id}/runs")
async def runs(rule_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "read"))):
    rule = await _rule(db, rule_id)
    rows = (await db.execute(select(WorkflowRun).where(WorkflowRun.rule_id == rule_id).order_by(WorkflowRun.created_at.desc()).limit(50))).scalars().all()
    from app.services import reporting

    out = []
    for r in rows:
        vals = await reporting.record_values(db, rule.source, r.record_id) if r.record_id else {}
        name = next((vals[k] for k in ("title", "name", "quote_number", "order_number", "subject") if vals.get(k)), None)
        out.append({"id": r.id, "record_id": r.record_id, "record": name, "trigger": r.trigger, "status": r.status, "detail": r.detail, "created_at": r.created_at,
                    "resume_at": r.resume_at, "steps_left": len(r.pending_actions or []) if r.status == "waiting" else 0})
    return out


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    run = await db.get(WorkflowRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    if run.status != "waiting":
        raise HTTPException(409, "Only a waiting run can be cancelled")
    run.status, run.resume_at, run.pending_actions = "cancelled", None, None
    run.detail = [*run.detail, {"action": "resume", "ok": True, "detail": "Cancelled by an admin"}]
    await db.commit()
    return {"id": run.id, "status": run.status}


@router.post("/{rule_id}/test")
async def test_rule(rule_id: uuid.UUID, body: TestIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    """Dry run: shows which records match and what each action would do. Changes nothing."""
    rule = await _rule(db, rule_id)
    out = await workflows.dry_run(db, rule, body.record_id)
    await db.rollback()
    return out
