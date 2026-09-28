"""Marketing campaigns: members, capture attribution, consent-checked email, unsubscribe and ROI.

Membership
    Leads and contacts join by hand, from a report-builder filter, or automatically when a lead is
    captured with the campaign's code or name as its campaign / utm_campaign.
Responses
    responded / registered / attended count as responses. A lead member's response is logged as an
    engagement event, so it feeds the lead score.
Attribution (all USD)
    Sourced     deals created from member leads (the conversion carries lead_id).
    Influenced  sourced deals plus deals opened on a member contact's account after the contact joined.
    ROI         (won influenced revenue - actual cost) / actual cost.
Email
    Goes to members still 'targeted' who have an address and allow email: contacts through the privacy
    rules (opt-outs, GDPR basis), leads unless they denied consent or are GDPR without a grant. Every
    message carries a personal unsubscribe link that opts the person out everywhere.
"""
from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rbac import Principal
from app.models import Account, Campaign, CampaignMember, Contact, Deal, EmailSend, Lead, PipelineStage, User
from app.services import fx, privacy, reporting
from app.services.notify import emit

log = logging.getLogger(__name__)

TYPES = ("email", "webinar", "event", "trade_show", "paid_ads", "content", "partner", "other")
STATUSES = ("planned", "active", "completed", "aborted")
MEMBER_STATUSES = ("targeted", "sent", "responded", "registered", "attended", "unsubscribed", "bounced")
RESPONSES = ("responded", "registered", "attended")
_EVENT_FOR = {"registered": "webinar_registered", "attended": "webinar_attended", "responded": "form_submit"}
MAX_FILTER_ADD = 5000


class CampaignError(ValueError):
    pass


def slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    if not s:
        raise CampaignError("The campaign code needs letters or digits")
    return s[:80]


def _token() -> str:
    return secrets.token_urlsafe(24)


# ---- membership ------------------------------------------------------------------------------------

async def add_members(db: AsyncSession, campaign: Campaign, *, lead_ids=(), contact_ids=(), source: str = "manual",
                      status: str = "targeted") -> dict:
    lead_ids, contact_ids = list(dict.fromkeys(lead_ids)), list(dict.fromkeys(contact_ids))
    have_l = set((await db.execute(select(CampaignMember.lead_id).where(CampaignMember.campaign_id == campaign.id,
                                                                        CampaignMember.lead_id.in_(lead_ids)))).scalars()) if lead_ids else set()
    have_c = set((await db.execute(select(CampaignMember.contact_id).where(CampaignMember.campaign_id == campaign.id,
                                                                           CampaignMember.contact_id.in_(contact_ids)))).scalars()) if contact_ids else set()
    real_l = set((await db.execute(select(Lead.id).where(Lead.id.in_(lead_ids)))).scalars()) if lead_ids else set()
    real_c = set((await db.execute(select(Contact.id).where(Contact.id.in_(contact_ids), Contact.status != "erased")))
                 .scalars()) if contact_ids else set()
    now = datetime.now(timezone.utc)
    added = 0
    for lid in lead_ids:
        if lid in real_l and lid not in have_l:
            db.add(CampaignMember(campaign_id=campaign.id, lead_id=lid, source=source, status=status, token=_token(),
                                  responded_at=now if status in RESPONSES else None))
            added += 1
    for cid in contact_ids:
        if cid in real_c and cid not in have_c:
            db.add(CampaignMember(campaign_id=campaign.id, contact_id=cid, source=source, status=status, token=_token(),
                                  responded_at=now if status in RESPONSES else None))
            added += 1
    await db.flush()
    return {"added": added, "skipped": len(lead_ids) + len(contact_ids) - added}


