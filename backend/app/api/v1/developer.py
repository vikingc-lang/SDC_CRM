"""Developer platform: API keys, webhook subscriptions, deliveries and the event catalogue."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.database import get_db
from app.core.rbac import Principal, authorize
from app.models import ApiKey, User, WebhookDelivery, WebhookSubscription
from app.services import developer as svc

router = APIRouter(prefix="/developer", tags=["developer"])


def _people_only(request: Request) -> None:
    if getattr(request.state, "api_key_id", None):
        raise HTTPException(403, "API keys can't manage API keys or webhooks")


def admin(action: str):
    async def _dep(request: Request, p: Principal = Depends(authorize("admin", action))) -> Principal:
        _people_only(request)
        return p
    return _dep


class KeyIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    user_id: uuid.UUID
    read_only: bool = False
    expires_at: datetime | None = None


class WebhookIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    url: str = Field(max_length=500)
    event_types: list[str] = ["*"]
    active: bool = True


@router.get("/events")
async def event_catalogue(_: Principal = Depends(admin("read"))):
    return [{"type": k, "description": v} for k, v in svc.EVENT_CATALOGUE.items()]


# ---- API keys --------------------------------------------------------------------------------------

@router.get("/api-keys")
async def list_keys(db: AsyncSession = Depends(get_db), _: Principal = Depends(admin("read"))):
    names = dict((await db.execute(select(User.id, User.full_name))).all())
    keys = (await db.execute(select(ApiKey).order_by(ApiKey.revoked_at.is_not(None), ApiKey.created_at.desc()))).scalars().all()
    return [svc.key_out(k, names) for k in keys]


@router.post("/api-keys", status_code=201)
async def create_key(body: KeyIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(admin("update"))):
    user = await db.get(User, body.user_id)
    if user is None or not user.is_active:
        raise HTTPException(422, "The key must act as an active user")
    if user.role == "partner":
        raise HTTPException(422, "Partner portal users can't have API keys")
    if body.expires_at and body.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(422, "The expiry date is in the past")
    raw, prefix, digest = svc.new_key()
    key = ApiKey(name=body.name.strip(), prefix=prefix, key_hash=digest, user_id=user.id, read_only=body.read_only,
                 expires_at=body.expires_at, created_by=p.id)
    db.add(key)
    await db.flush()
    log_action(db, "api_key_created", "api_keys", key.id, f"{key.name} as {user.email}{' (read-only)' if key.read_only else ''}")
    await db.commit()
    return {"id": key.id, "key": raw, "prefix": prefix}


@router.post("/api-keys/{key_id}/revoke")
async def revoke_key(key_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(admin("update"))):
    key = await db.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(404, "API key not found")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        log_action(db, "api_key_revoked", "api_keys", key.id, key.name)
        await db.commit()
    return {"status": "revoked"}


# ---- webhooks --------------------------------------------------------------------------------------

@router.get("/webhooks")
async def list_webhooks(db: AsyncSession = Depends(get_db), _: Principal = Depends(admin("read"))):
    subs = (await db.execute(select(WebhookSubscription).order_by(WebhookSubscription.created_at))).scalars().all()
    stats = await svc.stats(db, [s.id for s in subs])
    return [svc.sub_out(s, stats.get(s.id)) for s in subs]


def _clean(body: WebhookIn) -> tuple[str, list[str]]:
    try:
        return svc.clean_url(body.url), svc.clean_patterns(body.event_types)
    except svc.DeveloperError as e:
        raise HTTPException(422, str(e))


@router.post("/webhooks", status_code=201)
async def create_webhook(body: WebhookIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(admin("update"))):
    url, patterns = _clean(body)
    raw, enc = svc.new_secret()
    sub = WebhookSubscription(name=body.name.strip(), url=url, event_types=patterns, secret_enc=enc, active=body.active,
                              cursor_event_id=await svc.latest_event_id(db), created_by=p.id)
    db.add(sub)
    await db.flush()
    log_action(db, "webhook_created", "webhook_subscriptions", sub.id, f"{sub.name} -> {sub.url}")
    await db.commit()
    return {"id": sub.id, "secret": raw}


async def _sub(db: AsyncSession, sub_id: uuid.UUID) -> WebhookSubscription:
    sub = await db.get(WebhookSubscription, sub_id)
    if sub is None:
        raise HTTPException(404, "Webhook not found")
    return sub


@router.put("/webhooks/{sub_id}")
async def update_webhook(sub_id: uuid.UUID, body: WebhookIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(admin("update"))):
    sub = await _sub(db, sub_id)
    url, patterns = _clean(body)
    if body.active and not sub.active:  # re-enabling: start fresh, don't replay the backlog
        sub.consecutive_failures, sub.disabled_reason = 0, None
        sub.cursor_event_id = await svc.latest_event_id(db)
    sub.name, sub.url, sub.event_types, sub.active = body.name.strip(), url, patterns, body.active
    await db.commit()
    return svc.sub_out(sub)


@router.post("/webhooks/{sub_id}/rotate-secret")
async def rotate_secret(sub_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(admin("update"))):
    sub = await _sub(db, sub_id)
    raw, sub.secret_enc = svc.new_secret()
    log_action(db, "webhook_secret_rotated", "webhook_subscriptions", sub.id, sub.name)
    await db.commit()
    return {"secret": raw}


@router.delete("/webhooks/{sub_id}", status_code=204)
async def delete_webhook(sub_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(admin("update"))):
    sub = await db.get(WebhookSubscription, sub_id)
    if sub:
        await db.delete(sub)
        await db.commit()


@router.post("/webhooks/{sub_id}/test")
async def test_webhook(sub_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(admin("update"))):
    d = await svc.ping(db, await _sub(db, sub_id))
    await db.commit()
    return svc.delivery_out(d)


@router.get("/webhooks/{sub_id}/deliveries")
async def deliveries(sub_id: uuid.UUID, status: str | None = None, limit: int = 50, db: AsyncSession = Depends(get_db),
                     _: Principal = Depends(admin("read"))):
    await _sub(db, sub_id)
    stmt = select(WebhookDelivery).where(WebhookDelivery.subscription_id == sub_id).order_by(WebhookDelivery.id.desc()).limit(min(max(limit, 1), 200))
    if status:
        stmt = stmt.where(WebhookDelivery.status == status)
    return [svc.delivery_out(d) for d in (await db.execute(stmt)).scalars()]


@router.post("/deliveries/{delivery_id}/retry")
async def retry(delivery_id: int, db: AsyncSession = Depends(get_db), _: Principal = Depends(admin("update"))):
    d = await db.get(WebhookDelivery, delivery_id)
    if d is None:
        raise HTTPException(404, "Delivery not found")
    try:
        await svc.retry(db, d, await db.get(WebhookSubscription, d.subscription_id))
    except svc.DeveloperError as e:
        raise HTTPException(422, str(e))
    await db.commit()
    return svc.delivery_out(d)


@router.post("/webhooks/run")
async def run_now(db: AsyncSession = Depends(get_db), _: Principal = Depends(admin("update"))):
    """Fan out new events and attempt due deliveries now (the worker does this every minute)."""
    return await svc.run(db)
