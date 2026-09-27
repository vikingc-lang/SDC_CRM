"""Admin setup: validation rules, account sharing rules and configuration export / import."""
import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.database import get_db
from app.core.rbac import ROLE_LABELS, Principal, authorize, authorize_person, invalidate_shares
from app.models import Account, SharingRule, ValidationRule
from app.services import config_bundle, reporting, validation
from app.services.config_bundle import BundleError

router = APIRouter(prefix="/admin", tags=["admin setup"])


class RuleIn(BaseModel):
    entity: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    conditions: list[dict] = Field(min_length=1, max_length=20)
    message: str = Field(min_length=1, max_length=300)
    applies_on: Literal["create", "update", "both"] = "both"
    active: bool = True


class RuleTest(BaseModel):
    entity: str
    conditions: list[dict] = Field(min_length=1, max_length=20)


class ShareIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    criteria: list[dict] = Field(min_length=1, max_length=20)
    roles: list[str] = Field(min_length=1, max_length=10)
    active: bool = True


class ShareTest(BaseModel):
    criteria: list[dict] = Field(min_length=1, max_length=20)


class ImportIn(BaseModel):
    bundle: dict
    dry_run: bool = True


def _bad(e: Exception) -> HTTPException:
    return HTTPException(422, str(e))


def _rule_out(r: ValidationRule) -> dict:
    return {"id": r.id, "entity": r.entity, "name": r.name, "description": r.description, "conditions": r.conditions, "message": r.message,
            "applies_on": r.applies_on, "active": r.active, "updated_at": r.updated_at}


def _share_out(r: SharingRule) -> dict:
    return {"id": r.id, "name": r.name, "description": r.description, "criteria": r.criteria, "roles": r.roles, "active": r.active,
            "updated_at": r.updated_at}


# ---- validation rules ------------------------------------------------------------------------------

@router.get("/validation-rules")
async def list_rules(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("admin", "read"))):
    rows = (await db.execute(select(ValidationRule).order_by(ValidationRule.entity, ValidationRule.name))).scalars().all()
    await reporting.refresh_custom_fields(db)
    entities = [{"key": k, "label": reporting.src_of(k).label} for k in reporting.source_keys()
                if k in validation.SOURCES or k.startswith(reporting.OBJECT_PREFIX)]
    return {"rules": [_rule_out(r) for r in rows], "entities": entities}


async def _save_rule(db: AsyncSession, body: RuleIn, row: ValidationRule | None) -> ValidationRule:
    await reporting.refresh_custom_fields(db)
    try:
        config_bundle.check_validation_rule(body.model_dump())
    except (BundleError, reporting.ReportError) as e:
        raise _bad(e)
    if row is None:
        row = ValidationRule(**body.model_dump())
        db.add(row)
    else:
        for k, v in body.model_dump().items():
            setattr(row, k, v)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "A rule with this name already exists for this record type")
    await db.refresh(row)
    return row


@router.post("/validation-rules", status_code=201)
async def create_rule(body: RuleIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("admin", "update"))):
    return _rule_out(await _save_rule(db, body, None))


@router.put("/validation-rules/{rule_id}")
async def update_rule(rule_id: uuid.UUID, body: RuleIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("admin", "update"))):
    row = await db.get(ValidationRule, rule_id)
    if row is None:
        raise HTTPException(404, "Rule not found")
    return _rule_out(await _save_rule(db, body, row))


@router.delete("/validation-rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("admin", "update"))):
    row = await db.get(ValidationRule, rule_id)
    if row is not None:
        await db.delete(row)
        await db.commit()


@router.post("/validation-rules/test")
async def test_rule(body: RuleTest, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("admin", "read"))):
    """How many existing records already break the rule (they can't be saved until fixed), with a few examples."""
    try:
        await reporting.refresh_custom_fields(db)
        reporting.validate_filters(body.entity, body.conditions)
        return await validation.violations(db, body.entity, body.conditions)
    except reporting.ReportError as e:
        raise _bad(e)


