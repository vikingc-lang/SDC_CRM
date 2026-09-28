"""Notifications (in the app, by email, by Web Push and in chat) and the integration outbox (pillars 5, 8).

``notify()`` only writes the in-app notification, inside the caller's transaction, so a rolled-back change notifies
no one. Delivery beyond the app runs afterwards in the ``notifications`` job (every minute): for each new
notification it reads the person's preferences (per kind and channel, instant email or a daily digest, quiet
hours) and sends the email, the push to each of their devices, and a Slack direct message when Slack is connected.
High-priority kinds (SLA breaches, signatures) are delivered during quiet hours too.
"""
from __future__ import annotations

import copy
import logging
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tenancy
from app.core.config import settings
from app.models import IntegrationEvent, Notification, PushSubscription, User

log = logging.getLogger(__name__)

# Which neighbouring SDC Solutions modules care about which CRM events.
EVENT_TARGETS = {
    "account.updated": ["promo", "yield", "deduct", "nexora"],
    "deal.closed_won": ["promo", "yield", "nexora"],
    "deal.closed_lost": ["nexora"],
    "quote.approved": ["yield"],
    "contract.created": ["deduct", "yield", "nexora"],
    "contract.renewal_opened": ["yield"],
    "invoice.overdue": ["deduct"],
    "lead.created": ["promo", "nexora"],
    "lead.converted": ["promo", "nexora"],
    "order.created": ["deduct", "nexora"],
    "order.acknowledged": ["deduct", "yield", "nexora"],
    # published for webhook subscribers only (services/developer.py)
    "account.created": [], "contact.created": [], "deal.created": [], "deal.stage_changed": [],
    "case.created": [], "case.resolved": [], "campaign.launched": [], "campaign.member_responded": [],
    "segment.entered": [],
}

CHANNELS = ("in_app", "email", "push", "chat")
# kind -> (label, description, default channels)
KINDS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "assignment": ("Assigned to you", "A lead, account or record is now yours", ("in_app", "email", "push")),
    "task": ("Tasks", "Tasks assigned or delegated to you", ("in_app", "push")),
    "lead": ("Leads", "New and routed leads", ("in_app", "push")),
    "case": ("Cases", "Cases routed to you and customer replies", ("in_app", "push")),
    "sla": ("SLA warnings", "Cases and tasks about to breach or past their SLA", ("in_app", "email", "push")),
    "risk": ("Deal risk", "Aiden's risk and slippage alerts on your deals", ("in_app", "push")),
    "ai": ("Aiden suggestions", "Suggestions waiting for your approval and AI budget alerts", ("in_app",)),
    "renewal": ("Renewals", "Renewal deals opened for your accounts", ("in_app", "email")),
    "handoff": ("Handoffs", "Won deals handed over to customer success", ("in_app", "email")),
    "signature": ("E-signatures", "Documents signed, declined or waiting for your countersignature", ("in_app", "email", "push")),
    "partner": ("Partners", "Deal registrations and partner activity", ("in_app", "email")),
    "workflow": ("Automation", "Notifications sent by workflow rules", ("in_app",)),
    "report": ("Reports", "Scheduled report deliveries", ("in_app",)),
    "campaign": ("Campaigns", "Campaign sends finished or failed", ("in_app",)),
    "system": ("Other", "Everything else, including test notifications", ("in_app",)),
}
HIGH_PRIORITY = {"sla", "signature"}
DEFAULT_PREFS = {"email_mode": "instant", "digest_hour": 8, "quiet": {"enabled": False, "start": "20:00", "end": "07:00"}, "kinds": {}}


def notify(db: AsyncSession, user_ids, kind: str, title: str, body: str | None = None, link: str | None = None,
           priority: str | None = None) -> None:
    for uid in {u for u in user_ids if u}:
        db.add(Notification(user_id=uid, kind=kind, title=title[:300], body=body, link=link,
                            priority=priority or ("high" if kind in HIGH_PRIORITY else "normal")))


