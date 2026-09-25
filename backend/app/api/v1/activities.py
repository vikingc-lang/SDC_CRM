import secrets
import uuid
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.rbac import Principal, authorize
from app.models import Account, Activity, Attachment, Contact, MailboxConnection, Notification, Task, User
from app.services import calendar as cal
from app.services import mail, scoring, sla, storage
from app.services.jobs import enqueue
from app.services.serializers import activity_out, task_out

router = APIRouter(tags=["activities"])


class ActivityCreate(BaseModel):
    account_id: uuid.UUID
    deal_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    activity_type: Literal["meeting", "call", "note", "email"] = "note"
    subject: str | None = Field(default=None, max_length=500)
    summary: str = Field(min_length=1)
    sentiment: Literal["positive", "neutral", "negative"] = "neutral"
    direction: Literal["inbound", "outbound", "internal"] | None = None
    duration_seconds: int | None = Field(default=None, ge=0, le=86400)
    disposition: Literal["connected", "left_voicemail", "gatekeeper", "no_answer", "busy", "wrong_number"] | None = None
    agenda: str | None = None
    attendance: Literal["attended", "no_show", "cancelled", "scheduled"] | None = None
    occurred_at: datetime | None = None


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    due_date: date | None = None
    account_id: uuid.UUID | None = None
    deal_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    depends_on_id: uuid.UUID | None = None
    priority: Literal["low", "normal", "high", "urgent"] = "normal"


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    due_date: date | None = None
    completed: bool | None = None
    assignee_id: uuid.UUID | None = None
    depends_on_id: uuid.UUID | None = None
    clear_dependency: bool = False
    priority: Literal["low", "normal", "high", "urgent"] | None = None


class EmailSend(BaseModel):
    contact_id: uuid.UUID
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1)
    deal_id: uuid.UUID | None = None
    in_reply_to: str | None = None


class MailboxIn(BaseModel):
    email_address: str
    imap_host: str
    imap_port: int = 993
    smtp_host: str | None = None
    smtp_port: int = 587
    username: str | None = None
    password: str = Field(min_length=1)


# ---- activity ledger ----------------------------------------------------------------
@router.get("/activities")
async def list_activities(
    account_id: uuid.UUID | None = None,
    deal_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    activity_type: str | None = None,
    include_system: bool = True,
    limit: int = Query(30, le=200),
    db: AsyncSession = Depends(get_db),
    p: Principal = Depends(authorize("activities", "read")),
):
    stmt = p.scope_accounts(select(Activity), "activities", Activity.account_id).order_by(Activity.occurred_at.desc()).limit(limit)
    if account_id:
        stmt = stmt.where(Activity.account_id == account_id)
    if deal_id:
        stmt = stmt.where(Activity.deal_id == deal_id)
    if contact_id:
        stmt = stmt.where(Activity.contact_id == contact_id)
    if activity_type:
        stmt = stmt.where(Activity.activity_type.in_(activity_type.split(",")))
    if not include_system:
        stmt = stmt.where(Activity.activity_type != "system")
    acts = (await db.execute(stmt)).scalars().unique().all()
    atts = (await db.execute(select(Attachment).where(Attachment.activity_id.in_([a.id for a in acts] or [uuid.uuid4()])))).scalars().all()
    by_act: dict = {}
    for a in atts:
        by_act.setdefault(a.activity_id, []).append({"id": a.id, "filename": a.filename, "size_bytes": a.size_bytes, "content_type": a.content_type})
    return [activity_out(a, attachments=by_act.get(a.id)) for a in acts]


@router.post("/activities", status_code=201)
async def create_activity(body: ActivityCreate, background: BackgroundTasks, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("activities", "create"))):
    if await db.get(Account, body.account_id) is None:
        raise HTTPException(404, "Account not found")
    await p.ensure_account(db, body.account_id, "activities")
    activity = Activity(**body.model_dump(exclude={"occurred_at"}), occurred_at=body.occurred_at or datetime.now(timezone.utc), user_id=p.id)
    db.add(activity)
    await db.flush()
    await scoring.rescore_account(db, body.account_id)
    await db.commit()
    await db.refresh(activity)
    enqueue(background, "embed_activity", str(activity.id))
    return activity_out(activity)


