"""Shape ORM objects into API payloads."""
from __future__ import annotations

from datetime import datetime, timezone

from app.models import Activity, Contact, Deal, Task
from app.services.scoring import weighted_value


def days_between(ts: datetime | None, now: datetime | None = None) -> int:
    if ts is None:
        return 0
    now = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0, int((now - ts).total_seconds() // 86400))


def user_brief(user) -> dict | None:
    return {"id": user.id, "full_name": user.full_name} if user else None


def contact_out(c: Contact, account_name: str | None = None) -> dict:
    return {
        "id": c.id,
        "account_id": c.account_id,
        "first_name": c.first_name,
        "last_name": c.last_name,
        "name": c.full_name,
        "email": c.email,
        "phone": c.phone,
        "job_title": c.job_title,
        "buying_role": c.buying_role,
        "account_name": account_name,
    }


def deal_card(d: Deal) -> dict:
    stage = d.stage
    probability = stage.default_probability
    open_ = not (stage.is_closed_won or stage.is_closed_lost)
    return {
        "id": d.id,
        "title": d.title,
        "amount": float(d.amount or 0),
        "currency": d.currency,
        "account": {"id": d.account.id, "name": d.account.name, "domain": d.account.domain, "health_score": d.account.health_score},
        "stage": stage.name,
        "stage_id": stage.id,
        "probability": probability,
        "risk_score": d.risk_score if open_ else 0,
        "risk_factors": d.risk_factors or {},
        "weighted_value": weighted_value(float(d.amount or 0), probability, d.risk_score if open_ else 0),
        "target_close_date": d.target_close_date,
        "days_in_stage": days_between(d.stage_entered_at),
        "owner": user_brief(d.owner),
        "primary_contact": (
            {"id": d.primary_contact.id, "name": d.primary_contact.full_name, "buying_role": d.primary_contact.buying_role}
            if d.primary_contact
            else None
        ),
        "loss_reason": d.loss_reason,
        "ai_insights": d.ai_insights or {},
        "created_at": d.created_at,
    }


def activity_out(a: Activity, similarity: float | None = None) -> dict:
    return {
        "id": a.id,
        "date": a.occurred_at,
        "type": a.activity_type,
        "summary": a.summary,
        "sentiment": a.sentiment,
        "account": {"id": a.account.id, "name": a.account.name} if a.account else None,
        "deal": {"id": a.deal.id, "title": a.deal.title} if a.deal else None,
        "user": user_brief(a.user),
        "similarity": similarity,
    }


def task_out(t: Task) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "due_date": t.due_date,
        "completed": t.completed,
        "source": t.source,
        "account": {"id": t.account.id, "name": t.account.name} if t.account else None,
        "deal": {"id": t.deal.id, "title": t.deal.title} if t.deal else None,
        "created_at": t.created_at,
    }
