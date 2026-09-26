import json
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.database import get_db
from app.core.rbac import ACTIONS, RESOURCES, ROLE_LABELS, ROLES, Principal, authorize, invalidate_cache, load_matrix
from app.core.security import hash_password
from app.models import (
    Account, AuditLog, ConsentEvent, Contact, CustomFieldDefinition, DedupDismissal, ErasureLog, MergeLog, RolePermission, User,
)
from app.services import data_io, dedup, privacy

router = APIRouter(prefix="/admin", tags=["admin"])


class PermissionIn(BaseModel):
    role: str
    resource: str
    can_create: bool = False
    can_read: bool = False
    can_update: bool = False
    can_delete: bool = False
    can_export: bool = False
    scope: Literal["all", "own"] = "own"


class UserIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2)
    role: str
    password: str | None = Field(default=None, min_length=8)
    manager_id: uuid.UUID | None = None
    partner_id: uuid.UUID | None = None
    is_active: bool = True


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    manager_id: uuid.UUID | None = None
    partner_id: uuid.UUID | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8)


class CustomFieldIn(BaseModel):
    entity: Literal["account", "contact", "deal"]
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=120)
    field_type: Literal["text", "number", "date", "select", "boolean", "url"]
    options: list[str] = []
    required: bool = False


class MergeIn(BaseModel):
    entity: Literal["account", "contact"]
    survivor_id: uuid.UUID
    merged_id: uuid.UUID
    overrides: dict[str, Literal["survivor", "merged"]] = {}


class DismissIn(BaseModel):
    entity: Literal["account", "contact"]
    id_a: uuid.UUID
    id_b: uuid.UUID


# ---- RBAC ------------------------------------------------------------------------------------
@router.get("/permissions")
async def permissions(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "read"))):
    matrix = {}
    for role in ROLES:
        m = await load_matrix(db, role)
        matrix[role] = {res: {a: m[res].allows(a) for a in ACTIONS} | {"scope": m[res].scope} if res in m else
                        {a: False for a in ACTIONS} | {"scope": "own"} for res in RESOURCES}
    return {"roles": [{"key": r, "label": ROLE_LABELS[r]} for r in ROLES], "resources": list(RESOURCES), "actions": list(ACTIONS), "matrix": matrix}


@router.put("/permissions")
async def set_permissions(rows: list[PermissionIn], db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    for r in rows:
        if r.role not in ROLES or r.resource not in RESOURCES:
            raise HTTPException(422, f"Unknown role/resource {r.role}/{r.resource}")
        if r.role == "super_admin" and r.resource == "admin" and not (r.can_read and r.can_update):
            raise HTTPException(422, "Super Admin must keep admin access (lock-out protection)")
        existing = await db.get(RolePermission, (r.role, r.resource))
        if existing is None:
            db.add(RolePermission(**r.model_dump()))
        else:
            for k, v in r.model_dump().items():
                setattr(existing, k, v)
    await db.commit()
    invalidate_cache()
    return {"updated": len(rows)}


@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "read"))):
    users = (await db.execute(select(User).order_by(User.full_name))).scalars().all()
    return [{"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role, "manager_id": u.manager_id, "partner_id": u.partner_id,
             "is_active": u.is_active, "created_at": u.created_at} for u in users]


@router.post("/users", status_code=201)
async def create_user(body: UserIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "create"))):
    if body.role not in ROLES:
        raise HTTPException(422, "Unknown role")
    if body.role == "partner" and not body.partner_id:
        raise HTTPException(422, "Partner users need a partner organisation")
    if (await db.execute(select(User.id).where(User.email == body.email.lower()))).first():
        raise HTTPException(409, "A user with this email exists")
    import secrets

    temp = body.password or secrets.token_urlsafe(12)
    user = User(email=body.email.lower(), full_name=body.full_name, role=body.role, password_hash=hash_password(temp),
                manager_id=body.manager_id, partner_id=body.partner_id, is_active=body.is_active)
    db.add(user)
    await db.commit()
    return {"id": user.id, "temporary_password": None if body.password else temp}


@router.patch("/users/{user_id}")
async def update_user(user_id: uuid.UUID, body: UserUpdate, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("admin", "update"))):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    data = body.model_dump(exclude_unset=True)
    if data.get("role") and data["role"] not in ROLES:
        raise HTTPException(422, "Unknown role")
    if user.id == p.id and (data.get("is_active") is False or (data.get("role") and data["role"] != user.role)):
        raise HTTPException(422, "You cannot deactivate or demote yourself")
    if data.get("manager_id") == user.id:
        raise HTTPException(422, "A user cannot manage themselves")
    if "password" in data:
        pw = data.pop("password")
        if pw:
            user.password_hash = hash_password(pw)
    for k, v in data.items():
        setattr(user, k, v)
    await db.commit()
    return {"status": "ok"}


