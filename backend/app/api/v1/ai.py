import re
import uuid
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.rbac import Principal, authorize, get_principal
from app.models import Account, Activity, Contact, Deal, DealStageHistory, PipelineStage, Task, User
from app.schemas.ai import AskRequest, CommitLogRequest, CommitLogResponse, QuickLogRequest, QuickLogResponse, SemanticSearchRequest
from app.services import ai_extractor, dedup, insights, llm, pipeline_service, scoring, voice
from app.services.search import hybrid_search
from app.services.jobs import enqueue
from app.services.serializers import deal_card

router = APIRouter(tags=["ai"])


@router.get("/ai/status")
async def ai_status(_: User = Depends(get_current_user)):
    return {
        "llm_provider": llm.provider_name(),
        "model": {
            "ollama": settings.ollama_model,
            "aws_bedrock": settings.bedrock_model_id,
            "anthropic": settings.anthropic_model,
            "heuristic": "Aiden offline engine (deterministic)",
        }[llm.provider_name()],
        "embedding_provider": settings.embedding_provider,
        "embedding_dim": settings.embedding_dim,
        "background": "celery" if settings.use_celery else "in-process",
        "transcription_provider": settings.transcription_provider,
        "erp_connector": settings.erp_connector,
    }


async def _open_deals(db: AsyncSession, account_id: uuid.UUID, p: Principal | None = None) -> list[Deal]:
    stmt = (select(Deal).join(PipelineStage, Deal.stage_id == PipelineStage.id)
            .where(Deal.account_id == account_id, PipelineStage.is_closed_won.is_(False), PipelineStage.is_closed_lost.is_(False))
            .order_by(Deal.updated_at.desc()))
    if p is not None:
        stmt = p.scope_deals(stmt)
    return (await db.execute(stmt)).scalars().unique().all()


def _best_deal(deals: list[Deal], title: str | None) -> Deal | None:
    if not deals:
        return None
    if title:
        words = set(re.findall(r"\w{4,}", title.lower()))
        scored = sorted(deals, key=lambda d: -len(words & set(re.findall(r"\w{4,}", d.title.lower()))))
        if words & set(re.findall(r"\w{4,}", scored[0].title.lower())):
            return scored[0]
    return deals[0] if len(deals) == 1 else None


@router.post("/ai/quick-log", response_model=QuickLogResponse)
async def quick_log(body: QuickLogRequest, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("activities", "read"))):
    """Parse unstructured notes into a validated preview. Nothing is written."""
    known = [(a.name, a.domain) for a in (await db.execute(p.scope_accounts(select(Account)))).scalars().unique().all()]
    result = await ai_extractor.extract(body.raw_text, known)
    return await _enrich(db, p, result, body.account_id)


async def _enrich(db: AsyncSession, p: Principal, result: QuickLogResponse, account_id: uuid.UUID | None) -> QuickLogResponse:
    """Match the extraction to existing records the caller may see. Accounts and deals outside their scope are
    never named in the preview (nor is their existence hinted at); the commit step applies the same rule."""
    visible = p.scope_accounts(select(Account))

    async def seen(acc_id) -> Account | None:
        return (await db.execute(visible.where(Account.id == acc_id))).scalars().first() if acc_id else None

    account = await seen(account_id)
    if account is None and (result.domain or result.account_name):
        conds = []
        if result.domain:
            conds.append(Account.domain == result.domain.lower())
        if result.account_name:
            conds.append(func.lower(Account.name) == result.account_name.lower())
        account = (await db.execute(visible.where(or_(*conds)))).scalars().first()
    if account is None and result.account_name:
        # fuzzy (Jaro-Winkler / Levenshtein / domain) match avoids creating duplicates
        match = await dedup.find_account_duplicate(db, result.account_name, result.domain)
        if match and match["score"] >= dedup.ACCOUNT_SUGGEST_AT:
            account = await seen(match["account"]["id"])
            if account is not None:
                result.signals = {**result.signals, "fuzzy_account_match": {"score": match["score"], "reasons": match["reasons"]}}
    if account is not None:
        result.matched_account_id = account.id
        result.account_name = account.name
        result.domain = account.domain
        deal = _best_deal(await _open_deals(db, account.id, p), result.deal.title if result.deal else None)
        if deal is not None:
            result.matched_deal_id = deal.id
            if result.deal:
                result.deal.title = deal.title
    return result


