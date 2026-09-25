"""Tenant-defined custom attributes stored in schemaless JSONB (pillar 1)."""
from __future__ import annotations

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CustomFieldDefinition


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
    out = dict(existing or {})
    for key, value in (values or {}).items():
        if not re.match(r"^[a-z][a-z0-9_]{0,63}$", key):
            raise CustomFieldError(f"Invalid field key '{key}' (lowercase letters, digits, underscore)")
        out[key] = _coerce(defs[key], value) if key in defs else value
        if out[key] is None:
            out.pop(key)
    if not partial:
        missing = [d.label for k, d in defs.items() if d.required and out.get(k) in (None, "")]
        if missing:
            raise CustomFieldError(f"Required: {', '.join(missing)}")
    return out
