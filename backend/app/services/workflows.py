"""No-code workflows: when a record is created, when watched fields change, or on a schedule,
check conditions and run actions (create a task, notify people, update a field, emit an event).

How record triggers fire: a session listener notes which watched records were inserted or had a
watched column change during each flush; after the transaction commits, the rules are evaluated in
a fresh session (so a rolled-back change never triggers anything). Changes made by a workflow can
trigger other workflows, up to MAX_DEPTH levels, and a rule never re-triggers itself. The noted changes are
written to ``workflow_events`` in the same transaction, so a restart between commit and evaluation loses nothing.

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

from sqlalchemy import and_, delete, event, func, inspect, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.rbac import ROLES
from app.models import (
    Account, Activity, Contact, Deal, Lead, Order, Quote, SupportTicket, Task, User, WorkflowEvent, WorkflowRule, WorkflowRun,
)
from app.services import reporting
from app.services.notify import emit, notify

log = logging.getLogger(__name__)

MAX_DEPTH = 3
SCHEDULE_BATCH = 200
TRIGGERS = ("created", "updated", "schedule")
CHANNELS = ("slack", "teams")
HTTP_TIMEOUT = 10.0
http_transport = None  # tests swap in an httpx transport


def _check_url(url: str | None, https_only: bool = False) -> None:
    from urllib.parse import urlparse

    from app.services import developer

    u = urlparse((url or "").strip())
    if https_only and u.scheme != "https":
        raise WorkflowError("Enter a full https:// URL")
    try:
        developer.clean_url(url)
    except developer.DeveloperError as e:
        raise WorkflowError(str(e)) from e
    if _blocked_host(u.hostname or ""):
        raise WorkflowError("That address points at this server or a cloud metadata service")


def _blocked_host(host: str) -> bool:
    """Loopback, link-local (incl. 169.254.169.254 metadata), unspecified and multicast targets are refused.
    Private ranges stay allowed: in a private-cloud install the ERP usually lives on one."""
    import ipaddress
    import socket

    host = host.strip("[]").lower()
    if not host or host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        addrs = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addrs = {ipaddress.ip_address(a[4][0]) for a in socket.getaddrinfo(host, None)}
        except (socket.gaierror, UnicodeError, ValueError):
            return False  # unresolvable now; the POST itself will fail and be logged
    return any(a.is_loopback or a.is_link_local or a.is_unspecified or a.is_multicast for a in addrs)


async def _post(url: str, payload: dict, headers: dict | None = None, attempts: int = 2) -> tuple[bool, str]:
    """POST JSON to an outside system, retrying once on a network error or 5xx. Returns (ok, status) for the run
    log. The response body is never recorded, so the log can't be used to read internal services."""
    import asyncio
    from urllib.parse import urlparse

    import httpx

    if await asyncio.to_thread(_blocked_host, urlparse(url).hostname or ""):
        return False, "Blocked: address points at this server or a cloud metadata service"
    what = "no attempt"
    for n in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, transport=http_transport, follow_redirects=False) as client:
                r = await client.post(url, json=payload, headers={"User-Agent": "Cirra-Workflows/1.0", **(headers or {})})
            if 200 <= r.status_code < 300:
                return True, f"HTTP {r.status_code}"
            what = f"HTTP {r.status_code}"
            if r.status_code < 500:
                break
        except httpx.HTTPError as e:
            what = e.__class__.__name__
        if n + 1 < attempts:
            await asyncio.sleep(1.5)
    return False, what


async def send_pending(db: AsyncSession) -> None:
    """Deliver the HTTP / chat actions queued by execute(), after the rule's changes committed, a few at a time,
    then record each outcome on its run. Nothing is sent for a rule whose commit failed (the queue is dropped)."""
    import asyncio

    pending = db.info.pop("wf_http", None)
    if not pending:
        return
    gate = asyncio.Semaphore(5)

    async def one(item):
        async with gate:
            return await _post(item["url"], item["payload"], item.get("headers"))

    outcomes = await asyncio.gather(*(one(i) for i in pending))
    for item, (ok, what) in zip(pending, outcomes):
        run = await db.get(WorkflowRun, item["run_id"])
        if run is None:
            continue
        detail = [dict(d) for d in run.detail]
        detail[item["index"]] = {"action": item["kind"], "ok": ok, "detail": f"{item['label']}: {what}"}
        run.detail = detail
        if run.status in ("done", "failed"):  # a waiting run settles its status when it finishes
            run.status = "done" if all(d["ok"] for d in detail) else "failed"
    await db.commit()
