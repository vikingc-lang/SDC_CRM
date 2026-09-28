"""Buying committees and org charts.

* **Org chart**: each contact can report to another contact at the same account (or its parent or subsidiary);
  cycles are refused. The account page draws the tree with each person's buying role, influence, stance, deal
  roles and how recently we spoke to them.
* **Buying committee**: per deal, the contacts involved in this decision with their role (economic buyer,
  champion, technical buyer, …), influence and stance. Coverage checks what the stage needs (a champion from the
  start; economic buyer and decision maker from stage 2), flags blockers and single-threaded deals, and suggests
  who to add: senior people at the account not yet involved, and the champion's manager ("go one level up").
* The deal's roles feed the missing-roles risk alert and Aiden's next best actions. A deal without a committee
  falls back to the contacts' usual buying roles at the account, as before.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import COMMITTEE_ROLES, Account, Activity, Contact, Deal, DealContact

INFLUENCE = ("high", "medium", "low")
STANCES = ("champion", "supporter", "neutral", "skeptic", "blocker")
SENIOR = re.compile(r"\b(chief|c[efiot]o|vp|vice president|president|head|director|svp|evp|owner|founder|partner)\b", re.I)


class StakeholderError(ValueError):
    pass


def needed_roles(stage_order: int) -> list[str]:
    return ["Champion"] + (["Economic Buyer", "Decision Maker"] if stage_order >= 2 else [])


async def deal_roles(db: AsyncSession, deal: Deal) -> set[str]:
    """The buying roles covered on this deal: its committee when it has one, else the account contacts' roles."""
    committee = (await db.execute(select(DealContact.role).join(Contact, Contact.id == DealContact.contact_id)
                                  .where(DealContact.deal_id == deal.id, Contact.status == "active"))).scalars().all()
    if committee:
        return set(committee)
    return set((await db.execute(select(Contact.buying_role).where(Contact.account_id == deal.account_id, Contact.status == "active")))
               .scalars().all())


async def _family(db: AsyncSession, account_id: uuid.UUID) -> set[uuid.UUID]:
    acc = await db.get(Account, account_id)
    ids = {account_id}
    if acc and acc.parent_id:
        ids.add(acc.parent_id)
    ids |= set((await db.execute(select(Account.id).where(Account.parent_id == account_id))).scalars().all())
    return ids


async def set_manager(db: AsyncSession, contact: Contact, manager_id: uuid.UUID | None) -> None:
    if manager_id is None:
        contact.reports_to_id = None
        return
    if manager_id == contact.id:
        raise StakeholderError("A contact can't report to themselves")
    boss = await db.get(Contact, manager_id)
    if boss is None or boss.status == "erased":
        raise StakeholderError("Manager not found")
    if boss.account_id not in await _family(db, contact.account_id):
        raise StakeholderError("The manager must work at the same company (or its parent or subsidiary)")
    seen, cur = {contact.id}, boss
    while cur is not None and cur.reports_to_id:
        if cur.reports_to_id in seen:
            raise StakeholderError(f"{boss.full_name} already reports (directly or indirectly) to {contact.full_name}")
        seen.add(cur.id)
        cur = await db.get(Contact, cur.reports_to_id)
    contact.reports_to_id = manager_id


async def org_chart(db: AsyncSession, account_id: uuid.UUID) -> dict:
    contacts = (await db.execute(select(Contact).where(Contact.account_id == account_id, Contact.status != "erased")
                                 .order_by(Contact.last_name))).scalars().all()
    ids = [c.id for c in contacts]
    last_touch = dict((await db.execute(select(Activity.contact_id, func.max(Activity.occurred_at)).where(Activity.contact_id.in_(ids))
                                        .group_by(Activity.contact_id))).all()) if ids else {}
    roles: dict[uuid.UUID, list[dict]] = {}
    for dc, title in (await db.execute(select(DealContact, Deal.title).join(Deal, Deal.id == DealContact.deal_id)
                                       .where(DealContact.contact_id.in_(ids)))).all() if ids else []:
        roles.setdefault(dc.contact_id, []).append({"deal_id": dc.deal_id, "deal": title, "role": dc.role, "stance": dc.stance})
    in_chart = set(ids)
    now = datetime.now(timezone.utc)
    nodes = [{"id": c.id, "name": c.full_name, "title": c.job_title, "department": c.department, "buying_role": c.buying_role,
              "influence": c.influence, "stance": c.stance, "status": c.status, "email": c.email,
              "reports_to_id": c.reports_to_id if c.reports_to_id in in_chart else None,
              "reports_outside": c.reports_to_id is not None and c.reports_to_id not in in_chart,
              "relationship_strength": c.relationship_strength, "deal_roles": roles.get(c.id, []),
              "days_since_touch": (now - last_touch[c.id]).days if c.id in last_touch and last_touch[c.id] else None}
             for c in contacts]
    return {"nodes": nodes, "roots": [n["id"] for n in nodes if n["reports_to_id"] is None],
            "unplaced": sum(1 for n in nodes if n["reports_to_id"] is None and not any(m["reports_to_id"] == n["id"] for m in nodes))}


