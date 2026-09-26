import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import Principal, authorize
from app.models import (
    Account, Activity, Attachment, Contact, Contract, CustomFieldDefinition, Deal, DealAlert, DealStageHistory, OnboardingProject,
    PipelineStage, ProductUsage, SupportTicket, Task,
)
from app.services import custom_fields, dedup, erp, fx, hierarchy, scoring
from app.services.clm import contract_out
from app.services.notify import emit
from app.services.serializers import activity_out, contact_out, deal_card, task_out, user_brief
from app.services.success import project_out

router = APIRouter(prefix="/accounts", tags=["accounts"])

Tier = Literal["SMB", "Mid-Market", "Enterprise"]
Lifecycle = Literal["prospect", "customer", "churned", "partner"]


class Location(BaseModel):
    type: str = "office"
    city: str
    region: str | None = None
    country: str


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    domain: str = Field(min_length=3, max_length=255)
    industry: str | None = None
    tier: Tier = "Mid-Market"
    owner_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    country: str | None = Field(default=None, max_length=64)
    force: bool = False  # create even if a likely duplicate exists


class AccountUpdate(BaseModel):
    name: str | None = None
    industry: str | None = None
    industry_code: str | None = None
    tier: Tier | None = None
    lifecycle_stage: Lifecycle | None = None
    owner_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    clear_parent: bool = False
    annual_revenue: Decimal | None = Field(default=None, ge=0)
    employee_count: int | None = Field(default=None, ge=0)
    locations: list[Location] | None = None
    alt_domains: list[str] | None = None
    legal_name: str | None = None
    tax_id: str | None = None
    billing_address: dict | None = None
    payment_terms: str | None = None
    country: str | None = Field(default=None, max_length=64)
    region: Literal["NA", "EMEA", "APAC", "LATAM"] | None = None
    custom_fields: dict | None = None


def _normalize_domain(domain: str) -> str:
    d = domain.strip().lower().removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return d.split("/")[0]


@router.get("")
async def list_accounts(
    skip: int = 0,
    limit: int = Query(50, le=500),
    search: str | None = None,
    lifecycle: Lifecycle | None = None,
    parent_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    p: Principal = Depends(authorize("accounts", "read")),
):
    stmt = p.scope_accounts(select(Account)).order_by(Account.name).offset(skip).limit(limit)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Account.name.ilike(like), Account.domain.ilike(like), Account.industry.ilike(like), Account.legal_name.ilike(like)))
    if lifecycle:
        stmt = stmt.where(Account.lifecycle_stage == lifecycle)
    if parent_id:
        stmt = stmt.where(Account.parent_id == parent_id)
    accounts = (await db.execute(stmt)).scalars().unique().all()
    ids = [a.id for a in accounts]
    if not ids:
        return []
    rates = await fx.rates(db)
    open_rows = (
        await db.execute(
            select(Deal.account_id, Deal.amount, Deal.currency).join(PipelineStage, Deal.stage_id == PipelineStage.id)
            .where(Deal.account_id.in_(ids), PipelineStage.is_closed_won.is_(False), PipelineStage.is_closed_lost.is_(False))
        )
    ).all()
    open_map: dict = {}
    for acc_id, amount, cur in open_rows:
        n, total = open_map.get(acc_id, (0, 0.0))
        open_map[acc_id] = (n + 1, total + fx.to_usd(float(amount), cur, rates))
    last_map = dict((await db.execute(
        select(Activity.account_id, func.max(Activity.occurred_at)).where(Activity.account_id.in_(ids), Activity.activity_type != "system").group_by(Activity.account_id)
    )).all())
    child_map = dict((await db.execute(select(Account.parent_id, func.count()).where(Account.parent_id.in_(ids)).group_by(Account.parent_id))).all())
    return [
        {
            "id": a.id, "name": a.name, "domain": a.domain, "industry": a.industry, "tier": a.tier, "health": a.health_score,
            "lifecycle_stage": a.lifecycle_stage, "churn_risk": a.churn_risk, "relationship_strength": a.relationship_strength,
            "credit_hold": a.credit_hold, "parent_id": a.parent_id, "subsidiaries": child_map.get(a.id, 0),
            "annual_revenue": float(a.annual_revenue) if a.annual_revenue is not None else None, "employee_count": a.employee_count,
            "owner": user_brief(a.owner), "open_deals": open_map.get(a.id, (0, 0.0))[0], "open_pipeline": round(open_map.get(a.id, (0, 0.0))[1], 2),
            "contacts": len(a.contacts), "last_activity_at": last_map.get(a.id),
        }
        for a in accounts
    ]


