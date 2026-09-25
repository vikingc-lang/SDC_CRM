import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_writer
from app.models import Account, Activity, Task, User
from app.schemas.crm import ActivityCreate, TaskCreate, TaskUpdate
from app.services import scoring
from app.services.jobs import enqueue
from app.services.serializers import activity_out, task_out

router = APIRouter(tags=["activities"])


@router.get("/activities")
async def list_activities(
    account_id: uuid.UUID | None = None,
    deal_id: uuid.UUID | None = None,
    include_system: bool = True,
    limit: int = Query(30, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Activity).order_by(Activity.occurred_at.desc()).limit(limit)
    if account_id:
        stmt = stmt.where(Activity.account_id == account_id)
    if deal_id:
        stmt = stmt.where(Activity.deal_id == deal_id)
    if not include_system:
        stmt = stmt.where(Activity.activity_type != "system")
    return [activity_out(a) for a in (await db.execute(stmt)).scalars().unique().all()]


@router.post("/activities", status_code=201)
async def create_activity(body: ActivityCreate, background: BackgroundTasks, db: AsyncSession = Depends(get_db), user: User = Depends(require_writer)):
    if await db.get(Account, body.account_id) is None:
        raise HTTPException(404, "Account not found")
    activity = Activity(**body.model_dump(exclude={"occurred_at"}), occurred_at=body.occurred_at or datetime.now(timezone.utc), user_id=user.id)
    db.add(activity)
    await db.flush()
    await scoring.rescore_account(db, body.account_id)
    await db.commit()
    await db.refresh(activity)
    enqueue(background, "embed_activity", str(activity.id))
    return activity_out(activity)


@router.get("/tasks")
async def list_tasks(
    status: Literal["open", "done", "all"] = "open",
    account_id: uuid.UUID | None = None,
    deal_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Task).order_by(Task.completed, Task.due_date.nulls_last(), Task.created_at.desc())
    if status == "open":
        stmt = stmt.where(Task.completed.is_(False))
    elif status == "done":
        stmt = stmt.where(Task.completed.is_(True))
    if account_id:
        stmt = stmt.where(Task.account_id == account_id)
    if deal_id:
        stmt = stmt.where(Task.deal_id == deal_id)
    return [task_out(t) for t in (await db.execute(stmt.limit(300))).scalars().unique().all()]


@router.post("/tasks", status_code=201)
async def create_task(body: TaskCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_writer)):
    task = Task(**body.model_dump(), owner_id=user.id, source="manual")
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task_out(task)


@router.patch("/tasks/{task_id}")
async def update_task(task_id: uuid.UUID, body: TaskUpdate, db: AsyncSession = Depends(get_db), _: User = Depends(require_writer)):
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(task, field, value)
    if "completed" in data:
        task.completed_at = datetime.now(timezone.utc) if task.completed else None
    await db.commit()
    await db.refresh(task)
    return task_out(task)


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = Depends(require_writer)):
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    await db.delete(task)
    await db.commit()
