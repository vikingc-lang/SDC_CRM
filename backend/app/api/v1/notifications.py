"""Notification center: the inbox (unread, snoozed, archived), preferences per kind and channel, and the devices
that receive Web Push (services/notify.py, services/webpush.py)."""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import netguard
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import Notification, PushSubscription, User
from app.services import mailer, notify, webpush

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _out(n: Notification) -> dict:
    return {"id": n.id, "kind": n.kind, "kind_label": notify.KINDS.get(n.kind, notify.KINDS["system"])[0], "title": n.title,
            "body": n.body, "link": n.link, "priority": n.priority, "read": n.read_at is not None,
            "archived": n.archived_at is not None, "snoozed_until": n.snoozed_until, "created_at": n.created_at}


@router.get("")
async def inbox(view: Literal["inbox", "unread", "snoozed", "archived"] = "inbox", kind: str | None = None, limit: int = 50,
                before: datetime | None = None, unread_only: bool = False, db: AsyncSession = Depends(get_db),
                user: User = Depends(get_current_user)):
    now = _now()
    prefs = notify.prefs_of(user)
    muted = [k for k in notify.KINDS if not notify.wants(prefs, k, "in_app")]
    awake = or_(Notification.snoozed_until.is_(None), Notification.snoozed_until <= now)
    live = [Notification.user_id == user.id, Notification.kind.notin_(muted)] if muted else [Notification.user_id == user.id]
    view = "unread" if unread_only else view
    stmt = select(Notification).where(*live)
    if view == "archived":
        stmt = stmt.where(Notification.archived_at.is_not(None))
    elif view == "snoozed":
        stmt = stmt.where(Notification.archived_at.is_(None), Notification.snoozed_until > now)
    else:
        stmt = stmt.where(Notification.archived_at.is_(None), awake)
        if view == "unread":
            stmt = stmt.where(Notification.read_at.is_(None))
    if kind:
        stmt = stmt.where(Notification.kind == kind)
    if before:
        stmt = stmt.where(Notification.created_at < before)
    rows = (await db.execute(stmt.order_by(Notification.created_at.desc()).limit(min(max(limit, 1), 200)))).scalars().all()
    unread_by_kind = dict((await db.execute(select(Notification.kind, func.count()).where(
        *live, Notification.read_at.is_(None), Notification.archived_at.is_(None), awake).group_by(Notification.kind))).all())
    return {"unread": sum(unread_by_kind.values()), "unread_by_kind": unread_by_kind, "items": [_out(n) for n in rows],
            "kinds": [{"key": k, "label": v[0]} for k, v in notify.KINDS.items()]}


async def _mine(db: AsyncSession, user: User, ids: list[uuid.UUID] | None, *extra):
    stmt = select(Notification).where(Notification.user_id == user.id, *extra)
    if ids:
        stmt = stmt.where(Notification.id.in_(ids))
    return (await db.execute(stmt)).scalars().all()


@router.post("/read")
async def mark_read(ids: list[uuid.UUID] | None = Body(None), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    for n in await _mine(db, user, ids, Notification.read_at.is_(None)):
        n.read_at = _now()
    await db.commit()
    return {"status": "ok"}


class Ids(BaseModel):
    ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)


