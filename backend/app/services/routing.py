"""Case routing across channels (email, web, phone, chat, portal all land in queues the same way).

Each queue routes one of two ways:

* ``least_loaded`` (default): a new case goes to the active member with the fewest open cases.
* ``presence``: only members who are *Available* and under their capacity receive work. The one with the
  fewest open cases gets it, then whoever has waited longest since their last assignment. When nobody is
  free the case waits unassigned in the queue, and it is pushed to the next agent who becomes available or
  frees capacity: waiting cases go out by priority, then age.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import case as sql_case
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentPresence, SupportQueue, SupportTicket, User
from app.services.notify import notify

STATUSES = ("available", "busy", "away", "offline")
MODES = ("least_loaded", "presence")
OPEN = ("open", "pending")
PRIORITY_ORDER = sql_case({"critical": 0, "high": 1, "medium": 2, "low": 3}, value=SupportTicket.severity, else_=4)


def _members(queue: SupportQueue) -> list[uuid.UUID]:
    return [uuid.UUID(str(i)) for i in queue.member_ids or []]


async def loads(db: AsyncSession, user_ids) -> dict[uuid.UUID, int]:
    ids = list(user_ids)
    if not ids:
        return {}
    rows = (await db.execute(select(SupportTicket.owner_id, func.count()).where(SupportTicket.owner_id.in_(ids), SupportTicket.status.in_(OPEN))
                             .group_by(SupportTicket.owner_id))).all()
    out = {i: 0 for i in ids}
    out.update(dict(rows))
    return out


async def pick(db: AsyncSession, queue: SupportQueue) -> uuid.UUID | None:
    """The agent a new case in ``queue`` goes to, or None to leave it waiting."""
    ids = _members(queue)
    if not ids:
        return None
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(ids), User.is_active.is_(True)))).scalars()}
    if not users:
        return None
    load = await loads(db, users)
    if queue.routing != "presence":
        return min(users, key=lambda i: (load[i], users[i].full_name))
    presence = {p.user_id: p for p in (await db.execute(select(AgentPresence).where(AgentPresence.user_id.in_(list(users))))).scalars()}
    free = [i for i in users if i in presence and presence[i].status == "available" and load[i] < presence[i].capacity]
    if not free:
        return None
    never = datetime.min.replace(tzinfo=timezone.utc)
    chosen = min(free, key=lambda i: (load[i], presence[i].last_assigned_at or never, users[i].full_name))
    presence[chosen].last_assigned_at = datetime.now(timezone.utc)
    return chosen


async def assign_waiting(db: AsyncSession, queue_ids=None, limit: int = 200) -> int:
    """Push unassigned open cases in presence-routed queues to free agents. Returns how many were assigned."""
    stmt = select(SupportQueue).where(SupportQueue.routing == "presence", SupportQueue.auto_assign.is_(True))
    if queue_ids is not None:
        ids = [q for q in queue_ids if q]
        if not ids:
            return 0
        stmt = stmt.where(SupportQueue.id.in_(ids))
    assigned = 0
    for queue in (await db.execute(stmt)).scalars().all():
        waiting = (await db.execute(select(SupportTicket).where(SupportTicket.queue_id == queue.id, SupportTicket.owner_id.is_(None),
                                                                SupportTicket.status.in_(OPEN))
                                    .order_by(PRIORITY_ORDER, SupportTicket.opened_at).limit(limit))).scalars().all()
        for c in waiting:
            agent = await pick(db, queue)
            if agent is None:
                break
            c.owner_id, c.updated_at = agent, datetime.now(timezone.utc)
            notify(db, [agent], "case", f"{c.case_number} assigned to you: {c.subject}", f"Routed from {queue.name}", f"/cases/{c.id}")
            assigned += 1
            await db.flush()  # the next pick sees this agent's new load
    return assigned


async def set_presence(db: AsyncSession, user: User, status: str | None = None, capacity: int | None = None) -> AgentPresence:
    p = await db.get(AgentPresence, user.id)
    if p is None:
        p = AgentPresence(user_id=user.id, status="offline", capacity=5)
        db.add(p)
    if status is not None:
        p.status = status
    if capacity is not None:
        p.capacity = capacity
    p.updated_at = datetime.now(timezone.utc)
    await db.flush()
    if p.status == "available":
        await assign_waiting(db, [q.id for q in (await db.execute(select(SupportQueue))).scalars() if str(user.id) in (q.member_ids or [])])
    return p


async def console(db: AsyncSession) -> dict:
    """Supervisor view: every queue member's presence, load and capacity, and what is waiting in each queue."""
    queues = (await db.execute(select(SupportQueue).order_by(SupportQueue.name))).scalars().all()
    member_ids = {m for q in queues for m in _members(q)}
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(member_ids)))).scalars()} if member_ids else {}
    presence = {p.user_id: p for p in (await db.execute(select(AgentPresence).where(AgentPresence.user_id.in_(list(users))))).scalars()} if users else {}
    load = await loads(db, users)
    waiting = dict((await db.execute(select(SupportTicket.queue_id, func.count()).where(SupportTicket.owner_id.is_(None), SupportTicket.status.in_(OPEN))
                                     .group_by(SupportTicket.queue_id))).all())
    oldest = dict((await db.execute(select(SupportTicket.queue_id, func.min(SupportTicket.opened_at))
                                    .where(SupportTicket.owner_id.is_(None), SupportTicket.status.in_(OPEN)).group_by(SupportTicket.queue_id))).all())
    return {
        "agents": [{"id": u.id, "name": u.full_name, "active": u.is_active, "status": presence[u.id].status if u.id in presence else "offline",
                    "capacity": presence[u.id].capacity if u.id in presence else 5, "open_cases": load.get(u.id, 0),
                    "last_assigned_at": presence[u.id].last_assigned_at if u.id in presence else None,
                    "queues": [q.name for q in queues if u.id in _members(q)]} for u in sorted(users.values(), key=lambda u: u.full_name)],
        "queues": [{"id": q.id, "name": q.name, "routing": q.routing, "email_address": q.email_address, "waiting": waiting.get(q.id, 0),
                    "oldest_waiting_at": oldest.get(q.id)} for q in queues],
    }
