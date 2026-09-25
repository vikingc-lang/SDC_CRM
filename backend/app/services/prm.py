"""Partner relationship management (pillar 9): registrations, co-sell attribution, collateral."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account, Collateral, Deal, DealPartner, DealRegistration, DealStageHistory, Partner, PipelineStage, User,
)
from app.models.partners import TIER_ORDER
from app.services import fx
from app.services.dedup import registrable_domain
from app.services.notify import notify

EXCLUSIVITY_DAYS = 90


async def find_conflicts(db: AsyncSession, reg: DealRegistration) -> list[dict]:
    conflicts = []
    reg_domain = registrable_domain(reg.domain)
    for acc in (await db.execute(select(Account).where(Account.domain.ilike(f"%{reg_domain}")))).scalars().unique().all():
        if registrable_domain(acc.domain) != reg_domain:
            continue
        open_deals = (await db.execute(select(func.count()).select_from(Deal).join(PipelineStage, Deal.stage_id == PipelineStage.id)
                                       .where(Deal.account_id == acc.id, PipelineStage.is_closed_won.is_(False), PipelineStage.is_closed_lost.is_(False)))).scalar_one()
        conflicts.append({"type": "existing_account", "account_id": str(acc.id), "account": acc.name, "lifecycle": acc.lifecycle_stage,
                          "open_deals": open_deals, "severity": "high" if open_deals else "medium"})
    others = (await db.execute(select(DealRegistration).where(DealRegistration.id != reg.id, DealRegistration.status == "approved",
                                                              DealRegistration.exclusivity_expires_at >= date.today()))).scalars().unique().all()
    for o in others:
        if registrable_domain(o.domain) == reg_domain:
            conflicts.append({"type": "exclusivity", "registration_id": str(o.id), "partner": o.partner.name,
                              "expires": o.exclusivity_expires_at.isoformat(), "severity": "high"})
    return conflicts


async def submit(db: AsyncSession, partner: Partner, user: User, data: dict) -> DealRegistration:
    if data.get("territory") and partner.territories and data["territory"] not in partner.territories:
        raise ValueError(f"Territory {data['territory']} is outside your agreement ({', '.join(partner.territories)})")
    reg = DealRegistration(partner_id=partner.id, submitted_by=user.id, **data)
    db.add(reg)
    await db.flush()
    await db.refresh(reg, ["partner"])
    reg.conflicts = await find_conflicts(db, reg)
    managers = (await db.execute(select(User.id).where(User.role.in_(("sales_manager", "super_admin")), User.is_active.is_(True)))).scalars().all()
    notify(db, managers, "partner", f"Deal registration from {partner.name}: {reg.company_name}",
           f"{len(reg.conflicts)} potential conflict(s)" if reg.conflicts else "No conflicts detected", "/partners")
    await db.flush()
    return reg


async def decide(db: AsyncSession, reg: DealRegistration, approver: User, approve: bool, note: str | None) -> DealRegistration:
    from app.services.pipeline_service import default_pipeline

    if reg.status != "submitted":
        raise ValueError(f"Registration already {reg.status}")
    reg.decided_by, reg.decided_at, reg.decision_note = approver.id, datetime.now(timezone.utc), note
    partner_users = (await db.execute(select(User.id).where(User.partner_id == reg.partner_id))).scalars().all()
    if not approve:
        reg.status = "rejected"
        notify(db, partner_users, "partner", f"Registration rejected: {reg.company_name}", note, "/portal")
        await db.flush()
        return reg
    reg.status = "approved"
    reg.exclusivity_expires_at = date.today() + timedelta(days=EXCLUSIVITY_DAYS)
    domain = registrable_domain(reg.domain)
    account = next((a for a in (await db.execute(select(Account).where(Account.domain.ilike(f"%{domain}")))).scalars().unique().all()
                    if registrable_domain(a.domain) == domain), None)
    if account is None:
        account = Account(name=reg.company_name, domain=domain, owner_id=approver.id, custom_metadata={"source": "partner_registration"})
        db.add(account)
        await db.flush()
    pipeline = await default_pipeline(db, kind="partner")
    first = pipeline.stages[0]
    deal = Deal(title=f"{reg.company_name}: {reg.product_interest or 'partner opportunity'}", account_id=account.id, pipeline_id=pipeline.id,
                stage_id=first.id, owner_id=account.owner_id or approver.id, amount=reg.estimated_amount, currency=reg.currency, deal_type="partner",
                source="partner", risk_factors={}, ai_insights={"registration_notes": reg.notes})
    db.add(deal)
    await db.flush()
    db.add(DealStageHistory(deal_id=deal.id, from_stage_id=None, to_stage_id=first.id, changed_by=approver.id))
    role = "referral" if reg.partner.partner_type == "referral" else "resell"
    db.add(DealPartner(deal_id=deal.id, partner_id=reg.partner_id, role=role, split_pct=100, registration_id=reg.id))
    reg.deal_id = deal.id
    notify(db, partner_users, "partner", f"Registration approved: {reg.company_name}",
           f"Exclusive until {reg.exclusivity_expires_at:%b %d, %Y}", "/portal")
    await db.flush()
    return reg


def partner_rate(dp: DealPartner) -> float:
    if dp.commission_rate is not None:
        return float(dp.commission_rate)
    return float(dp.partner.referral_fee_rate if dp.role == "referral" else dp.partner.commission_rate)


async def commission_report(db: AsyncSession, partner_id=None) -> dict:
    stmt = select(DealPartner)
    if partner_id:
        stmt = stmt.where(DealPartner.partner_id == partner_id)
    rows = (await db.execute(stmt)).scalars().unique().all()
    rates = await fx.rates(db)
    partners: dict = {}
    lines = []
    for dp in rows:
        d = dp.deal
        amount = fx.to_usd(float(d.amount or 0), d.currency, rates)
        attributed = round(amount * float(dp.split_pct) / 100, 2)
        rate = partner_rate(dp)
        won = d.stage.is_closed_won
        commission = round(attributed * rate / 100, 2) if won else 0.0
        p = partners.setdefault(dp.partner_id, {"partner_id": dp.partner_id, "partner": dp.partner.name, "tier": dp.partner.tier,
                                                "attributed_pipeline": 0.0, "attributed_won": 0.0, "commission_earned": 0.0, "deals": 0})
        p["deals"] += 1
        p["attributed_won" if won else "attributed_pipeline"] += attributed if (won or not d.stage.is_closed_lost) else 0
        p["commission_earned"] += commission
        lines.append({"deal_id": d.id, "deal": d.title, "account": d.account.name, "stage": d.stage.name, "partner": dp.partner.name,
                      "role": dp.role, "split_pct": float(dp.split_pct), "rate_pct": rate, "amount_usd": amount, "attributed_usd": attributed,
                      "commission_usd": commission, "status": "earned" if won else ("lost" if d.stage.is_closed_lost else "pipeline")})
    return {"partners": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in p.items()} for p in partners.values()], "lines": lines}


def can_access_collateral(item: Collateral, partner: Partner) -> bool:
    if not item.is_published or partner.status != "active":
        return False
    if TIER_ORDER.index(partner.tier) < TIER_ORDER.index(item.min_tier):
        return False
    allowed = {d.lower() for d in (item.allowed_domains or [])}
    return not allowed or bool(allowed & {d.lower() for d in (partner.domains or [])})


def registration_out(r: DealRegistration) -> dict:
    return {"id": r.id, "partner": {"id": r.partner.id, "name": r.partner.name, "tier": r.partner.tier}, "company_name": r.company_name,
            "domain": r.domain, "contact_name": r.contact_name, "contact_email": r.contact_email, "estimated_amount": float(r.estimated_amount),
            "currency": r.currency, "territory": r.territory, "product_interest": r.product_interest, "notes": r.notes, "status": r.status,
            "exclusivity_expires_at": r.exclusivity_expires_at, "conflicts": r.conflicts, "decision_note": r.decision_note,
            "deal_id": r.deal_id, "created_at": r.created_at, "decided_at": r.decided_at}


def partner_out(p: Partner) -> dict:
    return {"id": p.id, "name": p.name, "partner_type": p.partner_type, "tier": p.tier, "domains": p.domains, "territories": p.territories,
            "commission_rate": float(p.commission_rate), "referral_fee_rate": float(p.referral_fee_rate), "status": p.status}
