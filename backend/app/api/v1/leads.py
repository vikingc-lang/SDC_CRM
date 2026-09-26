"""Leads (capture, qualification, routing, conversion) plus public intake for web forms and webhooks."""
import hashlib
import secrets
import time
import uuid
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.database import get_db
from app.core.rbac import Principal, authorize
from app.models import AssignmentRule, EngagementEvent, IntakeKey, Lead, User
from app.services import app_settings, enrichment, leads as svc

router = APIRouter(tags=["leads"])
intake = APIRouter(prefix="/intake", tags=["lead intake (public)"])

Source = Literal["web_form", "campaign", "trade_show", "partner", "outbound", "import", "api", "manual"]


class LeadIn(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    job_title: str | None = None
    company_name: str | None = None
    domain: str | None = None
    industry: str | None = None
    employee_count: int | None = Field(default=None, ge=0)
    annual_revenue: float | None = Field(default=None, ge=0)
    country: str | None = None
    source: Source = "outbound"
    campaign: str | None = None
    consent: Literal["granted", "denied", "unknown"] = "unknown"
    privacy_regime: Literal["GDPR", "CCPA", "OTHER"] | None = None
    owner_id: uuid.UUID | None = None


class LeadUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    job_title: str | None = None
    company_name: str | None = None
    domain: str | None = None
    industry: str | None = None
    employee_count: int | None = Field(default=None, ge=0)
    annual_revenue: float | None = Field(default=None, ge=0)
    country: str | None = None
    status: Literal["new", "working"] | None = None
    owner_id: uuid.UUID | None = None
    consent_email: Literal["granted", "denied", "unknown"] | None = None


class QualificationIn(BaseModel):
    framework: Literal["bant", "meddpicc"]
    criteria: dict[str, dict] = {}


class EventIn(BaseModel):
    event_type: str
    detail: str | None = Field(default=None, max_length=500)
    occurred_at: datetime | None = None


class ConvertIn(BaseModel):
    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    create_deal: bool = True
    deal_title: str | None = Field(default=None, max_length=255)
    amount: float = Field(default=0, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    pipeline_id: uuid.UUID | None = None
    target_close_date: date | None = None
    owner_id: uuid.UUID | None = None
    buying_role: Literal["Champion", "Decision Maker", "Economic Buyer", "Influencer", "Evaluator", "Legal Counsel", "Procurement"] = "Champion"
    override: bool = False


class DisqualifyIn(BaseModel):
    reason: Literal["no_budget", "no_need", "not_icp", "competitor", "student_or_personal", "duplicate", "unresponsive", "other"]
    note: str | None = None


async def _fresh(db: AsyncSession, lead_id: uuid.UUID) -> Lead:
    return (await db.execute(select(Lead).where(Lead.id == lead_id).execution_options(populate_existing=True))).scalars().unique().one()


def _scope(stmt, p: Principal):
    return stmt.where(or_(Lead.owner_id == p.id, Lead.owner_id.is_(None))) if p.is_own_scope("leads") else stmt


async def _lead(db: AsyncSession, p: Principal, lead_id: uuid.UUID) -> Lead:
    lead = await db.get(Lead, lead_id)
    if lead is None or (p.is_own_scope("leads") and lead.owner_id not in (None, p.id)):
        raise HTTPException(404, "Lead not found")
    return lead


# ---- internal API ----------------------------------------------------------------------------
@router.get("/leads")
async def list_leads(status: str | None = None, source: str | None = None, owner: str | None = None, q: str | None = None,
                     min_score: int | None = None, limit: int = Query(200, le=500), db: AsyncSession = Depends(get_db),
                     p: Principal = Depends(authorize("leads", "read"))):
    stmt = _scope(select(Lead), p).order_by(Lead.score.desc(), Lead.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Lead.status.in_(status.split(",")))
    if source:
        stmt = stmt.where(Lead.source.in_(source.split(",")))
    if owner == "me":
        stmt = stmt.where(Lead.owner_id == p.id)
    elif owner == "unassigned":
        stmt = stmt.where(Lead.owner_id.is_(None))
    if min_score is not None:
        stmt = stmt.where(Lead.score >= min_score)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Lead.email.ilike(like), Lead.company_name.ilike(like), Lead.first_name.ilike(like), Lead.last_name.ilike(like)))
    return [svc.lead_out(l) for l in (await db.execute(stmt)).scalars().unique().all()]


@router.get("/leads/summary")
async def funnel(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("leads", "read"))):
    rows = (await db.execute(_scope(select(Lead.status, Lead.source, func.count()), p).group_by(Lead.status, Lead.source))).all()
    by_status, by_source = {}, {}
    for status, source, n in rows:
        by_status[status] = by_status.get(status, 0) + n
        by_source[source] = by_source.get(source, 0) + n
    cfg = await app_settings.get(db, "lead_scoring")
    return {"by_status": by_status, "by_source": by_source, "mql_threshold": cfg["mql_threshold"], "total": sum(by_status.values())}


