"""Stage-gate pipeline progression machine (specification section 5) and
pipeline forecasting (section 7)."""
from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, Contact, Deal, DealStageHistory, Pipeline, PipelineStage, Task, User
from app.services import scoring
from app.services.serializers import deal_card

TRIGGER_DESCRIPTIONS = {
    "Discovery": "Extracting primary company pain points from notes",
    "Pain Fit": "Scanning transcripts for competitor mentions",
    "Solution Demo": "Drafting follow-up recap email and action items",
    "Proposal/InfoSec": "Checking deal stagnation against average velocity (14 days)",
    "Closed-Won": "Dispatching account onboarding event; health set to 100",
    "Closed-Lost": "Logging win/loss post-mortem to vector memory",
}


class GateError(Exception):
    def __init__(self, message: str, gates: list[dict]):
        super().__init__(message)
        self.gates = gates


async def default_pipeline(db: AsyncSession) -> Pipeline:
    pipeline = (await db.execute(select(Pipeline).order_by(Pipeline.is_default.desc(), Pipeline.created_at))).scalars().first()
    if pipeline is None:
        raise LookupError("No pipeline configured. Run `python -m app.seed`.")
    return pipeline


async def _deal_texts(db: AsyncSession, deal: Deal) -> list[str]:
    rows = (
        await db.execute(
            select(Activity.summary, Activity.raw_text, Activity.activity_type).where(
                Activity.account_id == deal.account_id, Activity.activity_type != "system"
            )
        )
    ).all()
    return [f"{r.activity_type} {r.summary} {r.raw_text or ''}".lower() for r in rows]


async def evaluate_gates(db: AsyncSession, deal: Deal, stage: PipelineStage, loss_reason: str | None) -> list[dict]:
    """Mandatory entry criteria for ``stage``."""
    contacts = (await db.execute(select(Contact.buying_role).where(Contact.account_id == deal.account_id))).scalars().all()
    texts = await _deal_texts(db, deal)

    def mentions(pattern: str) -> bool:
        return any(re.search(pattern, t) for t in texts)

    name = stage.name
    if stage.is_closed_lost:
        return [{"criterion": "Explicit loss declaration with a loss reason", "met": bool(loss_reason)}]
    if stage.is_closed_won:
        return [{"criterion": "Executed MSA or PO posted", "met": mentions(r"\b(msa|po|purchase order|signed|countersigned|executed)\b")}]
    if name == "Discovery":
        return [
            {"criterion": "Verified business domain", "met": "." in (deal.account.domain or "")},
            {"criterion": "At least 1 contact logged", "met": len(contacts) >= 1},
        ]
    if name == "Pain Fit":
        return [
            {"criterion": "Pain identified", "met": bool((deal.ai_insights or {}).get("pain_points")) or mentions(r"\b(pain|challenge|struggl|problem|manual|bottleneck)")},
            {"criterion": "Economic Buyer or Champion mapped", "met": any(r in ("Economic Buyer", "Champion") for r in contacts)},
        ]
    if name == "Solution Demo":
        return [
            {"criterion": "Demo completed", "met": mentions(r"\b(demo|walkthrough|poc|pilot)")},
            {"criterion": "Evaluation criteria agreed in writing", "met": mentions(r"\b(criteria|requirements|scorecard|success plan)")},
        ]
    if name == "Proposal/InfoSec":
        return [
            {"criterion": "Pricing delivered", "met": float(deal.amount or 0) > 0 and mentions(r"\b(pricing|proposal|quote|price)")},
            {"criterion": "InfoSec / legal review open", "met": mentions(r"\b(security|infosec|soc ?2|legal|questionnaire|redline)")},
        ]
    return []


async def change_stage(
    db: AsyncSession,
    deal: Deal,
    stage: PipelineStage,
    user: User | None,
    loss_reason: str | None = None,
    override_gates: bool = False,
) -> tuple[Deal, float, list[dict], str | None]:
    """Transition a deal across a stage gate.

    Writes an audit record, recalibrates risk and returns the weighted forecast
    delta. Gates are enforced when moving forward; ``override_gates`` lets a
    user consciously skip soft criteria, but a Closed-Lost loss reason is
    always mandatory.
    """
    if stage.pipeline_id != deal.pipeline_id:
        raise ValueError("Stage does not belong to the deal's pipeline")
    old_stage = deal.stage
    if old_stage.id == stage.id:
        return deal, 0.0, [], None

    gates = await evaluate_gates(db, deal, stage, loss_reason)
    moving_forward = stage.stage_order > old_stage.stage_order
    unmet = [g for g in gates if not g["met"]]
    if stage.is_closed_lost and not loss_reason:
        raise GateError("A loss reason is required to close a deal as lost", gates)
    if moving_forward and unmet and not override_gates and not stage.is_closed_lost:
        raise GateError("Stage-gate entry criteria not met", gates)

    before = _deal_weighted(deal, old_stage)
    now = datetime.now(timezone.utc)
    deal.stage_id = stage.id
    deal.stage = stage
    deal.stage_entered_at = now
    deal.loss_reason = loss_reason if stage.is_closed_lost else None
    deal.closed_at = now if stage.is_closed else None

    await scoring.rescore_account(db, deal.account_id)
    if stage.is_closed_won:
        deal.account.health_score = 100
    after = _deal_weighted(deal, stage)
    delta = round(after - before, 2)

    db.add(
        DealStageHistory(
            deal_id=deal.id,
            from_stage_id=old_stage.id,
            to_stage_id=stage.id,
            changed_by=user.id if user else None,
            forecast_delta=delta,
            gate_overridden=bool(moving_forward and unmet and override_gates),
        )
    )
    db.add(
        Activity(
            account_id=deal.account_id,
            deal_id=deal.id,
            user_id=user.id if user else None,
            activity_type="system",
            summary=f"Stage moved from {old_stage.name} to {stage.name}"
            + (f" (loss reason: {loss_reason.replace('_', ' ')})" if stage.is_closed_lost else ""),
            sentiment="neutral",
        )
    )
    await db.flush()
    return deal, delta, gates, TRIGGER_DESCRIPTIONS.get(stage.name)


