import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import Principal, authorize
from app.models import Account, Contract, OnboardingMilestone, OnboardingProject
from app.services import clm, scoring
from app.services.success import project_out

router = APIRouter(prefix="/success", tags=["customer success"])


class MilestoneUpdate(BaseModel):
    status: Literal["pending", "in_progress", "done", "blocked"] | None = None
    due_date: date | None = None


@router.get("/onboarding")
async def list_projects(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("success", "read"))):
    stmt = p.scope_accounts(select(OnboardingProject), "success", OnboardingProject.account_id).order_by(OnboardingProject.created_at.desc())
    return [project_out(pr) for pr in (await db.execute(stmt)).scalars().unique().all()]


@router.patch("/milestones/{milestone_id}")
async def update_milestone(milestone_id: uuid.UUID, body: MilestoneUpdate, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("success", "update"))):
    m = await db.get(OnboardingMilestone, milestone_id)
    if m is None:
        raise HTTPException(404, "Milestone not found")
    project = await db.get(OnboardingProject, m.project_id)
    await p.ensure_account(db, project.account_id, "success")
    if body.status:
        m.status = body.status
        m.completed_at = datetime.now(timezone.utc) if body.status == "done" else None
    if body.due_date:
        m.due_date = body.due_date
    await db.refresh(project, ["milestones"])
    statuses = {x.status for x in project.milestones}
    project.status = "completed" if statuses == {"done"} else "in_progress" if statuses & {"done", "in_progress"} else project.status
    await db.flush()
    await scoring.rescore_account(db, project.account_id)
    await db.commit()
    return project_out(await db.get(OnboardingProject, project.id))


@router.get("/churn")
async def churn_watchlist(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("success", "read"))):
    stmt = p.scope_accounts(select(Account), "success").where(Account.lifecycle_stage == "customer").order_by(Account.churn_risk.desc())
    rows = (await db.execute(stmt)).scalars().unique().all()
    return [{"id": a.id, "name": a.name, "churn_risk": a.churn_risk, "churn_factors": a.churn_factors, "health_score": a.health_score,
             "relationship_strength": a.relationship_strength, "owner": {"id": a.owner.id, "full_name": a.owner.full_name} if a.owner else None}
            for a in rows]


@router.get("/renewals")
async def renewals(days: int = 180, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("contracts", "read"))):
    stmt = p.scope_accounts(select(Contract), "contracts", Contract.account_id).where(
        Contract.status == "active", Contract.end_date <= date.today() + timedelta(days=days)).order_by(Contract.end_date)
    return [clm.contract_out(c) for c in (await db.execute(stmt)).scalars().unique().all()]


@router.post("/renewals/run")
async def run_renewals(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("contracts", "create"))):
    stats = await clm.run_renewals(db)
    await db.commit()
    return stats
