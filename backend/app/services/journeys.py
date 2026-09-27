"""Nurture journeys: a campaign's members move through emails and waits, with engagement-based branching.

A journey belongs to a campaign and is a list of steps:

* ``{"type": "email", "subject": ..., "body": ..., "send_if": "always"}``: send a tracked email. ``send_if``
  makes it conditional on the previous email of the journey: ``opened``, ``not_opened``, ``clicked`` or
  ``not_clicked`` (e.g. a follow-up only for people who didn't open, or a demo offer only for clickers).
  With no earlier email only ``always`` sends.
* ``{"type": "wait", "days": 3}``: pause before the next step.

Activating a journey enrolls every eligible member of its campaign, and members who join later are enrolled
on the next run. The ``journeys`` job (every five minutes) advances whoever is due. A person leaves the
journey when they unsubscribe, bounce, opt out, are erased, or their lead converts or is disqualified;
people who can't be emailed at a step (e.g. no consent) leave with that reason. Emails go out through the
journey sender's mailbox and are tracked like campaign emails.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Campaign, CampaignMember, EmailSend, Journey, JourneyEnrollment, Lead, User

log = logging.getLogger(__name__)
SEND_IF = ("always", "opened", "not_opened", "clicked", "not_clicked")
MAX_STEPS = 20
MAX_WAIT_DAYS = 180
ENROLLABLE = ("targeted", "sent", "responded", "registered", "attended")
STATUSES = ("draft", "active", "paused", "archived")


class JourneyError(ValueError):
    pass


def validate_steps(steps: list[dict]) -> list[dict]:
    if not steps:
        raise JourneyError("Add at least one step")
    if len(steps) > MAX_STEPS:
        raise JourneyError(f"At most {MAX_STEPS} steps")
    out = []
    for i, s in enumerate(steps, 1):
        t = (s or {}).get("type")
        if t == "wait":
            try:
                days = float(s.get("days"))
            except (TypeError, ValueError):
                raise JourneyError(f"Step {i}: how many days to wait?") from None
            if not 0 < days <= MAX_WAIT_DAYS:
                raise JourneyError(f"Step {i}: wait between a fraction of a day and {MAX_WAIT_DAYS} days")
            out.append({"type": "wait", "days": days})
        elif t == "email":
            subject, body = str(s.get("subject") or "").strip(), str(s.get("body") or "").strip()
            if not subject or not body:
                raise JourneyError(f"Step {i}: an email needs a subject and a body")
            if len(subject) > 200 or len(body) > 20000:
                raise JourneyError(f"Step {i}: subject up to 200 and body up to 20,000 characters")
            cond = s.get("send_if") or "always"
            if cond not in SEND_IF:
                raise JourneyError(f"Step {i}: send_if must be one of {', '.join(SEND_IF)}")
            out.append({"type": "email", "subject": subject, "body": body, "send_if": cond})
        else:
            raise JourneyError(f"Step {i}: choose an email or a wait")
    if not any(s["type"] == "email" for s in out):
        raise JourneyError("A journey needs at least one email")
    return out


async def enroll(db: AsyncSession, journey: Journey, now: datetime | None = None) -> int:
    """Enroll the campaign's eligible members who aren't in this journey yet."""
    now = now or datetime.now(timezone.utc)
    already = select(JourneyEnrollment.member_id).where(JourneyEnrollment.journey_id == journey.id)
    ids = (await db.execute(select(CampaignMember.id).where(CampaignMember.campaign_id == journey.campaign_id,
                                                            CampaignMember.status.in_(ENROLLABLE), CampaignMember.id.not_in(already)))).scalars().all()
    for mid in ids:
        db.add(JourneyEnrollment(journey_id=journey.id, member_id=mid, step=0, next_at=now, status="active"))
    await db.flush()
    return len(ids)


async def _exit_reason(db: AsyncSession, member: CampaignMember | None) -> str | None:
    if member is None:
        return "No longer a campaign member"
    if member.status in ("unsubscribed", "bounced"):
        return "Unsubscribed" if member.status == "unsubscribed" else "Email bounced"
    if member.lead_id:
        lead = await db.get(Lead, member.lead_id)
        if lead is None:
            return "Lead deleted"
        if lead.status in ("converted", "disqualified"):
            return f"Lead {lead.status}"
    return None


def _condition_met(cond: str, last: EmailSend | None) -> bool:
    if cond == "always":
        return True
    if last is None:
        return False
    return {"opened": last.opened_at is not None, "not_opened": last.opened_at is None,
            "clicked": last.clicked_at is not None, "not_clicked": last.clicked_at is None}[cond]


async def advance(db: AsyncSession, journey: Journey, campaign: Campaign, sender: User, e: JourneyEnrollment, now: datetime) -> None:
    """Run one enrollment's due steps until a wait, the end, or an exit."""
    from app.services import campaigns, tracking

    member = await db.get(CampaignMember, e.member_id)
    for _ in range(MAX_STEPS + 1):
        reason = await _exit_reason(db, member)
        if reason:
            e.status, e.exit_reason, e.next_at = "exited", reason, None
            return
        if e.step >= len(journey.steps):
            e.status, e.next_at = "completed", None
            return
        step = journey.steps[e.step]
        if step["type"] == "wait":
            e.step += 1
            e.next_at = now + timedelta(days=float(step["days"]))
            return
        last = (await db.execute(select(EmailSend).where(EmailSend.enrollment_id == e.id).order_by(EmailSend.sent_at.desc()).limit(1))).scalar_one_or_none()
        if _condition_met(step.get("send_if", "always"), last):
            who = await campaigns.person_for(db, member)
            if who["blocked"]:
                e.status, e.exit_reason, e.next_at = "exited", f"Can't email: {who['blocked']}"[:80], None
                return
            subject = campaigns.render_subject(step["subject"], who["person"])
            body = campaigns.render(step["body"], who["person"], member.token)
            await tracking.deliver_tracked(db, campaign=campaign, member=member, person=who["person"], sender=sender, subject=subject, body=body,
                                           journey_id=journey.id, step_index=e.step, enrollment_id=e.id)
            if member.status == "targeted":
                member.status, member.sent_at = "sent", now
        e.step += 1
        e.next_at = now
    e.updated_at = now