@router.post("/accounts/{account_id}/files", status_code=201)
async def upload_file(account_id: uuid.UUID, file: UploadFile = File(...), note: str | None = Form(default=None), deal_id: uuid.UUID | None = Form(default=None),
                      db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("activities", "create"))):
    """Attach a file to the account timeline (creates a 'file' ledger entry)."""
    if await db.get(Account, account_id) is None:
        raise HTTPException(404, "Account not found")
    await p.ensure_account(db, account_id, "activities")
    data = await file.read()
    activity = Activity(account_id=account_id, deal_id=deal_id, user_id=p.id, activity_type="file", source="manual", sentiment="neutral",
                        subject=file.filename, summary=note or f"Uploaded {file.filename}")
    db.add(activity)
    await db.flush()
    try:
        att = storage.save(data, file.filename or "file", file.content_type or "application/octet-stream", account_id=account_id,
                           activity_id=activity.id, uploaded_by=p.id)
    except ValueError as exc:
        raise HTTPException(413, str(exc))
    db.add(att)
    await db.commit()
    return {"id": att.id, "filename": att.filename, "size_bytes": att.size_bytes, "sha256": att.sha256, "activity_id": activity.id}


@router.get("/files/{attachment_id}")
async def download_file(attachment_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("activities", "read"))):
    att = await db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(404, "File not found")
    if att.account_id:
        await p.ensure_account(db, att.account_id, "activities")
    try:
        data = storage.read(att)
    except FileNotFoundError:
        raise HTTPException(410, "File content is no longer available")
    return Response(data, media_type=att.content_type, headers={"Content-Disposition": f'attachment; filename="{att.filename}"'})


# ---- tasks & SLA ---------------------------------------------------------------------
def _task_scope(stmt, p: Principal):
    if not p.is_own_scope("tasks"):
        return stmt
    return stmt.where(or_(Task.owner_id == p.id, Task.assignee_id == p.id, Task.account_id.in_(p.owned_account_ids())))


@router.get("/tasks")
async def list_tasks(
    status: Literal["open", "done", "all"] = "open",
    account_id: uuid.UUID | None = None,
    deal_id: uuid.UUID | None = None,
    assignee: Literal["me", "all"] = "all",
    db: AsyncSession = Depends(get_db),
    p: Principal = Depends(authorize("tasks", "read")),
):
    stmt = _task_scope(select(Task), p).order_by(Task.completed, Task.due_date.nulls_last(), Task.created_at.desc())
    if status == "open":
        stmt = stmt.where(Task.completed.is_(False))
    elif status == "done":
        stmt = stmt.where(Task.completed.is_(True))
    if account_id:
        stmt = stmt.where(Task.account_id == account_id)
    if deal_id:
        stmt = stmt.where(Task.deal_id == deal_id)
    if assignee == "me":
        stmt = stmt.where(or_(Task.assignee_id == p.id, (Task.assignee_id.is_(None)) & (Task.owner_id == p.id)))
    return [task_out(t) for t in (await db.execute(stmt.limit(500))).scalars().unique().all()]


@router.post("/tasks", status_code=201)
async def create_task(body: TaskCreate, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("tasks", "create"))):
    if body.account_id:
        await p.ensure_account(db, body.account_id, "tasks")
    if body.depends_on_id and await db.get(Task, body.depends_on_id) is None:
        raise HTTPException(404, "Dependency task not found")
    task = Task(**body.model_dump(), owner_id=p.id, source="manual")
    task.assignee_id = body.assignee_id or p.id
    db.add(task)
    await db.flush()
    if task.assignee_id != p.id:
        from app.services.notify import notify
        notify(db, [task.assignee_id], "task", f"{p.user.full_name} assigned you: {task.title}", None, "/tasks")
    await db.commit()
    await db.refresh(task)
    return task_out(task)


@router.patch("/tasks/{task_id}")
async def update_task(task_id: uuid.UUID, body: TaskUpdate, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("tasks", "update"))):
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    if p.is_own_scope("tasks") and p.id not in (task.owner_id, task.assignee_id) and task.account_id:
        await p.ensure_account(db, task.account_id, "tasks")
    data = body.model_dump(exclude_unset=True)
    if data.pop("clear_dependency", False):
        task.depends_on_id = None
    if data.get("depends_on_id"):
        if await sla.dependency_cycle(db, task.id, data["depends_on_id"]):
            raise HTTPException(422, "That dependency would create a cycle")
    if data.get("completed") and task.depends_on_id:
        dep = await db.get(Task, task.depends_on_id)
        if dep and not dep.completed:
            raise HTTPException(409, f"Blocked by '{dep.title}'. Complete it first.")
    if "assignee_id" in data and data["assignee_id"] and data["assignee_id"] != task.assignee_id:
        from app.services.notify import notify
        notify(db, [data["assignee_id"]], "task", f"{p.user.full_name} delegated: {task.title}", None, "/tasks")
        task.escalation_level = 0
    for field, value in data.items():
        setattr(task, field, value)
    if "completed" in data:
        task.completed_at = datetime.now(timezone.utc) if task.completed else None
        if task.completed and task.milestone_id:
            from app.models import OnboardingMilestone
            m = await db.get(OnboardingMilestone, task.milestone_id)
            if m and m.status != "done":
                m.status, m.completed_at = "done", task.completed_at
    await db.commit()
    await db.refresh(task)
    return task_out(task)


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("tasks", "delete"))):
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    if p.is_own_scope("tasks") and p.id not in (task.owner_id, task.assignee_id):
        raise HTTPException(404, "Task not found")
    await db.delete(task)
    await db.commit()


