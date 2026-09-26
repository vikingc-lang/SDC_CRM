"""Multi-pipeline stage-gate state machine (spec section 5, pillar 3) and forecasting (section 7).

Every stage carries declarative ``gate_rules`` evaluated on entry:

    min_contacts      {"min": 1}                        buying-committee size
    domain_verified   {}                                account has a real domain
    role_mapped       {"roles": ["Champion", ...]}      at least one active contact in a role
    activity_logged   {"activity_types": [...], "keywords": "regex", "since_stage": bool}
    field_present     {"field": "target_close_date"}    deal field populated ("custom:<key>" for custom fields)
    amount_approved   {}                                amount > 0 and an approved/sent/accepted quote
    keyword           {"pattern": "regex"}              mentioned anywhere in logged notes
    pain_identified   {}                                AI pain points captured or pain discussed
    signed_document   {"doc_type": "order_form"}       fully executed document
    any_of            {"rules": [...]}                  one of several rules
    loss_reason       {"min_debrief": 15}               Closed-Lost taxonomy + rep debrief
    qualification     {"framework": "meddpicc", "min": 5}   criteria confirmed (carried over from the lead)
    primary_quote     {}                                a primary quote that is approved, sent or accepted
    tax_exempt_cert   {}                                certificate on file when the deal is tax exempt

Forward moves with unmet rules are rejected (HTTP 409 + checklist) unless the
user explicitly overrides, which is written to the audit trail. The Closed-Lost
taxonomy and debrief can never be overridden.
"""
from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, Contact, Deal, DealStageHistory, Document, Pipeline, PipelineStage, Quote, Task, User
from app.services import fx, scoring
from app.services.notify import emit
from app.services.serializers import deal_card

LOSS_TAXONOMY = {
    "competitor": "Lost to competitor",
    "budget_frozen": "Budget frozen",
    "feature_gap": "Feature gap",
    "champion_departed": "Champion departed",
    "price": "Price",
    "no_decision": "No decision / status quo",
    "timing": "Timing",
    "other": "Other",
}

TRIGGER_DESCRIPTIONS = {
    "Discovery": "Extracting primary company pain points from notes",
    "Pain Fit": "Scanning transcripts for competitor mentions",
    "Solution Demo": "Drafting follow-up recap email and action items",
    "Demo Completed": "Drafting follow-up recap email and action items",
    "Joint Demo": "Drafting follow-up recap email and action items",
    "Proposal/InfoSec": "Checking deal stagnation against average velocity (14 days)",
    "Proposal Sent": "Checking deal stagnation against average velocity (14 days)",
    "Solution Design / Demo": "Drafting follow-up recap email and action items",
    "Technical Evaluation / PoC": "Scanning transcripts for competitor mentions",
    "Business Case Validation": "Checking deal stagnation against average velocity (14 days)",
    "Negotiation & Legal": "Checking deal stagnation against average velocity (14 days)",
    "won": "Provisioning onboarding workspace, contract and renewal schedule; health set to 100",
    "lost": "Logging win/loss post-mortem to vector memory",
}


def trigger_for(stage: PipelineStage) -> str | None:
    if stage.is_closed_won:
        return TRIGGER_DESCRIPTIONS["won"]
    if stage.is_closed_lost:
        return TRIGGER_DESCRIPTIONS["lost"]
    return TRIGGER_DESCRIPTIONS.get(stage.name)


class GateError(Exception):
    status_code = 409

    def __init__(self, message: str, gates: list[dict]):
        super().__init__(message)
        self.gates = gates


class GateOverrideDenied(GateError):
    """Only managers may push a deal past unmet entry criteria; the override is logged in the stage history."""

    status_code = 403


OVERRIDE_ROLES = ("sales_manager", "super_admin")


async def default_pipeline(db: AsyncSession, kind: str | None = None) -> Pipeline:
    stmt = select(Pipeline).order_by(Pipeline.is_default.desc(), Pipeline.created_at)
    if kind:
        stmt = select(Pipeline).where(Pipeline.kind == kind).order_by(Pipeline.created_at)
    pipeline = (await db.execute(stmt)).scalars().first()
    if pipeline is None:
        if kind:
            return await default_pipeline(db)
        raise LookupError("No pipeline configured. Run `python -m app.seed`.")
    return pipeline


