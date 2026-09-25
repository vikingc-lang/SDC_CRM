"""Shape ORM objects into API payloads."""
from __future__ import annotations

from datetime import datetime, timezone

from app.models import Activity, Contact, Deal, Task
from app.services import fx
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
        "id": c.id, "account_id": c.account_id, "first_name": c.first_name, "last_name": c.last_name, "name": c.full_name,
        "email": c.email, "phone": c.phone, "mobile": c.mobile, "linkedin_url": c.linkedin_url, "timezone": c.timezone,
        "department": c.department, "job_title": c.job_title, "buying_role": c.buying_role, "status": c.status,
        "departed_at": c.departed_at, "relationship_strength": c.relationship_strength, "rsi_factors": c.rsi_factors or {},
        "consent": {
            "email": c.consent_email, "basis": c.consent_basis, "regime": c.privacy_regime, "updated_at": c.consent_updated_at,
            "do_not_sell": c.do_not_sell, "opt_out": {"email": c.opt_out_email, "phone": c.opt_out_phone, "sms": c.opt_out_sms},
        },
        "custom_fields": c.custom_fields or {},
        "account_name": account_name,
    }


def deal_card(d: Deal, rates: dict | None = None) -> dict:
    stage = d.stage
    probability = stage.default_probability
    open_ = not (stage.is_closed_won or stage.is_closed_lost)
    amount = float(d.amount or 0)
    amount_usd = fx.to_usd(amount, d.currency, rates or fx.DEFAULT_RATES)
    return {
        "id": d.id,
        "title": d.title,
        "amount": amount,
        "currency": d.currency,
        "amount_usd": amount_usd,
        "account": {"id": d.account.id, "name": d.account.name, "domain": d.account.domain, "health_score": d.account.health_score,
                    "credit_hold": d.account.credit_hold},
        "pipeline_id": d.pipeline_id,
        "stage": stage.name,
        "stage_id": stage.id,
        "is_won": stage.is_closed_won,
        "is_lost": stage.is_closed_lost,
        "probability": probability,
        "risk_score": d.risk_score if open_ else 0,
        "risk_factors": d.risk_factors or {},
        "weighted_value": weighted_value(amount_usd, probability, d.risk_score if open_ else 0),
        "target_close_date": d.target_close_date,
        "original_close_date": d.original_close_date,
        "close_date_pushes": d.close_date_pushes,
        "days_in_stage": days_between(d.stage_entered_at),
        "owner": user_brief(d.owner),
        "primary_contact": (
            {"id": d.primary_contact.id, "name": d.primary_contact.full_name, "buying_role": d.primary_contact.buying_role}
            if d.primary_contact else None
        ),
        "deal_type": d.deal_type,
        "source": d.source,
        "contract_id": d.contract_id,
        "loss_reason": d.loss_reason,
        "loss_debrief": d.loss_debrief,
        "loss_competitor": d.loss_competitor,
        "win_debrief": d.win_debrief,
        "custom_fields": d.custom_fields or {},
        "ai_insights": d.ai_insights or {},
        "created_at": d.created_at,
    }


def activity_out(a: Activity, similarity: float | None = None, attachments: list | None = None) -> dict:
    return {
        "id": a.id,
        "date": a.occurred_at,
        "type": a.activity_type,
        "summary": a.summary,
        "subject": a.subject,
        "sentiment": a.sentiment,
        "direction": a.direction,
        "duration_seconds": a.duration_seconds,
        "disposition": a.disposition,
        "agenda": a.agenda,
        "attendance": a.attendance,
        "source": a.source,
        "account": {"id": a.account.id, "name": a.account.name} if a.account else None,
        "deal": {"id": a.deal.id, "title": a.deal.title} if a.deal else None,
        "contact": {"id": a.contact.id, "name": a.contact.full_name} if a.contact else None,
        "user": user_brief(a.user),
        "similarity": similarity,
        "attachments": attachments or [],
    }


def task_out(t: Task) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "due_date": t.due_date,
        "completed": t.completed,
        "completed_at": t.completed_at,
        "source": t.source,
        "priority": t.priority,
        "owner": user_brief(t.owner),
        "assignee": user_brief(t.assignee),
        "depends_on": {"id": t.depends_on.id, "title": t.depends_on.title, "completed": t.depends_on.completed} if t.depends_on else None,
        "blocked": bool(t.depends_on and not t.depends_on.completed),
        "escalation_level": t.escalation_level,
        "escalated_at": t.escalated_at,
        "milestone_id": t.milestone_id,
        "account": {"id": t.account.id, "name": t.account.name} if t.account else None,
        "deal": {"id": t.deal.id, "title": t.deal.title} if t.deal else None,
        "created_at": t.created_at,
    }
