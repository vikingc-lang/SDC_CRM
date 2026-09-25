"""Algorithmic health, risk, relationship and churn scoring (pillars 1, 2, 6, 7).

Account Health  H = 0.30*Recency + 0.25*Sentiment + 0.15*Velocity + 0.15*Support + 0.15*Milestones
    Recency    R = max(0, 100 - 5 * days_since_last_touch)
    Sentiment  S = avg(last 5 notes: positive=100, neutral=50, negative=0) + min(0, drift) / 2
               drift = avg(last 5) - avg(previous 5)   (only a *falling* trend is penalised)
    Velocity   V = 100 if deals move on pace; 30 if any open deal stalled in stage > 21 days
    Support    T = max(0, 100 - sum(open ticket weights: critical 35, high 20, medium 10, low 4))
    Milestones M = max(0, 100 - 20 * overdue milestones & tasks)

Deal Risk  R = 30*Stale + 30*SentimentDrop + 40*NoChampion                       (spec section 6)
Weighted pipeline = sum(amount_usd * probability * (1 - risk/200))                (spec section 7)

Relationship Strength Index (contact) = weighted mean of available signals:
    reply latency (40%): median hours from our outbound email to their reply; <=4h -> 100, >=72h -> 0
    inbound frequency (30%): inbound emails/calls in 30 days, 20 points each (max 100)
    meeting attendance (30%): attended / (attended + no-shows)
Churn risk (customers) = adoption + ticket severity + champion turnover + low health, capped at 100.
"""
from __future__ import annotations

import statistics
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account, Activity, Contact, Contract, Deal, OnboardingMilestone, OnboardingProject, PipelineStage, ProductUsage,
    SupportTicket, Task,
)

SENTIMENT_VALUE = {"positive": 100, "neutral": 50, "negative": 0}
STALL_DAYS = 21
STALE_DAYS = 14
CHAMPION_ROLES = {"Champion", "Decision Maker"}
TICKET_WEIGHT = {"critical": 35, "high": 20, "medium": 10, "low": 4}
HEALTH_WEIGHTS = {"recency": 0.30, "sentiment": 0.25, "velocity": 0.15, "support": 0.15, "milestones": 0.15}


# ---- pure functions (unit-tested) -------------------------------------------
def recency_score(days_since_touch: float) -> float:
    return max(0.0, 100.0 - 5.0 * days_since_touch)


def sentiment_score(sentiments: list[str]) -> float:
    recent = sentiments[:5]
    if not recent:
        return 50.0
    return sum(SENTIMENT_VALUE.get(s, 50) for s in recent) / len(recent)


def sentiment_drift(sentiments: list[str]) -> float:
    """Newest-first list -> avg(last 5) - avg(previous 5); 0 without history."""
    if len(sentiments) < 6:
        return 0.0
    return sentiment_score(sentiments[:5]) - sentiment_score(sentiments[5:10])


def drifted_sentiment(sentiments: list[str]) -> float:
    return max(0.0, sentiment_score(sentiments) + min(0.0, sentiment_drift(sentiments)) / 2)


def velocity_score(open_deal_days_in_stage: list[float]) -> float:
    return 30.0 if any(d > STALL_DAYS for d in open_deal_days_in_stage) else 100.0


def support_score(open_ticket_severities: list[str]) -> float:
    return max(0.0, 100.0 - sum(TICKET_WEIGHT.get(s, 0) for s in open_ticket_severities))


def milestone_score(overdue_count: int) -> float:
    return max(0.0, 100.0 - 20.0 * overdue_count)


def health_score(recency: float, sentiment: float, velocity: float, support: float = 100.0, milestones: float = 100.0) -> int:
    w = HEALTH_WEIGHTS
    value = w["recency"] * recency + w["sentiment"] * sentiment + w["velocity"] * velocity + w["support"] * support + w["milestones"] * milestones
    return int(round(max(0.0, min(100.0, value))))


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


def latency_score(hours: float) -> float:
    if hours <= 4:
        return 100.0
    if hours >= 72:
        return 0.0
    return round(100.0 * (72 - hours) / 68, 1)