def emit(db: AsyncSession, event_type: str, entity_type: str, entity_id: uuid.UUID | None, payload: dict) -> None:
    db.add(IntegrationEvent(event_type=event_type, entity_type=entity_type, entity_id=entity_id,
                            payload=payload, targets=EVENT_TARGETS.get(event_type, ["nexora"])))


# ---- preferences ---------------------------------------------------------------------------------------------------
def prefs_of(user: User) -> dict:
    out = copy.deepcopy(DEFAULT_PREFS)
    stored = user.notification_prefs or {}
    for k in ("email_mode", "digest_hour"):
        if k in stored:
            out[k] = stored[k]
    out["quiet"].update(stored.get("quiet") or {})
    for kind, (_, _, defaults) in KINDS.items():
        chosen = (stored.get("kinds") or {}).get(kind) or {}
        out["kinds"][kind] = {c: bool(chosen.get(c, c in defaults)) for c in CHANNELS}
    return out


def wants(prefs: dict, kind: str, channel: str) -> bool:
    return prefs["kinds"].get(kind if kind in KINDS else "system", {}).get(channel, False)


def clean_prefs(body: dict) -> dict:
    """Validate a preferences update from Settings → Notifications."""
    out: dict = {}
    if body.get("email_mode") in ("instant", "digest", "off"):
        out["email_mode"] = body["email_mode"]
    if isinstance(body.get("digest_hour"), int) and 0 <= body["digest_hour"] <= 23:
        out["digest_hour"] = body["digest_hour"]
    q = body.get("quiet")
    if isinstance(q, dict):
        quiet = {"enabled": bool(q.get("enabled"))}
        for k in ("start", "end"):
            try:
                quiet[k] = time.fromisoformat(str(q.get(k, DEFAULT_PREFS["quiet"][k]))).strftime("%H:%M")
            except ValueError:
                raise ValueError(f"Quiet hours {k} must be a time like 20:00")
        out["quiet"] = quiet
    kinds = {}
    for kind, chans in (body.get("kinds") or {}).items():
        if kind in KINDS and isinstance(chans, dict):
            kinds[kind] = {c: bool(v) for c, v in chans.items() if c in CHANNELS}
    out["kinds"] = kinds
    return out


