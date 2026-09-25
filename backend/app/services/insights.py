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
    """Activity results from hybrid (vector + keyword) retrieval."""
    from app.services.search import hybrid_search

    return [r for r in await hybrid_search(db, query, limit, None, account_id) if r["entity"] == "activity"]


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

    if stage.is_closed_won:
        from app.services.success import provision_onboarding, renew_contract_from_deal

        deal.account.health_score = 100
        deal.account.lifecycle_stage = "customer"
        await renew_contract_from_deal(db, deal)
        await provision_onboarding(db, deal)
    elif stage.is_closed_lost:
        reason = (deal.loss_reason or "other").replace("_", " ")
        comp = deal.loss_competitor or ", ".join(signals["competitors"]) or "none recorded"
        risks = " ".join(signals["risks"][:2]) or "No explicit risks were logged."
        postmortem = (
            f"Loss post-mortem for {deal.title} ({deal.account.name}): lost on {reason}. Amount {deal.currency} {float(deal.amount):,.0f}. "
            f"Competitors: {comp}. Rep debrief: {deal.loss_debrief or 'n/a'} Signals before loss: {risks}"
        )
        memo = Activity(account_id=deal.account_id, deal_id=deal.id, user_id=user_id, activity_type="note", summary=postmortem,
                        sentiment="negative", source="system")
        memo.embedding = await embeddings.embed(postmortem)
        db.add(memo)
        insights["postmortem"] = postmortem
        if deal.deal_type == "renewal":
            deal.account.lifecycle_stage = "churned"
    elif stage.name in ("Discovery", "Lead", "Registered", "Renewal Identified"):
        insights["pain_points"] = signals["pain_points"] or insights.get("pain_points", [])
    elif stage.name in ("Pain Fit", "Qualified", "Customer Review"):
        insights["competitors"] = signals["competitors"] or insights.get("competitors", [])
        insights["pain_points"] = insights.get("pain_points") or signals["pain_points"]
    elif stage.name in ("Solution Demo", "Demo Completed", "Joint Demo"):
        insights["recap_email"] = await draft_email(db, deal, purpose="demo recap")
        for title in ("Send demo recap email with agreed evaluation criteria", "Confirm technical validation owner and timeline"):
            db.add(Task(title=title, due_date=date.today() + timedelta(days=2), account_id=deal.account_id, deal_id=deal.id,
                        owner_id=deal.owner_id, assignee_id=deal.owner_id, source="ai"))
    elif stage.name in ("Proposal/InfoSec", "Proposal Sent", "Negotiation"):
        days_open = days_between(deal.created_at, now)
        insights["velocity"] = {"days_in_pipeline": days_open, "benchmark_days": 14, "status": "stagnating" if days_open > 14 else "on_pace"}
        if days_open > 14:
            db.add(Task(title="Deal is behind 14-day velocity benchmark: agree a mutual close plan", due_date=date.today() + timedelta(days=1),
                        account_id=deal.account_id, deal_id=deal.id, owner_id=deal.owner_id, assignee_id=deal.owner_id, source="ai", priority="high"))

    insights["last_trigger"] = {"stage": stage.name, "at": now.isoformat()}
    deal.ai_insights = insights
    await db.commit()


# ---- next best actions & briefing -------------------------------------------------
CADENCE_DAYS = {1: 10, 2: 7, 3: 5, 4: 3, 5: 3}  # stage order -> ideal days between touches
_ROLE_PROMPTS = {
    "Champion": ("Recruit an internal champion", "Ask your most engaged contact: \"What would make this a win for you personally, and who else should see it?\""),
    "Economic Buyer": ("Identify the economic buyer", "\"Who owns the budget line for this, and what do they need to see to approve it?\""),
    "Decision Maker": ("Map the final decision maker", "\"Besides you, who signs off on the final decision, and how have similar purchases been approved?\""),
}
_STAGE_MESSAGES = {
    1: "Share a short case study that mirrors their pain and propose a 30-minute discovery follow-up.",
    2: "Recap the quantified pain and ask for a meeting that includes the economic buyer.",
    3: "Send the demo recap with the agreed evaluation criteria and propose next validation steps.",
    4: "Confirm the mutual close plan: legal, security review and signature dates.",
    5: "Confirm commercial terms and the signature path.",
}