def relationship_strength(latency_hours: float | None, inbound_30d: int, attended: int, no_shows: int) -> tuple[int, dict]:
    parts: list[tuple[float, float]] = []
    factors: dict = {"inbound_30d": inbound_30d, "meetings_attended": attended, "meetings_missed": no_shows}
    if latency_hours is not None:
        factors["median_reply_hours"] = round(latency_hours, 1)
        factors["latency_score"] = latency_score(latency_hours)
        parts.append((0.4, factors["latency_score"]))
    factors["inbound_score"] = min(100.0, inbound_30d * 20.0)
    parts.append((0.3, factors["inbound_score"]))
    if attended + no_shows:
        factors["attendance_score"] = round(100.0 * attended / (attended + no_shows), 1)
        parts.append((0.3, factors["attendance_score"]))
    total_w = sum(w for w, _ in parts)
    return int(round(sum(w * v for w, v in parts) / total_w)), factors


def _days_since(ts: datetime | None, now: datetime) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds() / 86400.0)


# ---- persistence-aware recomputation -----------------------------------------
async def contact_rsi(db: AsyncSession, contact: Contact, now: datetime) -> tuple[int, dict]:
    acts = (
        await db.execute(
            select(Activity.occurred_at, Activity.activity_type, Activity.direction, Activity.attendance)
            .where(Activity.contact_id == contact.id)
            .order_by(Activity.occurred_at)
        )
    ).all()
    latencies: list[float] = []
    pending_outbound: datetime | None = None
    inbound_30d = attended = no_shows = 0
    for a in acts:
        if a.activity_type == "email" and a.direction == "outbound" and pending_outbound is None:
            pending_outbound = a.occurred_at
        elif a.activity_type == "email" and a.direction == "inbound" and pending_outbound is not None:
            latencies.append((a.occurred_at - pending_outbound).total_seconds() / 3600)
            pending_outbound = None
        if a.direction == "inbound" and a.activity_type in ("email", "call") and _days_since(a.occurred_at, now) <= 30:
            inbound_30d += 1
        if a.activity_type == "meeting" and a.attendance == "attended":
            attended += 1
        elif a.activity_type == "meeting" and a.attendance == "no_show":
            no_shows += 1
    if not acts:
        return 0, {"inbound_30d": 0, "note": "no two-way engagement recorded"}
    return relationship_strength(statistics.median(latencies) if latencies else None, inbound_30d, attended, no_shows)


async def rescore_account(db: AsyncSession, account_id: uuid.UUID, now: datetime | None = None) -> Account | None:
    """Recompute health, relationship strength, churn risk and every open deal's risk."""
    now = now or datetime.now(timezone.utc)
    account = await db.get(Account, account_id)
    if account is None:
        return None
    today = now.date()

    activities = (
        await db.execute(
            select(Activity.occurred_at, Activity.sentiment, Activity.deal_id)
            .where(Activity.account_id == account_id, Activity.activity_type.notin_(("system", "file", "document")))
            .order_by(Activity.occurred_at.desc())
        )
    ).all()
    contacts = (await db.execute(select(Contact).where(Contact.account_id == account_id))).scalars().all()
    active_contacts = [c for c in contacts if c.status == "active"]
    has_champion = any(c.buying_role in CHAMPION_ROLES for c in active_contacts)

    deals = (
        await db.execute(
            select(Deal).join(PipelineStage, Deal.stage_id == PipelineStage.id).where(
                Deal.account_id == account_id, PipelineStage.is_closed_won.is_(False), PipelineStage.is_closed_lost.is_(False)
            )
        )
    ).scalars().unique().all()
    tickets = (
        await db.execute(select(SupportTicket.severity).where(SupportTicket.account_id == account_id, SupportTicket.status.in_(("open", "pending"))))
    ).scalars().all()
    overdue_milestones = len(
        (
            await db.execute(
                select(OnboardingMilestone.id).join(OnboardingProject).where(
                    OnboardingProject.account_id == account_id, OnboardingMilestone.status != "done", OnboardingMilestone.due_date < today
                )
            )
        ).all()
    )
    overdue_tasks = len(
        (await db.execute(select(Task.id).where(Task.account_id == account_id, Task.completed.is_(False), Task.due_date < today))).all()
    )

    sentiments = [a.sentiment for a in activities]
    last_touch = activities[0].occurred_at if activities else account.created_at
    parts = {
        "recency": recency_score(_days_since(last_touch, now) or 0.0),
        "sentiment": drifted_sentiment(sentiments),
        "velocity": velocity_score([_days_since(d.stage_entered_at, now) or 0.0 for d in deals]),
        "support": support_score(list(tickets)),
        "milestones": milestone_score(overdue_milestones + overdue_tasks),
    }
    account.health_score = health_score(**parts)
    account.custom_metadata = {
        **(account.custom_metadata or {}),
        "health_breakdown": {
            **{k: round(v) for k, v in parts.items()},
            "sentiment_drift": round(sentiment_drift(sentiments)),
            "open_tickets": len(tickets),
            "overdue_items": overdue_milestones + overdue_tasks,
        },
    }

    # relationship strength per contact, account = mean of its three strongest relationships
    strengths = []
    for c in active_contacts:
        c.relationship_strength, c.rsi_factors = await contact_rsi(db, c, now)
        strengths.append(c.relationship_strength)
    account.relationship_strength = round(sum(sorted(strengths, reverse=True)[:3]) / min(3, len(strengths))) if strengths else None

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

    await compute_churn(db, account, contacts, list(tickets), now)
    await db.flush()
    return account


