"""No-code workflows: when a record is created, when watched fields change, or on a schedule,
check conditions and run actions (create a task, notify people, update a field, emit an event).

How record triggers fire: a session listener notes which watched records were inserted or had a
watched column change during each flush; after the transaction commits, the rules are evaluated in
a fresh session (so a rolled-back change never triggers anything). Changes made by a workflow can
trigger other workflows, up to MAX_DEPTH levels, and a rule never re-triggers itself.

Conditions use the reporting catalogue's filter syntax (services/reporting.py), so rules carry no SQL.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import event, func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.rbac import ROLES
from app.models import (
    Account, Activity, Contact, Deal, Lead, Order, Quote, SupportTicket, Task, User, WorkflowRule, WorkflowRun,
)
from app.services import reporting
from app.services.notify import emit, notify

log = logging.getLogger(__name__)

MAX_DEPTH = 3
MAX_ACTIONS = 10
SCHEDULE_BATCH = 200
TRIGGERS = ("created", "updated", "schedule")
ACTION_TYPES = ("create_task", "notify", "update_field", "emit_event")
PRIORITIES = ("low", "normal", "high", "urgent")


class WorkflowError(ValueError):
    pass


@dataclass
class Entity:
    model: Any
    label: str
    watch: dict[str, str]                                  # catalogue key -> model attribute
    owner: Callable[[AsyncSession, Any], Awaitable[uuid.UUID | None]]
    link: Callable[[Any], str]
    account_id: Callable[[Any], uuid.UUID | None] = lambda o: None
    deal_id: Callable[[Any], uuid.UUID | None] = lambda o: None
    settable: dict[str, tuple[str, str, list[str] | None]] = field(default_factory=dict)  # key -> (attr, kind, options)


async def _attr(o, name):
    return getattr(o, name, None)


async def _account_owner(db: AsyncSession, account_id) -> uuid.UUID | None:
    return (await db.execute(select(Account.owner_id).where(Account.id == account_id))).scalar() if account_id else None


async def _deal_owner(db: AsyncSession, deal_id) -> uuid.UUID | None:
    return (await db.execute(select(Deal.owner_id).where(Deal.id == deal_id))).scalar() if deal_id else None


ENTITIES: dict[str, Entity] = {
    "deals": Entity(Deal, "Opportunity",
                    {"stage": "stage_id", "owner": "owner_id", "amount": "amount", "close_date": "target_close_date", "risk_score": "risk_score",
                     "forecast_category": "forecast_category"},
                    lambda db, o: _attr(o, "owner_id"), lambda o: f"/deals/{o.id}", lambda o: o.account_id, lambda o: o.id,
                    {"owner": ("owner_id", "user", None)}),
    "leads": Entity(Lead, "Lead", {"status": "status", "owner": "owner_id", "score": "score"},
                    lambda db, o: _attr(o, "owner_id"), lambda o: f"/leads/{o.id}",
                    settable={"owner": ("owner_id", "user", None)}),
    "accounts": Entity(Account, "Account",
                       {"owner": "owner_id", "tier": "tier", "health": "health_score", "churn_risk": "churn_risk", "credit_hold": "credit_hold"},
                       lambda db, o: _attr(o, "owner_id"), lambda o: f"/accounts/{o.id}", lambda o: o.id,
                       settable={"owner": ("owner_id", "user", None), "tier": ("tier", "enum", ["SMB", "Mid-Market", "Enterprise"])}),
    "contacts": Entity(Contact, "Contact", {"buying_role": "buying_role", "status": "status"},
                       lambda db, o: _account_owner(db, o.account_id), lambda o: f"/contacts/{o.id}", lambda o: o.account_id),
    "activities": Entity(Activity, "Activity", {},
                         lambda db, o: _attr(o, "user_id"), lambda o: f"/accounts/{o.account_id}", lambda o: o.account_id, lambda o: o.deal_id),
    "tasks": Entity(Task, "Task", {"completed": "completed", "priority": "priority", "owner": "owner_id", "due": "due_date"},
                    lambda db, o: _attr(o, "assignee_id") if o.assignee_id else _attr(o, "owner_id"), lambda o: "/tasks",
                    lambda o: o.account_id, lambda o: o.deal_id,
                    {"priority": ("priority", "enum", list(PRIORITIES)), "assignee": ("assignee_id", "user", None)}),
    "quotes": Entity(Quote, "Quote", {"status": "status"}, lambda db, o: _deal_owner(db, o.deal_id), lambda o: f"/quotes/{o.id}",
                     deal_id=lambda o: o.deal_id),
    "orders": Entity(Order, "Order", {"status": "status"}, lambda db, o: _account_owner(db, o.account_id), lambda o: f"/orders/{o.id}",
                     lambda o: o.account_id, lambda o: o.deal_id),
    "cases": Entity(SupportTicket, "Case", {"status": "status", "priority": "severity", "owner": "owner_id", "queue": "queue_id",
                                            "sla_breached": "sla_breached", "csat": "csat_score"},
                    lambda db, o: _attr(o, "owner_id"), lambda o: f"/cases/{o.id}", lambda o: o.account_id,
                    settable={"owner": ("owner_id", "user", None), "priority": ("severity", "enum", ["critical", "high", "medium", "low"])}),
}
_MODEL_KEY = {e.model: k for k, e in ENTITIES.items()}


# ---- validation & metadata ------------------------------------------------------------------------

def _recipient_ok(v: str) -> bool:
    if v in ("owner", "manager"):
        return True
    kind, _, rest = (v or "").partition(":")
    if kind == "role":
        return rest in ROLES
    if kind == "user":
        try:
            uuid.UUID(rest)
            return True
        except ValueError:
            return False
    return False


def validate(source: str, trigger: dict, conditions: list, actions: list) -> None:
    ent = ENTITIES.get(source)
    if ent is None:
        raise WorkflowError("Choose a record type")
    t = (trigger or {}).get("type")
    if t not in TRIGGERS:
        raise WorkflowError("Choose when the workflow runs")
    if t == "updated":
        fields = trigger.get("fields") or []
        if not fields:
            raise WorkflowError("Pick at least one field to watch for changes")
        bad = [f for f in fields if f not in ent.watch]
        if bad:
            raise WorkflowError(f"Can't watch {', '.join(bad)} on {ent.label.lower()}s")
    if t == "schedule":
        rep = trigger.get("repeat_after_days")
        if rep is not None and not (isinstance(rep, int) and 1 <= rep <= 365):
            raise WorkflowError("Repeat interval must be 1 to 365 days")
        if not conditions:
            raise WorkflowError("Scheduled workflows need at least one condition, or they would act on every record")
    try:
        reporting.validate_filters(source, conditions)
    except reporting.ReportError as e:
        raise WorkflowError(str(e)) from e
    if not actions:
        raise WorkflowError("Add at least one action")
    if len(actions) > MAX_ACTIONS:
        raise WorkflowError(f"At most {MAX_ACTIONS} actions per workflow")
    for a in actions:
        kind = a.get("type")
        if kind == "create_task":
            if not (a.get("title") or "").strip():
                raise WorkflowError("A task needs a title")
            if a.get("priority", "normal") not in PRIORITIES:
                raise WorkflowError("Unknown task priority")
            days = a.get("due_in_days", 1)
            if not isinstance(days, int) or not 0 <= days <= 365:
                raise WorkflowError("Task due date must be 0 to 365 days out")
            if not _recipient_ok(a.get("assign_to", "owner")) or str(a.get("assign_to", "")).startswith("role:"):
                raise WorkflowError("Assign the task to the owner, their manager or a named user")
        elif kind == "notify":
            if not (a.get("title") or "").strip():
                raise WorkflowError("A notification needs a message")
            if not a.get("to") or not all(_recipient_ok(r) for r in a["to"]):
                raise WorkflowError("Choose who gets the notification")
        elif kind == "update_field":
            spec = ent.settable.get(a.get("field"))
            if spec is None:
                raise WorkflowError(f"That field can't be set on {ent.label.lower()}s")
            if spec[1] == "enum" and a.get("value") not in spec[2]:
                raise WorkflowError(f"Choose one of: {', '.join(spec[2])}")
            if spec[1] == "user":
                try:
                    uuid.UUID(str(a.get("value")))
                except ValueError as e:
                    raise WorkflowError("Choose a user") from e
        elif kind == "emit_event":
            if not re.fullmatch(r"[a-z][a-z0-9_.]{1,48}", a.get("event") or ""):
                raise WorkflowError("Event names use lower-case letters, digits, dots and underscores")
        else:
            raise WorkflowError(f"Unknown action '{kind}'")


def meta() -> dict:
    out = []
    for key, ent in ENTITIES.items():
        src = reporting.SOURCES[key]
        out.append({
            "key": key, "label": ent.label, "plural": src.label,
            "watch": [{"key": k, "label": src.fields[k].label} for k in ent.watch if k in src.fields],
            "settable": [{"key": k, "label": src.fields[k].label if k in src.fields else k.title(), "kind": kind, "options": opts}
                         for k, (_, kind, opts) in ent.settable.items()],
            "fields": [{"key": k, "label": f.label, "type": f.type, "options": f.options, "ops": list(reporting.OPS[f.type])}
                       for k, f in src.fields.items()],
        })
    return {"entities": out, "periods": list(reporting.RELATIVE), "roles": list(ROLES), "priorities": list(PRIORITIES)}


# ---- execution --------------------------------------------------------------------------------------

_TEMPLATE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def render(text: str | None, values: dict[str, str]) -> str:
    return _TEMPLATE.sub(lambda m: values.get(m.group(1), ""), text or "")


async def _recipients(db: AsyncSession, spec: str, owner_id) -> list[uuid.UUID]:
    if spec == "owner":
        return [owner_id] if owner_id else []
    if spec == "manager":
        return [m] if owner_id and (m := (await db.execute(select(User.manager_id).where(User.id == owner_id))).scalar()) else []
    kind, _, rest = spec.partition(":")
    if kind == "user":
        uid = uuid.UUID(rest)
        return [uid] if (await db.execute(select(User.id).where(User.id == uid, User.is_active.is_(True)))).first() else []
    if kind == "role":
        return list((await db.execute(select(User.id).where(User.role == rest, User.is_active.is_(True)))).scalars())
    return []


async def execute(db: AsyncSession, rule: WorkflowRule, record_id, trigger: str, dry_run: bool = False) -> list[dict]:
    """Run every action of ``rule`` on one record. Adds a WorkflowRun; the caller commits."""
    ent = ENTITIES[rule.source]
    obj = await db.get(ent.model, record_id)
    if obj is None:
        return [{"action": "-", "ok": False, "detail": "Record no longer exists"}]
    values = await reporting.record_values(db, rule.source, record_id)
    owner_id = await ent.owner(db, obj)
    results = []
    for a in rule.actions:
        kind = a["type"]
        try:
            if kind == "create_task":
                who = (await _recipients(db, a.get("assign_to", "owner"), owner_id)) or [owner_id]
                title = render(a["title"], values)[:255]
                due = date.today() + timedelta(days=int(a.get("due_in_days", 1)))
                if not dry_run:
                    db.add(Task(title=title, description=render(a.get("description"), values) or None, due_date=due,
                                priority=a.get("priority", "normal"), owner_id=who[0], assignee_id=who[0],
                                account_id=ent.account_id(obj), deal_id=ent.deal_id(obj), source="workflow"))
                results.append({"action": kind, "ok": True, "detail": f"Task “{title}” due {due.isoformat()}" + ("" if who[0] else " (unassigned)")})
            elif kind == "notify":
                ids: list[uuid.UUID] = []
                for r in a["to"]:
                    ids += await _recipients(db, r, owner_id)
                ids = list(dict.fromkeys(ids))
                title = render(a["title"], values)
                if not dry_run:
                    notify(db, ids, "workflow", title, render(a.get("body"), values) or None, ent.link(obj))
                results.append({"action": kind, "ok": bool(ids), "detail": f"Notified {len(ids)} people: {title}" if ids else "Nobody to notify"})
            elif kind == "update_field":
                attr, typ, _ = ent.settable[a["field"]]
                value = a["value"]
                if typ == "user":
                    value = uuid.UUID(str(value))
                    if not (await db.execute(select(User.id).where(User.id == value, User.is_active.is_(True)))).first():
                        raise WorkflowError("The chosen user is inactive or deleted")
                if not dry_run:
                    setattr(obj, attr, value)
                results.append({"action": kind, "ok": True, "detail": f"Set {a['field']}"})
            elif kind == "emit_event":
                name = f"workflow.{a['event']}"
                if not dry_run:
                    emit(db, name, rule.source.rstrip("s"), record_id, {"workflow": rule.name, "record": values})
                results.append({"action": kind, "ok": True, "detail": f"Emitted {name}"})
        except Exception as e:  # one failing action must not stop the others
            log.warning("Workflow %s action %s failed: %s", rule.id, kind, e)
            results.append({"action": kind, "ok": False, "detail": str(e)[:300]})
    status = "dry_run" if dry_run else ("done" if all(r["ok"] for r in results) else "failed")
    if not dry_run:
        db.add(WorkflowRun(rule_id=rule.id, record_id=record_id, trigger=trigger, status=status, detail=results))
        rule.run_count = (rule.run_count or 0) + 1
        rule.last_run_at = datetime.now(timezone.utc)
    return results


# ---- record triggers ----------------------------------------------------------------------------------

_rules_cache: tuple[float, list[dict]] = (0.0, [])
_pending: set[asyncio.Task] = set()


def invalidate_cache() -> None:
    global _rules_cache
    _rules_cache = (0.0, [])


async def _active_record_rules(db: AsyncSession) -> list[dict]:
    global _rules_cache
    if time.monotonic() - _rules_cache[0] < 10:
        return _rules_cache[1]
    rows = (await db.execute(select(WorkflowRule).where(WorkflowRule.enabled.is_(True)))).scalars().all()
    rules = [{"id": r.id, "source": r.source, "trigger": r.trigger, "conditions": r.conditions} for r in rows
             if r.trigger.get("type") in ("created", "updated")]
    _rules_cache = (time.monotonic(), rules)
    return rules


@event.listens_for(Session, "after_flush")
def _capture(session: Session, flush_context) -> None:
    events = session.info.setdefault("wf_events", [])
    for obj in session.new:
        key = _MODEL_KEY.get(type(obj))
        if key:
            events.append({"kind": "created", "source": key, "id": obj.id, "changed": []})
    for obj in session.dirty:
        key = _MODEL_KEY.get(type(obj))
        if not key or not ENTITIES[key].watch:
            continue
        state = inspect(obj)
        changed = [k for k, attr in ENTITIES[key].watch.items() if state.attrs[attr].history.has_changes()]
        if changed:
            events.append({"kind": "updated", "source": key, "id": obj.id, "changed": changed})


@event.listens_for(Session, "after_commit")
def _dispatch(session: Session) -> None:
    events = session.info.pop("wf_events", None)
    if not events:
        return
    depth, origin = session.info.get("wf_depth", 0), session.info.get("wf_rule")
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_process(events, depth, origin))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


@event.listens_for(Session, "after_rollback")
def _discard(session: Session) -> None:
    session.info.pop("wf_events", None)


async def drain() -> None:
    """Wait for in-flight workflow evaluations (tests, and background jobs before their loop closes)."""
    while _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


async def _process(events: list[dict], depth: int, origin) -> None:
    if depth >= MAX_DEPTH:
        log.warning("Workflow chain stopped at depth %s", depth)
        return
    try:
        async with SessionLocal() as db:
            rules = await _active_record_rules(db)
            if not rules:
                return
            db.info["wf_depth"] = depth + 1
            # Match every rule against the state that triggered it before running any action, so one
            # rule's changes can't make a sibling rule fire in the same pass (they cascade a level down).
            matched: list[tuple[uuid.UUID, uuid.UUID, str]] = []
            seen = set()
            for ev in events:
                if (ev["kind"], ev["source"], ev["id"]) in seen:
                    continue
                seen.add((ev["kind"], ev["source"], ev["id"]))
                for r in rules:
                    t = r["trigger"]
                    if r["source"] != ev["source"] or r["id"] == origin or t["type"] != ev["kind"]:
                        continue
                    if ev["kind"] == "updated" and not set(t.get("fields") or []) & set(ev["changed"]):
                        continue
                    if await reporting.match_ids(db, r["source"], r["conditions"], [ev["id"]]):
                        matched.append((r["id"], ev["id"], ev["kind"]))
            for rule_id, record_id, kind in matched:
                rule = await db.get(WorkflowRule, rule_id)
                if rule is None or not rule.enabled:
                    continue
                db.info["wf_rule"] = rule.id
                await execute(db, rule, record_id, kind)
                await db.commit()
    except Exception:  # never let automation break the request that triggered it
        log.exception("Workflow evaluation failed")


# ---- scheduled rules --------------------------------------------------------------------------------

async def run_scheduled(db: AsyncSession) -> dict:
    rules = (await db.execute(select(WorkflowRule).where(WorkflowRule.enabled.is_(True)))).scalars().all()
    stats = {"rules": 0, "actions_run": 0}
    db.info["wf_depth"] = 1
    for rule in [r for r in rules if r.trigger.get("type") == "schedule"]:
        stats["rules"] += 1
        ids = await reporting.match_ids(db, rule.source, rule.conditions, limit=SCHEDULE_BATCH)
        if not ids:
            continue
        done = select(WorkflowRun.record_id).where(WorkflowRun.rule_id == rule.id, WorkflowRun.record_id.in_(ids),
                                                   WorkflowRun.status != "dry_run")
        rep = rule.trigger.get("repeat_after_days")
        if rep:
            done = done.where(WorkflowRun.created_at > func.now() - timedelta(days=rep))
        already = set((await db.execute(done)).scalars())
        db.info["wf_rule"] = rule.id
        for rid in ids:
            if rid in already:
                continue
            await execute(db, rule, rid, "schedule")
            stats["actions_run"] += 1
        await db.commit()
    return stats


async def dry_run(db: AsyncSession, rule: WorkflowRule, record_id=None) -> dict:
    """What the rule would do, without doing it: the matching records and each action's outcome."""
    if record_id is not None:
        ids = await reporting.match_ids(db, rule.source, rule.conditions, [record_id])
        matched = bool(ids)
        results = await execute(db, rule, record_id, "test", dry_run=True) if matched else []
        return {"matched": matched, "matching_ids": ids, "results": results}
    ids = await reporting.match_ids(db, rule.source, rule.conditions, limit=SCHEDULE_BATCH)
    sample = []
    for rid in ids[:3]:
        vals = await reporting.record_values(db, rule.source, rid)
        name = next((vals[k] for k in ("title", "name", "quote_number", "order_number", "subject") if vals.get(k)), str(rid))
        sample.append({"id": rid, "name": name, "results": await execute(db, rule, rid, "test", dry_run=True)})
    return {"matching_count": len(ids), "capped": len(ids) >= SCHEDULE_BATCH, "sample": sample}
