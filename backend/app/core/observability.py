"""Request tracing and baseline response hardening.

Every request gets an ``X-Request-ID`` (the caller's, when it sends a sane one, else a new one), echoed on
the response and written on one access-log line with method, path, status and duration. With
LOG_FORMAT=json the whole process logs one JSON object per line for log shippers. API responses also get
conservative security headers (the API serves JSON only, so a strict CSP costs nothing).
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar

from app.core.config import settings

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
access_log = logging.getLogger("cirra.access")
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"cross-origin-resource-policy", b"cross-origin"),  # the web app and email clients load API responses (tracking pixel)
]
DOCS = ("/docs", "/redoc")  # the Swagger UI needs scripts and styles: no CSP there


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname, "logger": record.name, "message": record.getMessage()}
        rid = request_id.get()
        if rid:
            out["request_id"] = rid
        for key in ("method", "path", "status", "duration_ms"):
            if hasattr(record, key):
                out[key] = getattr(record, key)
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


class RequestContextMiddleware:
    """Pure ASGI: request id, access log line and security headers, including on 429s from the rate limiter."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        incoming = next((v.decode("latin-1") for k, v in scope.get("headers") or [] if k == b"x-request-id"), "")
        rid = incoming if SAFE_ID.match(incoming) else uuid.uuid4().hex
        token = request_id.set(rid)
        started = time.perf_counter()
        status = {"code": 500}
        docs = scope["path"].startswith(DOCS)

        async def send_wrapped(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                extra = [(b"x-request-id", rid.encode())] + ([] if docs else SECURITY_HEADERS)
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapped)
        finally:
            if not scope["path"].startswith("/health"):
                ms = round((time.perf_counter() - started) * 1000, 1)
                access_log.info("%s %s %s %sms", scope.get("method"), scope["path"], status["code"], ms,
                                extra={"method": scope.get("method"), "path": scope["path"], "status": status["code"], "duration_ms": ms})
            request_id.reset(token)