@router.post("/ai/commit-log", response_model=CommitLogResponse)
async def commit_log(body: CommitLogRequest, background: BackgroundTasks, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("activities", "create"))):
    """Persist a (user-confirmed) QuickLogResponse: account, contacts, deal, activity, tasks.

    Everything it reads or changes is held to the caller's permissions and row-level scope: an existing account
    or opportunity must be visible to them, contacts are only matched on that account, and records are only
    created or updated where the role allows (otherwise that part is skipped and the note is still logged)."""
    user = p.user
    # 1. Account
    account = None
    for candidate in (body.account_id, body.matched_account_id):
        if candidate and account is None:
            account = await db.get(Account, candidate)
    if account is None and body.domain:
        account = (await db.execute(select(Account).where(Account.domain == body.domain.lower()))).scalars().first()
    if account is None and body.account_name:
        account = (await db.execute(select(Account).where(func.lower(Account.name) == body.account_name.lower()))).scalars().first()
    if account is None and body.account_name:
        match = await dedup.find_account_duplicate(db, body.account_name, body.domain)
        if match and match["score"] >= dedup.ACCOUNT_SUGGEST_AT:
            account = await db.get(Account, match["account"]["id"])
    if account is not None:
        await p.ensure_account(db, account.id, "activities")
    if account is None:
        if not body.account_name:
            raise HTTPException(422, "Could not determine the account. Add an account name or pick an existing account.")
        if not p.can("accounts", "create"):
            raise HTTPException(403, "Your role can't create accounts. Pick an existing account for this note.")
        slug = re.sub(r"[^a-z0-9]+", "-", body.account_name.lower()).strip("-") or "account"
        domain = (body.domain or f"{slug}.unverified").lower()
        account = Account(
            name=body.account_name,
            domain=domain,
            owner_id=user.id,
            custom_metadata={"domain_unverified": not body.domain, "source": "quick-log"},
        )
        db.add(account)
        from app.services import performance

        await performance.assign(db, account)
        await db.flush()

    # 2. Contacts
    created_contacts: list[Contact] = []
    touched_contacts: list[Contact] = []
    for c in body.contacts:
        existing = None
        if c.email:  # only on this account: never touch another account's people
            existing = (await db.execute(select(Contact).where(Contact.account_id == account.id, Contact.status != "erased",
                                                               func.lower(Contact.email) == c.email.lower()))).scalars().first()
        if existing is None:
            existing = (
                await db.execute(
                    select(Contact).where(
                        Contact.account_id == account.id,
                        func.lower(Contact.first_name) == c.first_name.lower(),
                        func.lower(Contact.last_name) == (c.last_name or "").lower(),
                    )
                )
            ).scalars().first()
        if existing is not None:
            touched_contacts.append(existing)
            if not p.can("contacts", "update"):
                continue
            existing.job_title = existing.job_title or c.job_title
            existing.email = existing.email or (c.email.lower() if c.email else None)
            if c.buying_role != "Evaluator":
                existing.buying_role = c.buying_role
            continue
        if not p.can("contacts", "create"):
            continue
        email = c.email.lower() if c.email else None
        if email and (await db.execute(select(Contact.id).where(func.lower(Contact.email) == email))).first():
            email = None  # the address belongs to a contact elsewhere (emails are unique): keep the person, not the clash
        contact = Contact(
            account_id=account.id,
            first_name=c.first_name,
            last_name=c.last_name or "",
            job_title=c.job_title,
            email=email,
            buying_role=c.buying_role,
        )
        db.add(contact)
        created_contacts.append(contact)
        touched_contacts.append(contact)
    await db.flush()

    # 3. Deal (update the matched deal, or open a new one)
    deal = None
    for candidate in (body.deal_id, body.matched_deal_id):
        if candidate and deal is None:
            deal = (await db.execute(p.scope_deals(select(Deal).where(Deal.id == candidate, Deal.account_id == account.id)))).scalars().first()
            if deal is None and candidate == body.deal_id:
                raise HTTPException(404, "Opportunity not found on this account")
    if deal is not None and body.deal and p.can("deals", "update"):
        if body.deal.amount:
            deal.amount = body.deal.amount
        if body.deal.target_close_date:
            deal.target_close_date = body.deal.target_close_date
    elif deal is None and body.create_deal and body.deal and (body.deal.title or body.deal.amount) and p.can("deals", "create"):
        pipeline = await pipeline_service.default_pipeline(db)
        stage = next((s for s in pipeline.stages if s.name == body.deal.suggested_stage), pipeline.stages[0])
        key_contact = next((c for c in touched_contacts if c.buying_role in ("Champion", "Decision Maker")), touched_contacts[0] if touched_contacts else None)
        deal = Deal(
            title=body.deal.title or f"{account.name} Opportunity",
            account_id=account.id,
            pipeline_id=pipeline.id,
            stage_id=stage.id,
            amount=body.deal.amount or 0,
            target_close_date=body.deal.target_close_date,
            owner_id=user.id,
            primary_contact_id=key_contact.id if key_contact else None,
            risk_factors={},
            ai_insights={},
        )
        db.add(deal)
        await db.flush()
        db.add(DealStageHistory(deal_id=deal.id, from_stage_id=None, to_stage_id=stage.id, changed_by=user.id))
    if deal is not None and body.signals and p.can("deals", "update"):
        ins = dict(deal.ai_insights or {})
        for key in ("competitors", "pain_points"):
            merged = list(dict.fromkeys([*ins.get(key, []), *body.signals.get(key, [])]))
            if merged:
                ins[key] = merged[:8]
        deal.ai_insights = ins

    # 4. Activity + 5. Action items
    activity = Activity(
        account_id=account.id,
        deal_id=deal.id if deal else None,
        contact_id=touched_contacts[0].id if touched_contacts else None,
        user_id=user.id,
        activity_type=body.activity_type,
        summary=body.summary,
        raw_text=body.raw_text,
        sentiment=body.sentiment,
        source="quick_log",
    )
    db.add(activity)
    await db.flush()
    tasks = body.action_items if p.can("tasks", "create") else []
    for item in tasks:
        db.add(Task(title=item.task, due_date=item.due_date, account_id=account.id, deal_id=deal.id if deal else None, activity_id=activity.id, owner_id=user.id, source="ai"))

    await scoring.rescore_account(db, account.id)
    await db.commit()
    enqueue(background, "embed_activity", str(activity.id))
    return CommitLogResponse(
        account_id=account.id,
        deal_id=deal.id if deal else None,
        activity_id=activity.id,
        contacts_created=len(created_contacts),
        tasks_created=len(tasks),
    )


