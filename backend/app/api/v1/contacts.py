import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import Principal, authorize
from app.models import Account, Activity, ConsentEvent, Contact, CustomFieldDefinition, ErasureLog
from app.schemas.ai import BuyingRole
from app.services import custom_fields, privacy, scoring
from app.services.serializers import activity_out, contact_out

router = APIRouter(prefix="/contacts", tags=["contacts"])


class ContactCreate(BaseModel):
    account_id: uuid.UUID
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(default="", max_length=100)
    email: EmailStr | None = None
    phone: str | None = None
    mobile: str | None = None
    linkedin_url: str | None = None
    timezone: str | None = None
    department: str | None = None
    job_title: str | None = None
    buying_role: BuyingRole = "Evaluator"
    privacy_regime: Literal["GDPR", "CCPA", "OTHER"] | None = None


class ContactUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    mobile: str | None = None
    linkedin_url: str | None = None
    timezone: str | None = None
    department: str | None = None
    job_title: str | None = None
    buying_role: BuyingRole | None = None
    status: Literal["active", "departed"] | None = None
    custom_fields: dict | None = None


class ConsentUpdate(BaseModel):
    consent_email: Literal["granted", "denied", "unknown"] | None = None
    basis: Literal["consent", "legitimate_interest", "contract", "legal_obligation", ""] | None = None
    regime: Literal["GDPR", "CCPA", "OTHER", ""] | None = None
    opt_out_email: bool | None = None
    opt_out_phone: bool | None = None
    opt_out_sms: bool | None = None
    do_not_sell: bool | None = None
    source: str = Field(default="crm_user", max_length=60)


class EraseRequest(BaseModel):
    regulation: Literal["GDPR", "CCPA", "OTHER"] | None = None
    confirm: bool = False


async def _get(db: AsyncSession, p: Principal, contact_id: uuid.UUID) -> Contact:
    contact = await db.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(404, "Contact not found")
    await p.ensure_account(db, contact.account_id, "contacts")
    return contact


@router.get("")
async def list_contacts(
    search: str | None = None,
    account_id: uuid.UUID | None = None,
    buying_role: str | None = None,
    status: Literal["active", "departed", "erased", "all"] = "active",
    limit: int = Query(200, le=500),
    db: AsyncSession = Depends(get_db),
    p: Principal = Depends(authorize("contacts", "read")),
):
    stmt = select(Contact, Account.name).join(Account, Contact.account_id == Account.id).order_by(Contact.last_name, Contact.first_name).limit(limit)
    stmt = p.scope_accounts(stmt, "contacts", Contact.account_id)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Contact.first_name.ilike(like), Contact.last_name.ilike(like), Contact.email.ilike(like),
                              Contact.job_title.ilike(like), Contact.department.ilike(like), Account.name.ilike(like)))
    if account_id:
        stmt = stmt.where(Contact.account_id == account_id)
    if buying_role:
        stmt = stmt.where(Contact.buying_role == buying_role)
    if status != "all":
        stmt = stmt.where(Contact.status == status)
    return [contact_out(c, name) for c, name in (await db.execute(stmt)).all()]


@router.get("/{contact_id}")
async def get_contact(contact_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("contacts", "read"))):
    contact = await _get(db, p, contact_id)
    account = await db.get(Account, contact.account_id)
    acts = (await db.execute(select(Activity).where(Activity.contact_id == contact_id).order_by(Activity.occurred_at.desc()).limit(40))).scalars().unique().all()
    events = (await db.execute(select(ConsentEvent).where(ConsentEvent.contact_id == contact_id).order_by(ConsentEvent.created_at.desc()))).scalars().all()
    erasure = (await db.execute(select(ErasureLog).where(ErasureLog.contact_id == contact_id))).scalars().first()
    defs = (await db.execute(select(CustomFieldDefinition).where(CustomFieldDefinition.entity == "contact"))).scalars().all()
    allowed = {ch: dict(zip(("allowed", "reason"), privacy.can_contact(contact, ch))) for ch in privacy.CHANNELS}
    return {
        **contact_out(contact, account.name),
        "activities": [activity_out(a) for a in acts],
        "consent_events": [{"id": e.id, "event_type": e.event_type, "channel": e.channel, "regulation": e.regulation, "source": e.source,
                            "details": e.details, "created_at": e.created_at} for e in events],
        "erasure": {"subject_hash": erasure.subject_hash, "fields_erased": erasure.fields_erased, "regulation": erasure.regulation,
                    "created_at": erasure.created_at} if erasure else None,
        "channel_permissions": allowed,
        "custom_field_definitions": [{"key": d.key, "label": d.label, "field_type": d.field_type, "options": d.options} for d in defs],
    }