@router.post("", status_code=201)
async def create_account(body: AccountCreate, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("accounts", "create"))):
    domain = _normalize_domain(body.domain)
    if not body.force:
        match = await dedup.find_account_duplicate(db, body.name, domain)
        if match and match["score"] >= dedup.ACCOUNT_SUGGEST_AT:
            return JSONResponse(status_code=409, content={"detail": "Possible duplicate account", "duplicate": {
                **{k: v for k, v in match.items() if k != "account"}, "account": {k: str(v) for k, v in match["account"].items()}}})
    from app.services.enrichment import region_for
    account = Account(name=body.name.strip(), domain=domain, industry=body.industry, tier=body.tier, owner_id=body.owner_id or p.id,
                      parent_id=body.parent_id, country=body.country, region=region_for(body.country), custom_metadata={})
    db.add(account)
    try:
        await db.commit()
    except IntegrityError:
        raise HTTPException(409, f"An account with domain {domain} already exists")
    return {"id": account.id, "name": account.name, "domain": account.domain}


async def _get(db: AsyncSession, p: Principal, account_id: uuid.UUID, resource: str = "accounts") -> Account:
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    await p.ensure_account(db, account_id, resource)
    return account


@router.patch("/{account_id}")
async def update_account(account_id: uuid.UUID, body: AccountUpdate, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("accounts", "update"))):
    account = await _get(db, p, account_id)
    data = body.model_dump(exclude_unset=True)
    if data.pop("clear_parent", False):
        account.parent_id = None
    if data.get("parent_id"):
        if await hierarchy.would_create_cycle(db, account.id, data["parent_id"]):
            raise HTTPException(422, "That parent would create a hierarchy cycle")
    if "custom_fields" in data:
        try:
            account.custom_metadata = {
                **(account.custom_metadata or {}),
                **await custom_fields.validate(db, "account", data.pop("custom_fields"), {k: v for k, v in (account.custom_metadata or {}).items() if k != "health_breakdown"}),
            }
        except custom_fields.CustomFieldError as exc:
            raise HTTPException(422, str(exc))
    if "locations" in data:
        data["locations"] = [loc.model_dump() if hasattr(loc, "model_dump") else loc for loc in body.locations or []]
    if "alt_domains" in data:
        data["alt_domains"] = sorted({_normalize_domain(d) for d in data["alt_domains"] or [] if d})
    if "country" in data and "region" not in data:
        from app.services.enrichment import region_for
        data["region"] = region_for(data["country"])
    for field, value in data.items():
        setattr(account, field, value)
    emit(db, "account.updated", "account", account.id, {"account_id": str(account.id), "name": account.name, "changed": sorted(data)})
    await db.commit()
    return {"id": account.id}


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("accounts", "delete"))):
    account = await _get(db, p, account_id)
    if (await db.execute(select(func.count()).select_from(Deal).where(Deal.account_id == account_id))).scalar_one():
        raise HTTPException(409, "Account has deals. Close or delete them first.")
    await db.delete(account)
    await db.commit()


@router.get("/{account_id}/hierarchy")
async def account_hierarchy(account_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("accounts", "read"))):
    await _get(db, p, account_id)
    return await hierarchy.hierarchy(db, account_id)


@router.get("/{account_id}/duplicates")
async def account_duplicates(account_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("accounts", "read"))):
    account = await _get(db, p, account_id)
    return [c for c in await dedup.account_candidates(db) if account.id in (c["a"]["id"], c["b"]["id"])]


