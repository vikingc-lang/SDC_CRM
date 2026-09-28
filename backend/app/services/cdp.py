"""Behavioural event store and dynamic segments (a CDP slice).

Events arrive from four places: the website snippet (``/public/t.js`` sends page views and custom events with a
first-party anonymous id), marketing email (opens and clicks), web forms, and the API (``POST /events`` for product
usage). When a visitor identifies (a form, a login, ``cirra.identify(email)``) their earlier anonymous events are
linked to the matching contact or lead, so a profile shows everything they did.

A segment is a rule set over contacts or leads: attribute conditions (contact, account or lead fields) and
behaviour conditions ("viewed a page containing /pricing at least 3 times in 30 days", "never opened an email").
Segments are evaluated as one SQL query, previewed live while editing, and refreshed by the ``segments`` job, which
keeps the member list, records who entered and emits ``segment.entered`` for webhooks and connectors. Campaigns can
take a segment as their audience and Mailchimp can mirror one.
"""
from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, BehaviorEvent, Contact, Lead, Segment, SegmentMember
from app.services import app_settings

EVENT_NAME = re.compile(r"^[a-z][a-z0-9_.:-]{0,59}$")
MAX_BATCH = 500
# Events the system writes itself; anything else matching EVENT_NAME is a custom event.
STANDARD_EVENTS = {"page_view": "Viewed a page", "form_submit": "Submitted a form", "email_open": "Opened an email",
                   "email_click": "Clicked an email link", "identify": "Identified themselves"}

OPS = ("eq", "neq", "in", "not_in", "contains", "gte", "lte", "is_set", "not_set")
# object -> field key -> (label, column, kind)
FIELDS = {
    "contact": {
        "contact.job_title": ("Job title", Contact.job_title, "text"),
        "contact.department": ("Department", Contact.department, "text"),
        "contact.buying_role": ("Buying role", Contact.buying_role, "text"),
        "contact.influence": ("Influence", Contact.influence, "text"),
        "contact.stance": ("Stance", Contact.stance, "text"),
        "contact.consent_email": ("Email consent", Contact.consent_email, "text"),
        "contact.privacy_regime": ("Privacy regime", Contact.privacy_regime, "text"),
        "contact.relationship_strength": ("Relationship strength", Contact.relationship_strength, "number"),
        "account.name": ("Account name", Account.name, "text"),
        "account.industry": ("Account industry", Account.industry, "text"),
        "account.tier": ("Account tier", Account.tier, "text"),
        "account.country": ("Account country", Account.country, "text"),
        "account.region": ("Account region", Account.region, "text"),
        "account.lifecycle_stage": ("Account lifecycle stage", Account.lifecycle_stage, "text"),
        "account.health_score": ("Account health", Account.health_score, "number"),
        "account.employee_count": ("Account employees", Account.employee_count, "number"),
        "account.annual_revenue": ("Account annual revenue", Account.annual_revenue, "number"),
    },
    "lead": {
        "lead.status": ("Status", Lead.status, "text"),
        "lead.source": ("Source", Lead.source, "text"),
        "lead.campaign": ("Campaign", Lead.campaign, "text"),
        "lead.job_title": ("Job title", Lead.job_title, "text"),
        "lead.company_name": ("Company", Lead.company_name, "text"),
        "lead.industry": ("Industry", Lead.industry, "text"),
        "lead.country": ("Country", Lead.country, "text"),
        "lead.region": ("Region", Lead.region, "text"),
        "lead.score": ("Score", Lead.score, "number"),
        "lead.fit_score": ("Fit score", Lead.fit_score, "number"),
        "lead.engagement_score": ("Engagement score", Lead.engagement_score, "number"),
        "lead.employee_count": ("Employees", Lead.employee_count, "number"),
        "lead.consent_email": ("Email consent", Lead.consent_email, "text"),
    },
}


class SegmentError(ValueError):
    pass