@router.post("/leads", status_code=201)
async def create_lead(body: LeadIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("leads", "create"))):
    data = body.model_dump()
    try:
        lead, merged = await svc.capture(db, data, source=body.source, campaign=body.campaign, owner_id=body.owner_id or
                                         (p.id if p.user.role in ("sdr", "account_executive") else None))
    except svc.LeadError as exc:
        raise HTTPException(422, str(exc))
    await db.commit()
    return {**svc.lead_out(await _fresh(db, lead.id), detail=True), "merged": merged}


@router.get("/leads/{lead_id}")
async def get_lead(lead_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("leads", "read"))):
    lead = await _lead(db, p, lead_id)
    events = (await db.execute(select(EngagementEvent).where(EngagementEvent.lead_id == lead.id).order_by(EngagementEvent.occurred_at.desc()))).scalars().all()
    return {**svc.lead_out(lead, detail=True), "frameworks": {k: [{"key": a, "label": b} for a, b in v] for k, v in svc.FRAMEWORKS.items()},
            "events": [{"id": e.id, "event_type": e.event_type, "detail": e.detail, "points": e.points, "source": e.source,
                        "occurred_at": e.occurred_at} for e in events]}


@router.patch("/leads/{lead_id}")
async def update_lead(lead_id: uuid.UUID, body: LeadUpdate, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("leads", "update"))):
    lead = await _lead(db, p, lead_id)
    if lead.status == "converted":
        raise HTTPException(409, "Converted leads are read-only")
    data = body.model_dump(exclude_unset=True)
    if "domain" in data:
        data["domain"] = svc._clean_domain(data["domain"])
    if "consent_email" in data and data["consent_email"] != lead.consent_email:
        lead.consent_at, lead.consent_source = datetime.now(timezone.utc), f"updated by {p.user.full_name}"
    for k, v in data.items():
        setattr(lead, k, v)
    if "country" in data:
        lead.region = enrichment.region_for(lead.country)
    if "owner_id" in data:
        lead.assigned_at = datetime.now(timezone.utc) if lead.owner_id else None
    lead.duplicate_matches = await svc.find_duplicates(db, lead)
    await svc.rescore(db, lead)
    await db.commit()
    return svc.lead_out(await _fresh(db, lead.id), detail=True)


@router.put("/leads/{lead_id}/qualification")
async def qualify(lead_id: uuid.UUID, body: QualificationIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("leads", "update"))):
    lead = await _lead(db, p, lead_id)
    keys = {k for k, _ in svc.FRAMEWORKS[body.framework]}
    unknown = set(body.criteria) - keys
    if unknown:
        raise HTTPException(422, f"Unknown criteria for {body.framework}: {', '.join(sorted(unknown))}")
    lead.qualification_framework = body.framework
    lead.qualification = {k: {"met": bool(v.get("met")), "note": (v.get("note") or "")[:500] or None} for k, v in body.criteria.items()}
    if lead.status in ("new", "mql"):
        lead.status = "working"
    summary = svc.qualification_summary(lead)
    cfg = await app_settings.get(db, "lead_scoring")
    if summary["met"] >= int(cfg["conversion_min_criteria"].get(body.framework, 0)) and lead.status == "working":
        lead.status = "sql"
    await db.commit()
    return svc.lead_out(await _fresh(db, lead.id), detail=True)


@router.post("/leads/{lead_id}/events", status_code=201)
async def log_event(lead_id: uuid.UUID, body: EventIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("leads", "update"))):
    lead = await _lead(db, p, lead_id)
    try:
        await svc.add_event(db, lead, body.event_type, body.detail, source="crm_user", occurred_at=body.occurred_at)
    except svc.LeadError as exc:
        raise HTTPException(422, str(exc))
    await svc.rescore(db, lead)
    await db.commit()
    return svc.lead_out(await _fresh(db, lead.id), detail=True)


@router.post("/leads/{lead_id}/route")
async def reroute(lead_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("leads", "update"))):
    lead = await _lead(db, p, lead_id)
    result = await svc.route(db, lead, force=True)
    await db.commit()
    return {**svc.lead_out(await _fresh(db, lead.id), detail=True), "routing": {"rule": result["rule"]}}


@router.post("/leads/{lead_id}/enrich")
async def enrich_lead(lead_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("leads", "update"))):
    lead = await _lead(db, p, lead_id)
    await enrichment.enrich(db, lead)
    lead.duplicate_matches = await svc.find_duplicates(db, lead)
    await svc.rescore(db, lead)
    await db.commit()
    return svc.lead_out(await _fresh(db, lead.id), detail=True)