def _zone(user: User) -> ZoneInfo:
    try:
        return ZoneInfo(user.timezone or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def in_quiet_hours(prefs: dict, user: User, now: datetime) -> bool:
    q = prefs["quiet"]
    if not q.get("enabled"):
        return False
    local = now.astimezone(_zone(user)).time()
    start, end = time.fromisoformat(q["start"]), time.fromisoformat(q["end"])
    return (start <= local or local < end) if start > end else (start <= local < end)


# ---- delivery ------------------------------------------------------------------------------------------------------
def _abs(link: str | None) -> str:
    return f"{tenancy.web_url()}{link or '/notifications'}"


async def _email(n: Notification, user: User) -> str:
    from app.services import mailer

    text = f"{n.title}\n\n{n.body or ''}\n\nOpen in Cirra: {_abs(n.link)}\n\nChange what Cirra emails you: {_abs('/settings?tab=notifications')}\n"
    try:
        await mailer.send(user.email, f"[Cirra] {n.title}", text)
        return "sent"
    except mailer.MailError:
        return "failed"


async def _push(db: AsyncSession, n: Notification, subs: list[PushSubscription]) -> str:
    from app.services import webpush

    results = [await webpush.send(db, s, {"title": n.title, "body": (n.body or "")[:300], "url": n.link or "/notifications",
                                          "tag": str(n.id), "kind": n.kind}, urgency="high" if n.priority == "high" else "normal")
               for s in subs]
    return "sent" if "sent" in results else ("failed" if results else "no_device")


async def _chat(db: AsyncSession, n: Notification, user: User) -> str:
    from app.services import connectors

    return await connectors.direct_message(db, user.email, f"*{n.title}*\n{n.body or ''}\n<{_abs(n.link)}|Open in Cirra>")


async def deliver_pending(db: AsyncSession, now: datetime | None = None, limit: int = 500, *, user_id: uuid.UUID | None = None,
                          every_channel: bool = False) -> dict:
    """Deliver new notifications beyond the app (only ``user_id``'s when given; ``every_channel`` ignores the
    preferences and quiet hours, for the test notification). Returns counts per channel outcome."""
    from app.services import mailer

    now = now or datetime.now(timezone.utc)
    stats: dict[str, int] = {}
    stmt = select(Notification, User).join(User, User.id == Notification.user_id).where(Notification.delivered_at.is_(None))
    if user_id:
        stmt = stmt.where(Notification.user_id == user_id)
    rows = (await db.execute(stmt.order_by(Notification.created_at).limit(limit))).all()
    subs_by_user: dict[uuid.UUID, list[PushSubscription]] = {}
    if rows:
        for s in (await db.execute(select(PushSubscription).where(PushSubscription.user_id.in_({u.id for _, u in rows})))).scalars():
            subs_by_user.setdefault(s.user_id, []).append(s)
    for n, user in rows:
        prefs = prefs_of(user)
        if every_channel:
            prefs = {**prefs, "email_mode": "instant", "quiet": {"enabled": False},
                     "kinds": {k: {c: True for c in CHANNELS} for k in KINDS}}
        if not user.is_active:
            n.delivered_at = now
            continue
        if n.priority != "high" and in_quiet_hours(prefs, user, now):
            continue  # picked up again when quiet hours end
        delivery = dict(n.delivery or {})
        if wants(prefs, n.kind, "email") and prefs["email_mode"] != "off" and mailer.configured():
            delivery["email"] = "digest" if prefs["email_mode"] == "digest" and n.priority != "high" else await _email(n, user)
        if wants(prefs, n.kind, "push") and subs_by_user.get(user.id):
            delivery["push"] = await _push(db, n, subs_by_user[user.id])
        if wants(prefs, n.kind, "chat"):
            delivery["chat"] = await _chat(db, n, user)
        for ch, outcome in delivery.items():
            stats[f"{ch}:{outcome}"] = stats.get(f"{ch}:{outcome}", 0) + 1
        n.delivery, n.delivered_at = delivery, now
    await db.commit()
    return stats


async def send_digests(db: AsyncSession, now: datetime | None = None) -> int:
    """One email per person at their digest hour (their time zone) listing unread notifications held for it."""
    from app.services import mailer

    if not mailer.configured():
        return 0
    now = now or datetime.now(timezone.utc)
    rows = (await db.execute(select(Notification, User).join(User, User.id == Notification.user_id)
                             .where(Notification.delivery["email"].as_string() == "digest"))).all()
    by_user: dict[uuid.UUID, tuple[User, list[Notification]]] = {}
    for n, user in rows:
        by_user.setdefault(user.id, (user, []))[1].append(n)
    sent = 0
    for user, items in by_user.values():
        prefs = prefs_of(user)
        if now.astimezone(_zone(user)).hour != prefs["digest_hour"] and now - min(i.created_at for i in items) < timedelta(hours=26):
            continue
        unread = [i for i in items if i.read_at is None and i.archived_at is None]
        if unread:
            lines = "\n".join(f"- {i.title}{f': {i.body}' if i.body else ''}\n  {_abs(i.link)}" for i in unread[:50])
            more = f"\n…and {len(unread) - 50} more." if len(unread) > 50 else ""
            try:
                await mailer.send(user.email, f"[Cirra] Your daily summary: {len(unread)} notification{'s' if len(unread) != 1 else ''}",
                                  f"Here is what happened since your last summary.\n\n{lines}{more}\n\nAll notifications: {_abs('/notifications')}\n")
                sent += 1
            except mailer.MailError:
                continue
        for i in items:
            i.delivery = {**(i.delivery or {}), "email": "digested" if i in unread else "skipped_read"}
    await db.commit()
    return sent