def _deal_weighted(deal: Deal, stage: PipelineStage) -> float:
    if stage.is_closed_lost:
        return 0.0
    risk = 0 if stage.is_closed_won else deal.risk_score
    return scoring.weighted_value(float(deal.amount or 0), stage.default_probability, risk)


async def kanban(db: AsyncSession, pipeline_id: uuid.UUID) -> dict:
    pipeline = await db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise LookupError("Pipeline not found")
    deals = (await db.execute(select(Deal).where(Deal.pipeline_id == pipeline_id).order_by(Deal.amount.desc()))).scalars().unique().all()
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    by_stage: dict[uuid.UUID, list[dict]] = defaultdict(list)
    for d in deals:
        # closed deals older than 90 days drop off the board
        if d.stage.is_closed and d.closed_at and d.closed_at < cutoff:
            continue
        by_stage[d.stage_id].append(deal_card(d))
    columns = []
    for stage in pipeline.stages:
        cards = by_stage.get(stage.id, [])
        columns.append(
            {
                "id": stage.id,
                "name": stage.name,
                "stage_order": stage.stage_order,
                "probability": stage.default_probability,
                "is_closed_won": stage.is_closed_won,
                "is_closed_lost": stage.is_closed_lost,
                "deals": cards,
                "metrics": {
                    "count": len(cards),
                    "total": round(sum(c["amount"] for c in cards), 2),
                    "weighted": round(sum(c["weighted_value"] for c in cards), 2),
                },
            }
        )
    return {"pipeline": {"id": pipeline.id, "name": pipeline.name}, "columns": columns}


async def forecast(db: AsyncSession) -> dict:
    deals = (await db.execute(select(Deal))).scalars().unique().all()
    open_deals = [d for d in deals if not d.stage.is_closed]
    won = [d for d in deals if d.stage.is_closed_won]
    lost = [d for d in deals if d.stage.is_closed_lost]
    today = date.today()
    q_start = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    q_end = (date(q_start.year, q_start.month + 2, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    by_stage = []
    for stage in sorted({d.stage.id: d.stage for d in open_deals}.values(), key=lambda s: s.stage_order):
        ds = [d for d in open_deals if d.stage_id == stage.id]
        by_stage.append(
            {
                "stage": stage.name,
                "count": len(ds),
                "total": round(sum(float(d.amount) for d in ds), 2),
                "weighted": round(sum(scoring.weighted_value(float(d.amount), stage.default_probability, d.risk_score) for d in ds), 2),
            }
        )
    by_month: dict[str, float] = defaultdict(float)
    for d in open_deals:
        if d.target_close_date:
            key = d.target_close_date.strftime("%Y-%m")
            by_month[key] += scoring.weighted_value(float(d.amount), d.stage.default_probability, d.risk_score)
    closed = len(won) + len(lost)
    return {
        "total_pipeline": round(sum(float(d.amount) for d in open_deals), 2),
        "weighted_pipeline": round(sum(scoring.weighted_value(float(d.amount), d.stage.default_probability, d.risk_score) for d in open_deals), 2),
        "open_deals": len(open_deals),
        "at_risk_deals": sum(1 for d in open_deals if d.risk_score >= 60),
        "won_this_quarter": round(sum(float(d.amount) for d in won if d.closed_at and q_start <= d.closed_at.date() <= q_end), 2),
        "win_rate": round(100 * len(won) / closed, 1) if closed else None,
        "avg_deal_size": round(sum(float(d.amount) for d in open_deals) / len(open_deals), 2) if open_deals else 0,
        "by_stage": by_stage,
        "by_close_month": [{"month": k, "weighted": round(v, 2)} for k, v in sorted(by_month.items())],
        "quarter": {"start": q_start, "end": q_end},
    }


async def open_task_count(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(Task).where(Task.completed.is_(False)))).scalar_one()