ACTION_TYPES = ("create_task", "notify", "update_field", "emit_event", "http_request", "post_message", "wait", "branch")
MAX_STEPS = 25          # every step, including those inside branches
MAX_BRANCH_DEPTH = 2    # a branch may contain one more level of branches
RESUME_BATCH = 200
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


ENTITY_TYPE = {"deals": "deal", "leads": "lead", "accounts": "account", "contacts": "contact", "activities": "activity",
               "tasks": "task", "quotes": "quote", "orders": "order", "cases": "case"}

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
    counter = [0]
    _validate_steps(ent, source, actions, 0, counter)
    if counter[0] > MAX_STEPS:
        raise WorkflowError(f"At most {MAX_STEPS} steps per workflow (including steps inside branches)")


def _validate_steps(ent: "Entity", source: str, actions: list, depth: int, counter: list[int]) -> None:
    if not isinstance(actions, list):
        raise WorkflowError("Steps must be a list")
    for a in actions:
        counter[0] += 1
        kind = a.get("type")
        if kind == "wait":
            days, hours = a.get("days", 0), a.get("hours", 0)
            if not (isinstance(days, int) and isinstance(hours, int) and 0 <= days <= 365 and 0 <= hours <= 23):
                raise WorkflowError("A wait is 0 to 365 days plus 0 to 23 hours")
            if days == 0 and hours == 0:
                raise WorkflowError("A wait must be at least one hour")
            continue
        if kind == "branch":
            if depth >= MAX_BRANCH_DEPTH:
                raise WorkflowError(f"Branches can be nested at most {MAX_BRANCH_DEPTH} deep")
            if not a.get("conditions"):
                raise WorkflowError("A branch needs at least one condition")
            try:
                reporting.validate_filters(source, a["conditions"])
            except reporting.ReportError as e:
                raise WorkflowError(str(e)) from e
            if not a.get("then") and not a.get("else"):
                raise WorkflowError("Add steps to at least one side of the branch")
            _validate_steps(ent, source, a.get("then") or [], depth + 1, counter)
            _validate_steps(ent, source, a.get("else") or [], depth + 1, counter)
            continue
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
        elif kind == "http_request":
            _check_url(a.get("url"))
        elif kind == "post_message":
            if a.get("channel") not in CHANNELS:
                raise WorkflowError("Post to Slack or Microsoft Teams")
            _check_url(a.get("webhook_url"), https_only=True)
            if not (a.get("text") or "").strip():
                raise WorkflowError("Write the message to post")
        else:
            raise WorkflowError(f"Unknown action '{kind}'")


def meta() -> dict:
    out = []
    for key, ent in ENTITIES.items():
        src = reporting.src_of(key)
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

_TEMPLATE = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")


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


