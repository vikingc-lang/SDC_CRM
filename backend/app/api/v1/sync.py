"""Integration upsert (create-or-update by the caller's external id) and bulk actions on list selections."""
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import Principal, get_principal
from app.services import bulk, sync

router = APIRouter(tags=["integration"])
Entity = Literal["accounts", "contacts", "deals", "leads"]


class BatchIn(BaseModel):
    records: list[dict] = Field(min_length=1, max_length=sync.MAX_BATCH)


@router.put("/upsert/{entity}/{external_id}")
async def upsert_one(entity: Entity, external_id: str, data: dict = Body(default_factory=dict), db: AsyncSession = Depends(get_db),
                     p: Principal = Depends(get_principal)):
    """Create or update one record matched by your system's id. See the module docs for matching rules."""
    try:
        out = await sync.upsert(db, p, entity, external_id, data)
        await db.commit()
    except sync.SyncError as e:
        raise HTTPException(404 if str(e) == "Record not found" else 422, str(e))
    except IntegrityError as e:
        raise HTTPException(409, f"Conflicts with an existing record: {str(e.orig).splitlines()[0][:160]}")
    return out


@router.post("/upsert/{entity}")
async def upsert_batch(entity: Entity, body: BatchIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(get_principal)):
    """Up to 500 records; each succeeds or fails on its own and the response lists the outcome per row."""
    try:
        out = await sync.upsert_many(db, p, entity, body.records)
    except sync.SyncError as e:
        raise HTTPException(422, str(e))
    await db.commit()
    return out


class BulkIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=bulk.MAX_IDS)
    action: str
    value: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


@router.post("/bulk/{entity}")
async def bulk_action(entity: Literal["leads", "accounts", "contacts", "cases"], body: BulkIn, db: AsyncSession = Depends(get_db),
                      p: Principal = Depends(get_principal)):
    """Apply one action to a list selection. Records the caller can't see or update are skipped and counted."""
    try:
        out = await bulk.apply(db, p, entity, body.ids, body.action, body.value, body.note)
    except bulk.BulkError as e:
        raise HTTPException(422, str(e))
    await db.commit()
    return out