class _Ctx:
    """Lazily loaded facts a gate may need."""

    def __init__(self, db: AsyncSession, deal: Deal, loss_reason: str | None, loss_debrief: str | None):
        self.db, self.deal, self.loss_reason, self.loss_debrief = db, deal, loss_reason, loss_debrief
        self._acts = self._roles = self._quotes = self._docs = None

    async def activities(self):
        if self._acts is None:
            rows = (
                await self.db.execute(
                    select(Activity.activity_type, Activity.summary, Activity.raw_text, Activity.subject, Activity.occurred_at, Activity.deal_id)
                    .where(Activity.account_id == self.deal.account_id, Activity.activity_type != "system")
                )
            ).all()
            self._acts = rows
        return self._acts

    async def roles(self):
        if self._roles is None:
            self._roles = (
                await self.db.execute(select(Contact.buying_role).where(Contact.account_id == self.deal.account_id, Contact.status == "active"))
            ).scalars().all()
        return self._roles

    async def quotes(self):
        if self._quotes is None:
            self._quotes = (await self.db.execute(select(Quote.status).where(Quote.deal_id == self.deal.id))).scalars().all()
        return self._quotes

    async def primary_quote_status(self):
        return (await self.db.execute(select(Quote.status).where(Quote.deal_id == self.deal.id, Quote.is_primary.is_(True)))).scalar_one_or_none()

    async def documents(self):
        if self._docs is None:
            self._docs = (
                await self.db.execute(select(Document.doc_type, Document.status).where(Document.deal_id == self.deal.id))
            ).all()
        return self._docs


def _text(a) -> str:
    return f"{a.activity_type} {a.subject or ''} {a.summary} {a.raw_text or ''}".lower()


async def _check(rule: dict, ctx: _Ctx) -> bool:
    t = rule.get("type")
    deal = ctx.deal
    if t == "min_contacts":
        return len(await ctx.roles()) >= int(rule.get("min", 1))
    if t == "domain_verified":
        return "." in (deal.account.domain or "") and not (deal.account.custom_metadata or {}).get("domain_unverified")
    if t == "role_mapped":
        return any(r in rule.get("roles", []) for r in await ctx.roles())
    if t == "activity_logged":
        types = set(rule.get("activity_types") or [])
        pattern = re.compile(rule["keywords"], re.I) if rule.get("keywords") else None
        since = deal.stage_entered_at if rule.get("since_stage") else None
        for a in await ctx.activities():
            if types and a.activity_type not in types:
                continue
            if since and a.occurred_at < since:
                continue
            if pattern and not pattern.search(_text(a)):
                continue
            return True
        return False
    if t == "field_present":
        field = rule["field"]
        value = (deal.custom_fields or {}).get(field[7:]) if field.startswith("custom:") else getattr(deal, field, None)
        if isinstance(value, dict):
            return any(str(v).strip() for v in value.values() if v is not None)
        return value not in (None, "", 0, [])
    if t == "qualification":
        q = (deal.custom_fields or {}).get("qualification") or {}
        if rule.get("framework") and q.get("framework") != rule["framework"]:
            return False
        return sum(1 for c in (q.get("criteria") or {}).values() if c.get("met")) >= int(rule.get("min", 1))
    if t == "primary_quote":
        return await ctx.primary_quote_status() in ("approved", "sent", "accepted")
    if t == "tax_exempt_cert":
        return not deal.tax_exempt or deal.tax_exempt_cert_id is not None
    if t == "amount_approved":
        return float(deal.amount or 0) > 0 and any(s in ("approved", "sent", "accepted") for s in await ctx.quotes())
    if t == "keyword":
        pattern = re.compile(rule["pattern"], re.I)
        return any(pattern.search(_text(a)) for a in await ctx.activities())
    if t == "pain_identified":
        if (deal.ai_insights or {}).get("pain_points"):
            return True
        return any(re.search(r"\b(pain|challenge|struggl|problem|manual|bottleneck)", _text(a)) for a in await ctx.activities())
    if t == "signed_document":
        return any(d.doc_type == rule.get("doc_type", "order_form") and d.status == "completed" for d in await ctx.documents())
    if t == "any_of":
        for sub in rule.get("rules", []):
            if await _check(sub, ctx):
                return True
        return False
    if t == "loss_reason":
        return bool(ctx.loss_reason) and len((ctx.loss_debrief or "").strip()) >= int(rule.get("min_debrief", 15))
    return False


