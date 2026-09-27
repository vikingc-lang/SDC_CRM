"""Customer service: cases, queues, SLA targets, knowledge base and the public CSAT survey."""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rbac import Perm, Principal, authorize, load_matrix
from app.models import Account, CaseComment, Contact, KbArticle, SupportQueue, SupportTicket, User
from app.services import app_settings
from app.services import cases as svc

router = APIRouter(tags=["service"])
public = APIRouter(prefix="/public/csat", tags=["public"])

Priority = Literal["critical", "high", "medium", "low"]
Status = Literal["open", "pending", "resolved", "closed"]
Channel = Literal["email", "phone", "web", "portal", "chat"]


class CaseIn(BaseModel):
    account_id: uuid.UUID
    contact_id: uuid.UUID | None = None
    subject: str = Field(min_length=2, max_length=300)
    description: str | None = Field(default=None, max_length=20000)
    severity: Priority = "medium"
    channel: Channel = "web"
    category: str | None = Field(default=None, max_length=60)
    queue_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None


class CasePatch(BaseModel):
    subject: str | None = Field(default=None, min_length=2, max_length=300)
    description: str | None = Field(default=None, max_length=20000)
    status: Status | None = None
    severity: Priority | None = None
    channel: Channel | None = None
    category: str | None = Field(default=None, max_length=60)
    queue_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)
    internal: bool = False


class QueueIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str | None = None
    member_ids: list[uuid.UUID] = []
    auto_assign: bool = True
    is_default: bool = False


class ArticleIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=1, max_length=100000)
    category: str | None = Field(default=None, max_length=60)
    status: Literal["draft", "published"] = "draft"
    tags: list[str] = []


class CsatIn(BaseModel):
    score: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)


def _bad(e: svc.CaseError) -> HTTPException:
    return HTTPException(422, str(e))


async def _names(db: AsyncSession, ids) -> dict:
    ids = {i for i in ids if i}
    return {u.id: u.full_name for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars()} if ids else {}


def _case_row(c: SupportTicket, names: dict, accounts: dict, queues: dict) -> dict:
    return {"id": c.id, "case_number": c.case_number, "subject": c.subject, "status": c.status, "priority": c.severity, "channel": c.channel,
            "category": c.category, "account": accounts.get(c.account_id), "account_id": c.account_id, "owner": names.get(c.owner_id),
            "owner_id": c.owner_id, "queue": queues.get(c.queue_id), "queue_id": c.queue_id, "opened_at": c.opened_at, "updated_at": c.updated_at,
            "sla_breached": c.sla_breached, "clocks": svc.clocks(c), "csat_score": c.csat_score}


async def _case(db: AsyncSession, p: Principal, case_id: uuid.UUID) -> SupportTicket:
    c = (await db.execute(svc.scope(p, select(SupportTicket).where(SupportTicket.id == case_id)))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "Case not found")
    return c


async def _check_owner(db: AsyncSession, owner_id) -> None:
    if owner_id is None:
        return
    u = await db.get(User, owner_id)
    works_cases = u is not None and u.is_active and (await load_matrix(db, u.role)).get("cases", Perm()).allows("update")
    if not works_cases:
        raise HTTPException(422, "Cases can only be assigned to active users who can work cases")


# ---- cases -------------------------------------------------------------------------------------

@router.get("/cases/meta")
async def meta(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("cases", "read"))):
    queues = (await db.execute(select(SupportQueue).order_by(SupportQueue.name))).scalars().all()
    users = (await db.execute(select(User).where(User.is_active.is_(True), User.role != "partner").order_by(User.full_name))).scalars().all()
    agents = [u for u in users if "cases" in (m := await load_matrix(db, u.role)) and m["cases"].allows("update")]
    return {"queues": [{"id": q.id, "name": q.name} for q in queues], "agents": [{"id": u.id, "name": u.full_name, "role": u.role} for u in agents],
            "priorities": list(svc.PRIORITIES), "statuses": list(svc.STATUSES), "channels": list(svc.CHANNELS),
            "sla": await app_settings.get(db, "case_sla"), "can_edit": p.can("cases", "update")}