@router.post("/search/semantic")
async def semantic_search(body: SemanticSearchRequest, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("activities", "read"))):
    """Hybrid RAG retrieval: pgvector similarity fused with full-text rank over notes, emails and account records."""
    return await hybrid_search(db, body.query, body.limit, p)


@router.get("/search/global")
async def global_search(q: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("accounts", "read"))):
    like = f"%{q}%"
    accounts = (await db.execute(p.scope_accounts(select(Account)).where(or_(Account.name.ilike(like), Account.domain.ilike(like))).limit(5))).scalars().unique().all()
    contacts = (
        await db.execute(p.scope_accounts(select(Contact), "contacts", Contact.account_id).where(Contact.status != "erased", or_(Contact.first_name.ilike(like), Contact.last_name.ilike(like), Contact.email.ilike(like), (Contact.first_name + " " + Contact.last_name).ilike(like))).limit(5))
    ).scalars().all()
    deals = (await db.execute(p.scope_deals(select(Deal)).where(Deal.title.ilike(like)).limit(5))).scalars().unique().all()
    return {
        "accounts": [{"id": a.id, "name": a.name, "domain": a.domain, "health": a.health_score} for a in accounts],
        "contacts": [{"id": c.id, "name": c.full_name, "job_title": c.job_title, "account_id": c.account_id} for c in contacts],
        "deals": [{"id": d.id, "title": d.title, "account": d.account.name, "stage": d.stage.name, "amount": float(d.amount)} for d in deals],
    }


