"""Marketing campaigns: CRUD, members, list building, email, attribution; public unsubscribe."""
import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dialect import nulls_last
from app.core.rbac import Principal, authorize
from app.models import Account, Campaign, CampaignMember, Contact, Deal, Lead, User
from app.services import campaigns as svc
from app.services import fx
from app.services.notify import emit

router = APIRouter(prefix="/campaigns", tags=["campaigns"])
public = APIRouter(prefix="/public/unsubscribe", tags=["public"])

CampaignType = Literal["email", "webinar", "event", "trade_show", "paid_ads", "content", "partner", "other"]
CampaignStatus = Literal["planned", "active", "completed", "aborted"]
MemberStatus = Literal["targeted", "sent", "responded", "registered", "attended", "unsubscribed", "bounced"]


class CampaignIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    code: str | None = Field(default=None, max_length=80)
    campaign_type: CampaignType = "email"
    status: CampaignStatus = "planned"
    description: str | None = Field(default=None, max_length=5000)
    owner_id: uuid.UUID | None = None
    start_date: date | None = None
    end_date: date | None = None
    budget: float = Field(default=0, ge=0, le=1e12)
    actual_cost: float = Field(default=0, ge=0, le=1e12)
    expected_revenue: float = Field(default=0, ge=0, le=1e13)
    email_subject: str | None = Field(default=None, max_length=200)
    email_body: str | None = Field(default=None, max_length=20000)


class MembersIn(BaseModel):
    lead_ids: list[uuid.UUID] = Field(default=[], max_length=5000)
    contact_ids: list[uuid.UUID] = Field(default=[], max_length=5000)


class FilterIn(BaseModel):
    source: Literal["leads", "contacts"]
    filters: list[dict] = []


class MemberPatch(BaseModel):
    status: MemberStatus


async def _get(db: AsyncSession, campaign_id: uuid.UUID) -> Campaign:
    c = await db.get(Campaign, campaign_id)
    if c is None:
        raise HTTPException(404, "Campaign not found")
    return c


async def _owner_name(db: AsyncSession, owner_id) -> str | None:
    return (await db.execute(select(User.full_name).where(User.id == owner_id))).scalar() if owner_id else None


@router.get("/meta")
async def meta(_: Principal = Depends(authorize("campaigns", "read"))):
    return {"types": list(svc.TYPES), "statuses": list(svc.STATUSES), "member_statuses": list(svc.MEMBER_STATUSES),
            "responses": list(svc.RESPONSES)}


@router.get("")
async def list_campaigns(status: CampaignStatus | None = None, db: AsyncSession = Depends(get_db),
                         _: Principal = Depends(authorize("campaigns", "read"))):
    stmt = select(Campaign).order_by(nulls_last(Campaign.start_date, descending=True), Campaign.name)
    if status:
        stmt = stmt.where(Campaign.status == status)
    rows = (await db.execute(stmt)).scalars().all()
    rates = await fx.rates(db)
    names = dict((await db.execute(select(User.id, User.full_name))).all())
    out = []
    for c in rows:
        m = await svc.metrics(db, c, rates)
        out.append({**svc.campaign_out(c, names.get(c.owner_id)), "metrics": m})
    totals = {k: round(sum(r["metrics"][k] for r in out), 2) for k in ("members", "responses", "sourced_pipeline", "influenced_won", "cost")}
    totals["active"] = sum(1 for r in out if r["status"] == "active")
    totals["roi_pct"] = round((totals["influenced_won"] - totals["cost"]) / totals["cost"] * 100, 1) if totals["cost"] else None
    return {"campaigns": out, "totals": totals}


def _check_dates(body: CampaignIn) -> None:
    if body.start_date and body.end_date and body.end_date < body.start_date:
        raise HTTPException(422, "The end date is before the start date")


