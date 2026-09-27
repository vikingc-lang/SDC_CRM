"""Developer platform: API keys and signed webhooks over the integration outbox.

API keys
    ``ck_`` + 43 random characters, shown once; only the SHA-256 hash is stored. A key acts as its user,
    so role permissions and row-level scope apply unchanged. Read-only keys may only use GET/HEAD.
    Send it as ``Authorization: Bearer ck_...`` or ``X-API-Key: ck_...``.
Webhooks
    Every minute, new outbox events are fanned out to active subscriptions whose patterns match
    (``*``, ``deal.*``, ``lead.created``), one delivery per (subscription, event). Each POST carries
        X-Cirra-Event: deal.closed_won
        X-Cirra-Delivery: <delivery id>
        X-Cirra-Signature: t=<unix time>,v1=<hex HMAC-SHA256 of "<t>.<raw body>" with the endpoint secret>
    Any 2xx is success. Failures retry after 1 min, 5 min, 30 min, 2 h and 12 h, then the delivery is
    dead. Twenty failures in a row switch the subscription off until an admin turns it back on.
"""
from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiKey, IntegrationEvent, User, WebhookDelivery, WebhookSubscription
from app.services.mail import decrypt_secret, encrypt_secret

KEY_PREFIX = "ck_"
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2), timedelta(hours=12))
MAX_ATTEMPTS = len(BACKOFF) + 1
DISABLE_AFTER = 20
TIMEOUT = 10.0

# Published events (the outbox also carries custom events from workflow rules)
EVENT_CATALOGUE = {
    "account.created": "An account was created", "account.updated": "Customer master changed (ERP sync)",
    "contact.created": "A contact was created",
    "lead.created": "A lead was captured", "lead.converted": "A lead was converted",
    "deal.created": "An opportunity was created", "deal.stage_changed": "An opportunity moved stage",
    "deal.closed_won": "An opportunity was won", "deal.closed_lost": "An opportunity was lost",
    "quote.approved": "A quote was approved", "contract.created": "A contract was created",
    "contract.renewal_opened": "A renewal opportunity was opened", "order.created": "A sales order was raised",
    "order.acknowledged": "The ERP acknowledged an order", "invoice.overdue": "An invoice became overdue",
    "onboarding.provisioned": "An onboarding workspace was provisioned",
    "case.created": "A support case was opened", "case.resolved": "A support case was resolved",
    "campaign.launched": "A campaign went live", "campaign.member_responded": "A campaign member responded",
}

# Some HTTP client for tests to swap (an httpx transport).
transport: httpx.AsyncBaseTransport | None = None


class DeveloperError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- API keys --------------------------------------------------------------------------------------

def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_key() -> tuple[str, str, str]:
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, raw[:11], hash_key(raw)


async def authenticate(db: AsyncSession, raw: str) -> tuple[User, ApiKey] | None:
    key = (await db.execute(select(ApiKey).where(ApiKey.key_hash == hash_key(raw)))).scalar_one_or_none()
    now = _now()
    if key is None or key.revoked_at is not None or (key.expires_at and key.expires_at <= now):
        return None
    user = await db.get(User, key.user_id)
    if user is None or not user.is_active or user.role == "partner":
        return None
    if key.last_used_at is None or now - key.last_used_at > timedelta(minutes=1):
        await db.execute(update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=now))
        await db.commit()
    return user, key


def key_out(k: ApiKey, names: dict) -> dict:
    now = _now()
    state = "revoked" if k.revoked_at else "expired" if k.expires_at and k.expires_at <= now else "active"
    return {"id": k.id, "name": k.name, "prefix": k.prefix, "user_id": k.user_id, "user": names.get(k.user_id), "read_only": k.read_only,
            "expires_at": k.expires_at, "last_used_at": k.last_used_at, "revoked_at": k.revoked_at, "created_at": k.created_at,
            "created_by": names.get(k.created_by), "state": state}


# ---- webhook subscriptions -------------------------------------------------------------------------

