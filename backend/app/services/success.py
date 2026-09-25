"""Sales-to-CS handoff and retention engine (pillar 7)."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account, Activity, Contact, Contract, Deal, OnboardingMilestone, OnboardingProject, Quote, Task,
)
from app.services.clm import _add_months, create_contract_from_quote, next_contract_number
from app.services.notify import emit, notify

MILESTONE_TEMPLATE = [
    ("Kickoff & success plan", 3),
    ("Technical setup, SSO & integrations", 14),
    ("Data migration & validation", 30),
    ("Administrator and end-user training", 45),
    ("Go-live & hypercare", 60),
    ("90-day value review", 90),
]


async def provision_onboarding(db: AsyncSession, deal: Deal) -> OnboardingProject | None:
    """Create the onboarding workspace when a deal enters Closed-Won (idempotent)."""
    existing = (await db.execute(select(OnboardingProject).where(OnboardingProject.deal_id == deal.id))).scalars().first()
    if existing or deal.deal_type == "renewal":
        return existing
    account = await db.get(Account, deal.account_id)
    contacts = (await db.execute(select(Contact).where(Contact.account_id == account.id, Contact.status == "active"))).scalars().all()
    quote = (
        await db.execute(select(Quote).where(Quote.deal_id == deal.id, Quote.status.in_(("accepted", "sent", "approved"))).order_by(Quote.created_at.desc()))
    ).scalars().first()
    contract = (await db.execute(select(Contract).where(Contract.deal_id == deal.id))).scalars().first()
    notes = (
        await db.execute(select(Activity.summary).where(Activity.account_id == account.id, Activity.activity_type.in_(("meeting", "call", "email", "note")))
                         .order_by(Activity.occurred_at.desc()).limit(8))
    ).scalars().all()
    insights = deal.ai_insights or {}
    today = date.today()
    scope = {
        "pre_sales_summary": notes[:5],
        "buyer_priorities": insights.get("pain_points", []),
        "competitive_context": insights.get("competitors", []),
        "products": [{"sku": l.product.sku, "name": l.product.name, "quantity": float(l.quantity)} for l in (quote.lines if quote else [])],
        "stakeholders": [{"name": c.full_name, "title": c.job_title, "role": c.buying_role, "email": c.email} for c in contacts],
        "commercials": {"amount": float(deal.amount or 0), "currency": deal.currency, "term_months": quote.term_months if quote else None,
                        "contract": contract.contract_number if contract else None},
    }
    project = OnboardingProject(account_id=account.id, deal_id=deal.id, contract_id=contract.id if contract else None,
                                name=f"{account.name} onboarding: {deal.title}", status="not_started", owner_id=account.owner_id,
                                kickoff_date=today + timedelta(days=3), target_go_live=today + timedelta(days=60), scope=scope)
    db.add(project)
    await db.flush()
    milestones = []
    for i, (title, offset) in enumerate(MILESTONE_TEMPLATE):
        m = OnboardingMilestone(project_id=project.id, title=title, due_date=today + timedelta(days=offset), position=i)
        db.add(m)
        milestones.append(m)
    await db.flush()
    for m in milestones[:3]:  # delivery tasks for the first phase, linked to their milestone
        db.add(Task(title=m.title, due_date=m.due_date, account_id=account.id, deal_id=deal.id, owner_id=account.owner_id,
                    assignee_id=account.owner_id, milestone_id=m.id, source="system", priority="high" if m.position == 0 else "normal"))
    db.add(Activity(account_id=account.id, deal_id=deal.id, activity_type="system", source="system", sentiment="positive",
                    summary=f"Onboarding workspace provisioned: {len(milestones)} milestones, scope and stakeholders migrated from pre-sales."))
    notify(db, [account.owner_id], "handoff", f"Onboarding ready: {account.name}", "Pre-sales scope, priorities and milestones were migrated.", "/success")
    emit(db, "onboarding.provisioned", "account", account.id, {"project": project.name, "deal_id": str(deal.id), "go_live": project.target_go_live.isoformat()})
    await db.flush()
    await db.refresh(project, ["milestones", "account", "owner"])
    return project


async def renew_contract_from_deal(db: AsyncSession, deal: Deal) -> Contract | None:
    """A won renewal without its own Order Form extends the prior contract's terms."""
    if deal.deal_type != "renewal" or deal.contract_id is None:
        return None
    if (await db.execute(select(Contract.id).where(Contract.deal_id == deal.id))).first():
        return None  # an executed order form already produced the new contract
    prior = await db.get(Contract, deal.contract_id)
    if prior is None:
        return None
    quote = (await db.execute(select(Quote).where(Quote.deal_id == deal.id, Quote.status.in_(("approved", "sent", "accepted"))))).scalars().first()
    if quote:
        return await create_contract_from_quote(db, quote, start=prior.end_date + timedelta(days=1))
    term = int((prior.terms or {}).get("term_months") or 12)
    start = prior.end_date + timedelta(days=1)
    contract = Contract(account_id=prior.account_id, deal_id=deal.id, contract_number=await next_contract_number(db),
                        name=f"{prior.name} (renewal)", start_date=start, end_date=_add_months(start, term) - timedelta(days=1),
                        currency=deal.currency, acv=deal.amount, tcv=float(deal.amount) * term / 12, payment_terms=prior.payment_terms,
                        auto_renew=prior.auto_renew, status="active", terms={**(prior.terms or {}), "renewed_from": prior.contract_number})
    db.add(contract)
    prior.status = "renewed"
    await db.flush()
    emit(db, "contract.created", "contract", contract.id, {"contract_number": contract.contract_number, "renewal_of": prior.contract_number,
                                                          "account_id": str(contract.account_id), "acv": float(contract.acv)})
    return contract


def project_out(p: OnboardingProject) -> dict:
    today = date.today()
    done = sum(1 for m in p.milestones if m.status == "done")
    overdue = [m for m in p.milestones if m.status != "done" and m.due_date and m.due_date < today]
    return {
        "id": p.id, "name": p.name, "status": "at_risk" if overdue and p.status != "completed" else p.status,
        "account": {"id": p.account.id, "name": p.account.name, "health_score": p.account.health_score},
        "deal_id": p.deal_id, "contract_id": p.contract_id, "owner": {"id": p.owner.id, "full_name": p.owner.full_name} if p.owner else None,
        "kickoff_date": p.kickoff_date, "target_go_live": p.target_go_live, "scope": p.scope,
        "progress": round(100 * done / len(p.milestones)) if p.milestones else 0, "overdue": len(overdue),
        "milestones": [{"id": m.id, "title": m.title, "due_date": m.due_date, "status": m.status, "completed_at": m.completed_at,
                        "overdue": m in overdue} for m in p.milestones],
        "created_at": p.created_at,
    }
