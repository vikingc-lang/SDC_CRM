"""Cirra API: FastAPI application entrypoint."""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1 import accounts, activities, admin, ai, auth, contacts, cpq, deals, finance, partners, success
from app.core.config import settings
from app.core.database import engine

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
               success.router, finance.router, partners.router, partners.portal, admin.router):
    app.include_router(router, prefix="/api/v1")


@app.get("/health", tags=["system"])
async def health():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok", "service": "cirra-api", "llm_provider": settings.llm_provider}