async def run(db: AsyncSession, now: datetime | None = None, limit: int = 500) -> dict:
    """Enroll new members and advance due enrollments of every active journey. Commits per person, so one
    failure never re-sends to people already processed."""
    now = now or datetime.now(timezone.utc)
    done = {"enrolled": 0, "advanced": 0, "failed": 0}
    for journey in (await db.execute(select(Journey).where(Journey.status == "active"))).scalars().all():
        campaign = await db.get(Campaign, journey.campaign_id)
        sender = await db.get(User, journey.sender_id or journey.created_by) if (journey.sender_id or journey.created_by) else None
        if campaign is None or sender is None or campaign.status in ("completed", "aborted"):
            continue
        done["enrolled"] += await enroll(db, journey, now)
        await db.commit()
        due = (await db.execute(select(JourneyEnrollment).where(JourneyEnrollment.journey_id == journey.id, JourneyEnrollment.status == "active",
                                                                JourneyEnrollment.next_at <= now)
                                .order_by(JourneyEnrollment.next_at).limit(limit))).scalars().all()
        for e in due:
            eid = e.id
            try:
                await advance(db, journey, campaign, sender, e, now)
                await db.commit()
                done["advanced"] += 1
            except Exception:  # e.g. the sender's SMTP is down: retry this person next run
                log.exception("Journey %s: enrollment %s failed", journey.name, eid)
                await db.rollback()
                done["failed"] += 1
    return done


async def stats(db: AsyncSession, journey: Journey) -> dict:
    """Enrollments by status, where active people are, and email results per step."""
    by_status = dict((await db.execute(select(JourneyEnrollment.status, func.count()).where(JourneyEnrollment.journey_id == journey.id)
                                       .group_by(JourneyEnrollment.status))).all())
    at_step = dict((await db.execute(select(JourneyEnrollment.step, func.count()).where(JourneyEnrollment.journey_id == journey.id,
                                                                                        JourneyEnrollment.status == "active")
                                     .group_by(JourneyEnrollment.step))).all())
    per = {r.step_index: r for r in (await db.execute(select(EmailSend.step_index, func.count(EmailSend.id).label("sent"),
                                                              func.count(EmailSend.opened_at).label("opened"), func.count(EmailSend.clicked_at).label("clicked"))
                                                       .where(EmailSend.journey_id == journey.id).group_by(EmailSend.step_index))).all()}
    exits = dict((await db.execute(select(JourneyEnrollment.exit_reason, func.count()).where(JourneyEnrollment.journey_id == journey.id,
                                                                                             JourneyEnrollment.status == "exited")
                                   .group_by(JourneyEnrollment.exit_reason))).all())
    steps = []
    for i, s in enumerate(journey.steps or []):
        row = per.get(i)
        sent = row.sent if row else 0
        steps.append({"index": i, "type": s["type"], "waiting_here": at_step.get(i, 0),
                      **({"sent": sent, "opened": row.opened if row else 0, "clicked": row.clicked if row else 0,
                          "open_rate": round(row.opened / sent * 100, 1) if sent else None,
                          "click_rate": round(row.clicked / sent * 100, 1) if sent else None} if s["type"] == "email" else {})})
    return {"enrolled": sum(by_status.values()), "by_status": {k: by_status.get(k, 0) for k in ("active", "completed", "exited")},
            "exit_reasons": exits, "steps": steps}