def clean_url(url: str) -> str:
    url = (url or "").strip()
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise DeveloperError("The endpoint must be an http(s) URL")
    return url


def clean_patterns(patterns: list[str] | None) -> list[str]:
    out = []
    for p in patterns or ["*"]:
        p = (p or "").strip().lower()
        if not p:
            continue
        if not all(ch.isalnum() or ch in "._*" for ch in p):
            raise DeveloperError(f"'{p}' isn't an event pattern (use names like deal.closed_won, deal.* or *)")
        out.append(p)
    if not out:
        raise DeveloperError("Choose at least one event")
    return list(dict.fromkeys(out))


def wants(sub: WebhookSubscription, event_type: str) -> bool:
    return any(fnmatch.fnmatchcase(event_type, p) for p in sub.event_types or ["*"])


def new_secret() -> tuple[str, str]:
    raw = "whsec_" + secrets.token_urlsafe(24)
    return raw, encrypt_secret(raw)


async def latest_event_id(db: AsyncSession) -> int:
    return int((await db.execute(select(func.max(IntegrationEvent.id)))).scalar() or 0)


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def verify(secret: str, header: str, body: bytes, tolerance: int = 300) -> bool:
    """What a receiver does (also used in tests)."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
    except (ValueError, KeyError):
        return False
    return abs(time.time() - ts) <= tolerance and hmac.compare_digest(sign(secret, ts, body), f"t={ts},v1={parts.get('v1', '')}")


def sub_out(s: WebhookSubscription, stats: dict | None = None) -> dict:
    return {"id": s.id, "name": s.name, "url": s.url, "event_types": s.event_types, "active": s.active,
            "disabled_reason": s.disabled_reason, "consecutive_failures": s.consecutive_failures,
            "last_success_at": s.last_success_at, "last_failure_at": s.last_failure_at, "created_at": s.created_at,
            "stats": stats or {}}


def delivery_out(d: WebhookDelivery) -> dict:
    return {"id": d.id, "event_id": d.event_id, "event_type": d.event_type, "status": d.status, "attempts": d.attempts,
            "next_attempt_at": d.next_attempt_at if d.status in ("pending", "failed") else None, "response_code": d.response_code,
            "error": d.error, "duration_ms": d.duration_ms, "created_at": d.created_at, "delivered_at": d.delivered_at}


# ---- fan-out and delivery --------------------------------------------------------------------------

async def fan_out(db: AsyncSession, batch: int = 1000) -> int:
    created = 0
    for sub in (await db.execute(select(WebhookSubscription).where(WebhookSubscription.active.is_(True)))).scalars().all():
        events = (await db.execute(select(IntegrationEvent.id, IntegrationEvent.event_type).where(IntegrationEvent.id > sub.cursor_event_id)
                                   .order_by(IntegrationEvent.id).limit(batch))).all()
        for ev_id, ev_type in events:
            if wants(sub, ev_type):
                db.add(WebhookDelivery(subscription_id=sub.id, event_id=ev_id, event_type=ev_type, next_attempt_at=_now()))
                created += 1
        if events:
            sub.cursor_event_id = events[-1][0]
    await db.flush()
    return created


def _payload(d: WebhookDelivery, ev: IntegrationEvent | None) -> bytes:
    if ev is None:  # test ping
        body = {"id": None, "type": d.event_type, "created_at": d.created_at.isoformat() if d.created_at else _now().isoformat(),
                "data": {"message": "Webhook test from Cirra"}}
    else:
        body = {"id": ev.id, "type": ev.event_type, "created_at": ev.created_at.isoformat(), "entity": ev.entity_type,
                "entity_id": str(ev.entity_id) if ev.entity_id else None, "data": ev.payload}
    return json.dumps(body, separators=(",", ":"), default=str).encode()


async def attempt(db: AsyncSession, d: WebhookDelivery, sub: WebhookSubscription, client: httpx.AsyncClient) -> bool:
    ev = await db.get(IntegrationEvent, d.event_id) if d.event_id else None
    body = _payload(d, ev)
    ts = int(time.time())
    headers = {"Content-Type": "application/json", "User-Agent": "Cirra-Webhooks/1.0", "X-Cirra-Event": d.event_type,
               "X-Cirra-Delivery": str(d.id), "X-Cirra-Signature": sign(decrypt_secret(sub.secret_enc), ts, body)}
    started = time.monotonic()
    d.attempts += 1
    try:
        r = await client.post(sub.url, content=body, headers=headers)
        d.response_code, ok = r.status_code, 200 <= r.status_code < 300
        d.error = None if ok else (r.text or "")[:500] or f"HTTP {r.status_code}"
    except httpx.HTTPError as e:
        d.response_code, ok, d.error = None, False, (str(e) or e.__class__.__name__)[:500]
    d.duration_ms = int((time.monotonic() - started) * 1000)
    now = _now()
    if ok:
        d.status, d.delivered_at = "success", now
        sub.consecutive_failures, sub.last_success_at = 0, now
    else:
        d.status = "dead" if d.attempts >= MAX_ATTEMPTS else "failed"
        if d.status == "failed":
            d.next_attempt_at = now + BACKOFF[d.attempts - 1]
        sub.consecutive_failures += 1
        sub.last_failure_at = now
        if sub.consecutive_failures >= DISABLE_AFTER and sub.active:
            sub.active, sub.disabled_reason = False, f"Switched off after {DISABLE_AFTER} failed deliveries in a row"
    return ok


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, transport=transport, follow_redirects=False)


async def deliver_due(db: AsyncSession, limit: int = 200) -> dict:
    now = _now()
    rows = (await db.execute(select(WebhookDelivery, WebhookSubscription).join(WebhookSubscription, WebhookSubscription.id == WebhookDelivery.subscription_id)
                             .where(WebhookSubscription.active.is_(True), WebhookDelivery.status.in_(("pending", "failed")),
                                    WebhookDelivery.next_attempt_at <= now)
                             .order_by(WebhookDelivery.id).limit(limit))).all()
    ok = 0
    async with _client() as client:
        for d, sub in rows:
            if sub.active:  # may have been switched off earlier in this batch
                ok += await attempt(db, d, sub, client)
    await db.flush()
    return {"attempted": len(rows), "succeeded": ok}


async def run(db: AsyncSession) -> dict:
    created = await fan_out(db)
    out = await deliver_due(db)
    await db.commit()
    return {"queued": created, **out}


async def ping(db: AsyncSession, sub: WebhookSubscription) -> WebhookDelivery:
    d = WebhookDelivery(subscription_id=sub.id, event_id=None, event_type="ping", created_at=_now(), next_attempt_at=_now())
    db.add(d)
    await db.flush()
    async with _client() as client:
        await attempt(db, d, sub, client)
    if d.status != "success":
        d.status = "dead"  # a test isn't retried
    await db.flush()
    return d


async def retry(db: AsyncSession, d: WebhookDelivery, sub: WebhookSubscription) -> WebhookDelivery:
    if d.status == "success":
        raise DeveloperError("This delivery already succeeded")
    if d.event_id is None:
        raise DeveloperError("Send a new test instead")
    d.attempts = min(d.attempts, MAX_ATTEMPTS - 1)  # one more try even when dead
    async with _client() as client:
        await attempt(db, d, sub, client)
    await db.flush()
    return d


async def stats(db: AsyncSession, sub_ids: list) -> dict:
    since = _now() - timedelta(days=1)
    rows = (await db.execute(select(WebhookDelivery.subscription_id, WebhookDelivery.status, func.count())
                             .where(WebhookDelivery.subscription_id.in_(sub_ids), or_(WebhookDelivery.created_at >= since,
                                                                                       WebhookDelivery.status.in_(("pending", "failed"))))
                             .group_by(WebhookDelivery.subscription_id, WebhookDelivery.status))).all() if sub_ids else []
    out: dict = {}
    for sid, status, n in rows:
        out.setdefault(sid, {})[status] = n
    return out
