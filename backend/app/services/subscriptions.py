"""Scheduled report deliveries.

A subscription sends one saved report daily, weekly or monthly at an hour (UTC) to its subscriber and any extra
recipients. The report runs separately for every recipient with that recipient's own permissions and row-level
scope, so a subscription never shows anyone more than they could see by opening the report themselves. Each
recipient gets an in-app notification; when system email is configured they also get an email with the
result attached as CSV.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rbac import principal_for
from app.models import ReportSubscription, SavedReport, User
from app.services import mailer, reporting
from app.services.notify import notify

log = logging.getLogger(__name__)
MAX_RECIPIENTS = 20
PREVIEW_ROWS = 20


def last_slot(sub: ReportSubscription, now: datetime) -> datetime:
    """The most recent scheduled time at or before ``now``."""
    at = now.replace(hour=sub.hour, minute=0, second=0, microsecond=0)
    if sub.frequency == "daily":
        return at if at <= now else at - timedelta(days=1)
    if sub.frequency == "weekly":
        at -= timedelta(days=(at.weekday() - sub.weekday) % 7)
        return at if at <= now else at - timedelta(days=7)
    at = at.replace(day=sub.day_of_month)  # day_of_month is 1-28, so it exists in every month
    if at > now:
        prev = at.replace(day=1) - timedelta(days=1)
        at = at.replace(year=prev.year, month=prev.month)
    return at


def is_due(sub: ReportSubscription, now: datetime) -> bool:
    """Due when a scheduled time has passed since the last delivery (or since it was set up): a missed hour is
    caught up once on the next run, never repeated."""
    return bool(sub.active) and last_slot(sub, now) > (sub.last_sent_at or sub.created_at)


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{v:,}" if isinstance(v, int) else str(v)


def headline(result: dict) -> str:
    """One line for a notification: the totals of a single-row summary (with the previous period when compared),
    otherwise the row count."""
    cols, rows = result["columns"], result["rows"]
    if len(rows) == 1 and all(c["role"] == "measure" for c in cols):
        parts = [f"{c['label']}: {_fmt(v)}" for c, v in zip(cols, rows[0])]
        prev = (result.get("comparison") or {}).get("rows") or []
        if prev:
            parts = [f"{t} (vs {_fmt(b)} {result['comparison']['label']})" for t, b in zip(parts, prev[0])]
        return "; ".join(parts)
    n = result["row_count"]
    return f"{n} row{'s' if n != 1 else ''}" + (" (first rows only)" if result["truncated"] else "")


def _text(report: SavedReport, result: dict, link: str) -> str:
    lines = [report.name, "", headline(result), ""]
    cols = result["columns"]
    for row in result["rows"][:PREVIEW_ROWS]:
        lines.append(" | ".join(f"{c['label']}: {_fmt(v)}" for c, v in zip(cols, row)))
    if result["row_count"] > PREVIEW_ROWS:
        lines.append(f"... and {result['row_count'] - PREVIEW_ROWS} more in the attached CSV")
    lines += ["", f"Open in Cirra: {link}", "You get this because of a report subscription. Change or stop it from the report's Subscribe button."]
    return "\n".join(lines)


async def recipients(db: AsyncSession, sub: ReportSubscription) -> list[User]:
    """The subscriber first, then the extra recipients; inactive and partner users are dropped."""
    ids = [sub.user_id, *[uuid.UUID(str(i)) for i in sub.recipient_ids or []]]
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars()}
    return [users[i] for i in dict.fromkeys(ids) if i in users and users[i].is_active and users[i].role != "partner"]


async def deliver(db: AsyncSession, sub: ReportSubscription, now: datetime | None = None, only: uuid.UUID | None = None) -> str:
    """Send one subscription now (to everyone, or ``only`` one of its recipients). Returns a short status and records
    it on the subscription (the caller commits)."""
    now = now or datetime.now(timezone.utc)
    report = await db.get(SavedReport, sub.report_id)
    link = f"/reports/builder?id={sub.report_id}"
    sent = emailed = 0
    problems: list[str] = []
    people = await recipients(db, sub)
    if not people or people[0].id != sub.user_id:  # the subscriber left or was deactivated: the subscription lapses
        sub.active, sub.last_sent_at, sub.last_status = False, now, "Paused: the subscriber is no longer active"
        return sub.last_status
    for user in people:
        if only is not None and user.id != only:
            continue
        if not (report.owner_id == user.id or report.visibility == "shared"):
            problems.append(f"{user.full_name} can't see the report")
            continue
        p = await principal_for(db, user)
        if not p.can("reports", "read"):
            problems.append(f"{user.full_name} can't use reports")
            continue
        try:
            result = await reporting.run(db, p, {**report.definition, "limit": reporting.MAX_ROWS})
        except reporting.ReportError as e:
            problems.append(f"{user.full_name}: {e}")
            continue
        notify(db, [user.id], "report", f"{report.name}: {headline(result)}", f"Scheduled {sub.frequency} report", link)
        sent += 1
        if mailer.configured() and user.email:
            slug = "".join(ch if ch.isalnum() else "-" for ch in report.name.lower()).strip("-")[:60] or "report"
            try:
                await mailer.send(user.email, f"[Cirra] {report.name}", _text(report, result, settings.public_web_url.rstrip("/") + link),
                                  [(f"{slug}-{now.date().isoformat()}.csv", reporting.to_csv(result).encode("utf-8"), "text/csv")])
                emailed += 1
            except mailer.MailError as e:
                problems.append(f"email to {user.full_name} failed ({e})")
    status = f"Delivered to {sent}" + (f", emailed {emailed}" if mailer.configured() else " in-app (email not configured)")
    if problems:
        status += "; " + "; ".join(problems)
    sub.last_sent_at, sub.last_status = now, status[:200]
    return status


async def deliver_due(db: AsyncSession, now: datetime | None = None) -> int:
    """Hourly job: send every subscription whose scheduled time has come. Returns how many were sent."""
    now = now or datetime.now(timezone.utc)
    await reporting.refresh_custom_fields(db)
    subs = (await db.execute(select(ReportSubscription).where(ReportSubscription.active.is_(True)))).scalars().all()
    done = 0
    for sub in subs:
        if not is_due(sub, now):
            continue
        sub_id = sub.id
        try:
            await deliver(db, sub, now)
            await db.commit()
            done += 1
        except Exception:  # one broken report must not hold up the rest
            log.exception("Report subscription %s failed", sub_id)
            await db.rollback()
            failed = await db.get(ReportSubscription, sub_id)
            if failed is not None:  # mark it sent so a persistent failure isn't retried every hour
                failed.last_sent_at, failed.last_status = now, "Failed: see the server log"
                await db.commit()
    return done
