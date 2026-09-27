"""Public endpoints for email: open / click tracking, and the inbound support-mail webhook."""
import base64
import binascii
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.services import email_to_case, tracking

track = APIRouter(prefix="/t", tags=["public"])
inbound = APIRouter(prefix="/inbound", tags=["integration"])
NO_CACHE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}


@track.get("/o/{token}.gif", include_in_schema=False)
async def open_pixel(token: str):
    """Always a 1x1 GIF; unknown tokens are ignored so the pixel can't be used to probe."""
    try:
        async with SessionLocal() as db:  # its own session: a tracking hit must never be blocked by rules
            await tracking.record_open(db, token[:40])
            await db.commit()
    except Exception:
        pass
    return Response(tracking.PIXEL, media_type="image/gif", headers=NO_CACHE)


@track.get("/c/{token}", include_in_schema=False)
async def click(token: str, u: str = "", s: str = ""):
    async with SessionLocal() as db:
        url = await tracking.record_click(db, token[:40], u, s)
        await db.commit()
    if url is None:
        raise HTTPException(404, "This link isn't valid")
    return RedirectResponse(url, status_code=302, headers=NO_CACHE)


class InboundIn(BaseModel):
    """Either ``raw`` (the full RFC 822 message, base64) or the parsed fields."""
    raw: str | None = Field(default=None, max_length=15_000_000)
    message_id: str | None = Field(default=None, max_length=500)
    from_email: str | None = Field(default=None, max_length=255)
    from_name: str | None = Field(default=None, max_length=200)
    to: list[str] = Field(default_factory=list, max_length=50)
    subject: str = Field(default="", max_length=1000)
    text: str = Field(default="", max_length=200_000)
    in_reply_to: str | None = Field(default=None, max_length=500)
    references: list[str] = Field(default_factory=list, max_length=100)
    automatic: bool = False


@inbound.post("/email")
async def inbound_email(body: InboundIn, x_cirra_inbound_secret: str = Header(default=""), db: AsyncSession = Depends(get_db)):
    """File one support email as a case (or a reply on one). Authenticated with the shared INBOUND_EMAIL_SECRET."""
    if not settings.inbound_email_secret:
        raise HTTPException(404, "Inbound email isn't enabled")
    if not hmac.compare_digest(x_cirra_inbound_secret, settings.inbound_email_secret):
        raise HTTPException(401, "Bad inbound secret")
    db.info["validate"] = False  # the sender isn't a Cirra user: validation rules apply to people's edits only
    try:
        if body.raw:
            try:
                m = email_to_case.parse_raw(base64.b64decode(body.raw, validate=True))
            except (binascii.Error, ValueError):
                raise HTTPException(422, "raw must be a base64 RFC 822 message")
        else:
            if not body.message_id or not body.from_email:
                raise HTTPException(422, "Send raw, or at least message_id and from_email")
            m = email_to_case.Inbound(message_id=body.message_id, from_email=body.from_email.lower(), from_name=body.from_name,
                                      to=[t.lower() for t in body.to], subject=body.subject, text=body.text, in_reply_to=body.in_reply_to,
                                      references=body.references, automatic=body.automatic)
        rec = await email_to_case.ingest(db, m)
    except email_to_case.InboundError as e:
        raise HTTPException(422, str(e))
    await db.commit()
    return {"id": rec.id, "status": rec.status, "detail": rec.detail, "case_id": rec.case_id}
