"""Custom objects: admin-defined record types with their own typed fields.

An object's fields are custom field definitions with entity ``object:<key>`` (typed, validated, field-level
security). Records have a name, an optional account and an owner. Access uses the ``custom_objects``
permission; with ``own`` scope a user sees records they own or that sit on an account they can see. Every
object is also a report source (``obj_<key>``), so it can be reported on, used in dashboards, list views and
validation rules.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import Principal, authorize, get_principal
from app.models import Account, CustomFieldDefinition, CustomObject, CustomRecord, Dashboard, ListView, SavedReport, User, ValidationRule
from app.services import custom_fields, reporting

router = APIRouter(prefix="/objects", tags=["custom objects"])
KEY = r"^[a-z][a-z0-9_]{1,30}$"
RESERVED = {"account", "accounts", "contact", "contacts", "deal", "deals", "lead", "leads", "case", "cases", "task", "tasks",
            "quote", "quotes", "order", "orders", "user", "users", "campaign", "campaigns", "activity", "activities"}
MAX_OBJECTS = 50


class ObjectIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    plural_label: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)


class ObjectCreate(ObjectIn):
    key: str = Field(pattern=KEY)


class RecordIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    account_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    data: dict = Field(default_factory=dict)


class RecordPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    account_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    data: dict | None = None


def _entity(obj: CustomObject) -> str:
    return f"object:{obj.key}"


def _object_out(o: CustomObject, count: int | None = None) -> dict:
    out = {"id": o.id, "key": o.key, "label": o.label, "plural_label": o.plural_label, "description": o.description,
           "source": f"{reporting.OBJECT_PREFIX}{o.key}", "fields": custom_fields.definitions_out(_entity(o))}
    if count is not None:
        out["record_count"] = count
    return out


async def _object(db: AsyncSession, key: str) -> CustomObject:
    o = (await db.execute(select(CustomObject).where(CustomObject.key == key))).scalar_one_or_none()
    if o is None:
        raise HTTPException(404, "No such object")
    return o


def _scoped(p: Principal, stmt):
    if not p.is_own_scope("custom_objects"):
        return stmt
    return stmt.where(or_(CustomRecord.owner_id == p.id, CustomRecord.account_id.in_(p.owned_account_ids())))


async def _record(db: AsyncSession, p: Principal, obj: CustomObject, record_id: uuid.UUID) -> CustomRecord:
    r = (await db.execute(_scoped(p, select(CustomRecord).where(CustomRecord.id == record_id, CustomRecord.object_id == obj.id)))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, "Record not found")
    return r


async def _records_out(db: AsyncSession, obj: CustomObject, rows: list[CustomRecord]) -> list[dict]:
    acc_ids = {r.account_id for r in rows if r.account_id}
    user_ids = {r.owner_id for r in rows if r.owner_id}
    accounts = dict((await db.execute(select(Account.id, Account.name).where(Account.id.in_(acc_ids)))).all()) if acc_ids else {}
    users = dict((await db.execute(select(User.id, User.full_name).where(User.id.in_(user_ids)))).all()) if user_ids else {}
    return [{"id": r.id, "object": obj.key, "name": r.name,
             "account": {"id": r.account_id, "name": accounts.get(r.account_id)} if r.account_id else None,
             "owner": {"id": r.owner_id, "full_name": users.get(r.owner_id)} if r.owner_id else None,
             "data": custom_fields.redact(_entity(obj), r.data), "created_at": r.created_at, "updated_at": r.updated_at} for r in rows]


async def _check_links(db: AsyncSession, p: Principal, account_id, owner_id) -> None:
    if account_id is not None:
        if await db.get(Account, account_id) is None:
            raise HTTPException(422, "Account not found")
        await p.ensure_account(db, account_id, "custom_objects")
    if owner_id is not None:
        u = await db.get(User, owner_id)
        if u is None or not u.is_active or u.role == "partner":
            raise HTTPException(422, "Owner must be an active internal user")
        if p.is_own_scope("custom_objects") and owner_id != p.id:
            raise HTTPException(403, "Your role can only own its own records")


# ---- definitions -------------------------------------------------------------------------------------

@router.get("")
async def list_objects(db: AsyncSession = Depends(get_db), p: Principal = Depends(get_principal)):
    """The custom objects the caller can use (empty without read access), with their fields as the caller may use them."""
    if not p.can("custom_objects", "read"):
        return []
    rows = (await db.execute(select(CustomObject).order_by(CustomObject.plural_label))).scalars().all()
    return [_object_out(o) for o in rows]


@router.post("", status_code=201)
async def create_object(body: ObjectCreate, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("admin", "create"))):
    if body.key in RESERVED:
        raise HTTPException(422, f"'{body.key}' is a built-in record type; choose another key")
    if (await db.execute(select(CustomObject.id).where(CustomObject.key == body.key))).first():
        raise HTTPException(409, "An object with this key already exists")
    if (await db.execute(select(func.count(CustomObject.id)))).scalar_one() >= MAX_OBJECTS:
        raise HTTPException(422, f"At most {MAX_OBJECTS} custom objects")
    o = CustomObject(**body.model_dump())
    db.add(o)
    await db.commit()
    await reporting.refresh_custom_fields(db, force=True)
    return _object_out(o, 0)


@router.put("/{key}")
async def update_object(key: str, body: ObjectIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("admin", "update"))):
    o = await _object(db, key)
    o.label, o.plural_label, o.description = body.label, body.plural_label, body.description
    await db.commit()
    await db.refresh(o)
    await reporting.refresh_custom_fields(db, force=True)
    return _object_out(o)


@router.delete("/{key}", status_code=204)
async def delete_object(key: str, confirm: bool = False, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("admin", "delete"))):
    """Delete an object with its records, fields, validation rules, list views and reports (removed from dashboards).
    With records, ``confirm=true`` is required."""
    o = await _object(db, key)
    n = (await db.execute(select(func.count(CustomRecord.id)).where(CustomRecord.object_id == o.id))).scalar_one()
    if n and not confirm:
        raise HTTPException(409, f"{o.plural_label} has {n} record{'s' if n != 1 else ''}; confirm to delete them too")
    source = f"{reporting.OBJECT_PREFIX}{o.key}"
    await db.execute(delete(CustomFieldDefinition).where(CustomFieldDefinition.entity == _entity(o)))
    await db.execute(delete(ValidationRule).where(ValidationRule.entity == source))
    await db.execute(delete(ListView).where(ListView.source == source))
    gone = {str(i) for i in (await db.execute(select(SavedReport.id).where(SavedReport.source == source))).scalars()}
    if gone:
        for d in (await db.execute(select(Dashboard))).scalars():
            if any(t.get("report_id") in gone for t in d.tiles or []):
                d.tiles = [t for t in d.tiles if t.get("report_id") not in gone]
        await db.execute(delete(SavedReport).where(SavedReport.source == source))
    await db.delete(o)  # records cascade
    await db.commit()
    await reporting.refresh_custom_fields(db, force=True)


# ---- records -----------------------------------------------------------------------------------------

@router.get("/{key}/records")
async def list_records(key: str, account_id: uuid.UUID | None = None, q: str | None = Query(default=None, max_length=100),
                       limit: int = Query(default=100, ge=1, le=500), db: AsyncSession = Depends(get_db),
                       p: Principal = Depends(authorize("custom_objects", "read"))):
    obj = await _object(db, key)
    stmt = _scoped(p, select(CustomRecord).where(CustomRecord.object_id == obj.id))
    if account_id:
        stmt = stmt.where(CustomRecord.account_id == account_id)
    if q:
        stmt = stmt.where(CustomRecord.name.ilike(f"%{q.replace('%', '').replace('_', ' ')}%"))
    rows = (await db.execute(stmt.order_by(CustomRecord.updated_at.desc()).limit(limit))).scalars().all()
    return {"object": _object_out(obj), "records": await _records_out(db, obj, list(rows))}


@router.post("/{key}/records", status_code=201)
async def create_record(key: str, body: RecordIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("custom_objects", "create"))):
    obj = await _object(db, key)
    owner = body.owner_id or p.id
    await _check_links(db, p, body.account_id, owner)
    try:
        data = await custom_fields.validate(db, _entity(obj), body.data, {}, partial=False)
    except custom_fields.CustomFieldError as e:
        raise HTTPException(422, str(e))
    r = CustomRecord(object_id=obj.id, name=body.name.strip(), account_id=body.account_id, owner_id=owner, data=data, created_by=p.id)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return (await _records_out(db, obj, [r]))[0]


@router.get("/{key}/records/{record_id}")
async def get_record(key: str, record_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("custom_objects", "read"))):
    obj = await _object(db, key)
    r = await _record(db, p, obj, record_id)
    return {"object": _object_out(obj), "record": (await _records_out(db, obj, [r]))[0],
            "can_edit": p.can("custom_objects", "update"), "can_delete": p.can("custom_objects", "delete")}


@router.patch("/{key}/records/{record_id}")
async def update_record(key: str, record_id: uuid.UUID, body: RecordPatch, db: AsyncSession = Depends(get_db),
                        p: Principal = Depends(authorize("custom_objects", "update"))):
    obj = await _object(db, key)
    r = await _record(db, p, obj, record_id)
    sent = body.model_fields_set
    await _check_links(db, p, body.account_id if "account_id" in sent else None, body.owner_id if "owner_id" in sent and body.owner_id else None)
    if "name" in sent and body.name:
        r.name = body.name.strip()
    if "account_id" in sent:
        r.account_id = body.account_id
    if "owner_id" in sent and body.owner_id:
        r.owner_id = body.owner_id
    if "data" in sent and body.data is not None:
        try:
            r.data = await custom_fields.validate(db, _entity(obj), body.data, r.data, partial=False)
        except custom_fields.CustomFieldError as e:
            raise HTTPException(422, str(e))
    await db.commit()
    await db.refresh(r)
    return (await _records_out(db, obj, [r]))[0]


@router.delete("/{key}/records/{record_id}", status_code=204)
async def delete_record(key: str, record_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("custom_objects", "delete"))):
    obj = await _object(db, key)
    r = await _record(db, p, obj, record_id)
    await db.delete(r)
    await db.commit()
