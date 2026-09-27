"""Bulk actions on a list selection: reassign, change status / priority / tier / queue, add to a campaign.

Only records the caller can see (row-level scope) and may update are changed; the rest are skipped and
counted. Changes go through the same services as single edits where those carry side effects (cases re-time
SLAs and notify new owners; campaign membership uses the campaign service), and every change is audited.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.rbac import Principal
from app.models import Account, Campaign, Contact, Lead, SupportQueue, SupportTicket, User
from app.services.notify import notify

MAX_IDS = 500
ACTIONS = {
    "leads": ("assign_owner", "set_status", "add_to_campaign"),
    "accounts": ("assign_owner", "set_tier"),
    "contacts": ("add_to_campaign",),
    "cases": ("assign_owner", "set_status", "set_priority", "set_queue"),
}
LEAD_STATUSES = ("new", "working", "recycled", "disqualified")
TIERS = ("SMB", "Mid-Market", "Enterprise")


class BulkError(ValueError):
    pass


def _uuids(ids: list[str]) -> list[uuid.UUID]:
    out = []
    for i in ids:
        try:
            out.append(uuid.UUID(str(i)))
        except ValueError as e:
            raise BulkError(f"'{i}' is not a record id") from e
    return list(dict.fromkeys(out))


async def _active_user(db: AsyncSession, value: str | None) -> User:
    try:
        u = await db.get(User, uuid.UUID(str(value)))
    except (TypeError, ValueError):
        u = None
    if u is None or not u.is_active or u.role == "partner":
        raise BulkError("Choose an active internal user")
    return u


async def _visible(db: AsyncSession, p: Principal, entity: str, ids: list[uuid.UUID]) -> list:
    if entity == "leads":
        stmt = select(Lead).where(Lead.id.in_(ids))
        if p.is_own_scope("leads"):
            stmt = stmt.where((Lead.owner_id == p.id) | Lead.owner_id.is_(None))
    elif entity == "accounts":
        stmt = p.scope_accounts(select(Account).where(Account.id.in_(ids)))
    elif entity == "contacts":
        stmt = p.scope_accounts(select(Contact).where(Contact.id.in_(ids), Contact.status != "erased"), "contacts", Contact.account_id)
    else:
        from app.services import cases

        stmt = cases.scope(p, select(SupportTicket).where(SupportTicket.id.in_(ids)))
    return list((await db.execute(stmt)).scalars().unique().all())


async def apply(db: AsyncSession, p: Principal, entity: str, ids: list[str], action: str, value: str | None, note: str | None = None) -> dict:
    if action not in ACTIONS[entity]:
        raise BulkError(f"Choose one of: {', '.join(ACTIONS[entity])}")
    resource = "campaigns" if action == "add_to_campaign" else entity
    if not p.can(resource, "update"):
        raise BulkError(f"Your role can't update {resource}")
    uids = _uuids(ids)
    rows = await _visible(db, p, entity, uids)
    now = datetime.now(timezone.utc)
    changed = 0

    if action == "add_to_campaign":
        from app.services import campaigns

        try:
            camp = await db.get(Campaign, uuid.UUID(str(value)))
        except (TypeError, ValueError):
            camp = None
        if camp is None:
            raise BulkError("Choose a campaign")
        out = await campaigns.add_members(db, camp, lead_ids=[r.id for r in rows] if entity == "leads" else (),
                                          contact_ids=[r.id for r in rows] if entity == "contacts" else (), source="manual")
        changed = out["added"]
        log_action(db, "bulk_add_to_campaign", "campaigns", camp.id, f"{changed} {entity} added")
        return {"matched": len(rows), "changed": changed, "skipped": len(uids) - len(rows), "already": out["skipped"]}

    if action == "assign_owner":
        user = await _active_user(db, value)
        for r in rows:
            if r.owner_id != user.id:
                if entity == "cases":
                    from app.services import cases

                    await cases.update(db, r, {"owner_id": user.id})
                else:
                    r.owner_id = user.id
                    if entity == "leads":
                        r.assigned_at = now
                changed += 1
        if changed and entity != "cases":
            notify(db, [user.id], "assignment", f"{changed} {entity} assigned to you", note, f"/{entity}")
    elif action == "set_tier":
        if value not in TIERS:
            raise BulkError(f"Tier must be one of {', '.join(TIERS)}")
        for r in rows:
            if r.tier != value:
                r.tier, changed = value, changed + 1
    elif action == "set_status" and entity == "leads":
        if value not in LEAD_STATUSES:
            raise BulkError(f"Status must be one of {', '.join(LEAD_STATUSES)}")
        from app.services.leads import DISQUALIFY_REASONS

        if value == "disqualified" and (note or "other") not in DISQUALIFY_REASONS:
            raise BulkError(f"Disqualifying needs a reason: {', '.join(DISQUALIFY_REASONS)}")
        for r in rows:
            if r.status == "converted" or r.status == value:
                continue  # converted leads are final
            r.status = value
            if value == "disqualified":
                r.disqualified_reason, r.disqualify_note = note or "other", "Bulk disqualified"
            changed += 1
    else:  # cases: status / priority / queue through the case service (SLA timing, CSAT links, auto-assign)
        from app.services import cases

        field = {"set_status": "status", "set_priority": "severity", "set_queue": "queue_id"}[action]
        allowed = {"status": cases.STATUSES, "severity": cases.PRIORITIES}.get(field)
        if allowed and value not in allowed:
            raise BulkError(f"Choose one of: {', '.join(allowed)}")
        new = value
        if field == "queue_id":
            try:
                q = await db.get(SupportQueue, uuid.UUID(str(value))) if value else None
            except ValueError:
                q = None
            if q is None:
                raise BulkError("Choose a queue")
            new = q.id
        for r in rows:
            if getattr(r, field) != new:
                await cases.update(db, r, {field: new})
                changed += 1
    log_action(db, f"bulk_{action}"[:20], entity, None, f"{changed} of {len(uids)} {entity} -> {value}")
    await db.flush()
    return {"matched": len(rows), "changed": changed, "skipped": len(uids) - len(rows)}
