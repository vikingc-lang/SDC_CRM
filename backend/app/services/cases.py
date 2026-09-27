"""Customer service cases: numbering, SLA clocks, queue routing, conversations, breach alerts, CSAT
and knowledge-base suggestions.

SLA targets per priority live in app settings ("case_sla"). The first-response clock stops at the
first public reply from a Cirra user; the resolution clock stops when the case is resolved. The
hourly scan flags breaches once and notifies the owner, the queue and the owner's manager.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import Principal
from app.models import Account, CaseComment, Contact, KbArticle, SupportQueue, SupportTicket, User
from app.services import app_settings, scoring
from app.services.notify import emit, notify

PRIORITIES = ("critical", "high", "medium", "low")
STATUSES = ("open", "pending", "resolved", "closed")
CHANNELS = ("email", "phone", "web", "portal", "chat")
OPEN = ("open", "pending")

class CaseError(ValueError):
    pass


def scope(p: Principal, stmt):
    """Sellers with 'own' scope see cases they own or on accounts they own or sell into."""
    if not p.is_own_scope("cases"):
        return stmt
    return stmt.where(or_(SupportTicket.owner_id == p.id, SupportTicket.account_id.in_(p.owned_account_ids())))


async def _next_number(db: AsyncSession) -> str:
    n = (await db.execute(text("SELECT nextval('case_number_seq')"))).scalar_one()
    return f"CS-{n:05d}"


async def apply_sla(db: AsyncSession, case: SupportTicket, from_time: datetime | None = None) -> None:
    targets = (await app_settings.get(db, "case_sla")).get(case.severity) or {}
    start = from_time or case.opened_at or datetime.now(timezone.utc)
    if case.first_responded_at is None:
        case.first_response_due_at = start + timedelta(hours=float(targets.get("first_response_hours", 8)))
    case.resolve_due_at = start + timedelta(hours=float(targets.get("resolve_hours", 72)))


async def _pick_agent(db: AsyncSession, queue: SupportQueue) -> uuid.UUID | None:
    """The queue's routing rule decides (services/routing.py)."""
    from app.services import routing

    return await routing.pick(db, queue)


async def default_queue(db: AsyncSession) -> SupportQueue | None:
    return (await db.execute(select(SupportQueue).order_by(SupportQueue.is_default.desc(), SupportQueue.created_at).limit(1))).scalar_one_or_none()


async def create(db: AsyncSession, data: dict, actor: User | None) -> SupportTicket:
    if await db.get(Account, data["account_id"]) is None:
        raise CaseError("Account not found")
    if data.get("contact_id"):
        c = await db.get(Contact, data["contact_id"])
        if c is None or c.account_id != data["account_id"]:
            raise CaseError("The contact must belong to the case's account")
    queue = await db.get(SupportQueue, data["queue_id"]) if data.get("queue_id") else await default_queue(db)
    case = SupportTicket(**{k: v for k, v in data.items() if k != "queue_id"}, status="open", queue_id=queue.id if queue else None,
                         case_number=await _next_number(db), opened_at=datetime.now(timezone.utc))
    if not case.owner_id and queue and queue.auto_assign:
        case.owner_id = await _pick_agent(db, queue)
    await apply_sla(db, case)
    db.add(case)
    await db.flush()
    notify(db, [case.owner_id] if case.owner_id and (actor is None or case.owner_id != actor.id) else [], "case",
           f"{case.case_number} assigned to you: {case.subject}", None, f"/cases/{case.id}")
    emit(db, "case.created", "case", case.id, {"case_id": str(case.id), "case_number": case.case_number, "account_id": str(case.account_id),
                                               "subject": case.subject, "priority": case.severity, "channel": case.channel})
    await scoring.rescore_account(db, case.account_id)
    return case


async def add_comment(db: AsyncSession, case: SupportTicket, author: User, body: str, internal: bool) -> CaseComment:
    c = CaseComment(case_id=case.id, author_id=author.id, body=body, internal=internal)
    db.add(c)
    now = datetime.now(timezone.utc)
    if not internal and case.first_responded_at is None:
        case.first_responded_at = now  # stops the first-response clock
    if not internal and case.status == "open":
        case.status = "pending"  # waiting on the customer
    case.updated_at = now
    if case.owner_id and case.owner_id != author.id:
        notify(db, [case.owner_id], "case", f"New {'note' if internal else 'reply'} on {case.case_number}", body[:200], f"/cases/{case.id}")
    return c


