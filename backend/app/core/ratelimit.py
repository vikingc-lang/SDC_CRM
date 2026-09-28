"""API rate limiting: fixed one-minute windows counted in Redis, so every API replica shares one budget.

Each request is charged to one bucket, by path, and to one identity:

* ``auth``: sign-in and two-factor endpoints, per client IP (complements the per-account lockout in
  api/v1/auth.py, which a password spray across many accounts wouldn't trip).
* ``public``: unauthenticated public endpoints (lead intake forms, e-signature, CSAT, unsubscribe, inbound email),
  per client IP.
* ``tracking``: email open pixels and click-throughs, per client IP, with a generous budget.
* ``api``: everything else, per API key (``ck_…``), else per signed-in session token, else per client IP.
  API keys have their own, usually lower, budget.

Over the limit the API answers 429 with Retry-After; every limited response carries X-RateLimit-Limit and
X-RateLimit-Remaining. If Redis is unreachable requests are let through (and a warning is logged once a
minute): an outage of the limiter must not take the CRM down. Health checks are never limited.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

from app.core import tenancy
from app.core.config import settings

log = logging.getLogger(__name__)
WINDOW = 60
EXEMPT = ("/health", "/docs", "/openapi.json", "/redoc")
_redis = None
_last_warning = 0.0


def _client():
    global _redis
    if _redis is None:
        import redis.asyncio as aioredis

        _redis = aioredis.from_url(settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
    return _redis


def classify(path: str, auth: str) -> tuple[str, int, str | None]:
    """(bucket, limit per minute, identity from the Authorization header or None to use the IP)."""
    p = path[len("/api/v1"):] if path.startswith("/api/v1") else path
    if p.startswith("/auth/login") or p.startswith("/auth/mfa/verify") or p.startswith("/auth/sso/callback"):
        return "auth", settings.rate_limit_login_per_minute, None
    if p.startswith("/t/"):
        return "tracking", settings.rate_limit_tracking_per_minute, None
    if p.startswith(("/public/", "/inbound/", "/intake/", "/sign/", "/esign/")):
        return "public", settings.rate_limit_public_per_minute, None
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if token.startswith("ck_"):
        return "api", settings.rate_limit_api_key_per_minute, "key:" + hashlib.sha256(token.encode()).hexdigest()[:24]
    if token:
        return "api", settings.rate_limit_per_minute, "user:" + hashlib.sha256(token.encode()).hexdigest()[:24]
    return "api", settings.rate_limit_anonymous_per_minute, None


async def hit(key: str) -> int | None:
    """Count one request against ``key`` in the current window; None when Redis is unavailable."""
    global _last_warning
    try:
        r = _client()
        async with r.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, WINDOW + 5)
            count, _ = await pipe.execute()
        return int(count)
    except Exception as e:  # fail open
        if time.monotonic() - _last_warning > 60:
            log.warning("Rate limiter unavailable (%s); requests are not being limited", type(e).__name__)
            _last_warning = time.monotonic()
        return None


class RateLimitMiddleware:
    """Pure ASGI middleware (keeps streaming responses and background tasks intact)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not settings.rate_limit_enabled or scope.get("method") == "OPTIONS" \
                or scope["path"].startswith(EXEMPT):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        bucket, limit, identity = classify(scope["path"], headers.get("authorization", ""))
        ip = (scope.get("client") or ("unknown", 0))[0]
        window = int(time.time() // WINDOW)
        count = await hit(f"rl:{tenancy.rate_key_prefix()}{bucket}:{identity or 'ip:' + ip}:{window}")  # each tenant has its own budget
        if count is None:
            return await self.app(scope, receive, send)
        remaining = max(0, limit - count)
        extra = [(b"x-ratelimit-limit", str(limit).encode()), (b"x-ratelimit-remaining", str(remaining).encode())]
        if count > limit:
            retry = WINDOW - int(time.time()) % WINDOW
            body = json.dumps({"detail": f"Too many requests. Try again in {retry} seconds."}).encode()
            await send({"type": "http.response.start", "status": 429,
                        "headers": [(b"content-type", b"application/json"), (b"retry-after", str(retry).encode()), *extra]})
            await send({"type": "http.response.body", "body": body})
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, send_with_headers)