# ---- audit & compliance -----------------------------------------------------------------------------
@router.get("/audit")
async def audit_log(entity: str | None = None, record_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None,
                    action: str | None = None, field: str | None = None, before_id: int | None = None, limit: int = 100,
                    db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("audit", "read"))):
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500))
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    if record_id:
        stmt = stmt.where(AuditLog.record_id == record_id)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if field:
        stmt = stmt.where(AuditLog.field_name == field)
    if before_id:
        stmt = stmt.where(AuditLog.id < before_id)
    rows = (await db.execute(stmt)).scalars().all()
    users = {u.id: u.full_name for u in (await db.execute(select(User))).scalars().all()}
    out = await privacy.readable_audit(db, rows)
    for r in out:
        r["user"] = users.get(r["user_id"], "system") if r["user_id"] else "system"
    return {"items": out, "next_before_id": rows[-1].id if rows else None}


@router.get("/compliance")
async def compliance(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("audit", "read"))):
    erasures = (await db.execute(select(ErasureLog).order_by(ErasureLog.created_at.desc()).limit(100))).scalars().all()
    events = (await db.execute(select(ConsentEvent).order_by(ConsentEvent.created_at.desc()).limit(200))).scalars().all()
    contacts = {c.id: c.full_name for c in (await db.execute(select(Contact))).scalars().all()}
    stats = {}
    for c in (await db.execute(select(Contact.consent_email, Contact.opt_out_email, Contact.privacy_regime))).all():
        stats.setdefault("consent_" + c.consent_email, 0)
        stats["consent_" + c.consent_email] += 1
        stats["opted_out_email"] = stats.get("opted_out_email", 0) + int(c.opt_out_email)
        if c.privacy_regime:
            stats[c.privacy_regime] = stats.get(c.privacy_regime, 0) + 1
    return {"stats": stats,
            "erasures": [{"id": e.id, "contact_id": e.contact_id, "subject_hash": e.subject_hash, "fields_erased": e.fields_erased,
                          "regulation": e.regulation, "key_destroyed": e.key_destroyed, "created_at": e.created_at} for e in erasures],
            "consent_events": [{"id": e.id, "contact_id": e.contact_id, "contact": contacts.get(e.contact_id), "event_type": e.event_type,
                                "channel": e.channel, "regulation": e.regulation, "source": e.source, "created_at": e.created_at} for e in events]}


# ---- custom fields -----------------------------------------------------------------------------------
@router.get("/custom-fields")
async def list_custom_fields(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("accounts", "read"))):
    rows = (await db.execute(select(CustomFieldDefinition).order_by(CustomFieldDefinition.entity, CustomFieldDefinition.label))).scalars().all()
    return [{"id": d.id, "entity": d.entity, "key": d.key, "label": d.label, "field_type": d.field_type, "options": d.options, "required": d.required} for d in rows]


@router.post("/custom-fields", status_code=201)
async def create_custom_field(body: CustomFieldIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "create"))):
    if body.field_type == "select" and not body.options:
        raise HTTPException(422, "Select fields need options")
    if body.key in ("health_breakdown", "domain_unverified", "source"):
        raise HTTPException(422, "Reserved key")
    if (await db.execute(select(CustomFieldDefinition.id).where(CustomFieldDefinition.entity == body.entity, CustomFieldDefinition.key == body.key))).first():
        raise HTTPException(409, "Field already exists")
    d = CustomFieldDefinition(**body.model_dump())
    db.add(d)
    await db.commit()
    return {"id": d.id}


@router.delete("/custom-fields/{field_id}", status_code=204)
async def delete_custom_field(field_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "delete"))):
    d = await db.get(CustomFieldDefinition, field_id)
    if d:
        await db.delete(d)  # stored values stay in JSONB; they simply become untyped
        await db.commit()


# ---- deduplication --------------------------------------------------------------------------------------
@router.get("/dedup")
async def dedup_candidates(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("accounts", "update"))):
    return {"accounts": await dedup.account_candidates(db), "contacts": await dedup.contact_candidates(db)}