async def evaluate_gates(db: AsyncSession, deal: Deal, stage: PipelineStage, loss_reason: str | None = None,
                         loss_debrief: str | None = None) -> list[dict]:
    ctx = _Ctx(db, deal, loss_reason, loss_debrief)
    rules = list(stage.gate_rules or [])
    if stage.is_closed_lost and not any(r.get("type") == "loss_reason" for r in rules):
        rules.append({"type": "loss_reason", "label": "Loss reason and rep debrief recorded", "min_debrief": 15})
    return [{"criterion": r.get("label") or r.get("type"), "met": await _check(r, ctx), "type": r.get("type")} for r in rules]


async def change_stage(
    db: AsyncSession,
    deal: Deal,
    stage: PipelineStage,
    user: User | None,
    loss_reason: str | None = None,
    override_gates: bool = False,
    loss_debrief: str | None = None,
    loss_competitor: str | None = None,
    win_debrief: str | None = None,
) -> tuple[Deal, float, list[dict], str | None]:
    """Transition a deal across a stage gate; writes audit history and returns the forecast delta."""
    if stage.pipeline_id != deal.pipeline_id:
        raise ValueError("Stage does not belong to the deal's pipeline")
    old_stage = deal.stage
    if old_stage.id == stage.id:
        return deal, 0.0, [], None
    if stage.is_closed_lost and loss_reason and loss_reason not in LOSS_TAXONOMY:
        raise ValueError(f"loss_reason must be one of {', '.join(LOSS_TAXONOMY)}")

    gates = await evaluate_gates(db, deal, stage, loss_reason, loss_debrief)
    moving_forward = stage.stage_order > old_stage.stage_order
    unmet = [g for g in gates if not g["met"]]
    if stage.is_closed_lost and unmet:
        raise GateError("A loss reason from the taxonomy and a rep debrief (15+ characters) are required", gates)
    if moving_forward and unmet and not override_gates:
        raise GateError("Stage-gate entry criteria not met", gates)
    if moving_forward and unmet and user is not None and user.role not in OVERRIDE_ROLES:
        raise GateOverrideDenied("Only a sales manager can override unmet stage-gate criteria", gates)

    rates = await fx.rates(db)
    before = _deal_weighted(deal, old_stage, rates)
    now = datetime.now(timezone.utc)
    deal.stage_id = stage.id
    deal.stage = stage
    deal.stage_entered_at = now
    deal.loss_reason = loss_reason if stage.is_closed_lost else None
    deal.loss_debrief = loss_debrief.strip() if stage.is_closed_lost and loss_debrief else None
    deal.loss_competitor = loss_competitor if stage.is_closed_lost and loss_reason == "competitor" else None
    if stage.is_closed_won and win_debrief:
        deal.win_debrief = win_debrief.strip()
    deal.closed_at = now if stage.is_closed else None

    await scoring.rescore_account(db, deal.account_id)
    if stage.is_closed_won:
        deal.account.health_score = 100
        deal.account.lifecycle_stage = "customer"
    after = _deal_weighted(deal, stage, rates)
    delta = round(after - before, 2)

    db.add(DealStageHistory(deal_id=deal.id, from_stage_id=old_stage.id, to_stage_id=stage.id, changed_by=user.id if user else None,
                            forecast_delta=delta, gate_overridden=bool(moving_forward and unmet and override_gates)))
    summary = f"Stage moved from {old_stage.name} to {stage.name}"
    if stage.is_closed_lost:
        summary += f" (loss: {LOSS_TAXONOMY.get(loss_reason, loss_reason)}{f' to {loss_competitor}' if loss_competitor else ''})"
    db.add(Activity(account_id=deal.account_id, deal_id=deal.id, user_id=user.id if user else None, activity_type="system",
                    summary=summary, sentiment="neutral", source="system"))
    if stage.is_closed:
        emit(db, "deal.closed_won" if stage.is_closed_won else "deal.closed_lost", "deal", deal.id, {
            "deal_id": str(deal.id), "account_id": str(deal.account_id), "account": deal.account.name, "title": deal.title,
            "amount": float(deal.amount or 0), "currency": deal.currency, "loss_reason": deal.loss_reason,
        })
    await db.flush()
    if stage.is_closed_won:  # lock the primary quote and raise the order when the ERP has everything it needs
        from app.services import orders

        await orders.on_closed_won(db, deal, user)
    return deal, delta, gates, trigger_for(stage)


