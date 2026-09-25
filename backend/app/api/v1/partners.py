import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.rbac import Principal, authorize
from app.models import Attachment, Collateral, CollateralDownload, DealRegistration, Partner, User
from app.services import prm, storage

router = APIRouter(tags=["partners"])
portal = APIRouter(prefix="/portal", tags=["partner portal"])


class PartnerIn(BaseModel):
    name: str = Field(min_length=2)
    partner_type: Literal["distributor", "agency", "reseller", "referral", "technology"]
    tier: Literal["registered", "silver", "gold", "platinum"] = "registered"
    domains: list[str] = []
    territories: list[str] = []
    commission_rate: float = Field(default=10, ge=0, le=100)
    referral_fee_rate: float = Field(default=5, ge=0, le=100)
    status: Literal["active", "inactive"] = "active"


class RegistrationIn(BaseModel):
    company_name: str = Field(min_length=2, max_length=255)
    domain: str = Field(min_length=3, max_length=255)
    contact_name: str | None = None
    contact_email: EmailStr | None = None
    estimated_amount: float = Field(default=0, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    territory: str | None = None
    product_interest: str | None = None
    notes: str | None = None


class DecisionIn(BaseModel):
    approve: bool
    note: str | None = None


# ---- internal (channel team) ------------------------------------------------------------
@router.get("/partners")
async def list_partners(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("partners", "read"))):
    return [prm.partner_out(x) for x in (await db.execute(select(Partner).order_by(Partner.name))).scalars().all()]


@router.post("/partners", status_code=201)
async def create_partner(body: PartnerIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("partners", "create"))):
    partner = Partner(**{**body.model_dump(), "domains": [d.lower() for d in body.domains]})
    db.add(partner)
    await db.commit()
    return prm.partner_out(partner)


@router.put("/partners/{partner_id}")
async def update_partner(partner_id: uuid.UUID, body: PartnerIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("partners", "update"))):
    partner = await db.get(Partner, partner_id)
    if partner is None:
        raise HTTPException(404, "Partner not found")
    for k, v in body.model_dump().items():
        setattr(partner, k, [d.lower() for d in v] if k == "domains" else v)
    await db.commit()
    return prm.partner_out(partner)


@router.get("/partners/registrations")
async def registrations(status: str | None = None, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("partners", "read"))):
    stmt = select(DealRegistration).order_by(DealRegistration.created_at.desc()).limit(200)
    if status:
        stmt = stmt.where(DealRegistration.status.in_(status.split(",")))
    return [prm.registration_out(r) for r in (await db.execute(stmt)).scalars().unique().all()]


@router.post("/partners/registrations/{registration_id}/decide")
async def decide(registration_id: uuid.UUID, body: DecisionIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("partners", "update"))):
    reg = await db.get(DealRegistration, registration_id)
    if reg is None:
        raise HTTPException(404, "Registration not found")
    try:
        await prm.decide(db, reg, p.user, body.approve, body.note)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    await db.commit()
    return prm.registration_out(await db.get(DealRegistration, registration_id))


@router.get("/partners/commissions")
async def commissions(partner_id: uuid.UUID | None = None, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("partners", "read"))):
    return await prm.commission_report(db, partner_id)


@router.get("/partners/collateral")
async def list_collateral(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("partners", "read"))):
    rows = (await db.execute(select(Collateral).order_by(Collateral.created_at.desc()))).scalars().unique().all()
    counts = {}
    for d in (await db.execute(select(CollateralDownload.collateral_id))).scalars().all():
        counts[d] = counts.get(d, 0) + 1
    return [_collateral_out(c, counts.get(c.id, 0)) for c in rows]