@router.post("/leads/{lead_id}/convert")
async def convert(lead_id: uuid.UUID, body: ConvertIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("leads", "update"))):
    lead = await _lead(db, p, lead_id)
    if not (p.can("accounts", "create") or body.account_id) or not p.can("contacts", "create") or (body.create_deal and not p.can("deals", "create")):
        raise HTTPException(403, "Your role cannot create the account, contact or opportunity for this conversion")
    if body.override and p.user.role not in ("sales_manager", "super_admin"):
        raise HTTPException(403, "Only sales managers can override the qualification requirement")
    try:
        result = await svc.convert(db, lead, p.user, **body.model_dump())
    except svc.LeadError as exc:
        await db.rollback()
        raise HTTPException(422, str(exc))
    await db.commit()
    return result


@router.post("/leads/{lead_id}/disqualify")
async def disqualify(lead_id: uuid.UUID, body: DisqualifyIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("leads", "update"))):
    lead = await _lead(db, p, lead_id)
    if lead.status == "converted":
        raise HTTPException(409, "Converted leads cannot be disqualified")
    lead.status, lead.disqualified_reason, lead.disqualify_note = "disqualified", body.reason, body.note
    await db.commit()
    return svc.lead_out(await _fresh(db, lead.id), detail=True)


@router.post("/leads/{lead_id}/recycle")
async def recycle(lead_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("leads", "update"))):
    lead = await _lead(db, p, lead_id)
    if lead.status != "disqualified":
        raise HTTPException(409, "Only disqualified leads can be recycled")
    lead.status, lead.disqualified_reason = "recycled", None
    await svc.rescore(db, lead)
    await db.commit()
    return svc.lead_out(await _fresh(db, lead.id), detail=True)


# ---- admin: scoring, routing, intake keys --------------------------------------------------------------
class RuleIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    priority: int = Field(default=100, ge=0, le=10000)
    active: bool = True
    criteria: dict = {}
    method: Literal["round_robin", "account_owner", "specific"] = "round_robin"
    assignee_ids: list[uuid.UUID] = []


class KeyIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    kind: Literal["web_form", "webhook"] = "web_form"
    source: Source = "web_form"
    campaign: str | None = None


@router.get("/admin/lead-settings")
async def get_settings(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("leads", "read"))):
    return await app_settings.get(db, "lead_scoring")


@router.put("/admin/lead-settings")
async def put_settings(body: dict, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    current = await app_settings.get(db, "lead_scoring")
    if "mql_threshold" in body and not 0 < int(body["mql_threshold"]) <= 100:
        raise HTTPException(422, "MQL threshold must be between 1 and 100")
    if "score_weights" in body:
        w = body["score_weights"]
        if abs(float(w.get("fit", 0)) + float(w.get("engagement", 0)) - 1) > 0.001:
            raise HTTPException(422, "Fit and engagement weights must add up to 1")
    merged = {**current, **{k: v for k, v in body.items() if k in current}}
    value = await app_settings.put(db, "lead_scoring", merged)
    stats = await svc.rescore_open(db, value)
    return {**value, "rescored": stats["leads_rescored"]}


@router.get("/admin/assignment-rules")
async def rules(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("leads", "read"))):
    rows = (await db.execute(select(AssignmentRule).order_by(AssignmentRule.priority))).scalars().all()
    names = {u.id: u.full_name for u in (await db.execute(select(User))).scalars().all()}
    return [{"id": r.id, "name": r.name, "priority": r.priority, "active": r.active, "criteria": r.criteria, "method": r.method,
             "assignees": [{"id": a, "full_name": names.get(uuid.UUID(str(a)))} for a in r.assignee_ids]} for r in rows]


@router.post("/admin/assignment-rules", status_code=201)
async def create_rule(body: RuleIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    if body.method in ("round_robin", "specific") and not body.assignee_ids:
        raise HTTPException(422, "Pick at least one assignee")
    rule = AssignmentRule(**{**body.model_dump(), "assignee_ids": [str(a) for a in body.assignee_ids]})
    db.add(rule)
    await db.commit()
    return {"id": rule.id}


@router.put("/admin/assignment-rules/{rule_id}")
async def update_rule(rule_id: uuid.UUID, body: RuleIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    rule = await db.get(AssignmentRule, rule_id)
    if rule is None:
        raise HTTPException(404, "Rule not found")
    for k, v in {**body.model_dump(), "assignee_ids": [str(a) for a in body.assignee_ids]}.items():
        setattr(rule, k, v)
    await db.commit()
    return {"id": rule.id}


@router.delete("/admin/assignment-rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    rule = await db.get(AssignmentRule, rule_id)
    if rule:
        await db.delete(rule)
        await db.commit()


@router.get("/admin/intake-keys")
async def keys(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "read"))):
    rows = (await db.execute(select(IntakeKey).order_by(IntakeKey.created_at.desc()))).scalars().all()
    return [{"id": k.id, "name": k.name, "kind": k.kind, "key_prefix": k.key_prefix, "source": k.source, "campaign": k.campaign,
             "active": k.active, "last_used_at": k.last_used_at, "created_at": k.created_at} for k in rows]


@router.post("/admin/intake-keys", status_code=201)
async def create_key(body: KeyIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("admin", "update"))):
    raw = f"{'cf' if body.kind == 'web_form' else 'ck'}_{secrets.token_urlsafe(24)}"
    key = IntakeKey(name=body.name, kind=body.kind, key_hash=hashlib.sha256(raw.encode()).hexdigest(), key_prefix=raw[:10],
                    source=body.source, campaign=body.campaign, created_by=p.id)
    db.add(key)
    log_action(db, "create", "intake_keys", None, f"{body.kind} key '{body.name}'")
    await db.commit()
    return {"id": key.id, "key": raw, "key_prefix": key.key_prefix, "note": "Store this key now; it is not shown again."}