async def execute(db: AsyncSession, rule: WorkflowRule, record_id, trigger: str, dry_run: bool = False,
                  steps: list | None = None, run: WorkflowRun | None = None) -> list[dict]:
    """Run ``rule``'s steps on one record (or, resuming ``run``, the steps left after its wait). Adds or updates a
    WorkflowRun; the caller commits. A wait step stops here: the run is saved as ``waiting`` with the remaining
    steps, and ``resume_waiting`` carries on when the wait is over. Dry runs pass straight through waits and follow
    the branch the record matches today."""
    ent = ENTITIES[rule.source]
    obj = await db.get(ent.model, record_id)
    if obj is None:
        return [{"action": "-", "ok": False, "detail": "Record no longer exists"}]
    values = await reporting.record_values(db, rule.source, record_id)
    owner_id = await ent.owner(db, obj)
    results: list[dict] = []
    outbound: list[dict] = []  # HTTP / chat posts, sent after the commit by send_pending()
    base = len(run.detail) if run is not None else 0
    queue = list(steps if steps is not None else rule.actions)
    resume_at, remaining = None, None
    while queue:
        a = queue.pop(0)
        kind = a["type"]
        if kind == "wait":
            delta = timedelta(days=int(a.get("days", 0)), hours=int(a.get("hours", 0)))
            label = _wait_label(a)
            if dry_run:
                results.append({"action": kind, "ok": True, "detail": f"Would wait {label}, then continue"})
                continue
            resume_at, remaining = datetime.now(timezone.utc) + delta, queue
            results.append({"action": kind, "ok": True, "detail": f"Waiting {label} (until {resume_at:%Y-%m-%d %H:%M} UTC)"})
            break
        if kind == "branch":
            try:
                hit = bool(await reporting.match_ids(db, rule.source, a["conditions"], [record_id]))
            except Exception as e:  # a condition on a deleted field: take the else path, say why
                log.warning("Workflow %s branch condition failed: %s", rule.id, e)
                hit = False
            chosen = (a.get("then") if hit else a.get("else")) or []
            results.append({"action": kind, "ok": True, "detail": f"Condition {'met' if hit else 'not met'}: "
                                                                   f"{'then' if hit else 'else'} path ({len(chosen)} steps)"})
            queue = list(chosen) + queue
            continue
        await _do_step(db, rule, ent, obj, record_id, values, owner_id, a, trigger, dry_run, results, outbound, base)
    status = "dry_run" if dry_run else ("waiting" if resume_at else ("done" if all(r["ok"] for r in results) and
                                                                    (run is None or all(d["ok"] for d in run.detail)) else "failed"))
    if not dry_run:
        if run is None:
            run = WorkflowRun(id=uuid.uuid4(), rule_id=rule.id, record_id=record_id, trigger=trigger, status=status, detail=results)
            db.add(run)
        else:
            run.detail, run.status = [*run.detail, *results], status
        run.resume_at, run.pending_actions = resume_at, remaining
        for item in outbound:
            item["run_id"] = run.id
        db.info.setdefault("wf_http", []).extend(outbound)
        if base == 0:
            rule.run_count = (rule.run_count or 0) + 1
            rule.last_run_at = datetime.now(timezone.utc)
    return results


def _wait_label(a: dict) -> str:
    parts = [f"{a.get('days')} day{'s' if a.get('days') != 1 else ''}" if a.get("days") else "",
             f"{a.get('hours')} hour{'s' if a.get('hours') != 1 else ''}" if a.get("hours") else ""]
    return " ".join(p for p in parts if p)


async def _do_step(db: AsyncSession, rule: WorkflowRule, ent: Entity, obj, record_id, values: dict, owner_id, a: dict, trigger: str,
                   dry_run: bool, results: list[dict], outbound: list[dict], base: int) -> None:
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
                if rule.source == "cases":  # through the case service: priority re-times SLAs, owner changes notify
                    from app.services import cases as case_svc

                    await case_svc.update(db, obj, {attr: value})
                else:
                    setattr(obj, attr, value)
            results.append({"action": kind, "ok": True, "detail": f"Set {a['field']}"})
        elif kind == "emit_event":
            name = f"workflow.{a['event']}"
            if not dry_run:
                emit(db, name, ENTITY_TYPE.get(rule.source, rule.source), record_id, {"workflow": rule.name, "record": values})
            results.append({"action": kind, "ok": True, "detail": f"Emitted {name}"})
        elif kind == "http_request":
            host = a["url"].split("/")[2]
            if dry_run:
                results.append({"action": kind, "ok": True, "detail": f"Would POST the record to {host}"})
            else:  # sent after this rule's changes commit (send_pending)
                outbound.append({"index": base + len(results), "kind": kind, "label": f"POST {host}", "url": a["url"],
                                 "payload": {"workflow": rule.name, "source": rule.source, "record_id": str(record_id), "trigger": trigger,
                                             "record": values}, "headers": {"X-Cirra-Workflow": str(rule.id)}})
                results.append({"action": kind, "ok": True, "detail": f"POST {host}: queued"})
        elif kind == "post_message":
            text = render(a["text"], values)
            where = "Slack" if a["channel"] == "slack" else "Microsoft Teams"
            if dry_run:
                results.append({"action": kind, "ok": True, "detail": f"Would post to {where}: {text[:120]}"})
            else:
                outbound.append({"index": base + len(results), "kind": kind, "label": where, "url": a["webhook_url"], "payload": {"text": text}})
                results.append({"action": kind, "ok": True, "detail": f"{where}: queued"})
    except Exception as e:  # one failing action must not stop the others
        log.warning("Workflow %s action %s failed: %s", rule.id, kind, e)
        results.append({"action": kind, "ok": False, "detail": str(e)[:300]})