# ---- ingestion -----------------------------------------------------------------------------------------------------
async def tracking_config(db: AsyncSession) -> dict:
    cfg = await app_settings.get(db, "web_tracking")
    if not cfg.get("site_key"):
        cfg = await app_settings.put(db, "web_tracking", {**cfg, "site_key": "cs_" + secrets.token_urlsafe(18)})
    return cfg


def _clean_event(name: str) -> str:
    name = (name or "").strip().lower()
    if not EVENT_NAME.match(name):
        raise SegmentError("Event names are lowercase letters, digits and _ . : - (up to 60 characters)")
    return name


def _props(props) -> dict:
    """Small, flat properties only (strings, numbers, booleans), so the store stays queryable and bounded."""
    out = {}
    for k, v in list((props or {}).items())[:30] if isinstance(props, dict) else []:
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[str(k)[:60]] = v[:300] if isinstance(v, str) else v
    return out


def _when(value) -> datetime:
    now = datetime.now(timezone.utc)
    if isinstance(value, datetime):
        when = value
    elif isinstance(value, str):
        try:
            when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return now
    else:
        return now
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return min(when, now) if when > now - timedelta(days=30) else now  # no future or long-past backdating


async def resolve_person(db: AsyncSession, email: str | None = None, contact_id=None, lead_id=None) -> tuple[Contact | None, Lead | None]:
    if contact_id:
        return await db.get(Contact, contact_id), None
    if lead_id:
        return None, await db.get(Lead, lead_id)
    email = (email or "").strip().lower()
    if not email:
        return None, None
    contact = (await db.execute(select(Contact).where(func.lower(Contact.email) == email, Contact.status != "erased"))).scalars().first()
    if contact:
        return contact, None
    lead = (await db.execute(select(Lead).where(func.lower(Lead.email) == email, Lead.status != "converted")
                             .order_by(Lead.created_at.desc()))).scalars().first()
    return None, lead


def record(db: AsyncSession, event: str, *, contact: Contact | None = None, lead: Lead | None = None, anonymous_id: str | None = None,
           url: str | None = None, properties: dict | None = None, source: str = "web", occurred_at=None) -> BehaviorEvent:
    ev = BehaviorEvent(event=event, contact_id=contact.id if contact else None, lead_id=lead.id if lead else None,
                       account_id=contact.account_id if contact else None, anonymous_id=(anonymous_id or None) and anonymous_id[:64],
                       url=(url or None) and url[:1000], properties=_props(properties), source=source, occurred_at=_when(occurred_at))
    db.add(ev)
    return ev


async def identify(db: AsyncSession, anonymous_id: str, email: str) -> tuple[Contact | None, Lead | None, int]:
    """Link a visitor's anonymous history to the contact or lead with this email. Returns how many events moved."""
    contact, lead = await resolve_person(db, email)
    if not anonymous_id or (contact is None and lead is None):
        return contact, lead, 0
    values = {"contact_id": contact.id, "account_id": contact.account_id} if contact else {"lead_id": lead.id}
    moved = (await db.execute(update(BehaviorEvent).where(BehaviorEvent.anonymous_id == anonymous_id[:64],
                                                          BehaviorEvent.contact_id.is_(None), BehaviorEvent.lead_id.is_(None))
                              .values(**values))).rowcount
    return contact, lead, moved or 0