@router.get("/cases/stats")
async def stats(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("cases", "read"))):
    base = svc.scope(p, select(func.count(SupportTicket.id)))
    open_ = SupportTicket.status.in_(svc.OPEN)
    one = lambda s: db.execute(s)  # noqa: E731
    since = datetime.now(timezone.utc) - timedelta(days=30)
    csat = (await db.execute(svc.scope(p, select(func.avg(SupportTicket.csat_score), func.count(SupportTicket.csat_score))
                                      .where(SupportTicket.csat_at >= since)))).one()
    return {
        "mine": (await one(base.where(open_, SupportTicket.owner_id == p.id))).scalar_one(),
        "unassigned": (await one(base.where(open_, SupportTicket.owner_id.is_(None)))).scalar_one(),
        "open": (await one(base.where(open_))).scalar_one(),
        "breached": (await one(base.where(open_, SupportTicket.sla_breached.is_(True)))).scalar_one(),
        "csat_30d": round(float(csat[0]), 2) if csat[0] is not None else None, "csat_responses_30d": csat[1],
    }


@router.get("/cases")
async def list_cases(view: Literal["mine", "unassigned", "open", "breached", "resolved", "all"] = "open", queue_id: uuid.UUID | None = None,
                     priority: Priority | None = None, account_id: uuid.UUID | None = None, search: str | None = None, limit: int = 200,
                     db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("cases", "read"))):
    stmt = svc.scope(p, select(SupportTicket))
    open_ = SupportTicket.status.in_(svc.OPEN)
    stmt = {"mine": stmt.where(open_, SupportTicket.owner_id == p.id), "unassigned": stmt.where(open_, SupportTicket.owner_id.is_(None)),
            "open": stmt.where(open_), "breached": stmt.where(open_, SupportTicket.sla_breached.is_(True)),
            "resolved": stmt.where(SupportTicket.status.in_(("resolved", "closed"))), "all": stmt}[view]
    if queue_id:
        stmt = stmt.where(SupportTicket.queue_id == queue_id)
    if priority:
        stmt = stmt.where(SupportTicket.severity == priority)
    if account_id:
        stmt = stmt.where(SupportTicket.account_id == account_id)
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(or_(SupportTicket.subject.ilike(like), SupportTicket.case_number.ilike(like)))
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    rows = (await db.execute(stmt.order_by(SupportTicket.opened_at.desc()).limit(min(limit, 500)))).scalars().all()
    if view != "resolved":
        rows.sort(key=lambda c: (rank.get(c.severity, 9), c.resolve_due_at or datetime.max.replace(tzinfo=timezone.utc)))
    names = await _names(db, [c.owner_id for c in rows])
    accounts = {a.id: a.name for a in (await db.execute(select(Account).where(Account.id.in_({c.account_id for c in rows})))).scalars()} if rows else {}
    queues = {q.id: q.name for q in (await db.execute(select(SupportQueue))).scalars()}
    return [_case_row(c, names, accounts, queues) for c in rows]


@router.post("/cases", status_code=201)
async def create_case(body: CaseIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("cases", "create"))):
    await p.ensure_account(db, body.account_id, "cases")
    await _check_owner(db, body.owner_id)
    try:
        case = await svc.create(db, body.model_dump(), p.user)
    except svc.CaseError as e:
        raise _bad(e)
    await db.commit()
    return {"id": case.id, "case_number": case.case_number}


@router.get("/cases/{case_id}")
async def get_case(case_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("cases", "read"))):
    c = await _case(db, p, case_id)
    comments = (await db.execute(select(CaseComment).where(CaseComment.case_id == c.id).order_by(CaseComment.created_at))).scalars().all()
    names = await _names(db, [c.owner_id, *[x.author_id for x in comments]])
    account = await db.get(Account, c.account_id)
    contact = await db.get(Contact, c.contact_id) if c.contact_id else None
    queue = await db.get(SupportQueue, c.queue_id) if c.queue_id else None
    history = (await db.execute(select(SupportTicket.id, SupportTicket.case_number, SupportTicket.subject, SupportTicket.status)
                                .where(SupportTicket.account_id == c.account_id, SupportTicket.id != c.id)
                                .order_by(SupportTicket.opened_at.desc()).limit(5))).all()
    return {
        **_case_row(c, names, {account.id: account.name}, {queue.id: queue.name} if queue else {}),
        "description": c.description, "resolved_at": c.resolved_at, "first_responded_at": c.first_responded_at,
        "account_detail": {"id": account.id, "name": account.name, "tier": account.tier, "health_score": account.health_score, "owner_id": account.owner_id},
        "contact": {"id": contact.id, "name": f"{contact.first_name} {contact.last_name}", "email": contact.email, "phone": contact.phone,
                    "job_title": contact.job_title} if contact else None,
        "comments": [{"id": x.id, "author": names.get(x.author_id), "body": x.body, "internal": x.internal, "created_at": x.created_at} for x in comments],
        "suggested_articles": await svc.suggest_articles(db, f"{c.subject} {c.category or ''}"),
        "other_cases": [{"id": h.id, "case_number": h.case_number, "subject": h.subject, "status": h.status} for h in history],
        "csat": {"score": c.csat_score, "comment": c.csat_comment, "at": c.csat_at,
                 "survey_url": f"{settings.public_web_url.rstrip('/')}/csat/{c.csat_token}" if c.csat_token else None},
    }