@router.post("/dedup/merge")
async def merge(body: MergeIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("accounts", "update"))):
    model = Account if body.entity == "account" else Contact
    survivor, merged = await db.get(model, body.survivor_id), await db.get(model, body.merged_id)
    if survivor is None or merged is None:
        raise HTTPException(404, "Record not found")
    try:
        fn = dedup.merge_accounts if body.entity == "account" else dedup.merge_contacts
        log = await fn(db, survivor, merged, body.overrides, p.id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    from app.services import scoring

    await scoring.rescore_account(db, survivor.id if body.entity == "account" else survivor.account_id)
    await db.commit()
    return {"status": "merged", "merge_log_id": log.id, "field_resolution": log.field_resolution}


@router.post("/dedup/dismiss")
async def dismiss(body: DismissIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("accounts", "update"))):
    a, b = sorted((body.id_a, body.id_b), key=str)
    if await db.get(DedupDismissal, (body.entity, a, b)) is None:
        db.add(DedupDismissal(entity=body.entity, id_a=a, id_b=b, dismissed_by=p.id))
        await db.commit()
    return {"status": "dismissed"}


@router.post("/dedup/auto")
async def auto(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    return await dedup.auto_merge(db)


@router.get("/dedup/history")
async def merge_history(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("audit", "read"))):
    rows = (await db.execute(select(MergeLog).order_by(MergeLog.created_at.desc()).limit(100))).scalars().all()
    return [{"id": m.id, "entity": m.entity, "survivor_id": m.survivor_id, "merged_id": m.merged_id, "merged_name": m.snapshot.get("name") or
             f"{m.snapshot.get('first_name', '')} {m.snapshot.get('last_name', '')}".strip(), "score": float(m.score) if m.score else None,
             "automatic": m.automatic, "field_resolution": m.field_resolution, "created_at": m.created_at} for m in rows]


# ---- import / export ------------------------------------------------------------------------------------
Entity = Literal["accounts", "contacts", "deals", "products"]


@router.post("/import/{entity}/preview")
async def import_preview(entity: Entity, file: UploadFile = File(...), mapping: str | None = Form(default=None),
                         db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("data", "create"))):
    try:
        return await data_io.preview(db, entity, await file.read(), file.filename or "upload.csv", json.loads(mapping) if mapping else None)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(422, f"Could not read file: {exc}")


@router.post("/import/{entity}/commit")
async def import_commit(entity: Entity, file: UploadFile = File(...), mapping: str | None = Form(default=None),
                        db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("data", "create"))):
    try:
        result = await data_io.commit(db, entity, await file.read(), file.filename or "upload.csv", json.loads(mapping) if mapping else None, p.user)
    except data_io.ImportValidationError as exc:
        await db.rollback()
        raise HTTPException(422, {"message": str(exc), "errors": exc.errors[:100], "rolled_back": True})
    except (ValueError, json.JSONDecodeError) as exc:
        await db.rollback()
        raise HTTPException(422, f"Could not read file: {exc}")
    log_action(db, "import", entity, None, json.dumps(result))
    await db.commit()
    return result


@router.get("/export/{entity}")
async def export(entity: Entity, format: Literal["csv", "json"] = "csv", db: AsyncSession = Depends(get_db),
                 p: Principal = Depends(authorize("data", "export"))):
    resource = "products" if entity == "products" else entity
    if not p.can(resource, "export"):
        raise HTTPException(403, f"Your role cannot export {entity}")
    model = data_io.EXPORT_MODELS[entity]
    stmt = select(model)
    if entity == "accounts":
        stmt = p.scope_accounts(stmt)
    elif entity in ("contacts",):
        stmt = p.scope_accounts(stmt, "contacts", model.account_id)
    elif entity == "deals":
        stmt = p.scope_deals(stmt)
    rows = (await db.execute(stmt)).scalars().unique().all()
    data, media = data_io.serialize(rows, entity, format)
    log_action(db, "export", entity, None, f"{len(rows)} rows as {format}")
    await db.commit()
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{entity}-{datetime.now():%Y%m%d}.{format}"'})


# ---- background jobs on demand -------------------------------------------------------------------------------
@router.post("/jobs/{job}")
async def run_job(job: Literal["risk_scan", "escalations", "renewals", "rescore", "auto_dedup", "erp_sync", "erp_orders", "lead_rescore", "reindex"],
                  db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    from app.services import clm, erp, insights, scoring, sla

    if job == "risk_scan":
        return await insights.scan_pipeline(db)
    if job == "escalations":
        return await sla.escalate_overdue(db)
    if job == "renewals":
        stats = await clm.run_renewals(db)
        await db.commit()
        return stats
    if job == "rescore":
        return {"accounts_rescored": await scoring.rescore_all(db)}
    if job == "auto_dedup":
        return await dedup.auto_merge(db)
    if job == "erp_sync":
        run = await erp.sync_inbound(db)
        await db.commit()
        return {"status": run.status, "stats": run.stats, "error": run.error}
    if job == "erp_orders":
        from app.services import orders

        return await orders.process_queue(db)
    if job == "lead_rescore":
        from app.services import leads

        return await leads.rescore_open(db)
    if job == "reindex":
        return await insights.reindex(db)
