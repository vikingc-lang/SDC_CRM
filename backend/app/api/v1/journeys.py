"""Nurture journeys (see services/journeys.py): build, activate, pause, monitor, and test-send a step."""
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import Principal, authorize
from app.models import Campaign, CampaignMember, Contact, EmailSend, Journey, JourneyEnrollment, Lead, User
from app.services import campaigns, journeys as svc, mail

router = APIRouter(prefix="/journeys", tags=["marketing"])


class JourneyIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    campaign_id: uuid.UUID
    steps: list[dict] = Field(default_factory=list, max_length=svc.MAX_STEPS)
    sender_id: uuid.UUID | None = None


class TestIn(BaseModel):
    step: int = Field(ge=0)


def _out(j: Journey, names: dict, campaign: Campaign | None = None) -> dict:
    return {"id": j.id, "name": j.name, "description": j.description, "campaign_id": j.campaign_id, "campaign": campaign.name if campaign else None,
            "status": j.status, "steps": j.steps, "sender_id": j.sender_id, "sender": names.get(j.sender_id or j.created_by),
            "activated_at": j.activated_at, "updated_at": j.updated_at}


async def _journey(db: AsyncSession, journey_id: uuid.UUID) -> Journey:
    j = await db.get(Journey, journey_id)
    if j is None:
        raise HTTPException(404, "Journey not found")
    return j


async def _names(db: AsyncSession, ids) -> dict:
    ids = {i for i in ids if i}
    return {u.id: u.full_name for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars()} if ids else {}


async def _check(db: AsyncSession, body: JourneyIn, steps_required: bool) -> list[dict]:
    if await db.get(Campaign, body.campaign_id) is None:
        raise HTTPException(422, "Campaign not found")
    if body.sender_id:
        u = await db.get(User, body.sender_id)
        if u is None or not u.is_active or u.role == "partner":
            raise HTTPException(422, "The sender must be an active internal user")
    try:
        return svc.validate_steps(body.steps) if (steps_required or body.steps) else []
    except svc.JourneyError as e:
        raise HTTPException(422, str(e))


@router.get("")
async def list_journeys(campaign_id: uuid.UUID | None = None, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("campaigns", "read"))):
    stmt = select(Journey, Campaign).join(Campaign, Campaign.id == Journey.campaign_id).where(Journey.status != "archived")
    if campaign_id:
        stmt = stmt.where(Journey.campaign_id == campaign_id)
    rows = (await db.execute(stmt.order_by(Journey.updated_at.desc()))).all()
    names = await _names(db, [j.sender_id or j.created_by for j, _ in rows])
    out = []
    for j, c in rows:
        s = await svc.stats(db, j)
        out.append({**_out(j, names, c), "enrolled": s["enrolled"], "by_status": s["by_status"],
                    "email": await campaigns.email_stats(db, EmailSend.journey_id == j.id)})
    return out


@router.post("", status_code=201)
async def create_journey(body: JourneyIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("campaigns", "update"))):
    steps = await _check(db, body, steps_required=False)
    j = Journey(name=body.name.strip(), description=body.description, campaign_id=body.campaign_id, steps=steps, sender_id=body.sender_id,
                created_by=p.id, status="draft")
    db.add(j)
    await db.commit()
    await db.refresh(j)
    return _out(j, await _names(db, [j.sender_id or j.created_by]), await db.get(Campaign, j.campaign_id))


@router.get("/{journey_id}")
async def get_journey(journey_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("campaigns", "read"))):
    j = await _journey(db, journey_id)
    return {**_out(j, await _names(db, [j.sender_id or j.created_by]), await db.get(Campaign, j.campaign_id)),
            "stats": await svc.stats(db, j), "email": await campaigns.email_stats(db, EmailSend.journey_id == j.id)}