async def update(db: AsyncSession, case: SupportTicket, data: dict) -> SupportTicket:
    now = datetime.now(timezone.utc)
    if "severity" in data and data["severity"] != case.severity:
        case.severity = data.pop("severity")
        await apply_sla(db, case, case.opened_at)  # re-time against the new priority
        case.sla_breached = False
        case.breach_notified_at = None
    if "status" in data and data["status"] != case.status:
        new = data.pop("status")
        if new in ("resolved", "closed") and case.status in OPEN:
            case.resolved_at = now
            case.csat_token = case.csat_token or secrets.token_urlsafe(24)
            emit(db, "case.resolved", "case", case.id, {"case_id": str(case.id), "case_number": case.case_number,
                                                        "account_id": str(case.account_id), "status": new})
        if new in OPEN and case.status in ("resolved", "closed"):
            case.resolved_at = None  # reopened
        case.status = new
    if "queue_id" in data and data["queue_id"] != case.queue_id:
        case.queue_id = data.pop("queue_id")
        queue = await db.get(SupportQueue, case.queue_id) if case.queue_id else None
        if queue and queue.auto_assign and "owner_id" not in data:
            case.owner_id = await _pick_agent(db, queue)
    if "owner_id" in data and data["owner_id"] != case.owner_id:
        case.owner_id = data.pop("owner_id")
        if case.owner_id:
            notify(db, [case.owner_id], "case", f"{case.case_number} assigned to you: {case.subject}", None, f"/cases/{case.id}")
    for k, v in data.items():
        setattr(case, k, v)
    case.updated_at = now
    await db.flush()
    from app.services import routing

    await routing.assign_waiting(db, [case.queue_id])  # a resolved or handed-off case may have freed an agent
    await scoring.rescore_account(db, case.account_id)
    return case


def clocks(case: SupportTicket, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)

    def clock(due, done):
        if done:
            return {"state": "met" if due is None or done <= due else "missed", "due_at": due, "done_at": done}
        if due is None:
            return {"state": "none"}
        mins = (due - now).total_seconds() / 60
        return {"state": "breached" if mins < 0 else "due_soon" if mins < 30 else "running", "due_at": due, "minutes_left": round(mins)}
    return {"first_response": clock(case.first_response_due_at, case.first_responded_at), "resolution": clock(case.resolve_due_at, case.resolved_at)}


async def scan_breaches(db: AsyncSession) -> dict:
    now = datetime.now(timezone.utc)
    rows = (await db.execute(select(SupportTicket).where(SupportTicket.status.in_(OPEN), or_(
        (SupportTicket.first_responded_at.is_(None)) & (SupportTicket.first_response_due_at < now),
        SupportTicket.resolve_due_at < now)))).scalars().all()
    queues = {q.id: q for q in (await db.execute(select(SupportQueue))).scalars()}
    flagged = 0
    for case in rows:
        case.sla_breached = True
        if case.breach_notified_at is None:
            case.breach_notified_at = now
            flagged += 1
            people = [case.owner_id] if case.owner_id else [uuid.UUID(str(i)) for i in (queues.get(case.queue_id).member_ids if case.queue_id in queues else [])]
            if case.owner_id:
                mgr = (await db.execute(select(User.manager_id).where(User.id == case.owner_id))).scalar()
                people.append(mgr)
            notify(db, people, "case", f"SLA breached on {case.case_number} ({case.severity}): {case.subject}", None, f"/cases/{case.id}")
    await db.commit()
    return {"open_breached": len(rows), "newly_flagged": flagged}