@router.post("", status_code=201)
async def create_contact(body: ContactCreate, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("contacts", "create"))):
    if await db.get(Account, body.account_id) is None:
        raise HTTPException(404, "Account not found")
    await p.ensure_account(db, body.account_id, "contacts")
    contact = Contact(**body.model_dump())
    db.add(contact)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(409, "A contact with this email already exists")
    await scoring.rescore_account(db, body.account_id)
    await db.commit()
    return contact_out(contact)


@router.patch("/{contact_id}")
async def update_contact(contact_id: uuid.UUID, body: ContactUpdate, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("contacts", "update"))):
    contact = await _get(db, p, contact_id)
    if contact.status == "erased":
        raise HTTPException(409, "Erased contacts cannot be edited")
    data = body.model_dump(exclude_unset=True)
    if "custom_fields" in data:
        try:
            contact.custom_fields = await custom_fields.validate(db, "contact", data.pop("custom_fields"), contact.custom_fields)
        except custom_fields.CustomFieldError as exc:
            raise HTTPException(422, str(exc))
    if data.get("status") == "departed" and contact.status != "departed":
        contact.departed_at = datetime.now(timezone.utc)  # champion turnover feeds churn early-warning
    elif data.get("status") == "active":
        contact.departed_at = None
    for field, value in data.items():
        setattr(contact, field, value)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(409, "A contact with this email already exists")
    await scoring.rescore_account(db, contact.account_id)
    await db.commit()
    return contact_out(contact)


@router.post("/{contact_id}/consent")
async def update_consent(contact_id: uuid.UUID, body: ConsentUpdate, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("contacts", "update"))):
    contact = await _get(db, p, contact_id)
    if contact.status == "erased":
        raise HTTPException(409, "Contact was erased")
    await privacy.record_consent(db, contact, consent_email=body.consent_email, basis=body.basis, regime=body.regime,
                                 opt_outs={"email": body.opt_out_email, "phone": body.opt_out_phone, "sms": body.opt_out_sms},
                                 do_not_sell=body.do_not_sell, source=body.source)
    await db.commit()
    return contact_out(contact)


@router.post("/{contact_id}/erase")
async def erase_contact(contact_id: uuid.UUID, body: EraseRequest, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("contacts", "delete"))):
    """Right to erasure (GDPR Art. 17 / CCPA deletion): anonymise + crypto-shred the audit trail."""
    if not body.confirm:
        raise HTTPException(422, "Set confirm=true to erase this person's personal data irreversibly")
    contact = await _get(db, p, contact_id)
    try:
        entry = await privacy.erase_contact(db, contact, body.regulation)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    await scoring.rescore_account(db, contact.account_id)
    await db.commit()
    return {"status": "erased", "subject_hash": entry.subject_hash, "fields_erased": entry.fields_erased, "key_destroyed": entry.key_destroyed}


@router.delete("/{contact_id}", status_code=204)
async def delete_contact(contact_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("contacts", "delete"))):
    contact = await _get(db, p, contact_id)
    account_id = contact.account_id
    await db.delete(contact)
    await db.flush()
    await scoring.rescore_account(db, account_id)
    await db.commit()
