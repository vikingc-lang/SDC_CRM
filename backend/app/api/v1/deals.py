import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import Principal, authorize
from app.models import (
    Account, Activity, Contact, Deal, DealAlert, DealPartner, DealStageHistory, Document, Partner, Pipeline, PipelineStage, Quote, Task,
)
from app.services import custom_fields, fx, insights, pipeline_service, scoring
from app.services.clm import document_out
from app.services.cpq import quote_out
from app.services.jobs import enqueue
from app.services.serializers import activity_out, contact_out, deal_card, task_out, user_brief

router = APIRouter(tags=["deals"])

LossReason = Literal["competitor", "budget_frozen", "feature_gap", "champion_departed", "price", "no_decision", "timing", "other"]


class DealCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    account_id: uuid.UUID
    amount: float = Field(default=0, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    pipeline_id: uuid.UUID | None = None
    stage_id: uuid.UUID | None = None
    primary_contact_id: uuid.UUID | None = None
    target_close_date: date | None = None
    owner_id: uuid.UUID | None = None
    deal_type: Literal["new_business", "renewal", "upsell", "partner"] = "new_business"


class DealUpdate(BaseModel):
    title: str | None = None
    amount: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    primary_contact_id: uuid.UUID | None = None
    target_close_date: date | None = None
    owner_id: uuid.UUID | None = None
    deal_type: Literal["new_business", "renewal", "upsell", "partner"] | None = None
    custom_fields: dict | None = None


class StageChange(BaseModel):
    stage_id: uuid.UUID
    loss_reason: LossReason | None = None
    loss_debrief: str | None = Field(default=None, max_length=5000)
    loss_competitor: str | None = Field(default=None, max_length=100)
    win_debrief: str | None = Field(default=None, max_length=5000)
    override_gates: bool = False


class DealPartnerIn(BaseModel):
    partner_id: uuid.UUID
    role: Literal["referral", "co_sell", "resell"] = "co_sell"
    split_pct: float = Field(default=100, ge=0, le=100)
    commission_rate: float | None = Field(default=None, ge=0, le=100)


class GateRulesUpdate(BaseModel):
    gate_rules: list[dict]
    default_probability: int | None = Field(default=None, ge=0, le=100)


def _stage_out(s: PipelineStage) -> dict:
    return {"id": s.id, "name": s.name, "stage_order": s.stage_order, "default_probability": s.default_probability,
            "is_closed_won": s.is_closed_won, "is_closed_lost": s.is_closed_lost, "gate_rules": s.gate_rules or []}


@router.get("/pipelines")
async def list_pipelines(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("deals", "read"))):
    pipelines = (await db.execute(select(Pipeline).order_by(Pipeline.is_default.desc(), Pipeline.created_at))).scalars().all()
    return [{"id": p.id, "name": p.name, "kind": p.kind, "description": p.description, "is_default": p.is_default,
             "stages": [_stage_out(s) for s in p.stages]} for p in pipelines]


@router.put("/pipelines/stages/{stage_id}/gates")
async def update_gate_rules(stage_id: uuid.UUID, body: GateRulesUpdate, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    stage = await db.get(PipelineStage, stage_id)
    if stage is None:
        raise HTTPException(404, "Stage not found")
    allowed = {"min_contacts", "domain_verified", "role_mapped", "activity_logged", "field_present", "amount_approved", "keyword",
               "pain_identified", "signed_document", "any_of", "loss_reason"}
    for rule in body.gate_rules:
        if rule.get("type") not in allowed:
            raise HTTPException(422, f"Unknown gate rule type '{rule.get('type')}'")
    stage.gate_rules = body.gate_rules
    if body.default_probability is not None:
        stage.default_probability = body.default_probability
    await db.commit()
    return _stage_out(stage)


@router.get("/pipeline/{pipeline_id}/kanban")
async def pipeline_kanban(pipeline_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "read"))):
    try:
        return await pipeline_service.kanban(db, pipeline_id, p)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/deals")
async def list_deals(
    status: Literal["open", "won", "lost", "all"] = "open",
    search: str | None = None,
    account_id: uuid.UUID | None = None,
    pipeline_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    p: Principal = Depends(authorize("deals", "read")),
):
    stmt = p.scope_deals(select(Deal).join(PipelineStage, Deal.stage_id == PipelineStage.id).join(Account, Deal.account_id == Account.id).order_by(Deal.amount.desc()))
    if status == "open":
        stmt = stmt.where(PipelineStage.is_closed_won.is_(False), PipelineStage.is_closed_lost.is_(False))
    elif status == "won":
        stmt = stmt.where(PipelineStage.is_closed_won.is_(True))
    elif status == "lost":
        stmt = stmt.where(PipelineStage.is_closed_lost.is_(True))
    if search:
        stmt = stmt.where(or_(Deal.title.ilike(f"%{search}%"), Account.name.ilike(f"%{search}%")))
    if account_id:
        stmt = stmt.where(Deal.account_id == account_id)
    if pipeline_id:
        stmt = stmt.where(Deal.pipeline_id == pipeline_id)
    rates = await fx.rates(db)
    return [deal_card(d, rates) for d in (await db.execute(stmt)).scalars().unique().all()]


