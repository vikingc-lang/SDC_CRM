"""Multi-tenancy: one database per tenant workspace on the same PostgreSQL server.

Isolation is physical: every tenant's records, users, API keys, files and settings live in that tenant's own
database, so no query can see across tenants whatever it filters on. The primary database holds the ``default``
workspace plus the tenant registry (``tenants``).

How a request finds its tenant (``TenantMiddleware``, when MULTI_TENANT is on):
1. a ``/w/<slug>/…`` path prefix (links the API sends out: tracking pixels, calendar callbacks);
2. the ``X-Cirra-Tenant`` header naming the slug (API clients; when TENANT_HEADER is on);
3. the host name, from ``X-Cirra-Host`` (the web app sends its own host) or ``Host``, matched against each
   tenant's registered hosts;
4. otherwise the default workspace.
Unknown slugs get 404 and suspended tenants 403. Sign-in tokens carry the tenant (``tid``) and are refused in any
other tenant, rate limits are counted per tenant, stored files sit under a per-tenant directory, and background
jobs run with the tenant they were queued in (scheduled jobs run once per active tenant).

Sessions pick their database per statement (``RoutingSession.get_bind`` in core/database.py) from the
``current_tenant`` context variable, so application code is unchanged.
"""
from __future__ import annotations

import re
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

DEFAULT = "default"
SLUG = re.compile(r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")
current_tenant: ContextVar[str] = ContextVar("current_tenant", default=DEFAULT)


@dataclass
class TenantInfo:
    slug: str
    name: str
    db_name: str
    hosts: list[str] = field(default_factory=list)
    status: str = "active"
    max_users: int | None = None


_registry: dict[str, TenantInfo] = {}
_loaded_at = 0.0
_TTL = 30.0
_engines: dict[str, AsyncEngine] = {}


class TenantError(RuntimeError):
    pass


def slug() -> str:
    return current_tenant.get()


def db_name_for(slug_: str) -> str:
    return f"{make_url(settings.database_url).database}_t_{slug_.replace('-', '_')}"


def url_for(db_name: str, sync: bool = False) -> str:
    base = settings.sync_database_url if sync else settings.database_url
    return make_url(base).set(database=db_name).render_as_string(hide_password=False)


def _host(value: str | None) -> str:
    return (value or "").strip().lower().split(",")[0].strip().rsplit(":", 1)[0] if value else ""


async def load(force: bool = False) -> dict[str, TenantInfo]:
    """The registry, cached for 30 seconds (suspensions take effect within that)."""
    global _registry, _loaded_at
    if not force and _registry and time.monotonic() - _loaded_at < _TTL:
        return _registry
    from sqlalchemy import select

    from app.core.database import engine
    from app.models import Tenant

    async with engine.connect() as conn:
        rows = (await conn.execute(select(Tenant.slug, Tenant.name, Tenant.db_name, Tenant.hosts, Tenant.status, Tenant.max_users))).all()
    _registry = {r.slug: TenantInfo(r.slug, r.name, r.db_name, [h.lower() for h in (r.hosts or [])], r.status, r.max_users) for r in rows}
    _loaded_at = time.monotonic()
    return _registry


def invalidate() -> None:
    global _loaded_at
    _loaded_at = 0.0


async def by_host(host: str) -> TenantInfo | None:
    h = _host(host)
    if not h:
        return None
    return next((t for t in (await load()).values() if h in {_host(x) for x in t.hosts}), None)


def engine_for(slug_: str) -> AsyncEngine:
    if slug_ == DEFAULT:
        from app.core.database import engine

        return engine
    eng = _engines.get(slug_)
    if eng is None:
        info = _registry.get(slug_)
        if info is None:
            raise TenantError(f"Unknown workspace: {slug_}")
        url = url_for(info.db_name)
        eng = (create_async_engine(url, poolclass=NullPool) if settings.db_null_pool
               else create_async_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5))
        _engines[slug_] = eng
    return eng


def sync_bind():
    """The engine for the current tenant (used by every ORM session)."""
    return engine_for(current_tenant.get()).sync_engine


@asynccontextmanager
async def use(slug_: str):
    """Run a block as this tenant (background jobs, CLI)."""
    if slug_ != DEFAULT:
        info = (await load()).get(slug_) or (await load(force=True)).get(slug_)
        if info is None:
            raise TenantError(f"Unknown workspace: {slug_}")
    token = current_tenant.set(slug_)
    try:
        engine_for(slug_)
        yield
    finally:
        current_tenant.reset(token)


async def active_slugs() -> list[str]:
    if not settings.multi_tenant:
        return [DEFAULT]
    try:
        return [DEFAULT] + sorted(s for s, t in (await load(force=True)).items() if t.status == "active")
    except Exception:  # registry unreadable: still run the default workspace's jobs
        return [DEFAULT]


def web_url() -> str:
    """Base URL of the web app for links sent out (emails, Slack): the tenant's first host when it has one."""
    info = _registry.get(current_tenant.get())
    if info and info.hosts:
        host = info.hosts[0]
        scheme = settings.public_web_url.split("://", 1)[0] if "://" in settings.public_web_url else "https"
        return f"{scheme}://{host}"
    return settings.public_web_url.rstrip("/")


def api_url() -> str:
    """Base URL of the API for links sent out (tracking pixels, OAuth callbacks), with the tenant's path prefix."""
    base = settings.public_api_url.rstrip("/")
    s = current_tenant.get()
    return base if s == DEFAULT else f"{base}/w/{s}"


def storage_suffix() -> str:
    s = current_tenant.get()
    return "" if s == DEFAULT else f"tenants/{s}"


def rate_key_prefix() -> str:
    s = current_tenant.get()
    return "" if s == DEFAULT else f"{s}:"


async def can_add_user(db) -> bool:
    """False when the tenant's plan limit on active users is reached."""
    info = _registry.get(current_tenant.get())
    if info is None or info.max_users is None:
        return True
    from sqlalchemy import func, select

    from app.models import User

    active = (await db.execute(select(func.count()).select_from(User).where(User.is_active.is_(True), User.role != "partner"))).scalar_one()
    return active < info.max_users


class TenantMiddleware:
    """Pure ASGI, so the context variable is set for the whole request, including background tasks."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not settings.multi_tenant:
            return await self.app(scope, receive, send)
        path: str = scope.get("path", "")
        if path.startswith("/health"):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        chosen: str | None = None
        m = re.match(r"^/w/([a-z0-9-]{3,40})(/.*)$", path)
        if m:
            chosen, path = m.group(1), m.group(2)
            scope = {**scope, "path": path, "raw_path": path.encode()}
        elif settings.tenant_header and headers.get("x-cirra-tenant"):
            chosen = headers["x-cirra-tenant"].strip().lower()
        try:
            if chosen is None:
                found = await by_host(headers.get("x-cirra-host") or headers.get("host"))
                chosen = found.slug if found else DEFAULT
            info = None if chosen == DEFAULT else ((await load()).get(chosen) or (await load(force=True)).get(chosen))
        except Exception:
            return await _reply(send, 503, "The workspace directory is unavailable")
        if chosen != DEFAULT and info is None:
            return await _reply(send, 404, "Unknown workspace")
        if info is not None and info.status != "active":
            return await _reply(send, 403, "This workspace is suspended")
        token = current_tenant.set(chosen)
        try:
            engine_for(chosen)
            await self.app(scope, receive, send)
        finally:
            current_tenant.reset(token)


async def _reply(send, status: int, detail: str) -> None:
    import json

    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
