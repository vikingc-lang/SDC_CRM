import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_writer
from app.models import Account, Contact, User
from app.schemas.crm import ContactCreate, ContactUpdate
from app.services import scoring
from app.services.serializers import contact_out

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("")
async def list_contacts(
    search: str | None = None,
    account_id: uuid.UUID | None = None,
    buying_role: str | None = None,
    limit: int = Query(200, le=500),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Contact, Account.name).join(Account, Contact.account_id == Account.id).order_by(Contact.last_name, Contact.first_name).limit(limit)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Contact.first_name.ilike(like), Contact.last_name.ilike(like), Contact.email.ilike(like), Contact.job_title.ilike(like), Account.name.ilike(like)))
    if account_id:
        stmt = stmt.where(Contact.account_id == account_id)
    if buying_role:
        stmt = stmt.where(Contact.buying_role == buying_role)
    return [contact_out(c, name) for c, name in (await db.execute(stmt)).all()]


@router.post("", status_code=201)
async def create_contact(body: ContactCreate, db: AsyncSession = Depends(get_db), _: User = Depends(require_writer)):
    if await db.get(Account, body.account_id) is None:
        raise HTTPException(404, "Account not found")
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
async def update_contact(contact_id: uuid.UUID, body: ContactUpdate, db: AsyncSession = Depends(get_db), _: User = Depends(require_writer)):
    contact = await db.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(404, "Contact not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(contact, field, value)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(409, "A contact with this email already exists")
    await scoring.rescore_account(db, contact.account_id)  # buying role changes move deal risk
    await db.commit()
    return contact_out(contact)


@router.delete("/{contact_id}", status_code=204)
async def delete_contact(contact_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = Depends(require_writer)):
    contact = await db.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(404, "Contact not found")
    account_id = contact.account_id
    await db.delete(contact)
    await db.flush()
    await scoring.rescore_account(db, account_id)
    await db.commit()
