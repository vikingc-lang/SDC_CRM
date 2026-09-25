"""AI copilot: stage-gate triggers, next-best-actions, briefings, Q&A and drafts.

Each capability uses the private LLM when one is configured and falls back to
a deterministic, explainable implementation otherwise.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Activity, Contact, Deal, PipelineStage, Task
from app.services import embeddings, llm
from app.services.ai_extractor import detect_signals
from app.services.serializers import activity_out, days_between

COPILOT_SYSTEM = (
    "You are relate [R] Copilot, an assistant embedded in a private B2B CRM. Answer using only the CRM context "
    "provided. Be concise and specific: name accounts, people, amounts and dates. If the context does not contain "
    "the answer, say so plainly. Use short paragraphs or bullets; no preamble."
)


# ---- semantic memory ---------------------------------------------------------
async def embed_activity(db: AsyncSession, activity_id: uuid.UUID) -> None:
    activity = await db.get(Activity, activity_id)
    if activity is None:
        return
    content = f"{activity.summary}\n{activity.raw_text or ''}"
    activity.embedding = await embeddings.embed(content)
    await db.commit()


async def semantic_search(db: AsyncSession, query: str, limit: int = 10, account_id: uuid.UUID | None = None) -> list[dict]:
    vector = await embeddings.embed(query)
    results: list[dict] = []
    if vector is not None:
        stmt = (
            select(Activity, (1 - Activity.embedding.cosine_distance(vector)).label("similarity"))
            .where(Activity.embedding.is_not(None))
            .order_by(Activity.embedding.cosine_distance(vector))
            .limit(limit)
        )
        if account_id:
            stmt = stmt.where(Activity.account_id == account_id)
        for activity, similarity in (await db.execute(stmt)).unique().all():
            if similarity is not None and similarity > 0.05:
                results.append(activity_out(activity, round(float(similarity), 3)))
    if not results:  # keyword fallback when vectors are unavailable
        terms = [t for t in re.findall(r"\w{3,}", query.lower())][:6]
        if terms:
            stmt = select(Activity).where(or_(*[Activity.summary.ilike(f"%{t}%") for t in terms])).order_by(Activity.occurred_at.desc()).limit(limit)
            if account_id:
                stmt = stmt.where(Activity.account_id == account_id)
            results = [activity_out(a, None) for a in (await db.execute(stmt)).scalars().unique().all()]
    return results


# ---- stage-gate autonomous actions ---------------------------------------------
async def run_stage_trigger(db: AsyncSession, deal_id: uuid.UUID, user_id: uuid.UUID | None = None) -> None:
    deal = await db.get(Deal, deal_id)
    if deal is None:
        return
    stage = deal.stage
    notes = (
        await db.execute(
            select(Activity)
            .where(Activity.account_id == deal.account_id, Activity.activity_type != "system")
            .order_by(Activity.occurred_at.desc())
            .limit(20)
        )
    ).scalars().unique().all()
    corpus = "\n".join(f"{a.summary}\n{a.raw_text or ''}" for a in notes)
    signals = detect_signals(corpus)
    insights = dict(deal.ai_insights or {})
    now = datetime.now(timezone.utc)

    if stage.name == "Discovery":
        insights["pain_points"] = signals["pain_points"] or insights.get("pain_points", [])
    elif stage.name == "Pain Fit":
        insights["competitors"] = signals["competitors"]
    elif stage.name == "Solution Demo":
        insights["recap_email"] = await draft_email(db, deal, purpose="demo recap")
        for title in ("Send demo recap email with agreed evaluation criteria", "Confirm technical validation owner and timeline"):
            db.add(Task(title=title, due_date=date.today() + timedelta(days=2), account_id=deal.account_id, deal_id=deal.id, owner_id=deal.owner_id, source="ai"))
    elif stage.name == "Proposal/InfoSec":
        days_open = days_between(deal.created_at, now)
        insights["velocity"] = {
            "days_in_pipeline": days_open,
            "benchmark_days": 14,
            "status": "stagnating" if days_open > 14 else "on_pace",
        }
        if days_open > 14:
            db.add(Task(title="Deal is behind 14-day velocity benchmark: agree a mutual close plan", due_date=date.today() + timedelta(days=1), account_id=deal.account_id, deal_id=deal.id, owner_id=deal.owner_id, source="ai"))
    elif stage.is_closed_won:
        deal.account.health_score = 100
        db.add(Activity(account_id=deal.account_id, deal_id=deal.id, user_id=user_id, activity_type="system", summary=f"Onboarding event dispatched for {deal.title}. Customer success notified.", sentiment="positive"))
        db.add(Task(title=f"Run onboarding kickoff for {deal.account.name}", due_date=date.today() + timedelta(days=3), account_id=deal.account_id, deal_id=deal.id, owner_id=deal.owner_id, source="ai"))
    elif stage.is_closed_lost:
        reason = (deal.loss_reason or "other").replace("_", " ")
        comp = ", ".join(signals["competitors"]) or "none recorded"
        risks = " ".join(signals["risks"][:2]) or "No explicit risks were logged."
        postmortem = (
            f"Loss post-mortem for {deal.title} ({deal.account.name}): lost on {reason}. Amount ${float(deal.amount):,.0f}. "
            f"Competitors mentioned: {comp}. Signals before loss: {risks}"
        )
        memo = Activity(account_id=deal.account_id, deal_id=deal.id, user_id=user_id, activity_type="note", summary=postmortem, sentiment="negative")
        memo.embedding = await embeddings.embed(postmortem)
        db.add(memo)
        insights["postmortem"] = postmortem

    insights["last_trigger"] = {"stage": stage.name, "at": now.isoformat()}
    deal.ai_insights = insights
    await db.commit()


# ---- next best actions & briefing -------------------------------------------------
def next_best_actions(deal: dict) -> list[dict]:
    """Explainable recommendations derived from the risk factors."""
    f = deal.get("risk_factors") or {}
    actions: list[dict] = []
    if f.get("no_champion"):
        actions.append({"action": "Map a Champion or Decision Maker", "why": "No champion on the buying committee (+40 risk).", "impact": 40})
    if f.get("stale"):
        days = f.get("days_since_activity")
        actions.append({"action": "Re-engage with a value-focused touchpoint", "why": f"No activity for {int(days) if days else 'many'} days (+30 risk).", "impact": 30})
    if f.get("sentiment_drop"):
        actions.append({"action": "Address the latest objection directly", "why": "Most recent interaction was negative (+30 risk).", "impact": 30})
    if (f.get("days_in_stage") or 0) > 21:
        actions.append({"action": "Agree a mutual action plan to unstick the deal", "why": f"In {deal['stage']} for {int(f['days_in_stage'])} days (> 21).", "impact": 20})
    close = deal.get("target_close_date")
    if close and isinstance(close, date) and close < date.today():
        actions.append({"action": "Update the target close date", "why": f"Close date {close.isoformat()} has passed.", "impact": 10})
    return actions


async def briefing(db: AsyncSession, user_id: uuid.UUID | None = None) -> dict:
    today = date.today()
    tasks = (
        await db.execute(
            select(Task).where(Task.completed.is_(False), Task.due_date.is_not(None), Task.due_date <= today + timedelta(days=1)).order_by(Task.due_date).limit(8)
        )
    ).scalars().unique().all()
    risky = (
        await db.execute(
            select(Deal)
            .join(PipelineStage, Deal.stage_id == PipelineStage.id)
            .where(PipelineStage.is_closed_won.is_(False), PipelineStage.is_closed_lost.is_(False), Deal.risk_score >= 60)
            .order_by(Deal.amount.desc())
            .limit(5)
        )
    ).scalars().unique().all()
    closing = (
        await db.execute(
            select(Deal)
            .join(PipelineStage, Deal.stage_id == PipelineStage.id)
            .where(PipelineStage.is_closed_won.is_(False), PipelineStage.is_closed_lost.is_(False), Deal.target_close_date <= today + timedelta(days=30))
            .order_by(Deal.target_close_date)
            .limit(5)
        )
    ).scalars().unique().all()
    cooling = (await db.execute(select(Account).where(Account.health_score < 50).order_by(Account.health_score).limit(5))).scalars().unique().all()

    priorities: list[dict] = []
    for t in tasks:
        overdue = t.due_date < today
        priorities.append({
            "kind": "task", "id": str(t.id), "title": t.title,
            "detail": ("Overdue since " if overdue else "Due ") + t.due_date.strftime("%b %d") + (f" · {t.account.name}" if t.account else ""),
            "severity": "high" if overdue else "medium", "href": "/tasks",
        })
    for d in risky:
        priorities.append({
            "kind": "risk", "id": str(d.id), "title": f"{d.title} is at risk ({d.risk_score})",
            "detail": ", ".join(k.replace("_", " ") for k, v in (d.risk_factors or {}).items() if v is True) or "Multiple risk factors",
            "severity": "high", "href": f"/deals/{d.id}",
        })
    for d in closing:
        priorities.append({
            "kind": "closing", "id": str(d.id), "title": f"{d.title} closes {d.target_close_date.strftime('%b %d')}",
            "detail": f"${float(d.amount):,.0f} · {d.stage.name}", "severity": "medium", "href": f"/deals/{d.id}",
        })
    for a in cooling:
        priorities.append({
            "kind": "health", "id": str(a.id), "title": f"{a.name} health is {a.health_score}",
            "detail": "Relationship cooling. Schedule a check-in.", "severity": "medium", "href": f"/accounts/{a.id}",
        })

    headline = _headline(len(tasks), len(risky), len(closing))
    llm_text = None
    if priorities and llm.provider_name() != "heuristic":
        bullet_ctx = "\n".join(f"- {p['title']} ({p['detail']})" for p in priorities[:10])
        llm_text = await llm.complete_text(
            COPILOT_SYSTEM,
            f"Write a 2-sentence morning briefing for a sales rep based on these priorities:\n{bullet_ctx}",
            max_tokens=400,
        )
    return {"headline": llm_text or headline, "priorities": priorities[:12], "generated_at": datetime.now(timezone.utc)}


def _headline(tasks: int, risky: int, closing: int) -> str:
    parts = []
    if tasks:
        parts.append(f"{tasks} action item{'s' if tasks != 1 else ''} due")
    if risky:
        parts.append(f"{risky} deal{'s' if risky != 1 else ''} at risk")
    if closing:
        parts.append(f"{closing} closing within 30 days")
    if not parts:
        return "You're all caught up. A good day to prospect or deepen a champion relationship."
    return "Today: " + ", ".join(parts) + ". Start with the highlighted items below."


# ---- copilot Q&A ----------------------------------------------------------------
async def ask(db: AsyncSession, question: str, account_id: uuid.UUID | None = None, deal_id: uuid.UUID | None = None) -> dict:
    matches = await semantic_search(db, question, limit=6, account_id=account_id)
    facts = await _structured_facts(db, question, account_id, deal_id)
    if facts["text"]:
        # The question was answered from live pipeline data; only keep strongly related notes.
        matches = [m for m in matches if (m["similarity"] or 0) >= 0.35]
    answer = None
    if llm.provider_name() != "heuristic":
        context = "\n".join(f"[{i + 1}] {m['date']:%Y-%m-%d} {m['account']['name'] if m['account'] else ''}: {m['summary']}" for i, m in enumerate(matches))
        answer = await llm.complete_text(
            COPILOT_SYSTEM,
            f"CRM FACTS:\n{facts['text']}\n\nRELEVANT ACTIVITY NOTES:\n{context or 'none'}\n\nQUESTION: {question}\n"
            "Cite activity notes as [n] when you use them.",
            max_tokens=1200,
        )
    if not answer:
        answer = facts["text"] if facts["text"] else ""
        if matches:
            answer += ("\n\n" if answer else "") + "Most relevant notes from memory:\n" + "\n".join(
                f"• [{i + 1}] {m['summary'][:180]}" for i, m in enumerate(matches[:4])
            )
        if not answer:
            answer = "I couldn't find anything in your CRM memory about that yet. Try logging notes with ⌘K."
    return {"answer": answer, "sources": matches, "engine": llm.provider_name() if answer and llm.provider_name() != "heuristic" else "heuristic"}


async def _structured_facts(db: AsyncSession, question: str, account_id, deal_id) -> dict:
    q = question.lower()
    lines: list[str] = []
    open_q = select(Deal).join(PipelineStage, Deal.stage_id == PipelineStage.id).where(
        PipelineStage.is_closed_won.is_(False), PipelineStage.is_closed_lost.is_(False)
    )
    if account_id:
        open_q = open_q.where(Deal.account_id == account_id)
    if deal_id:
        open_q = open_q.where(Deal.id == deal_id)
    deals = (await db.execute(open_q)).scalars().unique().all()
    if re.search(r"risk|attention|trouble|slipping|worried", q):
        risky = sorted([d for d in deals if d.risk_score >= 40], key=lambda d: -d.risk_score)
        lines.append("Deals needing attention: " + ("; ".join(f"{d.title} ({d.account.name}, risk {d.risk_score})" for d in risky[:5]) or "none"))
    if re.search(r"forecast|pipeline|quarter|revenue|weighted|number", q):
        from app.services.scoring import weighted_value

        total = sum(float(d.amount) for d in deals)
        weighted = sum(weighted_value(float(d.amount), d.stage.default_probability, d.risk_score) for d in deals)
        lines.append(f"Open pipeline ${total:,.0f} across {len(deals)} deals; risk-adjusted weighted forecast ${weighted:,.0f}.")
    if re.search(r"clos|this month|next month|due", q):
        soon = sorted([d for d in deals if d.target_close_date], key=lambda d: d.target_close_date)[:5]
        lines.append("Upcoming closes: " + ("; ".join(f"{d.title} on {d.target_close_date:%b %d} (${float(d.amount):,.0f})" for d in soon) or "none scheduled"))
    if re.search(r"champion|decision maker|buying committee|who ", q):
        stmt = select(Contact).where(Contact.buying_role.in_(["Champion", "Decision Maker", "Economic Buyer"]))
        if account_id:
            stmt = stmt.where(Contact.account_id == account_id)
        people = (await db.execute(stmt.limit(8))).scalars().all()
        lines.append("Key stakeholders: " + ("; ".join(f"{c.full_name} ({c.buying_role}, {c.job_title or 'n/a'})" for c in people) or "none mapped"))
    if re.search(r"competit", q):
        comps = sorted({c for d in deals for c in (d.ai_insights or {}).get("competitors", [])})
        lines.append("Competitors detected in pipeline: " + (", ".join(comps) or "none detected yet"))
    return {"text": "\n".join(lines)}


# ---- drafting -------------------------------------------------------------------------
async def draft_email(db: AsyncSession, deal: Deal, purpose: str = "follow-up") -> str:
    contact = deal.primary_contact
    if contact is None:
        contact = (
            await db.execute(
                select(Contact).where(Contact.account_id == deal.account_id).order_by(
                    text("CASE buying_role WHEN 'Champion' THEN 0 WHEN 'Decision Maker' THEN 1 WHEN 'Economic Buyer' THEN 2 ELSE 3 END")
                )
            )
        ).scalars().first()
    last = (
        await db.execute(
            select(Activity).where(Activity.account_id == deal.account_id, Activity.activity_type != "system").order_by(Activity.occurred_at.desc()).limit(3)
        )
    ).scalars().unique().all()
    open_tasks = (await db.execute(select(Task).where(Task.deal_id == deal.id, Task.completed.is_(False)).limit(4))).scalars().unique().all()
    first = contact.first_name if contact else "there"
    sender = deal.owner.full_name if deal.owner else "The relate team"

    if llm.provider_name() != "heuristic":
        ctx = "\n".join(f"- {a.occurred_at:%Y-%m-%d} {a.activity_type}: {a.summary}" for a in last)
        out = await llm.complete_text(
            COPILOT_SYSTEM,
            f"Draft a concise, warm {purpose} email from {sender} to {first} at {deal.account.name} about '{deal.title}' "
            f"(stage: {deal.stage.name}). Recent context:\n{ctx}\nOpen action items: {', '.join(t.title for t in open_tasks) or 'none'}.\n"
            "Include a subject line on the first line as 'Subject: ...'. Under 170 words.",
            max_tokens=800,
        )
        if out:
            return out

    recap = last[0].summary if last else f"our recent conversations about {deal.title}"
    steps = "\n".join(f"  • {t.title}" for t in open_tasks) or "  • Confirm the evaluation timeline and success criteria\n  • Schedule our next working session"
    return (
        f"Subject: {deal.title}: recap & next steps\n\n"
        f"Hi {first},\n\n"
        f"Thank you for the time. Quick recap: {recap.rstrip('.')}.\n\n"
        f"Proposed next steps:\n{steps}\n\n"
        f"Does that match your understanding? Happy to adjust anything before we move forward.\n\n"
        f"Best regards,\n{sender}"
    )


async def account_brief(db: AsyncSession, account: Account, deals: list[dict], contacts: list[Contact], activities: list[Activity]) -> str:
    champions = [c for c in contacts if c.buying_role in ("Champion", "Decision Maker")]
    blockers = [c for c in contacts if c.buying_role == "Blocker"]
    open_deals = [d for d in deals if d["stage"] not in ("Closed-Won", "Closed-Lost")]
    if llm.provider_name() != "heuristic" and activities:
        ctx = "\n".join(f"- {a.occurred_at:%Y-%m-%d} {a.activity_type} ({a.sentiment}): {a.summary}" for a in activities[:10])
        deal_ctx = "\n".join(f"- {d['title']}: ${d['amount']:,.0f}, {d['stage']}, risk {d['risk_score']}" for d in open_deals)
        out = await llm.complete_text(
            COPILOT_SYSTEM,
            f"Write a 3-sentence account brief for {account.name} (health {account.health_score}/100).\nDeals:\n{deal_ctx or 'none'}\n"
            f"Stakeholders: {', '.join(f'{c.full_name} ({c.buying_role})' for c in contacts) or 'none'}\nRecent activity:\n{ctx}",
            max_tokens=500,
        )
        if out:
            return out
    parts = [f"{account.name} is a {account.tier} account with health {account.health_score}/100."]
    if open_deals:
        total = sum(d["amount"] for d in open_deals)
        parts.append(f"{len(open_deals)} open deal{'s' if len(open_deals) > 1 else ''} worth ${total:,.0f}, led by {open_deals[0]['title']} in {open_deals[0]['stage']}.")
    parts.append(
        (f"Champion / decision-maker coverage: {', '.join(c.full_name for c in champions)}." if champions else "No Champion or Decision Maker mapped yet: a key risk.")
        + (f" Watch blocker {blockers[0].full_name}." if blockers else "")
    )
    if activities:
        parts.append(f"Last touch {days_between(activities[0].occurred_at)} day(s) ago ({activities[0].sentiment}).")
    return " ".join(parts)
