"""Cirra API: FastAPI application entrypoint."""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import literal, select, text

from app.api.v1 import accounts, activities, admin, ai, analytics, auth, campaigns, cases, contacts, cpq, deals, developer, finance, leads, orders, partners, performance, forecasting, success, sync, views, workflows, objects, setup, journeys, inbound
from app.core.config import enforce_secure_settings, settings
from app.core.database import engine
from app.core.observability import RequestContextMiddleware, configure_logging
from app.core.ratelimit import RateLimitMiddleware
from app.services import validation  # registers the validation-rule change capture

configure_logging()
log = logging.getLogger(__name__)
enforce_secure_settings(settings)  # refuse to serve with a forgeable token secret outside development

app = FastAPI(
    title="Cirra API",
    version="2.0.0",
    description="AI-first, private-cloud CRM from the SDC Solutions portfolio.",
)
# Outermost last: request id / access log / security headers wrap CORS, which wraps the rate limiter, so even a
# 429 carries CORS headers the browser can read and a request id to trace.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"],
)
app.add_middleware(RequestContextMiddleware)

for router in (auth.router, accounts.router, contacts.router, deals.router, activities.router, ai.router, cpq.router, cpq.public,
               success.router, finance.router, partners.router, partners.portal, admin.router, leads.router, leads.intake,
               orders.router, analytics.router, workflows.router, forecasting.router, cases.router, cases.public, performance.router,
               campaigns.router, campaigns.public, developer.router, sync.router, views.router, objects.router, setup.router, journeys.router, inbound.track, inbound.inbound):
    app.include_router(router, prefix="/api/v1")


@app.exception_handler(validation.ValidationRuleError)
async def _validation_failed(request: Request, exc: validation.ValidationRuleError):
    """An admin validation rule blocked the save: nothing was written."""
    return JSONResponse(status_code=422, content={"detail": str(exc), "validation": exc.failures})


@app.get("/health", tags=["system"])
async def health():
    async with engine.connect() as conn:
        await conn.execute(select(literal(1)))
    return {"status": "ok", "service": "cirra-api", "llm_provider": settings.llm_provider}


@app.get("/health/live", tags=["system"])
async def live():
    """Liveness: the process is up and serving (no dependencies, so a database blip never restarts pods)."""
    return {"status": "ok"}


def _migration_head() -> str | None:
    import os

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    try:
        cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:
        return None


MIGRATION_HEAD = _migration_head()


@app.get("/health/ready", tags=["system"])
async def ready():
    """Readiness: database reachable and migrated to this build's schema, Redis reachable. 503 when not ready."""
    checks: dict[str, str] = {}
    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        checks["database"] = "ok"
        checks["migrations"] = "ok" if MIGRATION_HEAD is None or version == MIGRATION_HEAD else f"at {version}, expected {MIGRATION_HEAD}"
    except Exception as e:
        checks["database"] = f"unavailable ({type(e).__name__})"
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
        await r.ping()
        await r.aclose()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"unavailable ({type(e).__name__})"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)