async def resume_waiting(db: AsyncSession) -> dict:
    """Continue runs whose wait is over. A run stops (``cancelled``) when its rule was switched off or its record
    deleted, and ends early when the record no longer matches the rule's conditions (unless the rule keeps going:
    ``trigger.stop_if_unmatched = false``). Rows are claimed with SKIP LOCKED so two workers never resume one run."""
    now = datetime.now(timezone.utc)
    runs = (await db.execute(select(WorkflowRun).where(WorkflowRun.status == "waiting", WorkflowRun.resume_at <= now)
                             .order_by(WorkflowRun.resume_at).limit(RESUME_BATCH).with_for_update(skip_locked=True))).scalars().all()
    stats = {"resumed": 0, "cancelled": 0, "stopped": 0}
    db.info["wf_depth"] = 1
    for run in runs:
        rule = await db.get(WorkflowRule, run.rule_id)
        record_ok = rule is not None and run.record_id is not None and await db.get(ENTITIES[rule.source].model, run.record_id) is not None
        if rule is None or not rule.enabled or not record_ok:
            why = "Workflow switched off" if rule is not None and not rule.enabled else "Record no longer exists"
            run.detail = [*run.detail, {"action": "resume", "ok": True, "detail": f"Stopped: {why}"}]
            run.status, run.resume_at, run.pending_actions = "cancelled", None, None
            stats["cancelled"] += 1
            continue
        if rule.conditions and rule.trigger.get("stop_if_unmatched", True) and not await reporting.match_ids(db, rule.source, rule.conditions, [run.record_id]):
            run.detail = [*run.detail, {"action": "resume", "ok": True, "detail": "Stopped: the record no longer matches the conditions"}]
            run.status, run.resume_at, run.pending_actions = "done", None, None
            stats["stopped"] += 1
            continue
        db.info["wf_rule"] = rule.id
        await execute(db, rule, run.record_id, run.trigger, steps=run.pending_actions or [], run=run)
        stats["resumed"] += 1
    await db.commit()
    await send_pending(db)
    return stats


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
    """Queue the watched changes of this flush in ``workflow_events``, inside the same transaction: a rolled-back
    change leaves nothing behind, and a committed one is never lost even if this process stops before the
    rules run (the ``workflow_events`` job picks it up)."""
    depth, origin = session.info.get("wf_depth", 0), session.info.get("wf_rule")
    if depth >= MAX_DEPTH:
        if session.new or session.dirty:
            log.debug("Workflow chain stops at depth %s", depth)
        return
    rows = []
    for obj in session.new:
        key = _MODEL_KEY.get(type(obj))
        if key:
            rows.append({"kind": "created", "source": key, "record_id": obj.id, "changed": []})
    for obj in session.dirty:
        key = _MODEL_KEY.get(type(obj))
        if not key or not ENTITIES[key].watch:
            continue
        state = inspect(obj)
        changed = [k for k, attr in ENTITIES[key].watch.items() if state.attrs[attr].history.has_changes()]
        if changed:
            rows.append({"kind": "updated", "source": key, "record_id": obj.id, "changed": changed})
    if not rows:
        return
    now = datetime.now(timezone.utc)
    for r in rows:
        r.update(id=uuid.uuid4(), depth=depth, origin_rule_id=origin, created_at=now)
    session.connection().execute(WorkflowEvent.__table__.insert(), rows)
    session.info.setdefault("wf_event_ids", []).extend(r["id"] for r in rows)


@event.listens_for(Session, "after_commit")
def _dispatch(session: Session) -> None:
    ids = session.info.pop("wf_event_ids", None)
    if not ids:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (sync tooling): the workflow_events job will run them
    task = loop.create_task(_run_queued(ids))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


@event.listens_for(Session, "after_rollback")
def _discard(session: Session) -> None:
    session.info.pop("wf_event_ids", None)


async def _claim(db: AsyncSession, stmt) -> list[WorkflowEvent]:
    rows = list((await db.execute(stmt.with_for_update(skip_locked=True))).scalars())
    now = datetime.now(timezone.utc)
    for r in rows:
        r.claimed_at = now
    await db.commit()
    return rows


