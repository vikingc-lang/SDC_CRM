import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_writer
from app.models import Account, Activity, Contact, Deal, DealStageHistory, Pipeline, PipelineStage, Task, User
from app.schemas.crm import DealCreate, DealUpdate, PipelineOut, StageChangeRequest
from app.services import insights, pipeline_service, scoring
from app.services.jobs import enqueue
from app.services.serializers import activity_out, contact_out, deal_card, task_out, user_brief

router = APIRouter(tags=["deals"])


@router.get("/pipelines", response_model=list[PipelineOut])
async def list_pipelines(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    return (await db.execute(select(Pipeline).order_by(Pipeline.is_default.desc(), Pipeline.name))).scalars().all()


@router.get("/pipeline/{pipeline_id}/kanban")
async def pipeline_kanban(pipeline_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    try:
        return await pipeline_service.kanban(db, pipeline_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/deals")
async def list_deals(
    status: Literal["open", "won", "lost", "all"] = "open",
    search: str | None = None,
    account_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Deal).join(PipelineStage, Deal.stage_id == PipelineStage.id).join(Account, Deal.account_id == Account.id).order_by(Deal.amount.desc())
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
    return [deal_card(d) for d in (await db.execute(stmt)).scalars().unique().all()]


@router.post("/deals", status_code=201)
async def create_deal(body: DealCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_writer)):
    account = await db.get(Account, body.account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    pipeline = await pipeline_service.default_pipeline(db)
    stage = await db.get(PipelineStage, body.stage_id) if body.stage_id else pipeline.stages[0]
    if stage is None or stage.pipeline_id != pipeline.id:
        raise HTTPException(422, "Invalid stage")
    deal = Deal(
        title=body.title.strip(),
        account_id=account.id,
        pipeline_id=pipeline.id,
        stage_id=stage.id,
        amount=body.amount,
        primary_contact_id=body.primary_contact_id,
        target_close_date=body.target_close_date,
        owner_id=body.owner_id or user.id,
        risk_factors={},
        ai_insights={},
    )
    db.add(deal)
    await db.flush()
    db.add(DealStageHistory(deal_id=deal.id, from_stage_id=None, to_stage_id=stage.id, changed_by=user.id))
    await scoring.rescore_account(db, account.id)
    await db.commit()
    await db.refresh(deal)
    return deal_card(deal)


async def _get_deal(db: AsyncSession, deal_id: uuid.UUID) -> Deal:
    deal = await db.get(Deal, deal_id)
    if deal is None:
        raise HTTPException(404, "Deal not found")
    return deal


@router.get("/deals/{deal_id}")
async def get_deal(deal_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    deal = await _get_deal(db, deal_id)
    card = deal_card(deal)
    activities = (
        await db.execute(
            select(Activity).where(or_(Activity.deal_id == deal_id, Activity.account_id == deal.account_id)).order_by(Activity.occurred_at.desc()).limit(40)
        )
    ).scalars().unique().all()
    tasks = (await db.execute(select(Task).where(Task.deal_id == deal_id).order_by(Task.completed, Task.due_date.nulls_last()))).scalars().unique().all()
    history = (await db.execute(select(DealStageHistory).where(DealStageHistory.deal_id == deal_id).order_by(DealStageHistory.changed_at))).scalars().unique().all()
    contacts = (await db.execute(select(Contact).where(Contact.account_id == deal.account_id))).scalars().all()
    stages = (await db.execute(select(PipelineStage).where(PipelineStage.pipeline_id == deal.pipeline_id).order_by(PipelineStage.stage_order))).scalars().all()
    return {
        **card,
        "stages": [{"id": s.id, "name": s.name, "probability": s.default_probability, "stage_order": s.stage_order, "is_closed_won": s.is_closed_won, "is_closed_lost": s.is_closed_lost} for s in stages],
        "activities": [activity_out(a) for a in activities],
        "tasks": [task_out(t) for t in tasks],
        "contacts": [contact_out(c) for c in contacts],
        "history": [
            {"from": h.from_stage.name if h.from_stage else None, "to": h.to_stage.name, "forecast_delta": float(h.forecast_delta), "gate_overridden": h.gate_overridden, "by": user_brief(h.user), "at": h.changed_at}
            for h in history
        ],
        "next_best_actions": insights.next_best_actions(card),
    }


@router.patch("/deals/{deal_id}")
async def update_deal(deal_id: uuid.UUID, body: DealUpdate, db: AsyncSession = Depends(get_db), _: User = Depends(require_writer)):
    deal = await _get_deal(db, deal_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(deal, field, value)
    await db.commit()
    await db.refresh(deal)
    return deal_card(deal)


@router.patch("/deals/{deal_id}/stage")
async def change_deal_stage(
    deal_id: uuid.UUID,
    body: StageChangeRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_writer),
):
    deal = await _get_deal(db, deal_id)
    stage = await db.get(PipelineStage, body.stage_id)
    if stage is None:
        raise HTTPException(404, "Stage not found")
    try:
        deal, delta, gates, action = await pipeline_service.change_stage(db, deal, stage, user, body.loss_reason, body.override_gates)
    except pipeline_service.GateError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc), "gates": exc.gates, "stage": stage.name})
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    await db.commit()
    await db.refresh(deal)
    if action:
        enqueue(background, "stage_trigger", str(deal.id), str(user.id))
    return {"deal": deal_card(deal), "forecast_delta": delta, "gates": gates, "triggered_action": action}


@router.delete("/deals/{deal_id}", status_code=204)
async def delete_deal(deal_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = Depends(require_writer)):
    deal = await _get_deal(db, deal_id)
    await db.delete(deal)
    await db.commit()