async def add_from_filter(db: AsyncSession, p: Principal, campaign: Campaign, source: str, filters: list[dict]) -> dict:
    if source not in ("leads", "contacts"):
        raise CampaignError("Build the list from leads or contacts")
    if not p.can(source, "read") or p.is_own_scope(source):
        raise CampaignError(f"Building lists needs read access to every {source[:-1]}")
    await reporting.refresh_custom_fields(db)
    try:
        reporting.validate_filters(source, filters)
    except reporting.ReportError as e:
        raise CampaignError(str(e))
    ids = await reporting.match_ids(db, source, filters, limit=MAX_FILTER_ADD)
    out = await add_members(db, campaign, lead_ids=ids if source == "leads" else (), contact_ids=ids if source == "contacts" else (),
                            source="filter")
    return {**out, "matched": len(ids)}


async def find_by_key(db: AsyncSession, key: str | None) -> Campaign | None:
    """Match a lead's campaign text to a campaign by code or name (case-insensitive)."""
    k = (key or "").strip().lower()
    if not k:
        return None
    codes = {k}
    try:
        codes.add(slug(k))
    except CampaignError:
        pass
    return (await db.execute(select(Campaign).where(or_(Campaign.code.in_(codes), func.lower(Campaign.name) == k))
                             .order_by(Campaign.created_at).limit(1))).scalar_one_or_none()


async def attach_capture(db: AsyncSession, lead: Lead, key: str | None = None) -> Campaign | None:
    """A captured lead joins the campaign named by this capture (``key``), falling back to the lead's first-touch
    campaign. A returning lead keeps its first-touch campaign but is still credited to the new one."""
    campaign = await find_by_key(db, key or lead.campaign)
    if campaign is None:
        return None
    await add_members(db, campaign, lead_ids=[lead.id], source="capture", status="responded")
    return campaign


async def backfill(db: AsyncSession, campaign: Campaign) -> int:
    """Existing leads whose campaign text names this campaign (after creating it) become responders."""
    keys = {campaign.code.lower(), campaign.name.lower()}
    ids = list((await db.execute(select(Lead.id).where(func.lower(Lead.campaign).in_(keys)))).scalars())
    return (await add_members(db, campaign, lead_ids=ids, source="capture", status="responded"))["added"] if ids else 0


async def set_status(db: AsyncSession, campaign: Campaign, member: CampaignMember, status: str) -> CampaignMember:
    if status not in MEMBER_STATUSES:
        raise CampaignError(f"Status must be one of {', '.join(MEMBER_STATUSES)}")
    if member.status == "unsubscribed" and status != "unsubscribed":
        raise CampaignError("This person unsubscribed; they can only re-join by opting in again")
    first_response = status in RESPONSES and member.responded_at is None
    member.status = status
    if first_response:
        member.responded_at = datetime.now(timezone.utc)
        if member.lead_id:
            from app.services import leads as lead_svc

            lead = await db.get(Lead, member.lead_id)
            if lead:
                await lead_svc.add_event(db, lead, _EVENT_FOR[status], campaign.name, source="campaign")
                await lead_svc.rescore(db, lead)
        emit(db, "campaign.member_responded", "campaign", campaign.id, {"campaign": campaign.name, "code": campaign.code,
                                                                       "status": status, "lead_id": str(member.lead_id) if member.lead_id else None,
                                                                       "contact_id": str(member.contact_id) if member.contact_id else None})
    await db.flush()
    return member


# ---- email -----------------------------------------------------------------------------------------

def unsubscribe_url(token: str) -> str:
    return f"{settings.public_web_url.rstrip('/')}/unsubscribe/{token}"


def render(template: str, person: dict, token: str | None) -> str:
    """Fill {{first_name}}, {{last_name}}, {{company}} and the unsubscribe link. Values are inserted literally
    (a function replacement), so backslashes in names can't act as regex escapes. ``token=None`` renders an
    inert preview link."""
    out = template or ""
    for key in ("first_name", "last_name", "company"):
        value = person.get(key) or ""
        out = re.sub(r"\{\{\s*" + key + r"\s*\}\}", lambda _m, v=value: v, out)
    url = unsubscribe_url(token) if token else f"{settings.public_web_url.rstrip('/')}/unsubscribe/<personal-link>"
    if re.search(r"\{\{\s*unsubscribe_url\s*\}\}", out):
        return re.sub(r"\{\{\s*unsubscribe_url\s*\}\}", lambda _m: url, out)
    return f"{out.rstrip()}\n\n--\nYou received this because of your interest in our events and content. Unsubscribe: {url}"