# ---- account sharing rules ---------------------------------------------------------------------------

@router.get("/sharing-rules")
async def list_shares(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("admin", "read"))):
    rows = (await db.execute(select(SharingRule).order_by(SharingRule.name))).scalars().all()
    roles = [{"key": r, "label": ROLE_LABELS[r]} for r in ROLE_LABELS if r not in config_bundle.PROTECTED_ROLES]
    return {"rules": [_share_out(r) for r in rows], "roles": roles}


async def _save_share(db: AsyncSession, body: ShareIn, row: SharingRule | None) -> SharingRule:
    await reporting.refresh_custom_fields(db)
    try:
        config_bundle.check_sharing_rule(body.model_dump())
    except (BundleError, reporting.ReportError) as e:
        raise _bad(e)
    if row is None:
        row = SharingRule(**body.model_dump())
        db.add(row)
    else:
        for k, v in body.model_dump().items():
            setattr(row, k, v)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "A sharing rule with this name already exists")
    invalidate_shares()
    await db.refresh(row)
    return row


@router.post("/sharing-rules", status_code=201)
async def create_share(body: ShareIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize_person("admin", "update"))):
    r = await _save_share(db, body, None)
    log_action(db, "sharing_rule", "sharing_rules", r.id, f"{r.name} -> {', '.join(r.roles)}")
    await db.commit()
    return _share_out(r)


@router.put("/sharing-rules/{rule_id}")
async def update_share(rule_id: uuid.UUID, body: ShareIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize_person("admin", "update"))):
    row = await db.get(SharingRule, rule_id)
    if row is None:
        raise HTTPException(404, "Rule not found")
    r = await _save_share(db, body, row)
    log_action(db, "sharing_rule", "sharing_rules", r.id, f"{r.name} -> {', '.join(r.roles)}{'' if r.active else ' (inactive)'}")
    await db.commit()
    return _share_out(r)


@router.delete("/sharing-rules/{rule_id}", status_code=204)
async def delete_share(rule_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize_person("admin", "update"))):
    row = await db.get(SharingRule, rule_id)
    if row is not None:
        log_action(db, "sharing_rule", "sharing_rules", row.id, f"{row.name} deleted")
        await db.delete(row)
        await db.commit()
        invalidate_shares()


@router.post("/sharing-rules/test")
async def test_share(body: ShareTest, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("admin", "read"))):
    """How many accounts the criteria match today."""
    try:
        await reporting.refresh_custom_fields(db)
        reporting.validate_filters("accounts", body.criteria)
    except reporting.ReportError as e:
        raise _bad(e)
    q = reporting.criteria_ids("accounts", body.criteria)
    n = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    names = (await db.execute(select(Account.name).where(Account.id.in_(q)).order_by(Account.name).limit(5))).scalars().all()
    return {"count": n, "examples": names}


# ---- configuration export / import -------------------------------------------------------------------

@router.get("/config/export")
async def export_config(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize_person("admin", "read"))):
    bundle = await config_bundle.export(db)
    log_action(db, "config_export", "config", None, ", ".join(f"{len(bundle[s])} {s}" for s in config_bundle.SECTIONS))
    await db.commit()
    return JSONResponse(bundle, headers={"Content-Disposition": f'attachment; filename="cirra-config-{date.today().isoformat()}.json"'})


@router.post("/config/import")
async def import_config(body: ImportIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize_person("admin", "update"))):
    """Preview (``dry_run``, the default) or apply a configuration bundle. Changing permissions and sharing needs a Super Admin."""
    if p.user.role != "super_admin":
        raise HTTPException(403, "Only a Super Admin can import configuration")
    try:
        out = await config_bundle.apply(db, p.user, body.bundle, dry_run=body.dry_run)
    except BundleError as e:
        raise _bad(e)
    if out["applied"]:
        log_action(db, "config_import", "config", None, ", ".join(f"{v} {k}" for k, v in out["summary"].items()))
        await db.commit()
    return out