def _deal_weighted(deal: Deal, stage: PipelineStage, rates: dict | None = None) -> float:
    if stage.is_closed_lost:
        return 0.0
    risk = 0 if stage.is_closed_won else deal.risk_score
    amount = fx.to_usd(float(deal.amount or 0), deal.currency, rates or fx.DEFAULT_RATES)
    return scoring.weighted_value(amount, stage.default_probability, risk)


async def kanban(db: AsyncSession, pipeline_id: uuid.UUID, principal=None) -> dict:
    pipeline = await db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise LookupError("Pipeline not found")
    stmt = select(Deal).where(Deal.pipeline_id == pipeline_id).order_by(Deal.amount.desc())
    if principal is not None:
        stmt = principal.scope_deals(stmt)
    deals = (await db.execute(stmt)).scalars().unique().all()
    rates = await fx.rates(db)
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    by_stage: dict[uuid.UUID, list[dict]] = defaultdict(list)
    for d in deals:
        if d.stage.is_closed and d.closed_at and d.closed_at < cutoff:
            continue
        by_stage[d.stage_id].append(deal_card(d, rates))
    columns = []
    for stage in pipeline.stages:
        cards = by_stage.get(stage.id, [])
        columns.append({
            "id": stage.id, "name": stage.name, "stage_order": stage.stage_order, "probability": stage.default_probability,
            "is_closed_won": stage.is_closed_won, "is_closed_lost": stage.is_closed_lost, "gate_rules": stage.gate_rules,
            "deals": cards,
            "metrics": {"count": len(cards), "total": round(sum(c["amount_usd"] for c in cards), 2),
                        "weighted": round(sum(c["weighted_value"] for c in cards), 2)},
        })
    return {"pipeline": {"id": pipeline.id, "name": pipeline.name, "kind": pipeline.kind}, "columns": columns}