def render_subject(template: str, person: dict) -> str:
    """A subject line: placeholders filled, on one line, without the unsubscribe footer."""
    return " ".join(render(template, person, None).split("\n--\n")[0].split())[:200]


def _lead_blocked(lead: Lead) -> str | None:
    if lead.consent_email == "denied":
        return "Opted out"
    if lead.privacy_regime == "GDPR" and lead.consent_email != "granted":
        return "GDPR: no recorded consent"
    return None


async def person_for(db: AsyncSession, member: CampaignMember, lead: Lead | None = None, contact: Contact | None = None) -> dict:
    """Who a member is for email (name, company, address) and why they can't be emailed, if they can't."""
    lead = lead or (await db.get(Lead, member.lead_id) if member.lead_id else None)
    contact = contact or (await db.get(Contact, member.contact_id) if member.contact_id else None)
    if lead is not None:
        person = {"first_name": lead.first_name, "last_name": lead.last_name, "company": lead.company_name, "email": lead.email}
        blocked = (None if lead.email else "No email address") or _lead_blocked(lead)
    elif contact is not None:
        acct = await db.get(Account, contact.account_id)
        person = {"first_name": contact.first_name, "last_name": contact.last_name, "company": acct.name if acct else None, "email": contact.email}
        ok, why = privacy.can_contact(contact, "email")
        blocked = (None if contact.email else "No email address") or (None if ok else why)
    else:
        person, blocked = {}, "The person no longer exists"
    return {"member": member, "lead": lead, "contact": contact, "person": person, "blocked": blocked}


async def recipients(db: AsyncSession, campaign: Campaign) -> list[dict]:
    """Every 'targeted' member with whether they can be emailed, and why not."""
    rows = (await db.execute(select(CampaignMember, Lead, Contact)
                             .outerjoin(Lead, Lead.id == CampaignMember.lead_id).outerjoin(Contact, Contact.id == CampaignMember.contact_id)
                             .where(CampaignMember.campaign_id == campaign.id, CampaignMember.status == "targeted"))).unique().all()
    return [await person_for(db, m, lead, contact) for m, lead, contact in rows]


SEND_STALE_AFTER = timedelta(minutes=30)  # a send stuck this long (its worker died) may be started again


def queue_send(campaign: Campaign, user: User) -> None:
    """Mark the campaign for a background send (``run_send``); the request returns at once."""
    if not (campaign.email_subject and campaign.email_body):
        raise CampaignError("Write the email subject and body first")
    if campaign.status in ("completed", "aborted"):
        raise CampaignError("This campaign is closed")
    now = datetime.now(timezone.utc)
    if campaign.send_status in ("queued", "sending") and campaign.send_requested_at and now - campaign.send_requested_at < SEND_STALE_AFTER:
        raise CampaignError("This campaign's email is already being sent")
    campaign.send_status, campaign.send_result = "queued", None
    campaign.send_requested_by, campaign.send_requested_at = user.id, now


async def run_send(db: AsyncSession, campaign_id, user_id) -> None:
    """Background job: send the campaign email and record the outcome on the campaign."""
    campaign = await db.get(Campaign, campaign_id)
    user = await db.get(User, user_id)
    if campaign is None or user is None or campaign.send_status != "queued":
        return
    campaign.send_status = "sending"
    await db.commit()
    try:
        out = await send(db, campaign, user)
        campaign.send_status, campaign.send_result = "done", out
    except CampaignError as e:
        campaign.send_status, campaign.send_result = "failed", {"error": str(e)}
    except Exception:
        log.exception("Campaign send %s failed", campaign_id)
        campaign.send_status, campaign.send_result = "failed", {"error": "The send stopped unexpectedly; people already emailed are recorded. Send again to continue."}
    await db.commit()
    from app.services.notify import notify

    notify(db, [user.id], "campaign", f"{campaign.name}: " + (f"email sent to {campaign.send_result.get('sent', 0)}" if campaign.send_status == "done"
                                                              else "email send stopped"), campaign.send_result.get("error"), f"/campaigns/{campaign.id}")
    await db.commit()