@router.patch("/cases/{case_id}")
async def patch_case(case_id: uuid.UUID, body: CasePatch, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("cases", "update"))):
    c = await _case(db, p, case_id)
    data = body.model_dump(exclude_unset=True)
    cleared = [k for k in ("status", "severity", "subject", "channel") if k in data and data[k] is None]
    if cleared:
        raise HTTPException(422, f"{', '.join(cleared)} can't be empty")
    if "owner_id" in data:
        await _check_owner(db, data["owner_id"])
    if data.get("contact_id"):
        contact = await db.get(Contact, data["contact_id"])
        if contact is None or contact.account_id != c.account_id:
            raise HTTPException(422, "The contact must belong to the case's account")
    if data.get("queue_id") and await db.get(SupportQueue, data["queue_id"]) is None:
        raise HTTPException(422, "Queue not found")
    await svc.update(db, c, data)
    await db.commit()
    return {"status": "ok"}


@router.post("/cases/{case_id}/comments", status_code=201)
async def comment(case_id: uuid.UUID, body: CommentIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("cases", "update"))):
    c = await _case(db, p, case_id)
    x = await svc.add_comment(db, c, p.user, body.body.strip(), body.internal)
    await db.commit()
    return {"id": x.id}


# ---- service settings: queues & SLA ---------------------------------------------------------------

@router.get("/service/queues")
async def list_queues(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("cases", "read"))):
    qs = (await db.execute(select(SupportQueue).order_by(SupportQueue.name))).scalars().all()
    open_counts = dict((await db.execute(select(SupportTicket.queue_id, func.count()).where(SupportTicket.status.in_(svc.OPEN))
                                         .group_by(SupportTicket.queue_id))).all())
    return [{"id": q.id, "name": q.name, "description": q.description, "member_ids": q.member_ids, "auto_assign": q.auto_assign,
             "is_default": q.is_default, "open_cases": open_counts.get(q.id, 0)} for q in qs]


async def _save_queue(db: AsyncSession, q: SupportQueue, body: QueueIn) -> None:
    for uid in body.member_ids:
        await _check_owner(db, uid)
    if body.is_default:
        for other in (await db.execute(select(SupportQueue).where(SupportQueue.id != q.id))).scalars():
            other.is_default = False
    q.name, q.description, q.auto_assign, q.is_default = body.name.strip(), body.description, body.auto_assign, body.is_default
    q.member_ids = [str(u) for u in dict.fromkeys(body.member_ids)]


@router.post("/service/queues", status_code=201)
async def create_queue(body: QueueIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    if (await db.execute(select(SupportQueue.id).where(func.lower(SupportQueue.name) == body.name.strip().lower()))).first():
        raise HTTPException(409, "A queue with this name exists")
    q = SupportQueue(name=body.name)
    db.add(q)
    await _save_queue(db, q, body)
    await db.commit()
    return {"id": q.id}


@router.put("/service/queues/{queue_id}")
async def update_queue(queue_id: uuid.UUID, body: QueueIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    q = await db.get(SupportQueue, queue_id)
    if q is None:
        raise HTTPException(404, "Queue not found")
    await _save_queue(db, q, body)
    await db.commit()
    return {"status": "ok"}


@router.delete("/service/queues/{queue_id}", status_code=204)
async def delete_queue(queue_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("admin", "update"))):
    q = await db.get(SupportQueue, queue_id)
    if q:
        await db.delete(q)
        await db.commit()


@router.get("/service/sla")
async def get_sla(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("cases", "read"))):
    return await app_settings.get(db, "case_sla")


@router.put("/service/sla")
async def put_sla(body: dict[Priority, dict[Literal["first_response_hours", "resolve_hours"], float]], db: AsyncSession = Depends(get_db),
                  _: Principal = Depends(authorize("admin", "update"))):
    for pr, t in body.items():
        if not all(0 < float(v) <= 24 * 90 for v in t.values()):
            raise HTTPException(422, f"{pr}: targets must be between a few minutes and 90 days")
        if t.get("first_response_hours", 0) > t.get("resolve_hours", 1e9):
            raise HTTPException(422, f"{pr}: first response can't be later than resolution")
    out = await app_settings.put(db, "case_sla", {k: dict(v) for k, v in body.items()})
    await db.commit()
    return out


