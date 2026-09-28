"""Two-way calendar sync: connect Google Calendar or Microsoft 365, sync now, disconnect."""
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.config import settings
from app.core.database import get_db
from app.core.rbac import Principal, authorize, authorize_person
from app.models import CalendarConnection
from app.services import calendar_sync

router = APIRouter(prefix="/calendar", tags=["calendar sync"])


@router.get("/connections")
async def connections(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("activities", "read"))):
    rows = (await db.execute(select(CalendarConnection).where(CalendarConnection.user_id == p.user.id))).scalars().all()
    return {"providers": [{"key": k, "label": v.label, "configured": v.configured()} for k, v in calendar_sync.PROVIDERS.items()],
            "connections": [calendar_sync.connection_out(c) for c in rows]}


@router.post("/connect/{provider}")
async def connect(provider: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize_person("activities", "create"))):
    try:
        url = await calendar_sync.start_connect(db, p.user, provider)
    except calendar_sync.CalendarError as e:
        raise HTTPException(409, str(e)) from e
    await db.commit()
    return {"url": url}


@router.get("/oauth/callback", include_in_schema=False)
async def oauth_callback(state: str = "", code: str = "", error: str | None = None, db: AsyncSession = Depends(get_db)):
    """The provider redirects the browser here; the server-side state identifies the user (no session needed)."""
    web = settings.public_web_url.rstrip("/")
    if error or not code or not state:
        return RedirectResponse(f"{web}/settings?calendar=error&reason={quote(error or 'cancelled')}", status_code=302)
    try:
        conn = await calendar_sync.finish_connect(db, state, code)
        log_action(db, "calendar_link", "calendar", conn.id, conn.provider)
        await db.commit()
    except calendar_sync.CalendarError as e:
        await db.commit()  # the used state is gone either way
        return RedirectResponse(f"{web}/settings?calendar=error&reason={quote(str(e))}", status_code=302)
    await calendar_sync.sync_connection(db, conn)  # first sync right away
    return RedirectResponse(f"{web}/settings?calendar=connected", status_code=302)


async def _mine(db: AsyncSession, p: Principal, conn_id: uuid.UUID) -> CalendarConnection:
    conn = await db.get(CalendarConnection, conn_id)
    if conn is None or conn.user_id != p.user.id:
        raise HTTPException(404, "Not found")
    return conn


@router.post("/connections/{conn_id}/sync")
async def sync_now(conn_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("activities", "read"))):
    conn = await _mine(db, p, conn_id)
    stats = await calendar_sync.sync_connection(db, conn)
    await db.refresh(conn)
    return {"connection": calendar_sync.connection_out(conn), "stats": stats}


@router.delete("/connections/{conn_id}", status_code=204)
async def disconnect(conn_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize_person("activities", "read"))):
    conn = await _mine(db, p, conn_id)
    await db.delete(conn)
    log_action(db, "calendar_unlink", "calendar", conn_id, conn.provider)
    await db.commit()