@router.delete("/admin/intake-keys/{key_id}", status_code=204)
async def revoke_key(key_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    key = await db.get(IntakeKey, key_id)
    if key:
        key.active = False
        await db.commit()


# ---- public intake (web forms, campaign / trade-show / marketing webhooks) ---------------------------------
_hits: dict[str, list[float]] = {}
RATE_PER_MINUTE = 120


async def _key(db: AsyncSession, raw: str | None) -> IntakeKey:
    if not raw:
        raise HTTPException(401, "Intake key required (X-Cirra-Key header or ?key=)")
    key = (await db.execute(select(IntakeKey).where(IntakeKey.key_hash == hashlib.sha256(raw.encode()).hexdigest()))).scalars().first()
    if key is None or not key.active:
        raise HTTPException(401, "Unknown or revoked intake key")
    now = time.monotonic()
    window = [t for t in _hits.get(key.key_prefix, []) if now - t < 60]
    if len(window) >= RATE_PER_MINUTE:
        raise HTTPException(429, "Too many submissions; slow down")
    _hits[key.key_prefix] = [*window, now]
    key.last_used_at = datetime.now(timezone.utc)
    return key


EVENT_FOR_SOURCE = {"web_form": "form_submit", "trade_show": "trade_show_scan", "campaign": "form_submit"}


@intake.get("/forms/{raw_key}")
async def form_config(raw_key: str, db: AsyncSession = Depends(get_db)):
    key = await _key(db, raw_key)
    if key.kind != "web_form":
        raise HTTPException(404, "Not a web form key")
    await db.commit()
    return {"name": key.name, "campaign": key.campaign,
            "fields": ["first_name", "last_name", "email", "company_name", "job_title", "phone", "country", "employee_count", "message"]}


@intake.post("/leads", status_code=201)
async def intake_lead(request: Request, key: str | None = Query(default=None), x_cirra_key: str | None = Header(default=None),
                      db: AsyncSession = Depends(get_db)):
    ik = await _key(db, x_cirra_key or key)
    ctype = request.headers.get("content-type", "")
    data = dict(await request.form()) if "form" in ctype else await request.json()
    if not isinstance(data, dict):
        raise HTTPException(422, "Send one lead as a JSON object or form post")
    if data.get("website_url_confirm"):  # honeypot field, hidden from humans
        await db.commit()
        return {"status": "accepted"}
    try:
        lead, merged = await svc.capture(db, data, source=ik.source, campaign=data.get("campaign") or ik.campaign,
                                         event_type=EVENT_FOR_SOURCE.get(ik.source, "form_submit"),
                                         event_detail=(data.get("message") or ik.name)[:300])
    except svc.LeadError as exc:
        raise HTTPException(422, str(exc))
    await db.commit()
    return {"status": "accepted", "lead_id": lead.id, "merged": merged}


class IntakeEvent(BaseModel):
    email: str
    event_type: str
    detail: str | None = Field(default=None, max_length=500)
    occurred_at: datetime | None = None
    create_lead_if_missing: bool = False


@intake.post("/events", status_code=202)
async def intake_events(events: list[IntakeEvent], key: str | None = Query(default=None), x_cirra_key: str | None = Header(default=None),
                        db: AsyncSession = Depends(get_db)):
    ik = await _key(db, x_cirra_key or key)
    if ik.kind != "webhook":
        raise HTTPException(403, "Engagement events need a webhook key")
    if len(events) > 500:
        raise HTTPException(413, "At most 500 events per call")
    results = []
    for e in events:
        try:
            results.append(await svc.track(db, e.email, e.event_type, e.detail, e.occurred_at, ik.source,
                                           e.create_lead_if_missing, ik.campaign))
        except svc.LeadError as exc:
            results.append({"matched": None, "error": str(exc)})
    await db.commit()
    return {"accepted": len(events), "results": results}