def next_best_actions(deal: dict, roles: list[str] | None = None, stage_order: int = 1) -> list[dict]:
    """Explainable next steps: risk drivers, follow-up cadence, missing buying roles, messaging."""
    f = deal.get("risk_factors") or {}
    roles = set(roles or [])
    actions: list[dict] = []
    needed = ["Champion"] + (["Economic Buyer", "Decision Maker"] if stage_order >= 2 else [])
    missing = [r for r in needed if r not in roles]
    if "Champion" in missing and "Decision Maker" in roles:
        missing.remove("Champion")
    for r in missing:
        title, msg = _ROLE_PROMPTS[r]
        actions.append({"kind": "missing_role", "action": title, "why": f"No {r} mapped on the buying committee.", "impact": 40 if r == "Champion" else 25, "message": msg})
    days = f.get("days_since_activity")
    cadence = CADENCE_DAYS.get(stage_order, 7)
    if days is not None and days > cadence:
        actions.append({"kind": "cadence", "action": "Follow up today",
                        "why": f"{int(days)} days since the last touch; target cadence at this stage is every {cadence} days" + (" (+30 risk)." if f.get("stale") else "."),
                        "impact": 30 if f.get("stale") else 15, "message": _STAGE_MESSAGES.get(stage_order, _STAGE_MESSAGES[1])})
    if f.get("sentiment_drop"):
        actions.append({"kind": "sentiment", "action": "Address the latest objection directly", "why": "Most recent interaction was negative (+30 risk).",
                        "impact": 30, "message": "Acknowledge the concern in writing, propose a specific remedy and a date to review it together."})
    if (f.get("days_in_stage") or 0) > 21:
        actions.append({"kind": "stalled", "action": "Agree a mutual action plan to unstick the deal", "why": f"In {deal['stage']} for {int(f['days_in_stage'])} days (> 21).", "impact": 20,
                        "message": "Propose a dated mutual action plan listing each remaining step and owner on both sides."})
    if (deal.get("close_date_pushes") or 0) >= 2:
        actions.append({"kind": "slippage", "action": "Re-qualify the timeline", "why": f"Close date pushed {deal['close_date_pushes']} times.", "impact": 20,
                        "message": "Ask what has changed in their priorities and whether the compelling event still stands."})
    close = deal.get("target_close_date")
    if close and isinstance(close, date) and close < date.today():
        actions.append({"kind": "overdue", "action": "Update the target close date", "why": f"Close date {close.isoformat()} has passed.", "impact": 10, "message": None})
    if (deal.get("account") or {}).get("credit_hold"):
        actions.append({"kind": "finance", "action": "Coordinate with finance before quoting", "why": "Account is on ERP credit hold; quotes will route to finance approval.", "impact": 10, "message": None})
    return sorted(actions, key=lambda a: -a["impact"])


async def scan_pipeline(db: AsyncSession) -> dict:
    """Deal risk & slippage copilot: open/resolve alerts for every open deal."""
    from app.models import Account, Contact, DealAlert, PipelineStage
    from app.services.notify import notify

    today = date.today()
    deals = (
        await db.execute(select(Deal).join(PipelineStage, Deal.stage_id == PipelineStage.id)
                         .where(PipelineStage.is_closed_won.is_(False), PipelineStage.is_closed_lost.is_(False)))
    ).scalars().unique().all()
    open_alerts = {(a.deal_id, a.kind): a for a in (await db.execute(select(DealAlert).where(DealAlert.resolved_at.is_(None)))).scalars().unique().all()}
    stats = {"opened": 0, "resolved": 0, "deals_scanned": len(deals)}
    now = datetime.now(timezone.utc)
    seen = set()
    for d in deals:
        f = d.risk_factors or {}
        roles = set((await db.execute(select(Contact.buying_role).where(Contact.account_id == d.account_id, Contact.status == "active"))).scalars().all())
        drift = ((d.account.custom_metadata or {}).get("health_breakdown") or {}).get("sentiment_drift", 0)
        checks = []
        if f.get("days_since_activity") is None or f.get("days_since_activity", 0) > 14:
            checks.append(("stagnant", "high" if (f.get("days_since_activity") or 99) > 30 else "medium",
                           f"No touchpoint for {int(f.get('days_since_activity') or 0)} days (threshold 14).", {"days": f.get("days_since_activity")}))
        if d.close_date_pushes:
            checks.append(("close_date_pushed", "high" if d.close_date_pushes >= 2 else "medium",
                           f"Close date pushed {d.close_date_pushes}x (originally {d.original_close_date}, now {d.target_close_date}).",
                           {"pushes": d.close_date_pushes, "original": str(d.original_close_date), "current": str(d.target_close_date)}))
        if drift <= -20:
            checks.append(("sentiment_drift", "high" if drift <= -40 else "medium", f"Sentiment trending down ({drift} points vs previous notes).", {"drift": drift}))
        missing = [r for r in ("Champion", "Economic Buyer") if r not in roles and not (r == "Champion" and "Decision Maker" in roles)]
        if d.stage.stage_order >= 2 and missing:
            checks.append(("missing_roles", "medium", f"Missing buying roles: {', '.join(missing)}.", {"missing": missing}))
        if d.target_close_date and d.target_close_date < today:
            checks.append(("overdue_close", "medium", f"Target close {d.target_close_date} has passed.", {}))
        for kind, severity, message, details in checks:
            seen.add((d.id, kind))
            alert = open_alerts.get((d.id, kind))
            if alert:
                alert.severity, alert.message, alert.details = severity, message, details
                continue
            db.add(DealAlert(deal_id=d.id, kind=kind, severity=severity, message=message, details=details))
            stats["opened"] += 1
            if severity == "high":
                notify(db, [d.owner_id], "risk", f"{d.title}: {message}", None, f"/deals/{d.id}")
    for key, alert in open_alerts.items():
        if key not in seen:
            alert.resolved_at = now
            stats["resolved"] += 1
    await db.commit()
    return stats


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
        matches = [m for m in matches if (m["similarity"] or 0) >= 0.35 or "keyword" in m.get("matched_by", [])]
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


async def reindex(db: AsyncSession) -> dict:
    """Embed activities missing vectors (e.g. after erasure redaction) and refresh account-record vectors."""
    from app.models import Account
    from app.services.search import account_document

    acts = (await db.execute(select(Activity).where(Activity.embedding.is_(None), Activity.activity_type != "system").limit(2000))).scalars().unique().all()
    for a in acts:
        a.embedding = await embeddings.embed(f"{a.subject or ''}\n{a.summary}\n{a.raw_text or ''}")
    accounts = (await db.execute(select(Account))).scalars().unique().all()
    for acc in accounts:
        acc.embedding = await embeddings.embed(account_document(acc))
    await db.commit()
    return {"activities_embedded": len(acts), "accounts_embedded": len(accounts)}