async def send(db: AsyncSession, campaign: Campaign, sender: User) -> dict:
    """Email every eligible 'targeted' member. Each message is committed as soon as it is handed to SMTP, so a
    failure part-way never un-marks people who already received it (a retry won't email them twice). A refused
    recipient is marked bounced; any other mail error stops the run and reports how far it got."""
    import smtplib

    from app.models import Activity
    from app.services import tracking

    if not (campaign.email_subject and campaign.email_body):
        raise CampaignError("Write the email subject and body first")
    if campaign.status in ("completed", "aborted"):
        raise CampaignError("This campaign is closed")
    sent = delivered = 0
    skipped: dict[str, int] = {}
    now = datetime.now(timezone.utc)
    for r in await recipients(db, campaign):
        if r["blocked"]:
            skipped[r["blocked"]] = skipped.get(r["blocked"], 0) + 1
            continue
        m, person = r["member"], r["person"]
        subject = render_subject(campaign.email_subject, person)
        body = render(campaign.email_body, person, m.token)
        try:
            tracked = await tracking.deliver_tracked(db, campaign=campaign, member=m, person=person, sender=sender, subject=subject, body=body)
            message_id, ok = tracked.message_id, tracked.delivered
        except smtplib.SMTPRecipientsRefused:
            m.status = "bounced"
            await db.commit()
            skipped["Address refused by the mail server"] = skipped.get("Address refused by the mail server", 0) + 1
            continue
        except (smtplib.SMTPException, OSError) as e:
            campaign.last_sent_at = now if sent else campaign.last_sent_at
            await db.commit()
            raise CampaignError(f"Sending stopped after {sent} email(s): {e}. Sent members are recorded; send again to continue.")
        m.status, m.sent_at = "sent", now
        sent += 1
        delivered += ok
        if r["contact"] is not None:
            c = r["contact"]
            db.add(Activity(account_id=c.account_id, contact_id=c.id, user_id=sender.id, activity_type="email", direction="outbound",
                            subject=subject[:500], summary=f"Campaign email ({campaign.name}): {subject}", raw_text=body, sentiment="neutral",
                            external_id=message_id, thread_id=message_id, source="system"))
        await db.commit()  # this person is emailed: record it now
    campaign.last_sent_at = now
    if campaign.status == "planned":
        campaign.status = "active"
    await db.flush()
    return {"sent": sent, "delivered_via_smtp": delivered, "skipped": skipped}


async def unsubscribe(db: AsyncSession, token: str) -> dict:
    m = (await db.execute(select(CampaignMember).where(CampaignMember.token == token))).scalar_one_or_none()
    if m is None:
        raise CampaignError("This unsubscribe link isn't valid")
    campaign = await db.get(Campaign, m.campaign_id)
    m.status = "unsubscribed"
    if m.contact_id:
        contact = await db.get(Contact, m.contact_id)
        if contact:
            await privacy.record_consent(db, contact, opt_outs={"email": True}, source=f"unsubscribe:{campaign.code}")
    if m.lead_id:
        lead = await db.get(Lead, m.lead_id)
        if lead:
            lead.consent_email, lead.consent_at, lead.consent_source = "denied", datetime.now(timezone.utc), f"unsubscribe:{campaign.code}"
    await db.flush()
    return {"campaign": campaign.name}


# ---- metrics ---------------------------------------------------------------------------------------

async def _deal_rows(db: AsyncSession, stmt) -> list:
    return (await db.execute(stmt.add_columns(Deal.id, Deal.title, Deal.amount, Deal.currency, Deal.created_at,
                                              PipelineStage.is_closed_won, PipelineStage.is_closed_lost)
                             .join(PipelineStage, PipelineStage.id == Deal.stage_id))).all()