async def ingest_web(db: AsyncSession, body: dict) -> dict:
    """The website snippet's batch: {site_key, anonymous_id, events: [{event, url, properties, ts}], identify?: {email}}."""
    from app.services import leads as lead_svc

    cfg = await app_settings.get(db, "web_tracking")
    if not cfg.get("enabled") or not cfg.get("site_key") or not secrets.compare_digest(str(body.get("site_key", "")), cfg["site_key"]):
        raise SegmentError("Unknown site key")
    anon = str(body.get("anonymous_id") or "")[:64]
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", anon):
        raise SegmentError("A visitor id is required")
    contact = lead = None
    ident = body.get("identify") or {}
    if isinstance(ident, dict) and ident.get("email"):
        contact, lead, _ = await identify(db, anon, str(ident["email"]))
    elif anon:  # a returning visitor identified earlier: attribute new events to them
        prior = (await db.execute(select(BehaviorEvent.contact_id, BehaviorEvent.lead_id).where(
            BehaviorEvent.anonymous_id == anon, or_(BehaviorEvent.contact_id.is_not(None), BehaviorEvent.lead_id.is_not(None)))
            .order_by(BehaviorEvent.id.desc()).limit(1))).first()
        if prior:
            contact = await db.get(Contact, prior[0]) if prior[0] else None
            lead = await db.get(Lead, prior[1]) if prior[1] else None
    stored = 0
    for e in (body.get("events") or [])[:50]:
        if not isinstance(e, dict):
            continue
        try:
            name = _clean_event(str(e.get("event", "")))
        except SegmentError:
            continue
        record(db, name, contact=contact, lead=lead, anonymous_id=anon, url=e.get("url"), properties=e.get("properties"),
               occurred_at=e.get("ts"))
        stored += 1
        if lead is not None and lead.status != "converted" and name == "page_view":
            pricing = "/pricing" in str(e.get("url") or "").lower()
            await lead_svc.add_event(db, lead, "pricing_page_visit" if pricing else "web_visit", str(e.get("url") or "")[:200], source="web")
    if lead is not None and stored:
        await lead_svc.rescore(db, lead)
    await db.commit()
    return {"stored": stored, "identified": bool(contact or lead)}


async def ingest_api(db: AsyncSession, events: list[dict]) -> dict:
    """Server-side events (product usage, billing, support tools): each names its person by email, contact_id or
    lead_id. Unknown people are skipped and counted."""
    if len(events) > MAX_BATCH:
        raise SegmentError(f"Send at most {MAX_BATCH} events per request")
    stored, unknown, invalid = 0, 0, []
    for i, e in enumerate(events):
        try:
            name = _clean_event(str(e.get("event", "")))
            contact, lead = await resolve_person(db, e.get("email"), e.get("contact_id"), e.get("lead_id"))
        except (SegmentError, ValueError) as err:
            invalid.append({"index": i, "error": str(err)})
            continue
        if contact is None and lead is None:
            unknown += 1
            continue
        record(db, name, contact=contact, lead=lead, url=e.get("url"), properties=e.get("properties"), source="api",
               occurred_at=e.get("occurred_at"))
        stored += 1
    await db.commit()
    return {"stored": stored, "unknown_person": unknown, "invalid": invalid}


async def timeline(db: AsyncSession, *, contact_id=None, lead_id=None, limit: int = 50) -> dict:
    col = BehaviorEvent.contact_id if contact_id else BehaviorEvent.lead_id
    rid = contact_id or lead_id
    rows = (await db.execute(select(BehaviorEvent).where(col == rid).order_by(BehaviorEvent.occurred_at.desc()).limit(limit))).scalars().all()
    since = datetime.now(timezone.utc) - timedelta(days=30)
    counts = dict((await db.execute(select(BehaviorEvent.event, func.count()).where(col == rid, BehaviorEvent.occurred_at >= since)
                                    .group_by(BehaviorEvent.event))).all())
    pages = (await db.execute(select(BehaviorEvent.url, func.count()).where(col == rid, BehaviorEvent.event == "page_view",
                                                                            BehaviorEvent.occurred_at >= since)
                              .group_by(BehaviorEvent.url).order_by(func.count().desc()).limit(5))).all()
    return {"events": [{"id": r.id, "event": r.event, "label": STANDARD_EVENTS.get(r.event, r.event.replace("_", " ").capitalize()),
                        "url": r.url, "properties": r.properties, "source": r.source, "occurred_at": r.occurred_at} for r in rows],
            "last_30_days": counts, "top_pages": [{"url": u, "views": n} for u, n in pages],
            "last_seen": rows[0].occurred_at if rows else None}


