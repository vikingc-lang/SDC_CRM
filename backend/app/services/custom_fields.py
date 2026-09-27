"""Tenant-defined custom attributes stored in schemaless JSONB (pillar 1), with per-role field security.

Field security: a definition's ``access`` maps a role to "read" (visible, not editable) or "hidden" (never
returned, not reportable, not editable); roles not listed can edit. Super admins always have full access and
background jobs (no signed-in principal) act as the system. Hidden values are kept when others save the record.
"""
from __future__ import annotations

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import context
from app.models import CustomFieldDefinition

LEVELS = ("edit", "read", "hidden")
_cache: dict[str, list] = {}  # entity -> definitions, maintained by reporting.refresh_custom_fields


def set_cache(defs) -> None:
    global _cache
    by: dict[str, list] = {}
    for d in defs:
        by.setdefault(d.entity, []).append(d)
    _cache = by


def level(defn, role: str | None) -> str:
    """edit | read | hidden for ``role`` (None = the system)."""
    if role is None or role == "super_admin":
        return "edit"
    return (defn.access or {}).get(role, "edit")


def hidden_keys(entity: str, role: str | None) -> list[str]:
    return [d.key for d in _cache.get(entity, []) if level(d, role) == "hidden"]


def restricted_keys(entity: str) -> set[str]:
    """Keys hidden from at least one role (left out of shared artefacts such as search embeddings)."""
    return {d.key for d in _cache.get(entity, []) if "hidden" in (d.access or {}).values()}


def redact(entity: str, values: dict | None, role: str | None = None) -> dict:
    """``values`` without the fields hidden from the role (default: the signed-in user's)."""
    role = role if role is not None else context.role()
    hidden = {d.key for d in _cache.get(entity, []) if level(d, role) == "hidden"}
    return {k: v for k, v in (values or {}).items() if k not in hidden}


def definitions_out(entity: str, role: str | None = None) -> list[dict]:
    """The entity's fields as the role may use them in a form: hidden ones left out, read-only ones flagged."""
    role = role if role is not None else context.role()
    return [{"key": d.key, "label": d.label, "field_type": d.field_type, "options": d.options, "required": d.required,
             "read_only": level(d, role) == "read"}
            for d in sorted(_cache.get(entity, []), key=lambda d: d.label) if level(d, role) != "hidden"]


class CustomFieldError(ValueError):
    pass


def _coerce(defn: CustomFieldDefinition, value):
    if value in (None, ""):
        return None
    t = defn.field_type
    if t == "number":
        try:
            return float(value)
        except (TypeError, ValueError):
            raise CustomFieldError(f"{defn.label} must be a number")
    if t == "boolean":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "yes", "1"):
            return True
        if str(value).lower() in ("false", "no", "0"):
            return False
        raise CustomFieldError(f"{defn.label} must be true or false")
    if t == "date":
        try:
            return date.fromisoformat(str(value)[:10]).isoformat()
        except ValueError:
            raise CustomFieldError(f"{defn.label} must be a date (YYYY-MM-DD)")
    if t == "select":
        if value not in (defn.options or []):
            raise CustomFieldError(f"{defn.label} must be one of: {', '.join(defn.options or [])}")
        return value
    if t == "url" and not re.match(r"^https?://", str(value)):
        raise CustomFieldError(f"{defn.label} must be a URL starting with http(s)://")
    return str(value)[:2000]


async def validate(db: AsyncSession, entity: str, values: dict, existing: dict | None = None, partial: bool = True) -> dict:
    """Validate/coerce ``values`` against the entity's definitions and merge onto ``existing``.

    Unknown keys are kept (schemaless store) but defined keys are type-checked.
    """
    defs = {d.key: d for d in (await db.execute(select(CustomFieldDefinition).where(CustomFieldDefinition.entity == entity))).scalars().all()}
    role = context.role()
    out = dict(existing or {})
    for key, value in (values or {}).items():
        if not re.match(r"^[a-z][a-z0-9_]{0,63}$", key):
            raise CustomFieldError(f"Invalid field key '{key}' (lowercase letters, digits, underscore)")
        new = _coerce(defs[key], value) if key in defs else value
        if key in defs and level(defs[key], role) != "edit" and new != (existing or {}).get(key):
            # unchanged values may round-trip from a form; a change needs edit access
            raise CustomFieldError(f"Your role can't change {defs[key].label}")
        out[key] = new
        if out[key] is None:
            out.pop(key)
    if not partial:
        missing = [d.label for k, d in defs.items() if d.required and out.get(k) in (None, "")]
        if missing:
            raise CustomFieldError(f"Required: {', '.join(missing)}")
    return out