async def email_stats(db: AsyncSession, *where) -> dict:
    """Tracked email totals: sends, unique opens and clicks, and their rates (opens are indicative, see tracking.py)."""
    sent, opened, clicked = (await db.execute(select(func.count(EmailSend.id), func.count(EmailSend.opened_at), func.count(EmailSend.clicked_at))
                                              .where(*where))).one()
    return {"sent": sent, "opened": opened, "clicked": clicked, "open_rate": round(opened / sent * 100, 1) if sent else None,
            "click_rate": round(clicked / sent * 100, 1) if sent else None, "click_to_open": round(clicked / opened * 100, 1) if opened else None}


async def metrics(db: AsyncSession, campaign: Campaign, rates: dict | None = None, deals: bool = False) -> dict:
    rates = rates or await fx.rates(db)
    status_counts = dict((await db.execute(select(CampaignMember.status, func.count()).where(CampaignMember.campaign_id == campaign.id)
                                           .group_by(CampaignMember.status))).all())
    members = sum(status_counts.values())
    responses = sum(status_counts.get(s, 0) for s in RESPONSES)
    reached = members - status_counts.get("targeted", 0)
    lead_ids = list((await db.execute(select(CampaignMember.lead_id).where(CampaignMember.campaign_id == campaign.id,
                                                                           CampaignMember.lead_id.is_not(None)))).scalars())
    converted = (await db.execute(select(func.count()).select_from(Lead).where(Lead.id.in_(lead_ids), Lead.status == "converted"))).scalar() if lead_ids else 0
    sourced = await _deal_rows(db, select().select_from(Deal).where(Deal.lead_id.in_(lead_ids))) if lead_ids else []
    contact_rows = (await db.execute(select(Contact.account_id, CampaignMember.added_at).join(CampaignMember, CampaignMember.contact_id == Contact.id)
                                     .where(CampaignMember.campaign_id == campaign.id))).all()
    influenced = list(sourced)
    seen = {r.id for r in sourced}
    if contact_rows:
        joined = {}
        for acc, at in contact_rows:
            joined[acc] = min(at, joined.get(acc, at))
        for r in await _deal_rows(db, select().select_from(Deal).add_columns(Deal.account_id).where(Deal.account_id.in_(list(joined)))):
            if r.id not in seen and r.created_at >= joined[r.account_id]:
                influenced.append(r)
                seen.add(r.id)

    def total(rows, won=None):
        return round(sum(fx.to_usd(float(r.amount or 0), r.currency, rates) for r in rows
                         if won is None or (r.is_closed_won if won else not (r.is_closed_won or r.is_closed_lost))), 2)

    cost = float(campaign.actual_cost or 0)
    won = total(influenced, True)
    out = {
        "members": members, "by_status": {s: status_counts.get(s, 0) for s in MEMBER_STATUSES}, "responses": responses,
        "response_rate": round(responses / reached * 100, 1) if reached else None,
        "leads": len(lead_ids), "converted_leads": converted,
        "sourced_pipeline": total(sourced, False), "sourced_won": total(sourced, True), "sourced_deals": len(sourced),
        "influenced_pipeline": total(influenced, False), "influenced_won": won, "influenced_deals": len(influenced),
        "cost": cost, "budget": float(campaign.budget or 0), "budget_used_pct": round(cost / float(campaign.budget) * 100, 1) if campaign.budget else None,
        "cost_per_lead": round(cost / len(lead_ids), 2) if lead_ids and cost else None,
        "cost_per_response": round(cost / responses, 2) if responses and cost else None,
        "roi_pct": round((won - cost) / cost * 100, 1) if cost else None,
        "email": await email_stats(db, EmailSend.campaign_id == campaign.id),
    }
    if deals:
        src = {r.id for r in sourced}
        out["deals"] = [{"id": r.id, "title": r.title, "amount_usd": fx.to_usd(float(r.amount or 0), r.currency, rates),
                         "status": "Won" if r.is_closed_won else "Lost" if r.is_closed_lost else "Open",
                         "attribution": "sourced" if r.id in src else "influenced"} for r in influenced]
    return out