# ---- segments ------------------------------------------------------------------------------------------------------
def validate_rules(obj: str, rules: dict) -> dict:
    if obj not in FIELDS:
        raise SegmentError("A segment is built on contacts or leads")
    conds = rules.get("conditions") if isinstance(rules, dict) else None
    if not isinstance(conds, list) or not conds:
        raise SegmentError("Add at least one condition")
    if len(conds) > 20:
        raise SegmentError("A segment can have at most 20 conditions")
    out = []
    for c in conds:
        if not isinstance(c, dict):
            raise SegmentError("Each condition must be an object")
        if c.get("type") == "event":
            ev = _clean_event(str(c.get("event", "")))
            days = int(c.get("within_days") or 30)
            count = int(c.get("min_count") or 1)
            if not (1 <= days <= 730) or not (1 <= count <= 1000):
                raise SegmentError("Behaviour conditions look back 1–730 days and need 1–1000 occurrences")
            out.append({"type": "event", "event": ev, "within_days": days, "min_count": count, "negate": bool(c.get("negate")),
                        "url_contains": (str(c.get("url_contains") or "").strip()[:200] or None)})
        else:
            field = c.get("field")
            if field not in FIELDS[obj]:
                raise SegmentError(f"Unknown field for a {obj} segment: {field}")
            op = c.get("op", "eq")
            if op not in OPS:
                raise SegmentError(f"Unknown operator: {op}")
            kind = FIELDS[obj][field][2]
            value = c.get("value")
            if op in ("in", "not_in"):
                value = [str(v)[:200] for v in (value if isinstance(value, list) else str(value or "").split(",")) if str(v).strip()][:50]
                if not value:
                    raise SegmentError("List conditions need at least one value")
            elif op not in ("is_set", "not_set"):
                if kind == "number":
                    try:
                        value = float(value)
                    except (TypeError, ValueError):
                        raise SegmentError(f"{FIELDS[obj][field][0]} needs a number")
                else:
                    value = str(value or "").strip()[:200]
            out.append({"type": "field", "field": field, "op": op, "value": value})
    return {"match": "any" if rules.get("match") == "any" else "all", "conditions": out}


def _field_clause(obj: str, c: dict):
    _, col, kind = FIELDS[obj][c["field"]]
    op, v = c["op"], c["value"]
    text = func.lower(col) if kind == "text" else col
    lv = (lambda x: str(x).lower()) if kind == "text" else (lambda x: x)
    if op == "eq":
        return text == lv(v)
    if op == "neq":
        return or_(col.is_(None), text != lv(v))
    if op == "in":
        return text.in_([lv(x) for x in v])
    if op == "not_in":
        return or_(col.is_(None), text.notin_([lv(x) for x in v]))
    if op == "contains":
        return func.lower(col).contains(str(v).lower())
    if op == "gte":
        return col >= v
    if op == "lte":
        return col <= v
    if op == "is_set":
        return and_(col.is_not(None), col != "") if kind == "text" else col.is_not(None)
    return or_(col.is_(None), col == "") if kind == "text" else col.is_(None)


def _event_clause(obj: str, c: dict, now: datetime):
    owner = BehaviorEvent.contact_id == Contact.id if obj == "contact" else BehaviorEvent.lead_id == Lead.id
    where = [owner, BehaviorEvent.event == c["event"], BehaviorEvent.occurred_at >= now - timedelta(days=c["within_days"])]
    if c.get("url_contains"):
        where.append(func.lower(BehaviorEvent.url).contains(c["url_contains"].lower()))
    count = select(func.count(BehaviorEvent.id)).where(*where).scalar_subquery()
    return count < c["min_count"] if c["negate"] else count >= c["min_count"]