# ---- notifications ---------------------------------------------------------------------
@router.get("/notifications")
async def list_notifications(unread_only: bool = False, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(50)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = (await db.execute(stmt)).scalars().all()
    unread = (await db.execute(select(Notification.id).where(Notification.user_id == user.id, Notification.read_at.is_(None)))).all()
    return {"unread": len(unread), "items": [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "link": n.link,
                                              "read": n.read_at is not None, "created_at": n.created_at} for n in rows]}


@router.post("/notifications/read")
async def mark_read(ids: list[uuid.UUID] | None = None, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = select(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    if ids:
        stmt = stmt.where(Notification.id.in_(ids))
    for n in (await db.execute(stmt)).scalars().all():
        n.read_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "ok"}


# ---- calendar -----------------------------------------------------------------------------
@router.post("/calendar/token")
async def calendar_token(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Create/rotate the private iCal subscription URL for Google/Outlook/Apple calendars."""
    user.ical_token = secrets.token_urlsafe(32)
    await db.commit()
    return {"feed_path": f"/api/v1/calendar/feed/{user.ical_token}.ics"}


@router.get("/calendar/feed/{token}.ics")
async def calendar_feed(token: str, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.ical_token == token, User.is_active.is_(True)))).scalars().first()
    if user is None:
        raise HTTPException(404, "Unknown calendar")
    return Response(await cal.user_feed(db, user), media_type="text/calendar")


@router.post("/calendar/import")
async def calendar_import(file: UploadFile = File(...), db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("activities", "create"))):
    try:
        result = await cal.import_ics(db, await file.read(), p.user)
    except ValueError as exc:
        raise HTTPException(422, f"Invalid .ics file: {exc}")
    await db.commit()
    return result


# ---- email -------------------------------------------------------------------------------------
@router.post("/email/send", status_code=201)
async def send_email(body: EmailSend, background: BackgroundTasks, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("activities", "create"))):
    contact = await db.get(Contact, body.contact_id)
    if contact is None:
        raise HTTPException(404, "Contact not found")
    await p.ensure_account(db, contact.account_id, "activities")
    try:
        act = await mail.send_email(db, p.user, contact, body.subject, body.body, body.deal_id, body.in_reply_to)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except OSError as exc:
        raise HTTPException(502, f"SMTP delivery failed: {exc}")
    await scoring.rescore_account(db, contact.account_id)
    await db.commit()
    enqueue(background, "embed_activity", str(act.id))
    return {"activity_id": act.id, "delivered": act.source == "email_sync"}


@router.post("/email/ingest", status_code=201)
async def ingest_raw_email(file: UploadFile = File(...), db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("activities", "create"))):
    """Log a raw .eml (drag-drop or a BCC/forwarding gateway)."""
    act = await mail.ingest_message(db, await file.read(), p.user)
    if act is None:
        raise HTTPException(422, "No CRM contact on this message, or it was already logged")
    await scoring.rescore_account(db, act.account_id)
    await db.commit()
    await db.refresh(act)
    return activity_out(act)


@router.get("/email/mailboxes")
async def list_mailboxes(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (await db.execute(select(MailboxConnection).where(MailboxConnection.user_id == user.id))).scalars().all()
    return [{"id": m.id, "provider": m.provider, "email_address": m.email_address, "imap_host": m.imap_host, "smtp_host": m.smtp_host,
             "status": m.status, "last_synced_at": m.last_synced_at, "last_error": m.last_error} for m in rows]


@router.post("/email/mailboxes", status_code=201)
async def connect_mailbox(body: MailboxIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    conn = MailboxConnection(user_id=user.id, provider="imap", email_address=body.email_address, imap_host=body.imap_host, imap_port=body.imap_port,
                             smtp_host=body.smtp_host, smtp_port=body.smtp_port, username=body.username or body.email_address,
                             secret_encrypted=mail.encrypt_secret(body.password))
    db.add(conn)
    await db.commit()
    return {"id": conn.id}


@router.post("/email/mailboxes/{mailbox_id}/sync")
async def sync_mailbox(mailbox_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    conn = await db.get(MailboxConnection, mailbox_id)
    if conn is None or conn.user_id != user.id:
        raise HTTPException(404, "Mailbox not found")
    return await mail.sync_mailbox(db, conn)


@router.delete("/email/mailboxes/{mailbox_id}", status_code=204)
async def delete_mailbox(mailbox_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    conn = await db.get(MailboxConnection, mailbox_id)
    if conn and conn.user_id == user.id:
        await db.delete(conn)
        await db.commit()