def campaign_out(c: Campaign, owner: str | None = None) -> dict:
    return {"id": c.id, "name": c.name, "code": c.code, "type": c.campaign_type, "status": c.status, "description": c.description,
            "owner_id": c.owner_id, "owner": owner, "start_date": c.start_date, "end_date": c.end_date,
            "budget": float(c.budget or 0), "actual_cost": float(c.actual_cost or 0), "expected_revenue": float(c.expected_revenue or 0),
            "email_subject": c.email_subject, "email_body": c.email_body, "last_sent_at": c.last_sent_at,
            "send_status": c.send_status, "send_result": c.send_result, "send_requested_at": c.send_requested_at,
            "created_at": c.created_at, "updated_at": c.updated_at}


# ---- demo content ----------------------------------------------------------------------------------

async def ensure_demo(db: AsyncSession) -> bool:
    """A Marketing user and campaigns tied to the seeded leads' campaign names. Idempotent."""
    from datetime import date, timedelta

    from app.core.security import hash_password

    if (await db.execute(select(Campaign.id).limit(1))).first():
        return False
    nina = (await db.execute(select(User).where(User.email == "nina@cirra.demo"))).scalar_one_or_none()
    if nina is None:
        marcus = (await db.execute(select(User).where(User.email == "marcus@cirra.demo"))).scalar_one_or_none()
        nina = User(email="nina@cirra.demo", full_name="Nina Okafor", role="marketing", password_hash=hash_password("cirra123"),
                    manager_id=marcus.id if marcus else None)
        db.add(nina)
        await db.flush()
    today = date.today()
    specs = [
        ("EMEA nurture", "emea-nurture", "email", "active", 40, 20, 12000, 9800, 250000,
         "Quarterly forecasting nurture for EMEA prospects.",
         "{{first_name}}, how {{company}} can forecast with confidence",
         "Hi {{first_name}},\n\nOur new e-book shows how distribution teams cut forecast error by a third. "
         "Would a 20-minute walkthrough for {{company}} be useful?\n\nBest,\nNina"),
        ("SaaStr 2026", "saastr-2026", "trade_show", "completed", 80, 76, 45000, 51200, 600000,
         "Booth and executive dinner at SaaStr Annual.", None, None),
        ("Website", "website", "content", "active", 365, 0, 0, 0, 0, "Always-on inbound: website forms and content.", None, None),
        ("Q4 pipeline webinar", "q4-pipeline-webinar", "webinar", "planned", -10, -10, 8000, 0, 150000,
         "Live webinar: building a quarter's pipeline in six weeks.", "Join us: pipeline in six weeks",
         "Hi {{first_name}},\n\nJoin our live session on building a quarter's pipeline in six weeks. Reserve a seat for the {{company}} team.\n\nNina"),
    ]
    for name, code, typ, status, start_ago, end_ago, budget, cost, expected, desc, subj, body in specs:
        c = Campaign(name=name, code=code, campaign_type=typ, status=status, description=desc, owner_id=nina.id,
                     start_date=today - timedelta(days=start_ago), end_date=today - timedelta(days=end_ago) if typ != "content" else None,
                     budget=budget, actual_cost=cost, expected_revenue=expected, email_subject=subj, email_body=body)
        db.add(c)
        await db.flush()
        await backfill(db, c)
    webinar = (await db.execute(select(Campaign).where(Campaign.code == "q4-pipeline-webinar"))).scalar_one()
    contacts = list((await db.execute(select(Contact.id).where(Contact.status == "active", Contact.email.is_not(None))
                                      .order_by(Contact.created_at).limit(12))).scalars())
    await add_members(db, webinar, contact_ids=contacts, source="filter")
    return True
