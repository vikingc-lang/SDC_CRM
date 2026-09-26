"""Lead capture, hygiene, scoring, routing and conversion (lead-to-order, steps 1-3).

Capture   web forms, campaign/trade-show webhooks, partner and SDR entry all call ``capture``:
          normalise -> real-time dedup (open lead with the same email is updated, not duplicated;
          matching contacts/accounts are flagged) -> enrichment -> consent -> score -> route.
Scoring   fit (explicit, 0-100) against the ICP: industry, size, revenue, geography, seniority;
          engagement (implicit, 0-100) = sum of event points decayed by a half-life;
          score = weighted blend. Crossing the MQL threshold marks the lead MQL and notifies the owner.
Routing   ordered assignment rules (criteria -> round robin / account owner / specific user);
          fallback round robin across active SDRs.
Convert   one transaction creates or links Account, Contact and Opportunity, carries consent,
          qualification and engagement history across, and requires the framework minimum
          (BANT 3/4, MEDDPICC 5/8 by default) unless a manager overrides.
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account, Activity, AppSetting, AssignmentRule, Contact, Deal, DealStageHistory, EngagementEvent, Lead, Pipeline, User,
)
from app.services import app_settings, enrichment, privacy, scoring
from app.services.dedup import find_account_duplicate, registrable_domain
from app.services.notify import emit, notify

FRAMEWORKS = {
    "bant": [("budget", "Budget"), ("authority", "Authority"), ("need", "Need"), ("timeline", "Timeline")],
    "meddpicc": [("metrics", "Metrics"), ("economic_buyer", "Economic buyer"), ("decision_criteria", "Decision criteria"),
                 ("decision_process", "Decision process"), ("paper_process", "Paper process"), ("identify_pain", "Identified pain"),
                 ("champion", "Champion"), ("competition", "Competition")],
}
SOURCES = ("web_form", "campaign", "trade_show", "partner", "outbound", "import", "api", "manual")
EVENT_TYPES = ("form_submit", "content_download", "pricing_page_visit", "webinar_registered", "webinar_attended", "email_open",
               "email_click", "trade_show_scan", "meeting_booked", "web_visit")
DISQUALIFY_REASONS = ("no_budget", "no_need", "not_icp", "competitor", "student_or_personal", "duplicate", "unresponsive", "other")
OPEN_STATUSES = ("new", "working", "mql", "sql", "recycled")

ALIASES = {
    "first_name": ("first_name", "firstname", "firstName", "given_name"),
    "last_name": ("last_name", "lastname", "lastName", "family_name", "surname"),
    "email": ("email", "email_address", "emailAddress", "work_email"),
    "phone": ("phone", "phone_number", "phoneNumber", "mobile"),
    "job_title": ("job_title", "title", "jobTitle", "position"),
    "company_name": ("company_name", "company", "companyName", "organization", "organisation"),
    "domain": ("domain", "website", "company_website", "companyWebsite", "url"),
    "industry": ("industry", "vertical"),
    "employee_count": ("employee_count", "employees", "company_size", "companySize"),
    "annual_revenue": ("annual_revenue", "revenue", "annualRevenue"),
    "country": ("country", "country_code", "countryCode"),
    "campaign": ("campaign", "utm_campaign", "campaign_name"),
    "external_id": ("external_id", "id_from_source", "submission_id", "badge_id"),
}


class LeadError(ValueError):
    pass


def _clean_domain(value: str | None) -> str | None:
    if not value:
        return None
    v = re.sub(r"^https?://", "", str(value).strip().lower()).split("/")[0].removeprefix("www.")
    return v or None


def _int(value) -> int | None:
    try:
        return int(float(str(value).replace(",", ""))) if value not in (None, "") else None
    except ValueError:
        return None


def normalize(data: dict) -> dict:
    out: dict = {}
    for field, names in ALIASES.items():
        for n in names:
            if data.get(n) not in (None, ""):
                out[field] = data[n]
                break
    out["email"] = str(out["email"]).strip().lower() if out.get("email") else None
    out["domain"] = _clean_domain(out.get("domain")) or (None if enrichment.is_free_mail(enrichment.email_domain(out["email"]))
                                                          else enrichment.email_domain(out["email"]))
    out["employee_count"] = _int(out.get("employee_count"))
    rev = out.get("annual_revenue")
    out["annual_revenue"] = Decimal(str(rev).replace(",", "")) if rev not in (None, "") and _int(rev) is not None else None
    for k in ("first_name", "last_name", "job_title", "company_name", "industry", "country", "phone", "campaign", "external_id"):
        if out.get(k) is not None:
            out[k] = str(out[k]).strip()[:250] or None
    return out


def consent_from(data: dict, region: str | None) -> dict:
    raw = data.get("consent", data.get("consent_email", data.get("gdpr_consent")))
    if isinstance(raw, str):
        raw = raw.strip().lower()
    granted = raw in (True, "true", "yes", "on", "1", "granted", 1)
    denied = raw in (False, "false", "no", "0", "denied", 0)
    regime = data.get("privacy_regime") or ("GDPR" if region == "EMEA" else None)
    return {"consent_email": "granted" if granted else "denied" if denied else "unknown",
            "privacy_regime": regime if regime in ("GDPR", "CCPA", "OTHER") else None,
            "consent_source": (data.get("consent_text") or data.get("consent_source") or None)}


# ---- hygiene -------------------------------------------------------------------------
async def find_duplicates(db: AsyncSession, lead: Lead) -> list[dict]:
    matches: list[dict] = []
    if lead.email:
        for other in (await db.execute(select(Lead).where(func.lower(Lead.email) == lead.email, Lead.id != lead.id))).scalars().all():
            matches.append({"type": "lead", "id": str(other.id), "name": other.full_name, "status": other.status, "reason": "same email", "score": 1.0})
        contact = (await db.execute(select(Contact).where(func.lower(Contact.email) == lead.email))).scalars().first()
        if contact:
            matches.append({"type": "contact", "id": str(contact.id), "name": contact.full_name, "account_id": str(contact.account_id),
                            "reason": "existing contact with this email", "score": 1.0})
    if lead.company_name or lead.domain:
        acc = await find_account_duplicate(db, lead.company_name or lead.domain, lead.domain)
        if acc and (acc["score"] >= 0.85 or acc.get("same_domain")):
            full = await db.get(Account, acc["account"]["id"])
            matches.append({"type": "account", "id": str(acc["account"]["id"]), "name": acc["account"]["name"], "score": round(acc["score"], 3),
                            "reason": "; ".join(acc.get("reasons") or ["similar company"]),
                            "owner_id": str(full.owner_id) if full and full.owner_id else None,
                            "lifecycle": full.lifecycle_stage if full else None})
    return matches


# ---- scoring -----------------------------------------------------------------------------
def fit_score(lead: Lead, cfg: dict) -> tuple[int, dict]:
    icp, w = cfg["icp"], cfg["fit_weights"]
    parts: dict[str, dict] = {}
    ind = (lead.industry or "").strip().lower()
    parts["industry"] = {"value": lead.industry, "points": w["industry"] if ind and ind in {i.lower() for i in icp["industries"]} else 0}
    emp, lo, hi = lead.employee_count, icp.get("min_employees") or 0, icp.get("max_employees") or 10**9
    size = 0.0 if emp is None else 1.0 if lo <= emp <= hi else (emp / lo if emp < lo else 0.6)
    parts["size"] = {"value": emp, "points": round(w["size"] * size)}
    rev, min_rev = float(lead.annual_revenue) if lead.annual_revenue is not None else None, float(icp.get("min_revenue") or 0)
    revenue = 0.0 if rev is None else 1.0 if rev >= min_rev else rev / min_rev if min_rev else 1.0
    parts["revenue"] = {"value": rev, "points": round(w["revenue"] * revenue)}
    geo = bool((lead.region and lead.region in icp.get("regions", [])) or
               (lead.country and lead.country.lower() in {c.lower() for c in icp.get("countries", [])}))
    parts["geography"] = {"value": lead.region or lead.country, "points": w["geography"] if geo else 0}
    title = (lead.job_title or "").lower()
    senior = any(re.search(rf"\b{re.escape(t)}\b", title) for t in icp.get("senior_titles", []))
    manager = any(re.search(rf"\b{re.escape(t)}\b", title) for t in icp.get("manager_titles", []))
    parts["seniority"] = {"value": lead.job_title, "points": w["seniority"] if senior else round(w["seniority"] / 2) if manager else 0}
    max_points = sum(w.values()) or 1
    return min(100, round(100 * sum(p["points"] for p in parts.values()) / max_points)), parts


def engagement_score(events: list[EngagementEvent], cfg: dict, now: datetime | None = None) -> tuple[int, dict]:
    now = now or datetime.now(timezone.utc)
    half = max(1, int(cfg.get("engagement_half_life_days") or 30))
    by_type: dict[str, float] = {}
    for e in events:
        age = max(0.0, (now - e.occurred_at).total_seconds() / 86400)
        by_type[e.event_type] = by_type.get(e.event_type, 0) + e.points * math.pow(0.5, age / half)
    total = sum(by_type.values())
    return min(100, round(total)), {k: round(v, 1) for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])}


async def rescore(db: AsyncSession, lead: Lead, cfg: dict | None = None) -> bool:
    """Recompute scores; returns True when the lead has just become an MQL."""
    cfg = cfg or await app_settings.get(db, "lead_scoring")
    events = (await db.execute(select(EngagementEvent).where(EngagementEvent.lead_id == lead.id))).scalars().all()
    fit, fit_parts = fit_score(lead, cfg)
    eng, eng_parts = engagement_score(events, cfg)
    sw = cfg["score_weights"]
    lead.fit_score, lead.engagement_score = fit, eng
    lead.score = min(100, round(sw["fit"] * fit + sw["engagement"] * eng))
    lead.score_breakdown = {"fit": fit_parts, "engagement": eng_parts, "weights": sw, "mql_threshold": cfg["mql_threshold"]}
    if lead.status in ("new", "working", "recycled") and lead.score >= cfg["mql_threshold"]:
        lead.status, lead.mql_at = "mql", datetime.now(timezone.utc)
        return True
    return False


# ---- routing -----------------------------------------------------------------------------
def _matches(rule: AssignmentRule, lead: Lead, account_match: dict | None) -> bool:
    c = rule.criteria or {}
    if c.get("regions") and lead.region not in c["regions"]:
        return False
    if c.get("countries") and (lead.country or "").lower() not in {x.lower() for x in c["countries"]}:
        return False
    if c.get("industries") and (lead.industry or "").lower() not in {x.lower() for x in c["industries"]}:
        return False
    if c.get("sources") and lead.source not in c["sources"]:
        return False
    if c.get("min_employees") and (lead.employee_count or 0) < int(c["min_employees"]):
        return False
    if c.get("max_employees") and (lead.employee_count or 0) > int(c["max_employees"]):
        return False
    if c.get("named_domains") and registrable_domain(lead.domain) not in {registrable_domain(d) for d in c["named_domains"]}:
        return False
    if c.get("existing_account") and not account_match:
        return False
    if c.get("min_score") and lead.score < int(c["min_score"]):
        return False
    return True


async def rescore_open(db: AsyncSession, cfg: dict | None = None) -> dict:
    """Nightly decay: engagement halves every 30 days, so open leads are rescored on a schedule."""
    cfg = cfg or await app_settings.get(db, "lead_scoring")
    leads = (await db.execute(select(Lead).where(Lead.status.in_(OPEN_STATUSES)))).scalars().all()
    new_mqls = 0
    for lead in leads:
        new_mqls += bool(await rescore(db, lead, cfg))
    await db.commit()
    return {"leads_rescored": len(leads), "new_mqls": new_mqls}


async def route(db: AsyncSession, lead: Lead, force: bool = False) -> dict:
    if lead.owner_id and not force:
        return {"owner_id": lead.owner_id, "rule": None}
    account_match = next((m for m in lead.duplicate_matches or [] if m["type"] == "account"), None)
    rules = (await db.execute(select(AssignmentRule).where(AssignmentRule.active.is_(True)).order_by(AssignmentRule.priority))).scalars().all()
    owner, used = None, None
    for rule in rules:
        if not _matches(rule, lead, account_match):
            continue
        if rule.method == "account_owner":
            owner = uuid.UUID(account_match["owner_id"]) if account_match and account_match.get("owner_id") else None
        elif rule.method == "specific":
            owner = uuid.UUID(str(rule.assignee_ids[0])) if rule.assignee_ids else None
        elif rule.assignee_ids:
            owner = uuid.UUID(str(rule.assignee_ids[rule.rr_index % len(rule.assignee_ids)]))
            rule.rr_index += 1
        if owner and await _active(db, owner):
            used = rule
            break
        owner = None
    if owner is None:  # fallback: round robin across active SDRs
        sdrs = (await db.execute(select(User.id).where(User.role == "sdr", User.is_active.is_(True)).order_by(User.full_name))).scalars().all()
        if sdrs:
            state = await db.get(AppSetting, "lead_rr_fallback")
            idx = int((state.value or {}).get("index", 0)) if state else 0
            owner = sdrs[idx % len(sdrs)]
            if state:
                state.value = {"index": idx + 1}
            else:
                db.add(AppSetting(key="lead_rr_fallback", value={"index": idx + 1}))
    lead.owner_id, lead.assignment_rule_id = owner, used.id if used else None
    lead.assigned_at = datetime.now(timezone.utc) if owner else None
    if owner:
        notify(db, [owner], "lead", f"New lead assigned: {lead.full_name} ({lead.company_name or lead.domain or 'unknown company'})",
               f"Score {lead.score} · source {lead.source}" + (f" · rule '{used.name}'" if used else " · SDR round robin"), f"/leads/{lead.id}")
    return {"owner_id": owner, "rule": used.name if used else ("sdr_round_robin" if owner else None)}


async def _active(db: AsyncSession, user_id: uuid.UUID) -> bool:
    u = await db.get(User, user_id)
    return bool(u and u.is_active and u.role != "partner")


# ---- capture -------------------------------------------------------------------------------
async def add_event(db: AsyncSession, lead: Lead | None, event_type: str, detail: str | None = None, source: str | None = None,
                    occurred_at: datetime | None = None, contact_id: uuid.UUID | None = None, cfg: dict | None = None) -> EngagementEvent:
    if event_type not in EVENT_TYPES:
        raise LeadError(f"event_type must be one of {', '.join(EVENT_TYPES)}")
    cfg = cfg or await app_settings.get(db, "lead_scoring")
    ev = EngagementEvent(lead_id=lead.id if lead else None, contact_id=contact_id, event_type=event_type, detail=(detail or "")[:500] or None,
                         points=int(cfg["event_points"].get(event_type, 0)), source=source,
                         occurred_at=occurred_at or datetime.now(timezone.utc))
    db.add(ev)
    await db.flush()
    return ev


async def capture(db: AsyncSession, data: dict, *, source: str, campaign: str | None = None, owner_id: uuid.UUID | None = None,
                  event_type: str | None = None, event_detail: str | None = None) -> tuple[Lead, bool]:
    """Create (or update) a lead from any channel. Returns (lead, merged_into_existing)."""
    if source not in SOURCES:
        raise LeadError(f"source must be one of {', '.join(SOURCES)}")
    fields = normalize(data)
    if not fields.get("email") and not (fields.get("last_name") and (fields.get("company_name") or fields.get("domain"))):
        raise LeadError("A lead needs an email, or a name and company")
    if fields.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", fields["email"]):
        raise LeadError("Email address is not valid")
    cfg = await app_settings.get(db, "lead_scoring")
    existing = None
    if fields.get("external_id"):
        existing = (await db.execute(select(Lead).where(Lead.external_id == fields["external_id"]))).scalars().first()
    if existing is None and fields.get("email"):
        existing = (await db.execute(select(Lead).where(func.lower(Lead.email) == fields["email"], Lead.status.in_(OPEN_STATUSES))
                                     .order_by(Lead.created_at))).scalars().first()
    merged = existing is not None
    lead = existing or Lead(source=source, status="new")
    for k, v in fields.items():
        if v is not None and getattr(lead, k, None) in (None, ""):
            setattr(lead, k, v)
    lead.campaign = lead.campaign or campaign
    if owner_id and not lead.owner_id:
        lead.owner_id, lead.assigned_at = owner_id, datetime.now(timezone.utc)
    if lead.country and not lead.region:
        lead.region = enrichment.region_for(lead.country)
    if not merged:
        db.add(lead)
        await db.flush()
    await enrichment.enrich(db, lead)
    consent = consent_from(data, lead.region)
    if consent["consent_email"] != "unknown" or consent["privacy_regime"]:
        if consent["consent_email"] != "unknown":
            lead.consent_email, lead.consent_at = consent["consent_email"], datetime.now(timezone.utc)
        lead.privacy_regime = consent["privacy_regime"] or lead.privacy_regime
        lead.consent_source = consent["consent_source"] or f"{source}{f' / {campaign}' if campaign else ''}"
    lead.duplicate_matches = await find_duplicates(db, lead)
    if event_type:
        await add_event(db, lead, event_type, event_detail or campaign, source=source, cfg=cfg)
    became_mql = await rescore(db, lead, cfg)
    routed = await route(db, lead)
    if became_mql and lead.owner_id and not routed.get("rule"):
        notify(db, [lead.owner_id], "lead", f"MQL: {lead.full_name} scored {lead.score}", lead.company_name, f"/leads/{lead.id}")
    if not merged:
        emit(db, "lead.created", "lead", lead.id, {"lead_id": str(lead.id), "source": lead.source, "campaign": lead.campaign,
                                                  "domain": lead.domain, "score": lead.score})
    await db.flush()
    return lead, merged


async def track(db: AsyncSession, email: str, event_type: str, detail: str | None, occurred_at: datetime | None, source: str,
                create_if_missing: bool = False, campaign: str | None = None) -> dict:
    """Engagement from marketing systems: attach to the open lead or the contact with this email."""
    email = (email or "").strip().lower()
    if not email:
        raise LeadError("email is required")
    lead = (await db.execute(select(Lead).where(func.lower(Lead.email) == email, Lead.status.in_(OPEN_STATUSES)).order_by(Lead.created_at))).scalars().first()
    if lead:
        await add_event(db, lead, event_type, detail, source, occurred_at)
        became = await rescore(db, lead)
        if became and lead.owner_id:
            notify(db, [lead.owner_id], "lead", f"MQL: {lead.full_name} scored {lead.score}", f"Latest: {event_type.replace('_', ' ')}", f"/leads/{lead.id}")
        return {"matched": "lead", "lead_id": lead.id, "score": lead.score, "status": lead.status}
    contact = (await db.execute(select(Contact).where(func.lower(Contact.email) == email))).scalars().first()
    if contact:
        await add_event(db, None, event_type, detail, source, occurred_at, contact_id=contact.id)
        return {"matched": "contact", "contact_id": contact.id}
    if create_if_missing:
        lead, _ = await capture(db, {"email": email}, source="campaign" if source not in SOURCES else source, campaign=campaign,
                                event_type=event_type, event_detail=detail)
        return {"matched": "created", "lead_id": lead.id, "score": lead.score, "status": lead.status}
    return {"matched": None}


# ---- qualification & conversion ------------------------------------------------------------
def qualification_summary(lead: Lead) -> dict:
    fw = lead.qualification_framework
    q = lead.qualification or {}
    items = [{"key": k, "label": label, "met": bool((q.get(k) or {}).get("met")), "note": (q.get(k) or {}).get("note")}
             for k, label in FRAMEWORKS[fw]]
    return {"framework": fw, "items": items, "met": sum(i["met"] for i in items), "total": len(items)}


def _tier(employees: int | None) -> str:
    return "SMB" if (employees or 0) < 200 else "Mid-Market" if employees < 2000 else "Enterprise"


async def convert(db: AsyncSession, lead: Lead, user: User, *, account_id: uuid.UUID | None = None, contact_id: uuid.UUID | None = None,
                  create_deal: bool = True, deal_title: str | None = None, amount: float = 0, currency: str = "USD",
                  pipeline_id: uuid.UUID | None = None, target_close_date: date | None = None, owner_id: uuid.UUID | None = None,
                  buying_role: str = "Champion", override: bool = False) -> dict:
    if lead.status == "converted":
        raise LeadError("Lead is already converted")
    if lead.status == "disqualified":
        raise LeadError("Recycle the lead before converting it")
    cfg = await app_settings.get(db, "lead_scoring")
    q = qualification_summary(lead)
    need = int(cfg["conversion_min_criteria"].get(lead.qualification_framework, 0))
    if q["met"] < need and not override:
        raise LeadError(f"{q['framework'].upper()} qualification incomplete: {q['met']}/{q['total']} confirmed, {need} required to convert")
    owner_id = owner_id or lead.owner_id or user.id
    now = datetime.now(timezone.utc)

    # Account: explicit, else the matched account, else a new one
    account = await db.get(Account, account_id) if account_id else None
    if account is None:
        match = next((m for m in lead.duplicate_matches or [] if m["type"] in ("account",)), None) or \
            next((m for m in lead.duplicate_matches or [] if m["type"] == "contact"), None)
        if match:
            account = await db.get(Account, uuid.UUID(match["id"] if match["type"] == "account" else match["account_id"]))
    created_account = account is None
    if account is None:
        if not lead.domain or enrichment.is_free_mail(lead.domain):
            raise LeadError("A company domain is required to create the account (add a website or pick an existing account)")
        if not lead.company_name:
            raise LeadError("Company name is required to create the account")
        account = Account(name=lead.company_name, domain=lead.domain, industry=lead.industry, employee_count=lead.employee_count,
                          annual_revenue=lead.annual_revenue, country=lead.country, region=lead.region, tier=_tier(lead.employee_count),
                          owner_id=owner_id, lifecycle_stage="prospect", custom_metadata={"source": f"lead:{lead.source}"})
        db.add(account)
        await db.flush()

    # Contact: explicit, else same email, else new
    contact = await db.get(Contact, contact_id) if contact_id else None
    if contact is None and lead.email:
        contact = (await db.execute(select(Contact).where(func.lower(Contact.email) == lead.email))).scalars().first()
    created_contact = contact is None
    if contact is None:
        contact = Contact(account_id=account.id, first_name=lead.first_name or (lead.email or "Unknown").split("@")[0],
                          last_name=lead.last_name or "", email=lead.email, phone=lead.phone, job_title=lead.job_title,
                          buying_role=buying_role, custom_fields={})
        db.add(contact)
        await db.flush()
    if lead.consent_email != "unknown" or lead.privacy_regime:
        await privacy.record_consent(db, contact, consent_email=None if lead.consent_email == "unknown" else lead.consent_email,
                                     regime=lead.privacy_regime, basis="consent" if lead.consent_email == "granted" else None,
                                     source=f"lead:{lead.source}")

    deal = None
    if create_deal:
        pipeline = await db.get(Pipeline, pipeline_id) if pipeline_id else None
        if pipeline is None:
            from app.services.pipeline_service import default_pipeline
            pipeline = await default_pipeline(db)
        stage = next(s for s in sorted(pipeline.stages, key=lambda s: s.stage_order) if not s.is_closed_won and not s.is_closed_lost)
        deal = Deal(title=(deal_title or f"{account.name}: new opportunity").strip(), account_id=account.id, pipeline_id=pipeline.id,
                    stage_id=stage.id, amount=Decimal(str(amount or 0)), currency=(currency or "USD").upper(), primary_contact_id=contact.id,
                    target_close_date=target_close_date, original_close_date=target_close_date, owner_id=owner_id, source="lead",
                    lead_id=lead.id, risk_factors={}, ai_insights={},
                    custom_fields={"qualification": {"framework": q["framework"], "criteria": lead.qualification or {},
                                                     "lead_score": lead.score, "from_lead": str(lead.id)}})
        db.add(deal)
        await db.flush()
        db.add(DealStageHistory(deal_id=deal.id, from_stage_id=None, to_stage_id=stage.id, changed_by=user.id))

    # carry engagement history to the contact and log the conversion on the timeline
    events = (await db.execute(select(EngagementEvent).where(EngagementEvent.lead_id == lead.id))).scalars().all()
    for e in events:
        e.contact_id = contact.id
    met = ", ".join(i["label"] for i in q["items"] if i["met"]) or "none"
    db.add(Activity(account_id=account.id, deal_id=deal.id if deal else None, contact_id=contact.id, user_id=user.id, activity_type="system",
                    source="system", sentiment="neutral",
                    summary=(f"Lead converted: {lead.full_name} from {lead.source.replace('_', ' ')}{f' ({lead.campaign})' if lead.campaign else ''}. "
                             f"Score {lead.score} (fit {lead.fit_score}, engagement {lead.engagement_score}); "
                             f"{q['framework'].upper()} {q['met']}/{q['total']}: {met}; {len(events)} engagement events."
                             + (" Qualification override by manager." if override and q['met'] < need else ""))))
    lead.status, lead.converted_at, lead.converted_by = "converted", now, user.id
    lead.converted_account_id, lead.converted_contact_id = account.id, contact.id
    lead.converted_deal_id = deal.id if deal else None
    await db.flush()
    await scoring.rescore_account(db, account.id)
    emit(db, "lead.converted", "lead", lead.id, {"lead_id": str(lead.id), "account_id": str(account.id), "contact_id": str(contact.id),
                                                "deal_id": str(deal.id) if deal else None, "source": lead.source, "campaign": lead.campaign})
    return {"account_id": account.id, "contact_id": contact.id, "deal_id": deal.id if deal else None,
            "created": {"account": created_account, "contact": created_contact, "deal": deal is not None}}


def lead_out(lead: Lead, detail: bool = False) -> dict:
    out = {
        "id": lead.id, "first_name": lead.first_name, "last_name": lead.last_name, "full_name": lead.full_name, "email": lead.email,
        "phone": lead.phone, "job_title": lead.job_title, "company_name": lead.company_name, "domain": lead.domain,
        "industry": lead.industry, "employee_count": lead.employee_count,
        "annual_revenue": float(lead.annual_revenue) if lead.annual_revenue is not None else None,
        "country": lead.country, "region": lead.region, "source": lead.source, "campaign": lead.campaign, "status": lead.status,
        "owner": {"id": lead.owner.id, "full_name": lead.owner.full_name} if lead.owner else None,
        "score": lead.score, "fit_score": lead.fit_score, "engagement_score": lead.engagement_score,
        "consent_email": lead.consent_email, "privacy_regime": lead.privacy_regime,
        "duplicates": len([m for m in lead.duplicate_matches or [] if m["type"] != "account"]),
        "account_match": next((m for m in lead.duplicate_matches or [] if m["type"] == "account"), None),
        "qualification": qualification_summary(lead),
        "mql_at": lead.mql_at, "converted_at": lead.converted_at, "created_at": lead.created_at, "updated_at": lead.updated_at,
    }
    if detail:
        out.update({
            "score_breakdown": lead.score_breakdown, "duplicate_matches": lead.duplicate_matches, "enrichment": lead.enrichment,
            "enriched_at": lead.enriched_at, "consent_source": lead.consent_source, "consent_at": lead.consent_at,
            "assigned_at": lead.assigned_at, "assignment_rule_id": lead.assignment_rule_id,
            "disqualified_reason": lead.disqualified_reason, "disqualify_note": lead.disqualify_note,
            "converted": {"account_id": lead.converted_account_id, "contact_id": lead.converted_contact_id, "deal_id": lead.converted_deal_id},
        })
    return out