@router.post("/partners/collateral", status_code=201)
async def upload_collateral(title: str = Form(...), category: str = Form(...), description: str | None = Form(default=None),
                            min_tier: str = Form(default="registered"), allowed_domains: str = Form(default=""),
                            file: UploadFile = File(...), db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("partners", "create"))):
    if category not in ("deck", "whitepaper", "battlecard", "price_list", "case_study"):
        raise HTTPException(422, "Unknown category")
    att = storage.save(await file.read(), file.filename or "file", file.content_type or "application/octet-stream", uploaded_by=p.id)
    db.add(att)
    await db.flush()
    item = Collateral(title=title, category=category, description=description, attachment_id=att.id, min_tier=min_tier,
                      allowed_domains=[d.strip().lower() for d in allowed_domains.split(",") if d.strip()])
    db.add(item)
    await db.commit()
    return _collateral_out(await db.get(Collateral, item.id), 0)


def _collateral_out(c: Collateral, downloads: int = 0) -> dict:
    return {"id": c.id, "title": c.title, "description": c.description, "category": c.category, "min_tier": c.min_tier,
            "allowed_domains": c.allowed_domains, "is_published": c.is_published, "downloads": downloads,
            "file": {"filename": c.attachment.filename, "size_bytes": c.attachment.size_bytes, "content_type": c.attachment.content_type},
            "created_at": c.created_at}


# ---- external partner portal ------------------------------------------------------------------
async def partner_user(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> tuple[User, Partner]:
    if user.role != "partner" or user.partner_id is None:
        raise HTTPException(403, "Partner portal access only")
    partner = await db.get(Partner, user.partner_id)
    if partner is None or partner.status != "active":
        raise HTTPException(403, "Partner agreement inactive")
    return user, partner


@portal.get("/me")
async def portal_me(ctx=Depends(partner_user)):
    user, partner = ctx
    return {"user": {"id": user.id, "full_name": user.full_name, "email": user.email}, "partner": prm.partner_out(partner)}


@portal.get("/registrations")
async def portal_registrations(ctx=Depends(partner_user), db: AsyncSession = Depends(get_db)):
    _, partner = ctx
    rows = (await db.execute(select(DealRegistration).where(DealRegistration.partner_id == partner.id).order_by(DealRegistration.created_at.desc()))).scalars().unique().all()
    out = []
    for r in rows:
        item = prm.registration_out(r)
        # partners see that a conflict exists, not internal account details
        item["conflicts"] = [{"type": c["type"], "severity": c["severity"]} for c in r.conflicts]
        out.append(item)
    return out


@portal.post("/registrations", status_code=201)
async def portal_register(body: RegistrationIn, ctx=Depends(partner_user), db: AsyncSession = Depends(get_db)):
    user, partner = ctx
    try:
        reg = await prm.submit(db, partner, user, {**body.model_dump(), "domain": body.domain.lower().removeprefix("www."), "currency": body.currency.upper()})
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    await db.commit()
    return {"id": reg.id, "status": reg.status, "conflict_detected": bool(reg.conflicts)}


@portal.get("/commissions")
async def portal_commissions(ctx=Depends(partner_user), db: AsyncSession = Depends(get_db)):
    _, partner = ctx
    report = await prm.commission_report(db, partner.id)
    return {"summary": report["partners"][0] if report["partners"] else None,
            "lines": [{k: v for k, v in line.items() if k not in ("deal_id",)} for line in report["lines"]]}


@portal.get("/collateral")
async def portal_collateral(ctx=Depends(partner_user), db: AsyncSession = Depends(get_db)):
    _, partner = ctx
    rows = (await db.execute(select(Collateral).where(Collateral.is_published.is_(True)))).scalars().unique().all()
    return [_collateral_out(c) for c in rows if prm.can_access_collateral(c, partner)]


@portal.get("/collateral/{collateral_id}/download")
async def portal_download(collateral_id: uuid.UUID, ctx=Depends(partner_user), db: AsyncSession = Depends(get_db)):
    user, partner = ctx
    item = await db.get(Collateral, collateral_id)
    if item is None or not prm.can_access_collateral(item, partner):
        raise HTTPException(404, "Not available to your partner tier or domain")
    att = await db.get(Attachment, item.attachment_id)
    db.add(CollateralDownload(collateral_id=item.id, user_id=user.id, partner_id=partner.id))
    await db.commit()
    return Response(storage.read(att), media_type=att.content_type, headers={"Content-Disposition": f'attachment; filename="{att.filename}"'})