@router.post("", status_code=201)
async def create_campaign(body: CampaignIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("campaigns", "create"))):
    _check_dates(body)
    try:
        code = svc.slug(body.code or body.name)
    except svc.CampaignError as e:
        raise HTTPException(422, str(e))
    if (await db.execute(select(Campaign.id).where(Campaign.code == code))).first():
        raise HTTPException(409, f"A campaign with code '{code}' exists")
    c = Campaign(**{**body.model_dump(exclude={"code"}), "code": code, "owner_id": body.owner_id or p.id, "name": body.name.strip()})
    db.add(c)
    await db.flush()
    attached = await svc.backfill(db, c)
    if c.status == "active":
        emit(db, "campaign.launched", "campaign", c.id, {"campaign": c.name, "code": c.code, "type": c.campaign_type})
    await db.commit()
    return {"id": c.id, "code": c.code, "attached_leads": attached}


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("campaigns", "read"))):
    c = await _get(db, campaign_id)
    metrics = await svc.metrics(db, c, deals=True)
    # campaign-level totals stay whole; the itemised deal list follows the viewer's row-level scope
    if not p.can("deals", "read"):
        metrics["deals"] = []
    elif p.is_own_scope("deals") and metrics.get("deals"):
        ids = [d["id"] for d in metrics["deals"]]
        visible = set((await db.execute(p.scope_deals(select(Deal.id).where(Deal.id.in_(ids))))).scalars())
        metrics["deals"] = [d for d in metrics["deals"] if d["id"] in visible]
    return {**svc.campaign_out(c, await _owner_name(db, c.owner_id)), "metrics": metrics}


@router.put("/{campaign_id}")
async def update_campaign(campaign_id: uuid.UUID, body: CampaignIn, db: AsyncSession = Depends(get_db),
                          _: Principal = Depends(authorize("campaigns", "update"))):
    c = await _get(db, campaign_id)
    _check_dates(body)
    try:
        code = svc.slug(body.code or c.code)
    except svc.CampaignError as e:
        raise HTTPException(422, str(e))
    launched = body.status == "active" and c.status != "active"
    for k, v in body.model_dump(exclude={"code", "owner_id"}).items():
        setattr(c, k, v)
    c.name, c.code, c.owner_id = body.name.strip(), code, body.owner_id or c.owner_id
    if launched:
        emit(db, "campaign.launched", "campaign", c.id, {"campaign": c.name, "code": c.code, "type": c.campaign_type})
    try:
        await db.commit()
    except IntegrityError:
        raise HTTPException(409, f"A campaign with code '{code}' exists")
    return {"status": "ok"}


@router.delete("/{campaign_id}", status_code=204)
async def delete_campaign(campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("campaigns", "delete"))):
    c = await db.get(Campaign, campaign_id)
    if c:
        await db.delete(c)
        await db.commit()


# ---- members ---------------------------------------------------------------------------------------

def _scope_members(p: Principal, stmt):
    """Sellers with 'own' scope only see members that are their leads or contacts on their accounts."""
    conds = []
    conds.append(CampaignMember.lead_id.is_not(None) if not p.is_own_scope("leads") else Lead.owner_id == p.id)
    conds.append(CampaignMember.contact_id.is_not(None) if not p.is_own_scope("contacts")
                 else Contact.account_id.in_(p.owned_account_ids()))
    if not p.can("leads", "read"):
        conds[0] = False
    if not p.can("contacts", "read"):
        conds[1] = False
    return stmt.where(or_(*conds))


@router.get("/{campaign_id}/members")
async def list_members(campaign_id: uuid.UUID, status: MemberStatus | None = None, q: str | None = None, limit: int = 200,
                       db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("campaigns", "read"))):
    await _get(db, campaign_id)
    stmt = (select(CampaignMember, Lead, Contact, Account.name).outerjoin(Lead, Lead.id == CampaignMember.lead_id)
            .outerjoin(Contact, Contact.id == CampaignMember.contact_id).outerjoin(Account, Account.id == Contact.account_id)
            .where(CampaignMember.campaign_id == campaign_id))
    stmt = _scope_members(p, stmt)
    if status:
        stmt = stmt.where(CampaignMember.status == status)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Lead.email).like(like), func.lower(Lead.company_name).like(like),
                              func.lower(Lead.last_name).like(like), func.lower(Contact.email).like(like),
                              func.lower(Contact.last_name).like(like), func.lower(Account.name).like(like)))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar()
    rows = (await db.execute(stmt.order_by(CampaignMember.added_at.desc()).limit(min(max(limit, 1), 1000)))).unique().all()
    out = []
    for m, lead, contact, account in rows:
        if lead is not None:
            person = {"kind": "lead", "id": lead.id, "name": lead.full_name, "email": lead.email, "company": lead.company_name,
                      "link": f"/leads/{lead.id}"}
        else:
            person = {"kind": "contact", "id": contact.id, "name": f"{contact.first_name} {contact.last_name}", "email": contact.email,
                      "company": account, "link": f"/contacts/{contact.id}"}
        out.append({"id": m.id, "status": m.status, "source": m.source, "added_at": m.added_at, "sent_at": m.sent_at,
                    "responded_at": m.responded_at, "person": person})
    return {"total": total, "members": out}