def query(obj: str, rules: dict, now: datetime | None = None):
    """SELECT of the ids of the segment's members."""
    now = now or datetime.now(timezone.utc)
    clauses = [(_event_clause(obj, c, now) if c["type"] == "event" else _field_clause(obj, c)) for c in rules["conditions"]]
    combined = or_(*clauses) if rules["match"] == "any" else and_(*clauses)
    if obj == "contact":
        return select(Contact.id).join(Account, Account.id == Contact.account_id).where(Contact.status == "active", combined)
    return select(Lead.id).where(Lead.status != "converted", combined)


async def preview(db: AsyncSession, obj: str, rules: dict, sample: int = 10, principal=None) -> dict:
    rules = validate_rules(obj, rules)
    ids = query(obj, rules)
    total = (await db.execute(select(func.count()).select_from(ids.subquery()))).scalar_one()
    if obj == "contact":
        stmt = select(Contact, Account.name).join(Account, Account.id == Contact.account_id).where(Contact.id.in_(ids))
        if principal is not None:
            stmt = principal.scope_accounts(stmt, "contacts", Contact.account_id)
        people = [{"id": c.id, "name": c.full_name, "email": c.email, "detail": acc_name, "href": f"/contacts/{c.id}"}
                  for c, acc_name in (await db.execute(stmt.order_by(Contact.last_name, Contact.first_name).limit(sample))).unique().all()]
    else:
        stmt = select(Lead).where(Lead.id.in_(ids))
        if principal is not None and principal.is_own_scope("leads"):
            stmt = stmt.where(Lead.owner_id == principal.id)
        people = [{"id": x.id, "name": x.full_name, "email": x.email, "detail": x.company_name, "href": f"/leads/{x.id}"}
                  for x in (await db.execute(stmt.order_by(Lead.score.desc()).limit(sample))).scalars()]
    return {"count": total, "sample": people, "rules": rules}


async def refresh(db: AsyncSession, seg: Segment, emit_events: bool = True) -> dict:
    """Recompute membership; returns who entered and left."""
    from app.services.notify import emit

    now = datetime.now(timezone.utc)
    current = set((await db.execute(query(seg.object, validate_rules(seg.object, seg.rules), now))).scalars().all())
    before = set((await db.execute(select(SegmentMember.record_id).where(SegmentMember.segment_id == seg.id))).scalars().all())
    entered, left = current - before, before - current
    if left:
        await db.execute(delete(SegmentMember).where(SegmentMember.segment_id == seg.id, SegmentMember.record_id.in_(left)))
    for rid in entered:
        db.add(SegmentMember(segment_id=seg.id, record_id=rid, entered_at=now))
        if emit_events and before:  # the first computation is a baseline, not a wave of "entered" events
            emit(db, "segment.entered", "segment", seg.id, {"segment": seg.name, "object": seg.object, "record_id": str(rid)})
    seg.member_count, seg.refreshed_at = len(current), now
    await db.flush()
    return {"members": len(current), "entered": len(entered), "left": len(left)}


async def refresh_all(db: AsyncSession) -> dict:
    out = {}
    for seg in (await db.execute(select(Segment).where(Segment.active.is_(True)))).scalars().all():
        try:
            out[str(seg.id)] = await refresh(db, seg)
        except SegmentError as e:  # a rule that no longer validates (a removed field) stops only that segment
            out[str(seg.id)] = {"error": str(e)}
    await db.commit()
    return out


async def member_ids(db: AsyncSession, seg: Segment) -> list[uuid.UUID]:
    return list((await db.execute(query(seg.object, validate_rules(seg.object, seg.rules)))).scalars().all())


def segment_out(s: Segment) -> dict:
    return {"id": s.id, "name": s.name, "description": s.description, "object": s.object, "rules": s.rules, "member_count": s.member_count,
            "refreshed_at": s.refreshed_at, "active": s.active, "created_at": s.created_at}


def catalog() -> dict:
    return {"fields": {obj: [{"key": k, "label": v[0], "kind": v[2]} for k, v in f.items()] for obj, f in FIELDS.items()},
            "ops": list(OPS), "events": [{"key": k, "label": v} for k, v in STANDARD_EVENTS.items()]}