async def forecast(db: AsyncSession, principal=None, pipeline_id: uuid.UUID | None = None) -> dict:
    stmt = select(Deal)
    if principal is not None:
        stmt = principal.scope_deals(stmt)
    if pipeline_id:
        stmt = stmt.where(Deal.pipeline_id == pipeline_id)
    deals = (await db.execute(stmt)).scalars().unique().all()
    rates = await fx.rates(db)
    usd = lambda d: fx.to_usd(float(d.amount or 0), d.currency, rates)  # noqa: E731
    wv = lambda d: scoring.weighted_value(usd(d), d.stage.default_probability, d.risk_score)  # noqa: E731
    open_deals = [d for d in deals if not d.stage.is_closed]
    won = [d for d in deals if d.stage.is_closed_won]
    lost = [d for d in deals if d.stage.is_closed_lost]
    today = date.today()
    q_start = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    q_end = (date(q_start.year, q_start.month + 2, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    by_stage = []
    for stage in sorted({d.stage.id: d.stage for d in open_deals}.values(), key=lambda s: (str(s.pipeline_id), s.stage_order)):
        ds = [d for d in open_deals if d.stage_id == stage.id]
        by_stage.append({"stage": stage.name, "count": len(ds), "total": round(sum(usd(d) for d in ds), 2),
                         "weighted": round(sum(wv(d) for d in ds), 2)})
    pipelines = {p.id: p.name for p in (await db.execute(select(Pipeline))).scalars().all()}
    by_pipeline = []
    for pid, name in pipelines.items():
        ds = [d for d in open_deals if d.pipeline_id == pid]
        if ds:
            by_pipeline.append({"pipeline": name, "count": len(ds), "total": round(sum(usd(d) for d in ds), 2), "weighted": round(sum(wv(d) for d in ds), 2)})
    by_month: dict[str, float] = defaultdict(float)
    for d in open_deals:
        if d.target_close_date:
            by_month[d.target_close_date.strftime("%Y-%m")] += wv(d)
    closed = len(won) + len(lost)
    return {
        "currency": "USD",
        "total_pipeline": round(sum(usd(d) for d in open_deals), 2),
        "weighted_pipeline": round(sum(wv(d) for d in open_deals), 2),
        "open_deals": len(open_deals),
        "at_risk_deals": sum(1 for d in open_deals if d.risk_score >= 60),
        "won_this_quarter": round(sum(usd(d) for d in won if d.closed_at and q_start <= d.closed_at.date() <= q_end), 2),
        "win_rate": round(100 * len(won) / closed, 1) if closed else None,
        "avg_deal_size": round(sum(usd(d) for d in open_deals) / len(open_deals), 2) if open_deals else 0,
        "by_stage": by_stage,
        "by_pipeline": by_pipeline,
        "by_close_month": [{"month": k, "weighted": round(v, 2)} for k, v in sorted(by_month.items())],
        "quarter": {"start": q_start, "end": q_end},
    }


async def win_loss(db: AsyncSession, principal=None) -> dict:
    stmt = select(Deal).join(PipelineStage, Deal.stage_id == PipelineStage.id).where(
        (PipelineStage.is_closed_won.is_(True)) | (PipelineStage.is_closed_lost.is_(True))
    )
    if principal is not None:
        stmt = principal.scope_deals(stmt)
    deals = (await db.execute(stmt)).scalars().unique().all()
    rates = await fx.rates(db)
    reasons: dict[str, dict] = {}
    competitors: dict[str, dict] = defaultdict(lambda: {"count": 0, "amount": 0.0})
    for d in deals:
        if d.stage.is_closed_lost:
            r = reasons.setdefault(d.loss_reason or "other", {"reason": d.loss_reason or "other", "label": LOSS_TAXONOMY.get(d.loss_reason or "other"), "count": 0, "amount": 0.0, "debriefs": []})
            r["count"] += 1
            r["amount"] += fx.to_usd(float(d.amount), d.currency, rates)
            if d.loss_debrief:
                r["debriefs"].append({"deal_id": d.id, "title": d.title, "account": d.account.name, "debrief": d.loss_debrief})
            if d.loss_competitor:
                competitors[d.loss_competitor]["count"] += 1
                competitors[d.loss_competitor]["amount"] += fx.to_usd(float(d.amount), d.currency, rates)
    won = [d for d in deals if d.stage.is_closed_won]
    lost = [d for d in deals if d.stage.is_closed_lost]
    return {
        "won": {"count": len(won), "amount": round(sum(fx.to_usd(float(d.amount), d.currency, rates) for d in won), 2)},
        "lost": {"count": len(lost), "amount": round(sum(fx.to_usd(float(d.amount), d.currency, rates) for d in lost), 2)},
        "win_rate": round(100 * len(won) / len(deals), 1) if deals else None,
        "loss_reasons": sorted(({**r, "amount": round(r["amount"], 2)} for r in reasons.values()), key=lambda r: -r["count"]),
        "competitors": sorted(({"competitor": k, **v} for k, v in competitors.items()), key=lambda c: -c["count"]),
        "win_debriefs": [{"deal_id": d.id, "title": d.title, "account": d.account.name, "debrief": d.win_debrief} for d in won if d.win_debrief],
    }


async def open_task_count(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(Task).where(Task.completed.is_(False)))).scalar_one()