@router.post("/deals", status_code=201)
async def create_deal(body: DealCreate, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "create"))):
    account = await db.get(Account, body.account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    await p.ensure_account(db, account.id, "deals")
    pipeline = await db.get(Pipeline, body.pipeline_id) if body.pipeline_id else await pipeline_service.default_pipeline(db)
    if pipeline is None:
        raise HTTPException(404, "Pipeline not found")
    stage = await db.get(PipelineStage, body.stage_id) if body.stage_id else pipeline.stages[0]
    if stage is None or stage.pipeline_id != pipeline.id or stage.is_closed_won or stage.is_closed_lost:
        raise HTTPException(422, "Pick an open stage of the selected pipeline")
    deal = Deal(title=body.title.strip(), account_id=account.id, pipeline_id=pipeline.id, stage_id=stage.id, amount=body.amount,
                currency=body.currency.upper(), primary_contact_id=body.primary_contact_id, target_close_date=body.target_close_date,
                original_close_date=body.target_close_date, owner_id=body.owner_id or p.id, deal_type=body.deal_type,
                source="partner" if pipeline.kind == "partner" else "inbound" if pipeline.kind == "inbound" else "direct",
                risk_factors={}, ai_insights={})
    db.add(deal)
    await db.flush()
    db.add(DealStageHistory(deal_id=deal.id, from_stage_id=None, to_stage_id=stage.id, changed_by=p.id))
    await scoring.rescore_account(db, account.id)
    await db.commit()
    await db.refresh(deal)
    return deal_card(deal, await fx.rates(db))


async def _get_deal(db: AsyncSession, p: Principal, deal_id: uuid.UUID) -> Deal:
    deal = await db.get(Deal, deal_id)
    if deal is None:
        raise HTTPException(404, "Deal not found")
    if p.is_own_scope("deals") and deal.owner_id != p.id:
        await p.ensure_account(db, deal.account_id, "deals")
    return deal


@router.get("/deals/{deal_id}")
async def get_deal(deal_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "read"))):
    deal = await _get_deal(db, p, deal_id)
    rates = await fx.rates(db)
    card = deal_card(deal, rates)
    activities = (await db.execute(select(Activity).where(or_(Activity.deal_id == deal_id, Activity.account_id == deal.account_id))
                                   .order_by(Activity.occurred_at.desc()).limit(40))).scalars().unique().all()
    tasks = (await db.execute(select(Task).where(Task.deal_id == deal_id).order_by(Task.completed, Task.due_date.nulls_last()))).scalars().unique().all()
    history = (await db.execute(select(DealStageHistory).where(DealStageHistory.deal_id == deal_id).order_by(DealStageHistory.changed_at))).scalars().unique().all()
    contacts = (await db.execute(select(Contact).where(Contact.account_id == deal.account_id, Contact.status != "erased"))).scalars().all()
    pipeline = await db.get(Pipeline, deal.pipeline_id)
    quotes = (await db.execute(select(Quote).where(Quote.deal_id == deal_id).order_by(Quote.created_at.desc()))).scalars().unique().all()
    docs = (await db.execute(select(Document).where(Document.deal_id == deal_id).order_by(Document.created_at.desc()))).scalars().unique().all()
    partners = (await db.execute(select(DealPartner).where(DealPartner.deal_id == deal_id))).scalars().unique().all()
    alerts = (await db.execute(select(DealAlert).where(DealAlert.deal_id == deal_id, DealAlert.resolved_at.is_(None)))).scalars().unique().all()
    roles = [c.buying_role for c in contacts if c.status == "active"]
    return {
        **card,
        "pipeline": {"id": pipeline.id, "name": pipeline.name, "kind": pipeline.kind},
        "stages": [{"id": s.id, "name": s.name, "probability": s.default_probability, "stage_order": s.stage_order, "is_closed_won": s.is_closed_won,
                    "is_closed_lost": s.is_closed_lost, "gate_rules": s.gate_rules or []} for s in pipeline.stages],
        "activities": [activity_out(a) for a in activities],
        "tasks": [task_out(t) for t in tasks],
        "contacts": [contact_out(c) for c in contacts],
        "history": [{"from": h.from_stage.name if h.from_stage else None, "to": h.to_stage.name, "forecast_delta": float(h.forecast_delta),
                     "gate_overridden": h.gate_overridden, "by": user_brief(h.user), "at": h.changed_at} for h in history],
        "quotes": [quote_out(q) for q in quotes],
        "documents": [document_out(d, include_body=False) for d in docs],
        "partners": [{"id": dp.id, "partner": {"id": dp.partner.id, "name": dp.partner.name, "tier": dp.partner.tier}, "role": dp.role,
                      "split_pct": float(dp.split_pct), "commission_rate": float(dp.commission_rate) if dp.commission_rate is not None else None} for dp in partners],
        "alerts": [{"id": a.id, "kind": a.kind, "severity": a.severity, "message": a.message, "created_at": a.created_at} for a in alerts],
        "next_best_actions": insights.next_best_actions(card, roles, deal.stage.stage_order),
        "loss_taxonomy": pipeline_service.LOSS_TAXONOMY,
    }