@router.post("/{campaign_id}/members")
async def add_members(campaign_id: uuid.UUID, body: MembersIn, db: AsyncSession = Depends(get_db),
                      _: Principal = Depends(authorize("campaigns", "update"))):
    c = await _get(db, campaign_id)
    out = await svc.add_members(db, c, lead_ids=body.lead_ids, contact_ids=body.contact_ids)
    await db.commit()
    return out


@router.post("/{campaign_id}/members/from-filter")
async def add_from_filter(campaign_id: uuid.UUID, body: FilterIn, db: AsyncSession = Depends(get_db),
                          p: Principal = Depends(authorize("campaigns", "update"))):
    c = await _get(db, campaign_id)
    try:
        out = await svc.add_from_filter(db, p, c, body.source, body.filters)
    except svc.CampaignError as e:
        raise HTTPException(422, str(e))
    await db.commit()
    return out


async def _member(db: AsyncSession, campaign_id: uuid.UUID, member_id: uuid.UUID) -> CampaignMember:
    m = await db.get(CampaignMember, member_id)
    if m is None or m.campaign_id != campaign_id:
        raise HTTPException(404, "Member not found")
    return m


@router.patch("/{campaign_id}/members/{member_id}")
async def update_member(campaign_id: uuid.UUID, member_id: uuid.UUID, body: MemberPatch, db: AsyncSession = Depends(get_db),
                        _: Principal = Depends(authorize("campaigns", "update"))):
    c = await _get(db, campaign_id)
    m = await _member(db, campaign_id, member_id)
    try:
        await svc.set_status(db, c, m, body.status)
    except svc.CampaignError as e:
        raise HTTPException(422, str(e))
    await db.commit()
    return {"id": m.id, "status": m.status, "responded_at": m.responded_at}


@router.delete("/{campaign_id}/members/{member_id}", status_code=204)
async def remove_member(campaign_id: uuid.UUID, member_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                        _: Principal = Depends(authorize("campaigns", "update"))):
    m = await _member(db, campaign_id, member_id)
    await db.delete(m)
    await db.commit()


# ---- email -----------------------------------------------------------------------------------------

@router.get("/{campaign_id}/email/preview")
async def email_preview(campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("campaigns", "read"))):
    c = await _get(db, campaign_id)
    rec = await svc.recipients(db, c)
    blocked: dict[str, int] = {}
    for r in rec:
        if r["blocked"]:
            blocked[r["blocked"]] = blocked.get(r["blocked"], 0) + 1
    sample = next((r for r in rec if not r["blocked"]), None)
    return {"eligible": sum(1 for r in rec if not r["blocked"]), "blocked": blocked,
            "sample": {"to": sample["person"]["email"],
                       "subject": svc.render_subject(c.email_subject or "", sample["person"]),
                       "body": svc.render(c.email_body or "", sample["person"], None)} if sample else None}  # inert link: a click here unsubscribes nobody


@router.post("/{campaign_id}/email/send")
async def email_send(campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("campaigns", "update"))):
    c = await _get(db, campaign_id)
    try:
        out = await svc.send(db, c, p.user)
    except svc.CampaignError as e:
        raise HTTPException(422, str(e))
    await db.commit()
    return out


# ---- public unsubscribe ----------------------------------------------------------------------------

@public.get("/{token}")
async def unsubscribe_info(token: str, db: AsyncSession = Depends(get_db)):
    m = (await db.execute(select(CampaignMember).where(CampaignMember.token == token))).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, "This unsubscribe link isn't valid")
    c = await db.get(Campaign, m.campaign_id)
    return {"campaign": c.name, "unsubscribed": m.status == "unsubscribed"}


@public.post("/{token}")
async def unsubscribe(token: str, db: AsyncSession = Depends(get_db)):
    try:
        out = await svc.unsubscribe(db, token)
    except svc.CampaignError as e:
        raise HTTPException(404, str(e))
    await db.commit()
    return {**out, "unsubscribed": True}
