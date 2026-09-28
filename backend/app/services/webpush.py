"""Web Push (RFC 8030) to browsers and the installed app, with no third-party service in between.

Messages are encrypted for the device (RFC 8291, ``aes128gcm``) and signed with this server's VAPID key (RFC 8292),
so the browser vendor's push service only relays ciphertext. The VAPID key pair comes from VAPID_PUBLIC_KEY /
VAPID_PRIVATE_KEY, or is generated once and kept in ``app_settings`` with the private half encrypted.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import netguard
from app.core.config import settings
from app.models import AppSetting, PushSubscription
from app.services.mail import decrypt_secret, encrypt_secret

transport: httpx.AsyncBaseTransport | None = None  # tests swap in an httpx transport
RECORD_SIZE = 4096


class PushError(RuntimeError):
    pass


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _private_from_raw(raw: bytes) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


async def vapid_keys(db: AsyncSession) -> tuple[str, ec.EllipticCurvePrivateKey]:
    """(public key for the browser, private key for signing)."""
    if settings.vapid_public_key and settings.vapid_private_key:
        return settings.vapid_public_key, _private_from_raw(b64u_decode(settings.vapid_private_key))
    row = await db.get(AppSetting, "vapid")
    if row is None:
        key = ec.generate_private_key(ec.SECP256R1())
        raw = key.private_numbers().private_value.to_bytes(32, "big")
        row = AppSetting(key="vapid", value={"public": b64u(_public_bytes(key.public_key())), "private": encrypt_secret(b64u(raw))})
        db.add(row)
        await db.flush()
    return row.value["public"], _private_from_raw(b64u_decode(decrypt_secret(row.value["private"])))


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None,
            server_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """RFC 8291 message encryption: one aes128gcm record for the subscription's keys."""
    ua_public = b64u_decode(p256dh)
    auth_secret = b64u_decode(auth)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(server_key.public_key())
    shared = server_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = HKDF(hashes.SHA256(), 32, auth_secret, b"WebPush: info\x00" + ua_public + as_public).derive(shared)
    salt = salt or os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    if len(payload) > RECORD_SIZE - 17 - 86:
        raise PushError("Push payload too large")
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # 0x02: last (and only) record
    return salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public + body


def vapid_header(endpoint: str, public: str, private: ec.EllipticCurvePrivateKey) -> str:
    u = urlparse(endpoint)
    token = jwt.encode({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": settings.vapid_subject},
                       private, algorithm="ES256")
    return f"vapid t={token}, k={public}"


def check_endpoint(endpoint: str) -> str:
    """Push endpoints belong to browser vendors' push services: public HTTPS only (no internal hosts)."""
    endpoint = (endpoint or "").strip()
    if not endpoint.startswith("https://") or len(endpoint) > 2000:
        raise netguard.BlockedDestination("A push endpoint must be an https:// URL")
    netguard.check_url(endpoint, "The push endpoint")
    return endpoint


async def send(db: AsyncSession, sub: PushSubscription, message: dict, *, urgency: str = "normal", ttl: int = 86400) -> str:
    """Deliver one message; returns 'sent', 'gone' (subscription deleted) or 'failed'."""
    public, private = await vapid_keys(db)
    try:
        await asyncio.to_thread(check_endpoint, sub.endpoint)
        body = encrypt(json.dumps(message, separators=(",", ":")).encode(), sub.p256dh, sub.auth)
    except (netguard.BlockedDestination, PushError, ValueError):
        sub.failures = (sub.failures or 0) + 1
        return "failed"
    headers = {"Authorization": vapid_header(sub.endpoint, public, private), "Content-Encoding": "aes128gcm",
               "Content-Type": "application/octet-stream", "TTL": str(ttl), "Urgency": urgency}
    try:
        async with httpx.AsyncClient(timeout=15, transport=transport) as client:
            r = await client.post(sub.endpoint, content=body, headers=headers)
    except httpx.HTTPError:
        sub.failures = (sub.failures or 0) + 1
        return "failed"
    if r.status_code in (404, 410):  # the browser unsubscribed or the app was uninstalled
        await db.delete(sub)
        return "gone"
    if r.status_code >= 400:
        sub.failures = (sub.failures or 0) + 1
        if sub.failures >= 10:
            await db.delete(sub)
            return "gone"
        return "failed"
    sub.failures, sub.last_used_at = 0, datetime.now(timezone.utc)
    return "sent"
