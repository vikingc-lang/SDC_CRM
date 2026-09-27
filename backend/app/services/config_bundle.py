"""Configuration export / import: move a tenant's setup between environments (sandbox -> production).

A bundle is JSON holding custom objects, custom fields (with field security), validation rules, account
sharing rules, workflow rules, role permissions and the team-shared reports, dashboards and list views.
Records (accounts, deals, ...) are never included.

Import matches items by natural key (object key; field entity + key; rule entity + name; names elsewhere;
role + resource), reports what it would create, update or leave unchanged, and applies all or nothing: the
whole bundle is staged in one transaction, each item validated against the catalogue as it will be (so a
rule may use a field the same bundle creates), and committed only when nothing failed and it isn't a dry run.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import ACTIONS, RESOURCES, ROLES, invalidate_cache, invalidate_shares
from app.models import (
    CustomFieldDefinition, CustomObject, Dashboard, ListView, RolePermission, SavedReport, SharingRule, User, ValidationRule, WorkflowRule,
)
from app.services import reporting

FORMAT, VERSION = "cirra-config", 1
SECTIONS = ("custom_objects", "custom_fields", "validation_rules", "sharing_rules", "workflows", "role_permissions",
            "reports", "dashboards", "list_views")
PROTECTED_ROLES = ("super_admin", "partner")  # super admins always have full access; partners use the portal


class BundleError(ValueError):
    pass


async def export(db: AsyncSession) -> dict:
    objs = (await db.execute(select(CustomObject).order_by(CustomObject.key))).scalars().all()
    fields = (await db.execute(select(CustomFieldDefinition).order_by(CustomFieldDefinition.entity, CustomFieldDefinition.key))).scalars().all()
    vrules = (await db.execute(select(ValidationRule).order_by(ValidationRule.entity, ValidationRule.name))).scalars().all()
    srules = (await db.execute(select(SharingRule).order_by(SharingRule.name))).scalars().all()
    flows = (await db.execute(select(WorkflowRule).order_by(WorkflowRule.name, WorkflowRule.created_at, WorkflowRule.id))).scalars().all()
    perms = (await db.execute(select(RolePermission).order_by(RolePermission.role, RolePermission.resource))).scalars().all()
    await reporting.refresh_custom_fields(db)
    reports = [r for r in (await db.execute(select(SavedReport).where(SavedReport.visibility == "shared")
                                            .order_by(SavedReport.name, SavedReport.created_at, SavedReport.id))).scalars().all()
               if reporting.src_of(r.source) is not None]  # a report on a deleted object can't be moved anywhere
    dashes = (await db.execute(select(Dashboard).where(Dashboard.visibility == "shared").order_by(Dashboard.name, Dashboard.created_at, Dashboard.id))).scalars().all()
    views = (await db.execute(select(ListView).where(ListView.visibility == "shared").order_by(ListView.source, ListView.name, ListView.created_at, ListView.id))).scalars().all()
    report_names, occurrence, counts = {}, {}, {}
    for r in reports:  # a tile names its report, plus which one when shared reports share a name
        report_names[str(r.id)], occurrence[str(r.id)] = r.name, counts.get(r.name, 0)
        counts[r.name] = counts.get(r.name, 0) + 1
    return {
        "format": FORMAT, "version": VERSION, "exported_at": datetime.now(timezone.utc).isoformat(),
        "custom_objects": [{"key": o.key, "label": o.label, "plural_label": o.plural_label, "description": o.description} for o in objs],
        "custom_fields": [{"entity": d.entity, "key": d.key, "label": d.label, "field_type": d.field_type, "options": d.options or [],
                           "required": d.required, "access": d.access or {}} for d in fields],
        "validation_rules": [{"entity": r.entity, "name": r.name, "description": r.description, "conditions": r.conditions, "message": r.message,
                              "applies_on": r.applies_on, "active": r.active} for r in vrules],
        "sharing_rules": [{"name": r.name, "description": r.description, "criteria": r.criteria, "roles": r.roles, "active": r.active} for r in srules],
        "workflows": [{"name": w.name, "description": w.description, "enabled": w.enabled, "source": w.source, "trigger": w.trigger,
                       "conditions": w.conditions, "actions": w.actions} for w in flows],
        "role_permissions": [{"role": r.role, "resource": r.resource, "can_create": r.can_create, "can_read": r.can_read, "can_update": r.can_update,
                              "can_delete": r.can_delete, "can_export": r.can_export, "scope": r.scope}
                             for r in perms if r.role not in PROTECTED_ROLES],
        "reports": [{"name": r.name, "description": r.description, "definition": r.definition} for r in reports],
        "dashboards": [{"name": d.name, "description": d.description,
                        "tiles": [{"report": report_names[t["report_id"]], "size": t.get("size", "half"),
                                   **({"occurrence": occurrence[t["report_id"]]} if occurrence[t["report_id"]] else {})}
                                  for t in d.tiles or [] if t["report_id"] in report_names]} for d in dashes],
        "list_views": [{"source": v.source, "name": v.name, "columns": v.columns, "filters": v.filters, "sort": v.sort} for v in views],
    }


class _Occurrences:
    """Match items by name when names can repeat: the n-th bundle item with a name updates the n-th existing row
    with it (same stable order as the export), so an unchanged export always re-imports as unchanged."""

    def __init__(self) -> None:
        self.seen: dict[tuple, int] = {}

    async def row(self, db: AsyncSession, key: tuple, stmt):
        n = self.seen.get(key, 0)
        self.seen[key] = n + 1
        rows = (await db.execute(stmt)).scalars().all()
        return rows[n] if n < len(rows) else None


def _same(row, item: dict, keys: tuple[str, ...]) -> bool:
    return all(getattr(row, k) == item.get(k) for k in keys)


class _Plan:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, section: str, key: str, action: str, detail: str = "") -> None:
        self.items.append({"section": section, "key": key, "action": action, "detail": detail})

    @property
    def errors(self) -> list[dict]:
        return [i for i in self.items if i["action"] == "error"]

    def summary(self) -> dict:
        out = {"create": 0, "update": 0, "unchanged": 0, "error": 0}
        for i in self.items:
            out[i["action"]] += 1
        return out


def _items(bundle: dict, section: str) -> list[dict]:
    v = bundle.get(section) or []
    if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
        raise BundleError(f"'{section}' must be a list of objects")
    return v


async def _upsert(db: AsyncSession, plan: _Plan, section: str, key: str, row, create, item: dict, keys: tuple[str, ...]) -> object:
    """Create ``create()`` when ``row`` is None, else copy ``keys`` from the item onto it."""
    if row is None:
        row = create()
        db.add(row)
        plan.add(section, key, "create")
    elif _same(row, item, keys):
        plan.add(section, key, "unchanged")
    else:
        for k in keys:
            setattr(row, k, item.get(k))
        plan.add(section, key, "update")
    await db.flush()
    return row


async def apply(db: AsyncSession, importer: User, bundle: dict, dry_run: bool = True) -> dict:
    """Stage the bundle, validate every item, then commit (or roll back on a dry run or any error)."""
    from app.services import workflows

    if bundle.get("format") != FORMAT or bundle.get("version") != VERSION:
        raise BundleError(f"Not a Cirra configuration bundle (expected format '{FORMAT}' version {VERSION})")
    plan = _Plan()
    occ = _Occurrences()
    try:
        for o in _items(bundle, "custom_objects"):
            key = str(o.get("key", ""))
            if not re.match(r"^[a-z][a-z0-9_]{1,30}$", key) or not o.get("label") or not o.get("plural_label"):
                plan.add("custom_objects", key or "?", "error", "Needs a key, label and plural label")
                continue
            row = (await db.execute(select(CustomObject).where(CustomObject.key == key))).scalar_one_or_none()
            await _upsert(db, plan, "custom_objects", key, row, lambda o=o: CustomObject(key=o["key"], label=o["label"], plural_label=o["plural_label"],
                                                                                          description=o.get("description")),
                          o, ("label", "plural_label", "description"))

        for f in _items(bundle, "custom_fields"):
            key = f"{f.get('entity')}.{f.get('key')}"
            if f.get("field_type") not in ("text", "number", "date", "select", "boolean", "url") or not f.get("label"):
                plan.add("custom_fields", key, "error", "Unknown type or missing label")
                continue
            if any(r not in ROLES or r == "super_admin" or v not in ("read", "hidden") for r, v in (f.get("access") or {}).items()):
                plan.add("custom_fields", key, "error", "Field security names an unknown role or level")
                continue
            ent = str(f.get("entity", ""))
            if ent.startswith("object:") and not (await db.execute(select(CustomObject.id).where(CustomObject.key == ent[7:]))).first():
                plan.add("custom_fields", key, "error", "Its custom object isn't in this environment or the bundle")
                continue
            row = (await db.execute(select(CustomFieldDefinition).where(CustomFieldDefinition.entity == ent,
                                                                        CustomFieldDefinition.key == f.get("key")))).scalar_one_or_none()
            if row is not None and row.field_type != f["field_type"]:
                plan.add("custom_fields", key, "error", f"Type differs ({row.field_type} here); a field's type can't change")
                continue
            item = {**f, "options": f.get("options") or [], "required": bool(f.get("required")), "access": f.get("access") or {}}
            await _upsert(db, plan, "custom_fields", key, row, lambda f=item: CustomFieldDefinition(
                entity=f["entity"], key=f["key"], label=f["label"], field_type=f["field_type"], options=f["options"], required=f["required"],
                access=f["access"]), item, ("label", "options", "required", "access"))

        await reporting.refresh_custom_fields(db, force=True)  # rules and reports below may use what was just staged

        for r in _items(bundle, "validation_rules"):
            key = f"{r.get('entity')}.{r.get('name')}"
            try:
                check_validation_rule(r)
            except (BundleError, reporting.ReportError) as e:
                plan.add("validation_rules", key, "error", str(e))
                continue
            row = (await db.execute(select(ValidationRule).where(ValidationRule.entity == r["entity"], ValidationRule.name == r["name"]))).scalar_one_or_none()
            item = {**r, "applies_on": r.get("applies_on", "both"), "active": r.get("active", True)}
            await _upsert(db, plan, "validation_rules", key, row, lambda r=item: ValidationRule(
                entity=r["entity"], name=r["name"], description=r.get("description"), conditions=r["conditions"], message=r["message"],
                applies_on=r["applies_on"], active=r["active"]), item, ("description", "conditions", "message", "applies_on", "active"))

        for r in _items(bundle, "sharing_rules"):
            key = str(r.get("name"))
            try:
                check_sharing_rule(r)
            except (BundleError, reporting.ReportError) as e:
                plan.add("sharing_rules", key, "error", str(e))
                continue
            row = (await db.execute(select(SharingRule).where(SharingRule.name == r["name"]))).scalar_one_or_none()
            item = {**r, "active": r.get("active", True)}
            await _upsert(db, plan, "sharing_rules", key, row, lambda r=item: SharingRule(
                name=r["name"], description=r.get("description"), criteria=r["criteria"], roles=r["roles"], active=r["active"]),
                item, ("description", "criteria", "roles", "active"))

        for w in _items(bundle, "workflows"):
            key = str(w.get("name"))
            try:
                if not w.get("name"):
                    raise BundleError("Needs a name")
                workflows.validate(w.get("source"), w.get("trigger") or {}, w.get("conditions") or [], w.get("actions") or [])
            except (BundleError, workflows.WorkflowError, reporting.ReportError) as e:
                plan.add("workflows", key, "error", str(e))
                continue
            row = await occ.row(db, ("wf", w["name"]), select(WorkflowRule).where(WorkflowRule.name == w["name"])
                                .order_by(WorkflowRule.created_at, WorkflowRule.id))
            item = {**w, "enabled": bool(w.get("enabled")), "conditions": w.get("conditions") or [], "actions": w.get("actions") or []}
            await _upsert(db, plan, "workflows", key, row, lambda w=item: WorkflowRule(
                name=w["name"], description=w.get("description"), enabled=w["enabled"], source=w["source"], trigger=w["trigger"],
                conditions=w["conditions"], actions=w["actions"], created_by=importer.id),
                item, ("description", "enabled", "source", "trigger", "conditions", "actions"))

        for rp in _items(bundle, "role_permissions"):
            key = f"{rp.get('role')}.{rp.get('resource')}"
            if rp.get("role") not in ROLES or rp.get("resource") not in RESOURCES or rp.get("scope") not in ("all", "own"):
                plan.add("role_permissions", key, "error", "Unknown role, resource or scope")
                continue
            if rp["role"] in PROTECTED_ROLES:
                plan.add("role_permissions", key, "error", "Super Admin and Partner permissions can't be imported")
                continue
            item = {**rp, **{f"can_{a}": bool(rp.get(f"can_{a}")) for a in ACTIONS}}
            row = await db.get(RolePermission, (rp["role"], rp["resource"]))
            await _upsert(db, plan, "role_permissions", key, row, lambda r=item: RolePermission(
                role=r["role"], resource=r["resource"], scope=r["scope"], **{f"can_{a}": r[f"can_{a}"] for a in ACTIONS}),
                item, ("scope", *[f"can_{a}" for a in ACTIONS]))

        report_ids: dict[str, list[uuid.UUID]] = {}
        for r in _items(bundle, "reports"):
            key = str(r.get("name"))
            try:
                if not r.get("name"):
                    raise BundleError("Needs a name")
                reporting.validate(r.get("definition") or {})
            except (BundleError, reporting.ReportError) as e:
                plan.add("reports", key, "error", str(e))
                continue
            row = await occ.row(db, ("report", r["name"]), select(SavedReport).where(SavedReport.name == r["name"], SavedReport.visibility == "shared")
                                .order_by(SavedReport.created_at, SavedReport.id))
            item = {**r, "source": r["definition"]["source"]}
            row = await _upsert(db, plan, "reports", key, row, lambda r=item: SavedReport(
                name=r["name"], description=r.get("description"), owner_id=importer.id, source=r["source"], definition=r["definition"],
                visibility="shared"), item, ("description", "definition", "source"))
            report_ids.setdefault(r["name"], []).append(row.id)
        for d in _items(bundle, "dashboards"):
            key = str(d.get("name"))
            tiles = []
            for t in d.get("tiles") or []:
                n = t.get("occurrence", 0) if isinstance(t.get("occurrence", 0), int) else 0
                ids = report_ids.get(t.get("report")) or (await db.execute(select(SavedReport.id).where(
                    SavedReport.name == t.get("report"), SavedReport.visibility == "shared").order_by(SavedReport.created_at, SavedReport.id))).scalars().all()
                rid = ids[n] if 0 <= n < len(ids) else None
                if rid is None:
                    break
                tiles.append({"report_id": str(rid), "size": t.get("size") if t.get("size") in ("third", "half", "full") else "half"})
            if not d.get("name") or len(tiles) != len(d.get("tiles") or []):
                plan.add("dashboards", key, "error", "Needs a name, and every tile's report must be shared or in the bundle")
                continue
            row = await occ.row(db, ("dash", d["name"]), select(Dashboard).where(Dashboard.name == d["name"], Dashboard.visibility == "shared")
                                .order_by(Dashboard.created_at, Dashboard.id))
            item = {**d, "tiles": tiles}
            await _upsert(db, plan, "dashboards", key, row, lambda d=item: Dashboard(
                name=d["name"], description=d.get("description"), owner_id=importer.id, visibility="shared", tiles=d["tiles"]),
                item, ("description", "tiles"))

        for v in _items(bundle, "list_views"):
            key = f"{v.get('source')}.{v.get('name')}"
            src = reporting.src_of(str(v.get("source", "")))
            cols = v.get("columns") or []
            try:
                if src is None or not v.get("name") or not cols or any(c not in src.fields for c in cols):
                    raise BundleError("Unknown list or column, or no name")
                reporting.validate_filters(src.key, v.get("filters") or [])
            except (BundleError, reporting.ReportError) as e:
                plan.add("list_views", key, "error", str(e))
                continue
            row = await occ.row(db, ("view", v["source"], v["name"]), select(ListView).where(
                ListView.source == v["source"], ListView.name == v["name"], ListView.visibility == "shared").order_by(ListView.created_at, ListView.id))
            item = {**v, "filters": v.get("filters") or [], "sort": v.get("sort") or {}}
            await _upsert(db, plan, "list_views", key, row, lambda v=item: ListView(
                source=v["source"], name=v["name"], owner_id=importer.id, visibility="shared", columns=v["columns"], filters=v["filters"],
                sort=v["sort"]), item, ("columns", "filters", "sort"))

        applied = not dry_run and not plan.errors
        if applied:
            await db.commit()
        else:
            await db.rollback()
    except Exception:
        await db.rollback()
        raise
    finally:
        await reporting.refresh_custom_fields(db, force=True)  # never leave staged objects in the live catalogue
        invalidate_cache()
        invalidate_shares()
    return {"applied": applied, "dry_run": dry_run, "summary": plan.summary(), "items": plan.items}


def check_validation_rule(r: dict) -> None:
    from app.services import validation

    if not r.get("name") or not r.get("message"):
        raise BundleError("Needs a name and a message")
    if r.get("applies_on", "both") not in validation.APPLIES:
        raise BundleError("applies_on must be create, update or both")
    ent = str(r.get("entity", ""))
    if ent not in validation.SOURCES and not (ent.startswith(reporting.OBJECT_PREFIX) and reporting.src_of(ent)):
        raise BundleError("Choose accounts, contacts, deals, leads, cases or a custom object")
    if not r.get("conditions"):
        raise BundleError("Add at least one condition")
    reporting.validate_filters(ent, r["conditions"])


def check_sharing_rule(r: dict) -> None:
    if not r.get("name"):
        raise BundleError("Needs a name")
    roles = r.get("roles") or []
    if not roles or any(x not in ROLES or x in PROTECTED_ROLES for x in roles):
        raise BundleError("Pick one or more roles (not Super Admin or Partner)")
    if not r.get("criteria"):
        raise BundleError("Add at least one condition; to share every account, give the role 'all' scope instead")
    reporting.validate_filters("accounts", r["criteria"])
