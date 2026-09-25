"""Algorithmic health & risk scoring engine (specification sections 6 and 7).

Account Health  H = 0.40*Recency + 0.35*Sentiment + 0.25*Velocity
    Recency   R = max(0, 100 - 5 * days_since_last_touch)
    Sentiment S = rolling average of last 5 notes (positive=100, neutral=50, negative=0)
    Velocity  V = 100 if deals move on pace; 30 if any open deal stalled in stage > 21 days

Deal Risk  R = 30*Stale + 30*SentimentDrop + 40*NoChampion
    Stale         no activity recorded in the past 14 days
    SentimentDrop most recent activity logged negative sentiment
    NoChampion    account contacts lack a tagged Champion or Decision Maker

Weighted pipeline = sum(amount * probability * (1 - risk/200)) over active deals
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Activity, Contact, Deal, PipelineStage

SENTIMENT_VALUE = {"positive": 100, "neutral": 50, "negative": 0}
STALL_DAYS = 21
STALE_DAYS = 14
CHAMPION_ROLES = {"Champion", "Decision Maker"}


# ---- pure functions (unit-tested) -------------------------------------------
def recency_score(days_since_touch: float) -> float:
    return max(0.0, 100.0 - 5.0 * days_since_touch)


def sentiment_score(sentiments: list[str]) -> float:
    recent = sentiments[:5]
    if not recent:
        return 50.0
    return sum(SENTIMENT_VALUE.get(s, 50) for s in recent) / len(recent)


def velocity_score(open_deal_days_in_stage: list[float]) -> float:
    return 30.0 if any(d > STALL_DAYS for d in open_deal_days_in_stage) else 100.0


def health_score(recency: float, sentiment: float, velocity: float) -> int:
    return int(round(max(0.0, min(100.0, 0.40 * recency + 0.35 * sentiment + 0.25 * velocity))))


def deal_risk(days_since_activity: float | None, last_sentiment: str | None, has_champion: bool) -> tuple[int, dict]:
    stale = days_since_activity is None or days_since_activity > STALE_DAYS
    sentiment_drop = last_sentiment == "negative"
    no_champion = not has_champion
    score = 30 * stale + 30 * sentiment_drop + 40 * no_champion
    factors = {
        "stale": stale,
        "sentiment_drop": sentiment_drop,
        "no_champion": no_champion,
        "days_since_activity": None if days_since_activity is None else round(days_since_activity, 1),
    }
    return int(score), factors


def weighted_value(amount: float, probability: int, risk_score: int) -> float:
    return round(float(amount) * (probability / 100.0) * (1 - risk_score / 200.0), 2)


def _days_since(ts: datetime | None, now: datetime) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds() / 86400.0)


# ---- persistence-aware recomputation -----------------------------------------
async def rescore_account(db: AsyncSession, account_id: uuid.UUID, now: datetime | None = None) -> Account | None:
    """Recompute account health and the risk of every open deal on the account."""
    now = now or datetime.now(timezone.utc)
    account = await db.get(Account, account_id)
    if account is None:
        return None

    activities = (
        await db.execute(
            select(Activity.occurred_at, Activity.sentiment, Activity.deal_id)
            .where(Activity.account_id == account_id, Activity.activity_type != "system")
            .order_by(Activity.occurred_at.desc())
        )
    ).all()
    roles = (await db.execute(select(Contact.buying_role).where(Contact.account_id == account_id))).scalars().all()
    has_champion = any(r in CHAMPION_ROLES for r in roles)

    deals = (
        await db.execute(
            select(Deal).join(PipelineStage, Deal.stage_id == PipelineStage.id).where(
                Deal.account_id == account_id,
                PipelineStage.is_closed_won.is_(False),
                PipelineStage.is_closed_lost.is_(False),
            )
        )
    ).scalars().unique().all()

    last_touch = activities[0].occurred_at if activities else account.created_at
    recency = recency_score(_days_since(last_touch, now) or 0.0)
    sentiment = sentiment_score([a.sentiment for a in activities])
    velocity = velocity_score([_days_since(d.stage_entered_at, now) or 0.0 for d in deals])
    account.health_score = health_score(recency, sentiment, velocity)
    account.custom_metadata = {
        **(account.custom_metadata or {}),
        "health_breakdown": {"recency": round(recency), "sentiment": round(sentiment), "velocity": round(velocity)},
    }

    for deal in deals:
        deal_acts = [a for a in activities if a.deal_id == deal.id] or list(activities)
        last = deal_acts[0] if deal_acts else None
        score, factors = deal_risk(
            _days_since(last.occurred_at, now) if last else _days_since(deal.created_at, now),
            last.sentiment if last else None,
            has_champion,
        )
        factors["days_in_stage"] = round(_days_since(deal.stage_entered_at, now) or 0.0, 1)
        deal.risk_score = score
        deal.risk_factors = factors

    await db.flush()
    return account


async def rescore_all(db: AsyncSession) -> int:
    ids = (await db.execute(select(Account.id))).scalars().all()
    for account_id in ids:
        await rescore_account(db, account_id)
    await db.commit()
    return len(ids)