@router.get("/{account_id}/360")
async def account_360(account_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("accounts", "read"))):
    account = await _get(db, p, account_id)
    rates = await fx.rates(db)
    deals = (await db.execute(select(Deal).where(Deal.account_id == account_id).order_by(Deal.created_at.desc()))).scalars().unique().all()
    activities = (await db.execute(select(Activity).where(Activity.account_id == account_id).order_by(Activity.occurred_at.desc()).limit(60))).scalars().unique().all()
    atts = (await db.execute(select(Attachment).where(Attachment.account_id == account_id))).scalars().all()
    att_by_activity: dict = {}
    for a in atts:
        if a.activity_id:
            att_by_activity.setdefault(a.activity_id, []).append({"id": a.id, "filename": a.filename, "size_bytes": a.size_bytes, "content_type": a.content_type})
    tasks = (await db.execute(select(Task).where(Task.account_id == account_id).order_by(Task.completed, Task.due_date.nulls_last()))).scalars().unique().all()
    history = (await db.execute(
        select(DealStageHistory).join(Deal, DealStageHistory.deal_id == Deal.id).where(Deal.account_id == account_id).order_by(DealStageHistory.changed_at.desc()).limit(20)
    )).scalars().unique().all()
    contracts = (await db.execute(select(Contract).where(Contract.account_id == account_id).order_by(Contract.end_date.desc()))).scalars().unique().all()
    projects = (await db.execute(select(OnboardingProject).where(OnboardingProject.account_id == account_id))).scalars().unique().all()
    tickets = (await db.execute(select(SupportTicket).where(SupportTicket.account_id == account_id).order_by(SupportTicket.opened_at.desc()).limit(20))).scalars().all()
    usage = (await db.execute(select(ProductUsage).where(ProductUsage.account_id == account_id).order_by(ProductUsage.metric_date.desc()).limit(12))).scalars().all()
    alerts = (await db.execute(select(DealAlert).join(Deal, DealAlert.deal_id == Deal.id).where(Deal.account_id == account_id, DealAlert.resolved_at.is_(None)))).scalars().unique().all()
    defs = (await db.execute(select(CustomFieldDefinition).where(CustomFieldDefinition.entity == "account").order_by(CustomFieldDefinition.label))).scalars().all()
    parent = await db.get(Account, account.parent_id) if account.parent_id else None
    children = (await db.execute(select(Account.id, Account.name, Account.health_score).where(Account.parent_id == account_id))).all()
    role_order = ("Champion", "Decision Maker", "Economic Buyer", "Legal Counsel", "Procurement", "Influencer", "Evaluator", "Blocker")
    rank = {r: i for i, r in enumerate(role_order)}
    contacts = sorted(account.contacts, key=lambda c: (c.status != "active", rank.get(c.buying_role, len(rank))))
    cards = [deal_card(d, rates) for d in deals]
    open_cards = [c for c in cards if not c["is_won"] and not c["is_lost"]]
    ar = await erp.ar_summary(db, account)
    meta = account.custom_metadata or {}
    return {
        "account": {
            "id": account.id, "name": account.name, "domain": account.domain, "alt_domains": account.alt_domains, "industry": account.industry,
            "industry_code": account.industry_code, "tier": account.tier, "lifecycle_stage": account.lifecycle_stage,
            "annual_revenue": float(account.annual_revenue) if account.annual_revenue is not None else None, "employee_count": account.employee_count,
            "locations": account.locations, "country": account.country, "region": account.region,
            "credit_risk": {"score": account.credit_risk_score, "band": account.credit_risk_band, "factors": account.credit_risk_factors},
            "health_score": account.health_score, "health_breakdown": meta.get("health_breakdown"),
            "relationship_strength": account.relationship_strength, "churn_risk": account.churn_risk, "churn_factors": account.churn_factors,
            "owner": user_brief(account.owner), "created_at": account.created_at,
            "customer_master": {"legal_name": account.legal_name, "tax_id": account.tax_id, "billing_address": account.billing_address,
                                "payment_terms": account.payment_terms, "credit_limit": float(account.credit_limit) if account.credit_limit is not None else None,
                                "credit_hold": account.credit_hold, "erp_customer_id": account.erp_customer_id, "erp_synced_at": account.erp_synced_at},
            "custom_fields": {k: v for k, v in meta.items() if k not in ("health_breakdown",)},
            "parent": {"id": parent.id, "name": parent.name} if parent else None,
            "subsidiaries": [{"id": c.id, "name": c.name, "health_score": c.health_score} for c in children],
        },
        "custom_field_definitions": [{"key": d.key, "label": d.label, "field_type": d.field_type, "options": d.options, "required": d.required} for d in defs],
        "contacts": [contact_out(c) for c in contacts],
        "deals": cards,
        "recent_activities": [activity_out(a, attachments=att_by_activity.get(a.id)) for a in activities],
        "tasks": [task_out(t) for t in tasks],
        "stage_history": [{"deal_id": h.deal_id, "from": h.from_stage.name if h.from_stage else None, "to": h.to_stage.name,
                           "forecast_delta": float(h.forecast_delta), "gate_overridden": h.gate_overridden, "by": user_brief(h.user), "at": h.changed_at} for h in history],
        "contracts": [contract_out(c) for c in contracts],
        "onboarding": [project_out(pr) for pr in projects],
        "support_tickets": [{"id": t.id, "subject": t.subject, "severity": t.severity, "status": t.status, "opened_at": t.opened_at} for t in tickets],
        "usage": [{"date": u.metric_date, "active_users": u.active_users, "licensed_users": u.licensed_users, "feature_adoption": float(u.feature_adoption)} for u in reversed(usage)],
        "finance": {k: v for k, v in ar.items() if k != "invoices"} | {"invoices": ar["invoices"][:10]},
        "alerts": [{"id": a.id, "deal_id": a.deal_id, "kind": a.kind, "severity": a.severity, "message": a.message} for a in alerts],
        "files": [{"id": a.id, "filename": a.filename, "size_bytes": a.size_bytes, "content_type": a.content_type, "created_at": a.created_at} for a in atts],
        "summary": {
            "open_pipeline": round(sum(c["amount_usd"] for c in open_cards), 2),
            "weighted_pipeline": round(sum(c["weighted_value"] for c in open_cards), 2),
            "won_revenue": round(sum(c["amount_usd"] for c in cards if c["is_won"]), 2),
            "active_contract_value": round(sum(fx.to_usd(float(c.acv), c.currency, rates) for c in contracts if c.status == "active"), 2),
            "has_champion": any(c.buying_role in scoring.CHAMPION_ROLES and c.status == "active" for c in contacts),
        },
    }


