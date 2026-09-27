"""Cirra API: FastAPI application entrypoint."""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1 import accounts, activities, admin, ai, analytics, auth, campaigns, cases, contacts, cpq, deals, developer, finance, leads, orders, partners, performance, forecasting, success, sync, views, workflows, objects, setup
from app.core.config import settings
from app.core.database import engine
from app.services import validation  # registers the validation-rule change capture

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="Cirra API",
    version="2.0.0",
    description="AI-first, private-cloud CRM from the SDC Solutions portfolio.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (auth.router, accounts.router, contacts.router, deals.router, activities.router, ai.router, cpq.router, cpq.public,
               success.router, finance.router, partners.router, partners.portal, admin.router, leads.router, leads.intake,
               orders.router, analytics.router, workflows.router, forecasting.router, cases.router, cases.public, performance.router,
               campaigns.router, campaigns.public, developer.router, sync.router, views.router, objects.router, setup.router):
    app.include_router(router, prefix="/api/v1")


@app.exception_handler(validation.ValidationRuleError)
async def _validation_failed(request: Request, exc: validation.ValidationRuleError):
    """An admin validation rule blocked the save: nothing was written."""
    return JSONResponse(status_code=422, content={"detail": str(exc), "validation": exc.failures})


@app.get("/health", tags=["system"])
async def health():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok", "service": "cirra-api", "llm_provider": settings.llm_provider}