@router.post("/ai/ask")
async def ask(body: AskRequest, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("activities", "read"))):
    if body.account_id:
        await p.ensure_account(db, body.account_id, "activities")
    if body.deal_id:
        await _visible_deal(db, p, body.deal_id)
    return await insights.ask(db, body.question, body.account_id, body.deal_id, p)


@router.get("/ai/briefing")
async def briefing(db: AsyncSession = Depends(get_db), p: Principal = Depends(get_principal)):
    return await insights.briefing(db, p)


async def _visible_deal(db: AsyncSession, p: Principal, deal_id: uuid.UUID) -> Deal:
    """404 for a deal outside the caller's row-level scope, like the deals API."""
    deal = (await db.execute(p.scope_deals(select(Deal).where(Deal.id == deal_id)))).scalars().unique().one_or_none()
    if deal is None:
        raise HTTPException(404, "Deal not found")
    return deal


@router.post("/ai/deals/{deal_id}/draft-email")
async def draft_email(deal_id: uuid.UUID, purpose: str = "follow-up", db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "read"))):
    deal = await _visible_deal(db, p, deal_id)
    return {"draft": await insights.draft_email(db, deal, purpose), "engine": llm.provider_name()}


@router.get("/ai/accounts/{account_id}/brief")
async def account_brief(account_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("accounts", "read"))):
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    await p.ensure_account(db, account_id)
    deals = [deal_card(d) for d in (await db.execute(select(Deal).where(Deal.account_id == account_id))).scalars().unique().all()]
    activities = (
        await db.execute(select(Activity).where(Activity.account_id == account_id, Activity.activity_type != "system").order_by(Activity.occurred_at.desc()).limit(10))
    ).scalars().unique().all()
    return {"brief": await insights.account_brief(db, account, deals, list(account.contacts), activities), "engine": llm.provider_name()}


@router.get("/dashboard/summary")
async def dashboard(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "read"))):
    fc = await pipeline_service.forecast(db, p)
    task_q = select(func.count()).select_from(Task).where(Task.completed.is_(False))
    if p.is_own_scope("tasks"):
        task_q = task_q.where(or_(Task.owner_id == p.id, Task.assignee_id == p.id))
    fc["open_tasks"] = (await db.execute(task_q)).scalar_one()
    fc["overdue_tasks"] = (await db.execute(task_q.where(Task.due_date < date.today()))).scalar_one()
    return fc


@router.get("/alerts")
async def alerts(db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("deals", "read"))):
    """Open alerts from the deal risk & slippage copilot."""
    from app.models import DealAlert

    stmt = p.scope_deals(select(DealAlert).join(Deal, DealAlert.deal_id == Deal.id)).where(DealAlert.resolved_at.is_(None)).order_by(DealAlert.created_at.desc())
    rows = (await db.execute(stmt)).scalars().unique().all()
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted([{"id": a.id, "kind": a.kind, "severity": a.severity, "message": a.message, "details": a.details, "created_at": a.created_at,
                    "deal": {"id": a.deal.id, "title": a.deal.title, "account": a.deal.account.name, "amount": float(a.deal.amount), "currency": a.deal.currency}}
                   for a in rows], key=lambda x: order[x["severity"]])


@router.post("/ai/transcribe")
async def transcribe(file: UploadFile = File(...), account_id: uuid.UUID | None = Form(default=None), db: AsyncSession = Depends(get_db),
                     p: Principal = Depends(authorize("activities", "read"))):
    """Private audio transcription -> ambient extraction preview (same contract as /ai/quick-log)."""
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"Audio exceeds {settings.max_upload_mb} MB")
    try:
        transcript = await voice.transcribe(data, file.filename or "audio.webm", file.content_type)
    except voice.TranscriptionUnavailable as exc:
        raise HTTPException(503, str(exc))
    if not transcript.strip():
        raise HTTPException(422, "No speech detected in the recording")
    known = [(a.name, a.domain) for a in (await db.execute(p.scope_accounts(select(Account)))).scalars().unique().all()]
    result = await _enrich(db, p, await ai_extractor.extract(transcript, known), account_id)
    result.signals = {**result.signals, "transcript": transcript}
    return result