@router.patch("/deals/{deal_id}")
async def update_deal(deal_id: uuid.UUID, body: DealUpdate, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "update"))):
    deal = await _get_deal(db, p, deal_id)
    data = body.model_dump(exclude_unset=True)
    if "custom_fields" in data:
        try:
            deal.custom_fields = await custom_fields.validate(db, "deal", data.pop("custom_fields"), deal.custom_fields)
        except custom_fields.CustomFieldError as exc:
            raise HTTPException(422, str(exc))
    new_close = data.get("target_close_date")
    if new_close and deal.target_close_date and new_close > deal.target_close_date:
        deal.close_date_pushes += 1  # slippage signal for the risk copilot
    if new_close and deal.original_close_date is None:
        deal.original_close_date = new_close
    if "currency" in data and data["currency"]:
        data["currency"] = data["currency"].upper()
    for field, value in data.items():
        setattr(deal, field, value)
    await db.commit()
    await db.refresh(deal)
    return deal_card(deal, await fx.rates(db))


@router.patch("/deals/{deal_id}/stage")
async def change_deal_stage(deal_id: uuid.UUID, body: StageChange, background: BackgroundTasks, db: AsyncSession = Depends(get_db),
                            p: Principal = Depends(authorize("deals", "update"))):
    deal = await _get_deal(db, p, deal_id)
    stage = await db.get(PipelineStage, body.stage_id)
    if stage is None:
        raise HTTPException(404, "Stage not found")
    try:
        deal, delta, gates, action = await pipeline_service.change_stage(
            db, deal, stage, p.user, body.loss_reason, body.override_gates, body.loss_debrief, body.loss_competitor, body.win_debrief)
    except pipeline_service.GateError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc), "gates": exc.gates, "stage": stage.name,
                                                      "loss_taxonomy": pipeline_service.LOSS_TAXONOMY if stage.is_closed_lost else None})
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    await db.commit()
    await db.refresh(deal)
    if action:
        enqueue(background, "stage_trigger", str(deal.id), str(p.id))
    return {"deal": deal_card(deal, await fx.rates(db)), "forecast_delta": delta, "gates": gates, "triggered_action": action}


@router.delete("/deals/{deal_id}", status_code=204)
async def delete_deal(deal_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "delete"))):
    deal = await _get_deal(db, p, deal_id)
    await db.delete(deal)
    await db.commit()


@router.post("/deals/{deal_id}/partners", status_code=201)
async def add_deal_partner(deal_id: uuid.UUID, body: DealPartnerIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "update"))):
    await _get_deal(db, p, deal_id)
    if await db.get(Partner, body.partner_id) is None:
        raise HTTPException(404, "Partner not found")
    existing = (await db.execute(select(DealPartner).where(DealPartner.deal_id == deal_id))).scalars().all()
    if sum(float(x.split_pct) for x in existing if x.partner_id != body.partner_id) + body.split_pct > 100:
        raise HTTPException(422, "Partner splits on a deal cannot exceed 100%")
    dp = next((x for x in existing if x.partner_id == body.partner_id), None)
    if dp:
        dp.role, dp.split_pct, dp.commission_rate = body.role, body.split_pct, body.commission_rate
    else:
        db.add(DealPartner(deal_id=deal_id, **body.model_dump()))
    await db.commit()
    return {"status": "ok"}


@router.delete("/deals/{deal_id}/partners/{deal_partner_id}", status_code=204)
async def remove_deal_partner(deal_id: uuid.UUID, deal_partner_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "update"))):
    await _get_deal(db, p, deal_id)
    dp = await db.get(DealPartner, deal_partner_id)
    if dp and dp.deal_id == deal_id:
        await db.delete(dp)
        await db.commit()


@router.get("/reports/win-loss")
async def win_loss(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("reports", "read"))):
    return await pipeline_service.win_loss(db, p)


@router.get("/reports/forecast")
async def forecast(pipeline_id: uuid.UUID | None = None, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("reports", "read"))):
    return await pipeline_service.forecast(db, p, pipeline_id)
