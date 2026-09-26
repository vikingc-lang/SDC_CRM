"""Calendar interoperability (pillar 5): iCal feeds (CalDAV/iCal subscribers) and .ics import."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from icalendar import Calendar, Event
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, Contact, Task, User


async def user_feed(db: AsyncSession, user: User) -> bytes:
    """Subscribable calendar: the user's open tasks (all-day on due date) and scheduled meetings."""
    cal = Calendar()
    cal.add("prodid", "-//SDC Solutions//Cirra//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", f"Cirra: {user.full_name}")
    tasks = (await db.execute(select(Task).where(or_(Task.assignee_id == user.id, Task.owner_id == user.id), Task.completed.is_(False),
                                                 Task.due_date.is_not(None)))).scalars().unique().all()
    for t in tasks:
        ev = Event()
        ev.add("uid", f"task-{t.id}@cirra")
        ev.add("summary", f"{'[!] ' if t.priority in ('high', 'urgent') else ''}{t.title}")
        ev.add("dtstart", t.due_date)
        ev.add("dtend", t.due_date + timedelta(days=1))
        ev.add("dtstamp", datetime.now(timezone.utc))
        if t.account:
            ev.add("description", f"Account: {t.account.name}")
        cal.add_component(ev)
    meetings = (await db.execute(select(Activity).where(Activity.user_id == user.id, Activity.activity_type == "meeting",
                                                        Activity.occurred_at >= datetime.now(timezone.utc) - timedelta(days=1)))).scalars().unique().all()
    for m in meetings:
        ev = Event()
        ev.add("uid", f"meeting-{m.id}@cirra")
        ev.add("summary", m.subject or m.summary[:80])
        ev.add("dtstart", m.occurred_at)
        ev.add("dtend", m.occurred_at + timedelta(seconds=m.duration_seconds or 1800))
        ev.add("dtstamp", datetime.now(timezone.utc))
        if m.agenda:
            ev.add("description", m.agenda)
        cal.add_component(ev)
    return cal.to_ical()


async def import_ics(db: AsyncSession, data: bytes, user: User) -> dict:
    """Create meeting activities for events whose attendees match CRM contacts."""
    cal = Calendar.from_ical(data)
    created = skipped = 0
    for comp in cal.walk("VEVENT"):
        uid = str(comp.get("uid", ""))
        external = f"ics:{uid}" if uid else None
        if external and (await db.execute(select(Activity.id).where(Activity.external_id == external))).first():
            skipped += 1
            continue
        attendees = comp.get("attendee") or []
        if not isinstance(attendees, list):
            attendees = [attendees]
        emails = {str(a).lower().removeprefix("mailto:") for a in attendees}
        contact = (await db.execute(select(Contact).where(func.lower(Contact.email).in_(emails)))).scalars().first() if emails else None
        if contact is None:
            skipped += 1
            continue
        start = comp.decoded("dtstart")
        end = comp.decoded("dtend") if comp.get("dtend") else None
        if isinstance(start, date) and not isinstance(start, datetime):
            start = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        duration = int((end - start).total_seconds()) if isinstance(end, datetime) and end.tzinfo else None
        summary = str(comp.get("summary", "Meeting"))
        db.add(Activity(account_id=contact.account_id, contact_id=contact.id, user_id=user.id, activity_type="meeting", subject=summary[:500],
                        summary=summary, agenda=str(comp.get("description", "")) or None, occurred_at=start, duration_seconds=duration,
                        attendance="scheduled" if start > datetime.now(timezone.utc) else "attended", sentiment="neutral",
                        external_id=external, source="calendar"))
        created += 1
    await db.flush()
    return {"created": created, "skipped": skipped}