async def suggest_articles(db: AsyncSession, text_: str, limit: int = 5) -> list[dict]:
    words = " OR ".join(w for w in "".join(ch if ch.isalnum() else " " for ch in (text_ or "")).split() if len(w) > 2)
    if not words:
        return []
    q = func.websearch_to_tsquery("english", words)
    rows = (await db.execute(select(KbArticle.id, KbArticle.title, KbArticle.category, func.ts_rank(KbArticle.search_tsv, q).label("rank"))
                             .where(KbArticle.status == "published", KbArticle.search_tsv.op("@@")(q))
                             .order_by(text("rank DESC")).limit(limit))).all()
    return [{"id": r.id, "title": r.title, "category": r.category} for r in rows]


async def submit_csat(db: AsyncSession, token: str, score: int, comment: str | None) -> SupportTicket:
    case = (await db.execute(select(SupportTicket).where(SupportTicket.csat_token == token))).scalar_one_or_none()
    if case is None:
        raise CaseError("This survey link isn't valid")
    if case.csat_score is not None:
        raise CaseError("Thanks, we already have your rating for this case")
    case.csat_score, case.csat_comment, case.csat_at = score, (comment or None), datetime.now(timezone.utc)
    if case.owner_id:
        notify(db, [case.owner_id], "case", f"{case.case_number} rated {score}/5", comment, f"/cases/{case.id}")
    return case


# ---- demo content (seed, and once for workspaces created before case management) -----------------

DEMO_ARTICLES = [
    ("Fix an SSO login loop", "Access",
     "If users bounce between the sign-in page and your identity provider:\n\n1. Check the redirect URI registered at the IdP matches exactly, including the trailing path.\n2. Confirm the user's email at the IdP matches their CRM login.\n3. Clear the browser's cookies for both sites and try a private window.\n4. If it persists, capture the time of the attempt and share it with support so we can match the IdP logs."),
    ("Troubleshoot a failing nightly sync", "Integrations",
     "Nightly EHR and ERP syncs fail most often because of expired credentials or a changed field mapping.\n\n- Open Settings and re-enter the connector credentials.\n- Run the sync manually and read the first error line.\n- Compare the mapping with the source system's latest export.\n- Critical data gaps: raise a Critical case so the 1-hour response target applies."),
    ("Export dashboards to Excel", "Reporting",
     "Open the report, choose Export CSV, then open the file in Excel. Numbers use a dot as the decimal separator; set Excel's import locale to English (United States) if values land in one column."),
    ("API rate limits", "Developers",
     "The API allows 600 requests per minute per key. Responses include X-RateLimit-Remaining. On HTTP 429, wait for the Retry-After seconds, then retry with exponential back-off."),
]


async def ensure_demo(db: AsyncSession) -> bool:
    """Queues, a Support Agent, knowledge articles, and SLA clocks on existing tickets. Idempotent."""
    from app.core.security import hash_password

    if (await db.execute(select(SupportQueue.id).limit(1))).first():
        return False
    agent = (await db.execute(select(User).where(User.email == "sofia@cirra.demo"))).scalar_one_or_none()
    if agent is None:
        admin = (await db.execute(select(User).where(User.email == "admin@cirra.demo"))).scalar_one_or_none()
        agent = User(email="sofia@cirra.demo", full_name="Sofia Lindqvist", role="support_agent", password_hash=hash_password("cirra123"),
                     manager_id=admin.id if admin else None)
        db.add(agent)
        await db.flush()
    support = SupportQueue(name="Customer Support", description="All new cases land here.", member_ids=[str(agent.id)], auto_assign=True, is_default=True)
    billing = SupportQueue(name="Billing", description="Invoices, credit holds and payment questions.", member_ids=[str(agent.id)], auto_assign=True)
    db.add_all([support, billing])
    for title, category, body in DEMO_ARTICLES:
        db.add(KbArticle(title=title, category=category, body=body, status="published", author_id=agent.id))
    await db.flush()
    for case in (await db.execute(select(SupportTicket))).scalars():
        case.case_number = case.case_number or await _next_number(db)
        case.queue_id = case.queue_id or support.id
        case.owner_id = case.owner_id or agent.id
        case.channel = case.channel or "email"
        await apply_sla(db, case)
        if case.status in ("resolved", "closed"):
            case.first_responded_at = case.first_responded_at or case.opened_at + timedelta(minutes=45)
            case.csat_token = case.csat_token or secrets.token_urlsafe(24)
    return True
