"""Validation rules: admin-defined conditions that block saving a record.

A rule names a record type (a report source: accounts, contacts, deals, leads, cases or a custom object),
conditions in the report filter syntax, and a message. When every condition matches a record a person just
created or changed, the save is refused with the message (HTTP 422) and nothing is written.

Changes are captured after each flush and checked by ``CirraSession.commit`` (core/database.py) against the
flushed state inside the transaction, so a rule sees exactly what would be committed, whichever screen, API
or import made the change. Only request sessions are checked; background jobs and workflow automation act as
the system. A rule that no longer compiles (its field was deleted) is skipped and logged, never blocking.
"""
from __future__ import annotations

import logging

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import Account, Contact, CustomObject, CustomRecord, Deal, Lead, SupportTicket, ValidationRule

log = logging.getLogger(__name__)

MODELS = {Account: "accounts", Contact: "contacts", Deal: "deals", Lead: "leads", SupportTicket: "cases"}
SOURCES = ("accounts", "contacts", "deals", "leads", "cases")
APPLIES = ("create", "update", "both")


class ValidationRuleError(ValueError):
    def __init__(self, failures: list[dict]):
        self.failures = failures
        super().__init__("; ".join(f["message"] for f in failures))


@event.listens_for(Session, "after_flush")
def _capture(session: Session, flush_context) -> None:
    if not session.info.get("validate"):
        return
    pending: dict = session.info.setdefault("vr_pending", {})
    for obj in session.new:
        key = ("object", obj.object_id) if isinstance(obj, CustomRecord) else MODELS.get(type(obj))
        if key:
            pending[(key, obj.id)] = "create"
    for obj in session.dirty:
        if not session.is_modified(obj, include_collections=False):
            continue
        key = ("object", obj.object_id) if isinstance(obj, CustomRecord) else MODELS.get(type(obj))
        if key:
            pending.setdefault((key, obj.id), "update")  # created earlier in this transaction stays "create"


@event.listens_for(Session, "after_rollback")
def _discard(session: Session) -> None:
    session.info.pop("vr_pending", None)


async def check(db: AsyncSession) -> None:
    """Raise ValidationRuleError when a changed record matches an active rule of its type."""
    from app.services import reporting

    pending: dict = db.info.pop("vr_pending", None) or {}
    if not pending:
        return
    obj_ids = {k[1] for (k, _) in pending if isinstance(k, tuple)}
    obj_keys = dict((await db.execute(select(CustomObject.id, CustomObject.key).where(CustomObject.id.in_(obj_ids)))).all()) if obj_ids else {}
    by_source: dict[str, dict] = {}
    for (key, rid), kind in pending.items():
        source = f"{reporting.OBJECT_PREFIX}{obj_keys.get(key[1])}" if isinstance(key, tuple) else key
        by_source.setdefault(source, {})[rid] = kind
    rules = (await db.execute(select(ValidationRule).where(ValidationRule.active.is_(True), ValidationRule.entity.in_(list(by_source)))
                              .order_by(ValidationRule.name))).scalars().all()
    failures = []
    for rule in rules:
        ids = [rid for rid, kind in by_source[rule.entity].items() if rule.applies_on in ("both", kind)]
        if not ids:
            continue
        try:
            hit = await reporting.match_ids(db, rule.entity, rule.conditions, ids=ids, limit=len(ids))
        except Exception:  # a broken rule must never block every save
            log.warning("Validation rule %s (%s) couldn't be evaluated; skipped", rule.name, rule.id, exc_info=True)
            continue
        if hit:
            failures.append({"rule": rule.name, "entity": rule.entity, "message": rule.message, "records": [str(i) for i in hit]})
    db.info.pop("vr_pending", None)  # queries above may have flushed; nothing new to check
    if failures:
        raise ValidationRuleError(failures)


async def violations(db: AsyncSession, entity: str, conditions: list[dict], limit: int = 5) -> dict:
    """Existing records that would fail a rule (for the admin's test button): a count and a few names."""
    from sqlalchemy import func

    from app.services import reporting

    await reporting.refresh_custom_fields(db)
    src = reporting.src_of(entity)
    q = reporting.criteria_ids(entity, conditions)
    if src is None or q is None:
        raise reporting.ReportError("The conditions don't fit this record type")
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    first = next(iter(src.fields))
    names = (await db.execute(src.base().add_columns(src.fields[first].expr).where(src.id_col.in_(q)).limit(limit))).scalars().all()
    return {"count": total, "examples": [str(n) for n in names]}