async def _run_queued(ids: list | None = None, stale: bool = False) -> int:
    """Evaluate queued events (``ids`` from a commit just now, or with ``stale`` anything left behind by a
    stopped process), then remove them. Claiming uses SKIP LOCKED, so no event runs twice concurrently."""
    try:
        async with SessionLocal() as db:
            stmt = select(WorkflowEvent)
            if ids is not None:
                stmt = stmt.where(WorkflowEvent.id.in_(ids), WorkflowEvent.claimed_at.is_(None))
            else:
                now = datetime.now(timezone.utc)
                stmt = stmt.where(or_(and_(WorkflowEvent.claimed_at.is_(None), WorkflowEvent.created_at < now - timedelta(minutes=1)),
                                      WorkflowEvent.claimed_at < now - timedelta(minutes=10))).order_by(WorkflowEvent.created_at).limit(500)
            rows = await _claim(db, stmt)
        if not rows:
            return 0
        groups: dict[tuple, list[dict]] = {}
        for r in rows:
            groups.setdefault((r.depth, r.origin_rule_id), []).append({"kind": r.kind, "source": r.source, "id": r.record_id, "changed": r.changed})
        for (depth, origin), events in groups.items():
            await _process(events, depth, origin)
        async with SessionLocal() as db:
            await db.execute(delete(WorkflowEvent).where(WorkflowEvent.id.in_([r.id for r in rows])))
            await db.commit()
        return len(rows)
    except Exception:  # never let automation break the request that triggered it; the rescue job retries
        log.exception("Queued workflow evaluation failed")
        return 0


async def run_stale_events() -> dict:
    """Job: evaluate triggers a stopped process left in the queue."""
    return {"recovered": await _run_queued(stale=True)}


async def drain() -> None:
    """Wait for in-flight workflow evaluations on this event loop (tests, and background jobs before their loop
    closes). Tasks left on a loop that has since closed are dropped: their events are still in the queue and the
    ``workflow_events`` job evaluates them."""
    loop = asyncio.get_running_loop()
    while True:
        for t in [t for t in _pending if t.get_loop() is not loop]:
            _pending.discard(t)
        mine = [t for t in _pending if not t.done()]
        if not mine:
            return
        await asyncio.gather(*mine, return_exceptions=True)


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
                try:  # each rule commits on its own: one failure must not skip the others
                    rule = await db.get(WorkflowRule, rule_id)
                    if rule is None or not rule.enabled:
                        continue
                    db.info["wf_rule"] = rule.id
                    await execute(db, rule, record_id, kind)
                    await db.commit()
                    await send_pending(db)
                except Exception:
                    db.info.pop("wf_http", None)  # nothing leaves for a rule that didn't commit
                    await db.rollback()
                    log.exception("Workflow rule %s failed on %s", rule_id, record_id)
    except Exception:  # never let automation break the request that triggered it
        log.exception("Workflow evaluation failed")


# ---- scheduled rules --------------------------------------------------------------------------------

async def run_scheduled(db: AsyncSession) -> dict:
    rules = (await db.execute(select(WorkflowRule).where(WorkflowRule.enabled.is_(True)))).scalars().all()
    stats = {"rules": 0, "actions_run": 0}
    db.info["wf_depth"] = 1
    for rule in [r for r in rules if r.trigger.get("type") == "schedule"]:
        stats["rules"] += 1
        rule_id = rule.id
        # records already handled (within the repeat window) are excluded in the query itself, so each batch is
        # new work and matches beyond the first batch are reached on later runs
        done = select(WorkflowRun.record_id).where(WorkflowRun.rule_id == rule_id, WorkflowRun.record_id.is_not(None),
                                                   WorkflowRun.status != "dry_run")
        rep = rule.trigger.get("repeat_after_days")
        if rep:
            done = done.where(WorkflowRun.created_at > func.now() - timedelta(days=rep))
        try:
            ids = await reporting.match_ids(db, rule.source, rule.conditions, limit=SCHEDULE_BATCH, exclude=done)
            db.info["wf_rule"] = rule_id
            for rid in ids:
                await execute(db, rule, rid, "schedule")
                stats["actions_run"] += 1
            await db.commit()
            await send_pending(db)
        except Exception:
            db.info.pop("wf_http", None)
            await db.rollback()
            log.exception("Scheduled workflow %s failed", rule_id)
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
