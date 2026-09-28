"""Marketing email tracking: open pixels, click-through links and the engagement they feed.

Every tracked email is an ``EmailSend`` with an unguessable token. Its HTML part carries a 1x1 pixel
(``/api/v1/t/o/<token>.gif``) and every link, in HTML and plain text alike, goes through
``/api/v1/t/c/<token>?u=<url>&s=<signature>``. The signature is an HMAC of token and URL, so the redirect
only ever goes to links Cirra itself put in the email (never an open redirect). The unsubscribe link is left
untracked.

First open and first click per email are recorded on the send, logged as events, and become lead
engagement (``email_open`` / ``email_click`` points in lead scoring) for leads and contacts alike. A click
also marks the campaign member as having responded. Opens are a signal, not proof: mail clients that
pre-load images can report opens nobody made.
"""
from __future__ import annotations

import hashlib
import hmac
import html as html_lib
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tenancy
from app.core.config import settings
from app.models import Campaign, CampaignMember, Contact, EmailEvent, EmailSend, Lead

URL = re.compile(r"https?://[^\s<>\"')\]]+")
PIXEL = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")


def new_token() -> str:
    return secrets.token_urlsafe(24)


def _key() -> bytes:
    return hashlib.sha256(f"tracking:{settings.jwt_secret}".encode()).digest()


def sign(token: str, url: str) -> str:
    return hmac.new(_key(), f"{token}\n{url}".encode(), hashlib.sha256).hexdigest()[:32]


def verify(token: str, url: str, signature: str) -> bool:
    return hmac.compare_digest(sign(token, url), signature or "")


def _base() -> str:
    return tenancy.api_url() + "/api/v1/t"


def click_url(token: str, url: str) -> str:
    return f"{_base()}/c/{token}?u={quote(url, safe='')}&s={sign(token, url)}"


def pixel_url(token: str) -> str:
    return f"{_base()}/o/{token}.gif"


def _untracked(url: str) -> bool:
    return "/unsubscribe/" in url


def tracked_text(text: str, token: str) -> str:
    return URL.sub(lambda m: m.group(0) if _untracked(m.group(0)) else click_url(token, m.group(0)), text)


def tracked_html(text: str, token: str) -> str:
    """The plain-text email as simple HTML: escaped, links clickable (and tracked), plus the open pixel."""
    parts, last = [], 0
    for m in URL.finditer(text):
        parts.append(html_lib.escape(text[last:m.start()]))
        url = m.group(0)
        href = url if _untracked(url) else click_url(token, url)
        parts.append(f'<a href="{html_lib.escape(href, quote=True)}">{html_lib.escape(url)}</a>')
        last = m.end()
    parts.append(html_lib.escape(text[last:]))
    body = "".join(parts).replace("\n", "<br>\n")
    return (f'<!doctype html><html><body style="font-family:Arial,sans-serif;font-size:14px;line-height:1.5">{body}'
            f'<img src="{pixel_url(token)}" width="1" height="1" alt="" style="display:none"></body></html>')


async def _engagement(db: AsyncSession, send: EmailSend, kind: str) -> None:
    """First open / click of a send: lead engagement, and a click counts as a campaign response."""
    from app.services import campaigns, leads as lead_svc

    member = await db.get(CampaignMember, send.member_id) if send.member_id else None
    if member is None:
        return
    detail = send.subject[:200]
    if member.lead_id:
        lead = await db.get(Lead, member.lead_id)
        if lead is not None and lead.status != "converted":
            await lead_svc.add_event(db, lead, f"email_{kind}", detail, source="email")
            await lead_svc.rescore(db, lead)
    elif member.contact_id:
        await lead_svc.add_event(db, None, f"email_{kind}", detail, source="email", contact_id=member.contact_id)
    if kind == "click" and member.status == "sent":
        campaign = await db.get(Campaign, member.campaign_id)
        await campaigns.set_status(db, campaign, member, "responded")


async def _behavior(db: AsyncSession, send: EmailSend, kind: str, url: str | None = None) -> None:
    """Every open and click also goes to the behavioural event store (services/cdp.py) for segments and profiles."""
    from app.services import cdp

    member = await db.get(CampaignMember, send.member_id) if send.member_id else None
    if member is None:
        return
    contact = await db.get(Contact, member.contact_id) if member.contact_id else None
    lead = await db.get(Lead, member.lead_id) if member.lead_id else None
    cdp.record(db, f"email_{kind}", contact=contact, lead=lead, url=url, properties={"subject": send.subject[:200]}, source="email")


async def record_open(db: AsyncSession, token: str) -> None:
    send = (await db.execute(select(EmailSend).where(EmailSend.token == token))).scalar_one_or_none()
    if send is None:
        return
    first = send.opened_at is None
    send.open_count += 1
    send.opened_at = send.opened_at or datetime.now(timezone.utc)
    db.add(EmailEvent(send_id=send.id, kind="open"))
    await _behavior(db, send, "open")
    if first:
        await _engagement(db, send, "open")
    await db.flush()


async def record_click(db: AsyncSession, token: str, url: str, signature: str) -> str | None:
    """The URL to redirect to, or None when the link isn't one Cirra signed for this email."""
    if not url or not verify(token, url, signature) or not url.startswith(("http://", "https://")):
        return None
    send = (await db.execute(select(EmailSend).where(EmailSend.token == token))).scalar_one_or_none()
    if send is None:
        return None
    now = datetime.now(timezone.utc)
    first = send.clicked_at is None
    send.click_count += 1
    send.clicked_at = send.clicked_at or now
    if send.opened_at is None:  # a click proves the email was opened, even with images blocked
        send.opened_at, send.open_count = now, send.open_count + 1
        await _engagement(db, send, "open")
    db.add(EmailEvent(send_id=send.id, kind="click", url=url[:2000]))
    await _behavior(db, send, "click", url)
    if first:
        await _engagement(db, send, "click")
    await db.flush()
    return url


async def deliver_tracked(db: AsyncSession, *, campaign: Campaign, member: CampaignMember, person: dict, sender, subject: str, body: str,
                          journey_id=None, step_index: int | None = None, enrollment_id=None) -> EmailSend:
    """Render, track and send one marketing email through the sender's mailbox, and record it."""
    from app.services import mail

    token = new_token()
    text = tracked_text(body, token)
    message_id, delivered = await mail.deliver(db, sender, person["email"], subject, text, html=tracked_html(body, token))
    send = EmailSend(token=token, campaign_id=campaign.id, journey_id=journey_id, step_index=step_index, member_id=member.id,
                     enrollment_id=enrollment_id, email=person["email"], subject=subject[:300], message_id=message_id, delivered=delivered,
                     sent_at=datetime.now(timezone.utc))
    db.add(send)
    await db.flush()
    return send