@router.put("/{journey_id}")
async def update_journey(journey_id: uuid.UUID, body: JourneyIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("campaigns", "update"))):
    """Edit a draft or paused journey. People already past a step aren't sent it again; steps keep their positions."""
    j = await _journey(db, journey_id)
    if j.status == "active":
        raise HTTPException(409, "Pause the journey before editing it")
    if body.campaign_id != j.campaign_id and j.activated_at:
        raise HTTPException(422, "A journey that has run can't move to another campaign")
    steps = await _check(db, body, steps_required=False)
    j.name, j.description, j.campaign_id, j.steps, j.sender_id = body.name.strip(), body.description, body.campaign_id, steps, body.sender_id
    await db.commit()
    await db.refresh(j)
    return _out(j, await _names(db, [j.sender_id or j.created_by]), await db.get(Campaign, j.campaign_id))


@router.post("/{journey_id}/status")
async def set_status(journey_id: uuid.UUID, status: Literal["active", "paused", "archived"], db: AsyncSession = Depends(get_db),
                     p: Principal = Depends(authorize("campaigns", "update"))):
    """Activate (enrolls the campaign's members and starts sending on the next run), pause or archive."""
    j = await _journey(db, journey_id)
    if status == "active":
        try:
            svc.validate_steps(j.steps)
        except svc.JourneyError as e:
            raise HTTPException(422, str(e))
        campaign = await db.get(Campaign, j.campaign_id)
        if campaign.status in ("completed", "aborted"):
            raise HTTPException(422, "The campaign is closed")
        j.activated_at = j.activated_at or datetime.now(timezone.utc)
        j.status = "active"
        enrolled = await svc.enroll(db, j)
        await db.commit()
        return {"status": j.status, "enrolled": enrolled}
    j.status = status
    await db.commit()
    return {"status": j.status}


@router.delete("/{journey_id}", status_code=204)
async def delete_journey(journey_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("campaigns", "update"))):
    j = await _journey(db, journey_id)
    if j.activated_at:
        raise HTTPException(409, "A journey that has run can only be archived, so its results stay reportable")
    await db.delete(j)
    await db.commit()


@router.get("/{journey_id}/enrollments")
async def enrollments(journey_id: uuid.UUID, status: Literal["active", "completed", "exited"] | None = None, limit: int = Query(100, le=500),
                      db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("campaigns", "read"))):
    await _journey(db, journey_id)
    stmt = (select(JourneyEnrollment, CampaignMember, Lead, Contact).join(CampaignMember, CampaignMember.id == JourneyEnrollment.member_id)
            .outerjoin(Lead, Lead.id == CampaignMember.lead_id).outerjoin(Contact, Contact.id == CampaignMember.contact_id)
            .where(JourneyEnrollment.journey_id == journey_id))
    if status:
        stmt = stmt.where(JourneyEnrollment.status == status)
    rows = (await db.execute(stmt.order_by(JourneyEnrollment.updated_at.desc()).limit(limit))).all()
    return [{"id": e.id, "status": e.status, "step": e.step, "next_at": e.next_at, "exit_reason": e.exit_reason, "enrolled_at": e.enrolled_at,
             "person": (f"{lead.first_name or ''} {lead.last_name or ''}".strip() or lead.email) if lead else
             (f"{contact.first_name} {contact.last_name}" if contact else None),
             "kind": "lead" if lead else "contact", "record_id": (lead or contact).id if (lead or contact) else None} for e, m, lead, contact in rows]


@router.post("/{journey_id}/test")
async def test_send(journey_id: uuid.UUID, body: TestIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("campaigns", "update"))):
    """Send one email step to yourself, filled with your own name (not tracked, not counted)."""
    j = await _journey(db, journey_id)
    if body.step >= len(j.steps or []) or j.steps[body.step]["type"] != "email":
        raise HTTPException(422, "Choose an email step")
    step = j.steps[body.step]
    first, _, last = p.user.full_name.partition(" ")
    person = {"first_name": first, "last_name": last, "company": "Your company", "email": p.user.email}
    subject = "[Test] " + campaigns.render_subject(step["subject"], person)[:190]
    _, delivered = await mail.deliver(db, p.user, p.user.email, subject, campaigns.render(step["body"], person, None))
    return {"sent_to": p.user.email, "delivered": delivered}


@router.post("/run")
async def run_now(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("campaigns", "update"))):
    """Process due journey steps now instead of waiting for the scheduler."""
    return await svc.run(db)