async def compute_churn(db: AsyncSession, account: Account, contacts: list[Contact], open_severities: list[str], now: datetime) -> None:
    """Churn early-warning for customers: adoption, ticket severity, champion turnover, health."""
    active_contract = (
        await db.execute(select(Contract.id).where(Contract.account_id == account.id, Contract.status == "active").limit(1))
    ).first()
    if account.lifecycle_stage != "customer" and active_contract is None:
        account.churn_risk, account.churn_factors = 0, {}
        return
    factors: dict = {}
    risk = 0
    usage = (
        await db.execute(select(ProductUsage).where(ProductUsage.account_id == account.id).order_by(ProductUsage.metric_date.desc()).limit(90))
    ).scalars().all()
    if usage:
        latest = usage[0]
        util = latest.active_users / latest.licensed_users if latest.licensed_users else 0
        factors["utilization_pct"] = round(util * 100)
        if util < 0.3:
            risk += 40
        elif util < 0.5:
            risk += 25
        elif util < 0.7:
            risk += 10
        baseline = next((u for u in usage if (latest.metric_date - u.metric_date).days >= 60), None)
        if baseline and baseline.active_users:
            trend = (latest.active_users - baseline.active_users) / baseline.active_users
            factors["active_user_trend_pct"] = round(trend * 100)
            if trend < -0.15:
                risk += 10
    sev = {s: open_severities.count(s) for s in ("critical", "high")}
    if sev["critical"] or sev["high"]:
        factors["open_critical_tickets"], factors["open_high_tickets"] = sev["critical"], sev["high"]
        risk += min(30, 15 * sev["critical"] + 8 * sev["high"])
    cutoff = now - timedelta(days=180)
    departed = [c for c in contacts if c.status == "departed" and c.buying_role in CHAMPION_ROLES and c.departed_at and c.departed_at >= cutoff]
    if departed:
        factors["champions_departed"] = [c.full_name for c in departed]
        risk += 30
        if not any(c.status == "active" and c.buying_role in CHAMPION_ROLES for c in contacts):
            factors["no_remaining_champion"] = True
            risk += 10
    if account.health_score < 50:
        factors["low_health"] = account.health_score
        risk += 10
    account.churn_risk = min(100, risk)
    account.churn_factors = factors


async def rescore_all(db: AsyncSession) -> int:
    ids = (await db.execute(select(Account.id))).scalars().all()
    for account_id in ids:
        await rescore_account(db, account_id)
    await db.commit()
    return len(ids)


def _today() -> date:
    return datetime.now(timezone.utc).date()
