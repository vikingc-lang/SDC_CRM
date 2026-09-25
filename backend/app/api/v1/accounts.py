import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_writer
from app.models import Account, Activity, Contact, Deal, DealStageHistory, PipelineStage, Task, User
from app.schemas.crm import AccountCreate, AccountListItem, AccountUpdate
from app.services import scoring
from app.services.serializers import activity_out, contact_out, deal_card, task_out, user_brief

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _normalize_domain(domain: str) -> str:
    d = domain.strip().lower().removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return d.split("/")[0]


@router.get("", response_model=list[AccountListItem])
async def list_accounts(
    skip: int = 0,
    limit: int = Query(50, le=200),
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Account).order_by(Account.name).offset(skip).limit(limit)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Account.name.ilike(like), Account.domain.ilike(like), Account.industry.ilike(like)))
    accounts = (await db.execute(stmt)).scalars().unique().all()
    ids = [a.id for a in accounts]
    if not ids:
        return []
    open_rows = (
        await db.execute(
            select(Deal.account_id, func.count(Deal.id), func.coalesce(func.sum(Deal.amount), 0))
            .join(PipelineStage, Deal.stage_id == PipelineStage.id)
            .where(Deal.account_id.in_(ids), PipelineStage.is_closed_won.is_(False), PipelineStage.is_closed_lost.is_(False))
            .group_by(Deal.account_id)
        )
    ).all()
    last_rows = (
        await db.execute(
            select(Activity.account_id, func.max(Activity.occurred_at))
            .where(Activity.account_id.in_(ids), Activity.activity_type != "system")
            .group_by(Activity.account_id)
        )
    ).all()
    open_map = {r[0]: (r[1], float(r[2])) for r in open_rows}
    last_map = {r[0]: r[1] for r in last_rows}
    return [
        AccountListItem(
            id=a.id,
            name=a.name,
            domain=a.domain,
            industry=a.industry,
            tier=a.tier,
            health=a.health_score,
            owner=user_brief(a.owner),
            open_deals=open_map.get(a.id, (0, 0.0))[0],
            open_pipeline=open_map.get(a.id, (0, 0.0))[1],
            contacts=len(a.contacts),
            last_activity_at=last_map.get(a.id),
        )
        for a in accounts
    ]


@router.post("", status_code=201)
async def create_account(body: AccountCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_writer)):
    account = Account(
        name=body.name.strip(),
        domain=_normalize_domain(body.domain),
        industry=body.industry,
        tier=body.tier,
        owner_id=body.owner_id or user.id,
        custom_metadata={},
    )
    db.add(account)
    try:
        await db.commit()
    except IntegrityError:
        raise HTTPException(409, f"An account with domain {account.domain} already exists")
    return {"id": account.id, "name": account.name, "domain": account.domain}


@router.patch("/{account_id}")
async def update_account(account_id: uuid.UUID, body: AccountUpdate, db: AsyncSession = Depends(get_db), _: User = Depends(require_writer)):
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(account, field, value)
    await db.commit()
    return {"id": account.id}


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = Depends(require_writer)):
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    if (await db.execute(select(func.count()).select_from(Deal).where(Deal.account_id == account_id))).scalar_one():
        raise HTTPException(409, "Account has deals. Close or delete them first.")
    await db.delete(account)
    await db.commit()


@router.get("/{account_id}/360")
async def account_360(account_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    deals = (await db.execute(select(Deal).where(Deal.account_id == account_id).order_by(Deal.created_at.desc()))).scalars().unique().all()
    activities = (
        await db.execute(select(Activity).where(Activity.account_id == account_id).order_by(Activity.occurred_at.desc()).limit(50))
    ).scalars().unique().all()
    tasks = (
        await db.execute(select(Task).where(Task.account_id == account_id).order_by(Task.completed, Task.due_date.nulls_last()))
    ).scalars().unique().all()
    history = (
        await db.execute(
            select(DealStageHistory).join(Deal, DealStageHistory.deal_id == Deal.id).where(Deal.account_id == account_id).order_by(DealStageHistory.changed_at.desc()).limit(20)
        )
    ).scalars().unique().all()
    contacts = sorted(account.contacts, key=lambda c: ("Champion", "Decision Maker", "Economic Buyer", "Influencer", "Evaluator", "Blocker").index(c.buying_role))
    cards = [deal_card(d) for d in deals]
    return {
        "account": {
            "id": account.id,
            "name": account.name,
            "domain": account.domain,
            "industry": account.industry,
            "tier": account.tier,
            "health_score": account.health_score,
            "health_breakdown": (account.custom_metadata or {}).get("health_breakdown"),
            "owner": user_brief(account.owner),
            "created_at": account.created_at,
        },
        "contacts": [contact_out(c) for c in contacts],
        "deals": cards,
        "recent_activities": [activity_out(a) for a in activities],
        "tasks": [task_out(t) for t in tasks],
        "stage_history": [
            {
                "deal_id": h.deal_id,
                "from": h.from_stage.name if h.from_stage else None,
                "to": h.to_stage.name,
                "forecast_delta": float(h.forecast_delta),
                "gate_overridden": h.gate_overridden,
                "by": user_brief(h.user),
                "at": h.changed_at,
            }
            for h in history
        ],
        "summary": {
            "open_pipeline": round(sum(c["amount"] for c in cards if c["stage"] not in ("Closed-Won", "Closed-Lost")), 2),
            "weighted_pipeline": round(sum(c["weighted_value"] for c in cards if c["stage"] not in ("Closed-Won", "Closed-Lost")), 2),
            "won_revenue": round(sum(c["amount"] for c in cards if c["stage"] == "Closed-Won"), 2),
            "has_champion": any(c.buying_role in scoring.CHAMPION_ROLES for c in contacts),
        },
    }