def member_out(dc: DealContact) -> dict:
    c = dc.contact
    return {"contact_id": dc.contact_id, "name": c.full_name, "title": c.job_title, "email": c.email, "status": c.status,
            "role": dc.role, "influence": dc.influence, "stance": dc.stance, "is_primary": dc.is_primary, "notes": dc.notes,
            "reports_to_id": c.reports_to_id, "added_at": dc.added_at}


async def committee(db: AsyncSession, deal: Deal) -> dict:
    members = (await db.execute(select(DealContact).where(DealContact.deal_id == deal.id).order_by(DealContact.added_at))).scalars().unique().all()
    active = [m for m in members if m.contact.status == "active"]
    roles = {m.role for m in active}
    stage_order = deal.stage.stage_order if deal.stage else 1
    missing = [r for r in needed_roles(stage_order) if r not in roles]
    if "Champion" in missing and "Decision Maker" in roles:  # a committed decision maker can carry the deal, as before
        missing.remove("Champion")
    gaps = [{"kind": "missing_role", "role": r, "message": f"No {r} on the buying committee"} for r in missing]
    blockers = [m for m in active if m.stance == "blocker" or m.role == "Blocker"]
    for b in blockers:
        gaps.append({"kind": "blocker", "contact_id": b.contact_id,
                     "message": f"{b.contact.full_name} is a blocker{' with high influence' if b.influence == 'high' else ''}: plan how to address their concerns"})
    departed = [m for m in members if m.contact.status == "departed"]
    for d in departed:
        gaps.append({"kind": "departed", "contact_id": d.contact_id, "message": f"{d.contact.full_name} ({d.role}) has left the company"})
    if len(active) == 1:
        gaps.append({"kind": "single_threaded", "message": "Single-threaded: only one person is engaged on this deal"})
    if active and not any(m.influence == "high" and m.stance in ("champion", "supporter") for m in active):
        gaps.append({"kind": "no_power_sponsor", "message": "Nobody with high influence supports this deal yet"})
    on = {m.contact_id for m in members}
    candidates = (await db.execute(select(Contact).where(Contact.account_id == deal.account_id, Contact.status == "active"))).scalars().all()
    suggestions = []
    by_id = {c.id: c for c in candidates}
    for m in active:
        if m.role == "Champion" or m.stance == "champion":
            boss = by_id.get(m.contact.reports_to_id) if m.contact.reports_to_id else None
            if boss and boss.id not in on:
                suggestions.append({"contact_id": boss.id, "name": boss.full_name, "title": boss.job_title,
                                    "suggested_role": "Economic Buyer" if "Economic Buyer" in missing else "Decision Maker",
                                    "why": f"{m.contact.full_name}'s manager: go one level up"})
    for c in candidates:
        if c.id in on or any(s["contact_id"] == c.id for s in suggestions):
            continue
        if c.buying_role in missing or (c.job_title and SENIOR.search(c.job_title)):
            suggestions.append({"contact_id": c.id, "name": c.full_name, "title": c.job_title,
                                "suggested_role": c.buying_role if c.buying_role in COMMITTEE_ROLES else "Influencer",
                                "why": f"Usually the {c.buying_role}" if c.buying_role in missing else "Senior at the account, not yet involved"})
    score = 0
    if active:
        need = needed_roles(stage_order)
        score = round(100 * (0.5 * (len([r for r in need if r in roles]) / len(need))
                             + 0.2 * min(len(active), 4) / 4
                             + 0.3 * (sum(1 for m in active if m.stance in ("champion", "supporter")) / len(active))))
    return {"members": [member_out(m) for m in members], "coverage": score, "needed": needed_roles(stage_order), "gaps": gaps,
            "suggestions": suggestions[:6], "roles": list(COMMITTEE_ROLES), "stances": list(STANCES), "influence": list(INFLUENCE)}


async def upsert_member(db: AsyncSession, deal: Deal, contact_id: uuid.UUID, *, role: str, influence: str = "medium",
                        stance: str = "neutral", is_primary: bool = False, notes: str | None = None) -> DealContact:
    if role not in COMMITTEE_ROLES:
        raise StakeholderError(f"Role must be one of: {', '.join(COMMITTEE_ROLES)}")
    if influence not in INFLUENCE or stance not in STANCES:
        raise StakeholderError("Choose a valid influence and stance")
    contact = await db.get(Contact, contact_id)
    if contact is None or contact.status == "erased" or contact.account_id not in await _family(db, deal.account_id):
        raise StakeholderError("Add contacts from the deal's account")
    dc = await db.get(DealContact, (deal.id, contact_id))
    if dc is None:
        dc = DealContact(deal_id=deal.id, contact_id=contact_id, role=role)
        db.add(dc)
    dc.role, dc.influence, dc.stance, dc.notes = role, influence, stance, (notes or None) and notes[:500]
    if is_primary:
        for other in (await db.execute(select(DealContact).where(DealContact.deal_id == deal.id, DealContact.is_primary.is_(True)))).scalars():
            other.is_primary = False
    dc.is_primary = is_primary
    await db.flush()
    await db.refresh(dc)
    return dc