@router.post("/unread")
async def mark_unread(body: Ids, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    if not body.ids:
        raise HTTPException(422, "Choose the notifications to mark unread")
    for n in await _mine(db, user, body.ids):
        n.read_at = None
    await db.commit()
    return {"status": "ok"}


class Archive(BaseModel):
    ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    all_read: bool = False  # archive everything already read
    restore: bool = False


@router.post("/archive")
async def archive(body: Archive, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    if not body.ids and not body.all_read:
        raise HTTPException(422, "Choose the notifications to archive")
    extra = [Notification.read_at.is_not(None), Notification.archived_at.is_(None)] if body.all_read and not body.ids else []
    rows = await _mine(db, user, body.ids or None, *extra)
    for n in rows:
        n.archived_at = None if body.restore else _now()
        if not body.restore:
            n.read_at = n.read_at or _now()
    await db.commit()
    return {"changed": len(rows)}


class Snooze(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    until: datetime | None = None
    hours: int | None = Field(None, ge=1, le=24 * 30)


@router.post("/snooze")
async def snooze(body: Snooze, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    until = body.until or (_now() + timedelta(hours=body.hours or 3))
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    if until <= _now():
        raise HTTPException(422, "Snooze until a time in the future")
    for n in await _mine(db, user, body.ids):
        n.snoozed_until, n.read_at = until, None  # comes back unread
    await db.commit()
    return {"until": until}


# ---- preferences ---------------------------------------------------------------------------------------------------
async def _prefs_out(db: AsyncSession, user: User) -> dict:
    from app.services import connectors

    prefs = notify.prefs_of(user)
    devices = (await db.execute(select(PushSubscription).where(PushSubscription.user_id == user.id)
                                .order_by(PushSubscription.created_at))).scalars().all()
    return {**prefs, "catalog": [{"key": k, "label": v[0], "description": v[1], "high_priority": k in notify.HIGH_PRIORITY}
                                 for k, v in notify.KINDS.items()],
            "channels": {"in_app": True, "email": mailer.configured(), "push": True, "chat": await connectors.chat_available(db)},
            "devices": [{"id": d.id, "user_agent": d.user_agent, "created_at": d.created_at, "last_used_at": d.last_used_at} for d in devices],
            "timezone": user.timezone or "UTC"}


@router.get("/preferences")
async def get_preferences(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await _prefs_out(db, user)


@router.put("/preferences")
async def put_preferences(body: dict, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        cleaned = notify.clean_prefs(body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    user.notification_prefs = {**(user.notification_prefs or {}), **cleaned}
    await db.commit()
    return await _prefs_out(db, user)


# ---- Web Push devices ----------------------------------------------------------------------------------------------
@router.get("/push/key")
async def push_key(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    public, _key = await webpush.vapid_keys(db)
    await db.commit()
    return {"public_key": public}


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=20, max_length=200)
    auth: str = Field(min_length=8, max_length=64)


class PushSubscribe(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)
    keys: PushKeys
    user_agent: str | None = Field(None, max_length=200)


@router.post("/push/subscribe", status_code=201)
async def push_subscribe(body: PushSubscribe, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        endpoint = webpush.check_endpoint(body.endpoint)
        if len(webpush.b64u_decode(body.keys.p256dh)) != 65 or len(webpush.b64u_decode(body.keys.auth)) != 16:
            raise ValueError
    except netguard.BlockedDestination as e:
        raise HTTPException(422, str(e))
    except ValueError:
        raise HTTPException(422, "Those push keys aren't valid")
    sub = (await db.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint))).scalars().first()
    if sub is None:
        sub = PushSubscription(endpoint=endpoint, user_id=user.id, p256dh=body.keys.p256dh, auth=body.keys.auth)
        db.add(sub)
    # a device signed in as someone else now belongs to this person
    sub.user_id, sub.p256dh, sub.auth, sub.user_agent, sub.failures = user.id, body.keys.p256dh, body.keys.auth, body.user_agent, 0
    await db.commit()
    return {"id": sub.id}


class PushUnsubscribe(BaseModel):
    endpoint: str | None = None
    id: uuid.UUID | None = None


@router.post("/push/unsubscribe")
async def push_unsubscribe(body: PushUnsubscribe, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    cond = PushSubscription.endpoint == body.endpoint if body.endpoint else PushSubscription.id == body.id
    n = (await db.execute(delete(PushSubscription).where(PushSubscription.user_id == user.id, cond))).rowcount
    await db.commit()
    return {"removed": n}


@router.post("/test")
async def test_notification(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Send yourself a test notification on every channel available to you (email, this device's push, Slack)."""
    notify.notify(db, [user.id], "system", "Test notification", "Notifications reach you here.", "/notifications")
    await db.commit()
    stats = await notify.deliver_pending(db, user_id=user.id, every_channel=True)
    return {"status": "sent", "delivery": stats}
