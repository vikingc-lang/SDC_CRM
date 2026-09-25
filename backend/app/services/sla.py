"""Task & SLA engine (pillar 5): delegation, dependency chains and automated escalation.

Escalation ladder for overdue open tasks:
    level 1  >= 1 day overdue  -> remind the assignee
    level 2  >= 3 days overdue -> notify the assignee's manager, priority high
    level 3  >= 7 days overdue -> reassign to the manager, priority urgent
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, User
from app.services.notify import notify

LADDER = [(1, 1), (2, 3), (3, 7)]  # (level, days overdue)


async def escalate_overdue(db: AsyncSession, today: date | None = None) -> dict:
    today = today or date.today()
    stats = {"reminded": 0, "escalated_to_manager": 0, "reassigned": 0}
    tasks = (await db.execute(select(Task).where(Task.completed.is_(False), Task.due_date < today))).scalars().unique().all()
    for t in tasks:
        overdue = (today - t.due_date).days
        target = max((lvl for lvl, days in LADDER if overdue >= days), default=0)
        if target <= t.escalation_level:
            continue
        assignee_id = t.assignee_id or t.owner_id
        assignee = await db.get(User, assignee_id) if assignee_id else None
        manager_id = assignee.manager_id if assignee else None
        link = f"/tasks?focus={t.id}"
        if target >= 1 and t.escalation_level < 1:
            notify(db, [assignee_id], "sla", f"Overdue: {t.title}", f"{overdue} day(s) past due", link)
            stats["reminded"] += 1
        if target >= 2 and t.escalation_level < 2:
            t.priority = "high" if t.priority in ("low", "normal") else t.priority
            notify(db, [manager_id], "sla", f"Escalated: {t.title}", f"{assignee.full_name if assignee else 'Unassigned'} is {overdue} days overdue", link)
            stats["escalated_to_manager"] += 1
        if target >= 3 and t.escalation_level < 3 and manager_id:
            t.assignee_id, t.priority = manager_id, "urgent"
            notify(db, [manager_id], "sla", f"Reassigned to you: {t.title}", f"Auto-escalated after {overdue} days overdue", link)
            stats["reassigned"] += 1
        t.escalation_level, t.escalated_at = target, datetime.now(timezone.utc)
    await db.commit()
    return stats


async def dependency_cycle(db: AsyncSession, task_id, depends_on_id) -> bool:
    seen, current = set(), depends_on_id
    while current:
        if current == task_id or current in seen:
            return True
        seen.add(current)
        t = await db.get(Task, current)
        current = t.depends_on_id if t else None
    return False