# ---- knowledge base ------------------------------------------------------------------------------

def _article_out(a: KbArticle, names: dict, full: bool = False) -> dict:
    out = {"id": a.id, "title": a.title, "category": a.category, "status": a.status, "tags": a.tags, "author": names.get(a.author_id),
           "views": a.views, "helpful": a.helpful, "not_helpful": a.not_helpful, "updated_at": a.updated_at}
    if full:
        out["body"] = a.body
    else:
        out["excerpt"] = (a.body or "")[:220]
    return out


@router.get("/knowledge")
async def list_articles(search: str | None = None, category: str | None = None, db: AsyncSession = Depends(get_db),
                        p: Principal = Depends(authorize("knowledge", "read"))):
    stmt = select(KbArticle)
    if not p.can("knowledge", "update"):
        stmt = stmt.where(KbArticle.status == "published")
    if category:
        stmt = stmt.where(KbArticle.category == category)
    if search and search.strip():
        q = func.websearch_to_tsquery("english", search.strip())
        stmt = stmt.where(or_(KbArticle.search_tsv.op("@@")(q), KbArticle.title.ilike(f"%{search.strip()}%"))).order_by(
            func.ts_rank(KbArticle.search_tsv, q).desc())
    else:
        stmt = stmt.order_by(KbArticle.updated_at.desc())
    rows = (await db.execute(stmt.limit(200))).scalars().all()
    names = await _names(db, [a.author_id for a in rows])
    cats = sorted({c for (c,) in (await db.execute(select(KbArticle.category).where(KbArticle.category.isnot(None)).distinct())).all()})
    return {"articles": [_article_out(a, names) for a in rows], "categories": cats, "can_edit": p.can("knowledge", "update")}


@router.post("/knowledge", status_code=201)
async def create_article(body: ArticleIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("knowledge", "create"))):
    a = KbArticle(**body.model_dump(), author_id=p.id)
    db.add(a)
    await db.commit()
    return {"id": a.id}


@router.get("/knowledge/{article_id}")
async def get_article(article_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("knowledge", "read"))):
    a = await db.get(KbArticle, article_id)
    if a is None or (a.status != "published" and not p.can("knowledge", "update")):
        raise HTTPException(404, "Article not found")
    a.views += 1
    await db.commit()
    return _article_out(a, await _names(db, [a.author_id]), full=True)


@router.put("/knowledge/{article_id}")
async def update_article(article_id: uuid.UUID, body: ArticleIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("knowledge", "update"))):
    a = await db.get(KbArticle, article_id)
    if a is None:
        raise HTTPException(404, "Article not found")
    for k, v in body.model_dump().items():
        setattr(a, k, v)
    await db.commit()
    return {"status": "ok"}


@router.delete("/knowledge/{article_id}", status_code=204)
async def delete_article(article_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("knowledge", "delete"))):
    a = await db.get(KbArticle, article_id)
    if a:
        await db.delete(a)
        await db.commit()


@router.post("/knowledge/{article_id}/feedback")
async def feedback(article_id: uuid.UUID, helpful: bool, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("knowledge", "read"))):
    a = await db.get(KbArticle, article_id)
    if a is None:
        raise HTTPException(404, "Article not found")
    if helpful:
        a.helpful += 1
    else:
        a.not_helpful += 1
    await db.commit()
    return {"helpful": a.helpful, "not_helpful": a.not_helpful}


# ---- public CSAT survey --------------------------------------------------------------------------

@public.get("/{token}")
async def csat_get(token: str, db: AsyncSession = Depends(get_db)):
    c = (await db.execute(select(SupportTicket).where(SupportTicket.csat_token == token))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "This survey link isn't valid")
    account = await db.get(Account, c.account_id)
    return {"case_number": c.case_number, "subject": c.subject, "account": account.name if account else None, "rated": c.csat_score is not None}


@public.post("/{token}")
async def csat_post(token: str, body: CsatIn, db: AsyncSession = Depends(get_db)):
    try:
        await svc.submit_csat(db, token, body.score, body.comment)
    except svc.CaseError as e:
        raise HTTPException(409 if "already" in str(e) else 404, str(e))
    await db.commit()
    return {"status": "thanks"}