class TicketCreate(BaseModel):
    subject: str = Field(min_length=2, max_length=300)
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    external_id: str | None = None


@router.post("/{account_id}/tickets", status_code=201)
async def create_ticket(account_id: uuid.UUID, body: TicketCreate, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("success", "create"))):
    await _get(db, p, account_id, "success")
    db.add(SupportTicket(account_id=account_id, **body.model_dump()))
    await db.flush()
    await scoring.rescore_account(db, account_id)
    await db.commit()
    return {"status": "created"}


@router.patch("/{account_id}/tickets/{ticket_id}")
async def update_ticket(account_id: uuid.UUID, ticket_id: uuid.UUID, status: Literal["open", "pending", "resolved", "closed"],
                        db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("success", "update"))):
    t = await db.get(SupportTicket, ticket_id)
    if t is None or t.account_id != account_id:
        raise HTTPException(404, "Ticket not found")
    await p.ensure_account(db, account_id, "success")
    t.status = status
    if status in ("resolved", "closed"):
        from datetime import datetime, timezone
        t.resolved_at = datetime.now(timezone.utc)
    await db.flush()
    await scoring.rescore_account(db, account_id)
    await db.commit()
    return {"status": t.status}


class UsagePoint(BaseModel):
    metric_date: date
    active_users: int = Field(ge=0)
    licensed_users: int = Field(gt=0)
    feature_adoption: float = Field(default=0, ge=0, le=100)


@router.post("/{account_id}/usage")
async def ingest_usage(account_id: uuid.UUID, points: list[UsagePoint], db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("success", "create"))):
    """Product adoption telemetry (e.g. pushed nightly by the product analytics pipeline)."""
    await _get(db, p, account_id, "success")
    for pt in points:
        existing = (await db.execute(select(ProductUsage).where(ProductUsage.account_id == account_id, ProductUsage.metric_date == pt.metric_date))).scalars().first()
        if existing:
            existing.active_users, existing.licensed_users, existing.feature_adoption = pt.active_users, pt.licensed_users, pt.feature_adoption
        else:
            db.add(ProductUsage(account_id=account_id, **pt.model_dump()))
    await db.flush()
    await scoring.rescore_account(db, account_id)
    await db.commit()
    return {"ingested": len(points)}
