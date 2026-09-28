"""Email-to-case: support email becomes cases, and agents' public replies go back by email.

Mail reaches Cirra through the inbound webhook (``POST /api/v1/inbound/email``, for a mail provider's inbound
parse or a relay) or the support IMAP mailbox the ``support_mail`` job polls. Each message is logged once
(by Message-ID) and then:

* a reply to an existing case (its ``[CS-00012]`` tag in the subject, or In-Reply-To / References naming a
  message in the case's thread) is added to that case as the customer's reply; a pending case goes back to
  open, a resolved one reopens, and a closed one gets a new follow-up case;
* otherwise it opens a case on the sender's account: the contact with that email address, else the account
  whose domain matches (free-mail domains never match). The queue is the one whose support address the mail
  was sent to, else the default queue, and its routing picks the owner;
* mail that matches no account waits in the *Unmatched* tray for an agent to file or dismiss.

Automatic replies, mail from Cirra's own addresses and bursts from one sender (loops) are logged and ignored.
When system email is configured, new cases are acknowledged and public replies are emailed with the case tag
and threading headers so the customer's answer finds its way back.
"""
from __future__ import annotations

import email
import email.policy
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dialect import json_array_has
from app.models import Account, CaseComment, Contact, InboundEmail, SupportQueue, SupportTicket
from app.services import cases, mailer
from app.services.notify import notify

log = logging.getLogger(__name__)
CASE_REF = re.compile(r"\[(CS-\d{5,})\]")
MAX_PER_SENDER_PER_HOUR = 20
FREE_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "icloud.com", "me.com", "aol.com",
             "proton.me", "protonmail.com", "gmx.com", "gmx.de", "web.de", "yandex.com", "mail.com", "zoho.com"}


class InboundError(ValueError):
    pass


@dataclass
class Inbound:
    message_id: str
    from_email: str
    from_name: str | None
    to: list[str]
    subject: str
    text: str
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    automatic: bool = False


def _clean_body(text: str) -> str:
    text = re.split(r"\n(?:On .+wrote:|-----Original Message-----|From: .+\nSent: )", text or "", maxsplit=1)[0]
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:20000]


def parse_raw(raw: bytes) -> Inbound:
    """An RFC 822 message as an Inbound."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    name, addr = parseaddr(str(msg.get("From", "")))
    part = msg.get_body(preferencelist=("plain", "html"))
    body = part.get_content() if part is not None else ""
    if part is not None and part.get_content_type() == "text/html":
        body = re.sub(r"<[^>]+>", " ", body)
    auto = str(msg.get("Auto-Submitted", "no")).lower() not in ("", "no") or str(msg.get("Precedence", "")).lower() in ("bulk", "junk", "list", "auto_reply") \
        or bool(msg.get("X-Autoreply") or msg.get("X-Autorespond"))
    return Inbound(message_id=(str(msg.get("Message-ID") or "").strip() or f"<{hashlib.sha256(raw).hexdigest()}@inbound.cirra>")[:500],
                   from_email=addr.lower(), from_name=name or None,
                   to=[a.lower() for _, a in getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Delivered-To", [])) if a],
                   subject=str(msg.get("Subject") or "(no subject)"), text=body,
                   in_reply_to=(str(msg.get("In-Reply-To") or "").strip() or None), references=str(msg.get("References") or "").split(),
                   automatic=auto)


async def _own_addresses(db: AsyncSession) -> set[str]:
    own = {a.lower() for a in (await db.execute(select(SupportQueue.email_address).where(SupportQueue.email_address.is_not(None)))).scalars()}
    if settings.smtp_from:
        own.add(settings.smtp_from.lower())
    if settings.support_imap_user and "@" in settings.support_imap_user:
        own.add(settings.support_imap_user.lower())
    return own


async def _thread_case(db: AsyncSession, m: Inbound) -> SupportTicket | None:
    ref = CASE_REF.search(m.subject or "")
    if ref:
        c = (await db.execute(select(SupportTicket).where(SupportTicket.case_number == ref.group(1)))).scalar_one_or_none()
        if c is not None:
            return c
    ids = [i for i in [m.in_reply_to, *m.references] if i]
    if not ids:
        return None
    case_id = (await db.execute(select(CaseComment.case_id).where(CaseComment.message_id.in_(ids)).limit(1))).scalar() \
        or (await db.execute(select(InboundEmail.case_id).where(InboundEmail.message_id.in_(ids), InboundEmail.case_id.is_not(None)).limit(1))).scalar()
    return await db.get(SupportTicket, case_id) if case_id else None


async def _match_account(db: AsyncSession, address: str) -> tuple[Account | None, Contact | None]:
    contact = (await db.execute(select(Contact).where(func.lower(Contact.email) == address, Contact.status != "erased")
                                .order_by(Contact.status == "departed", Contact.created_at))).scalars().first()
    if contact is not None:
        return await db.get(Account, contact.account_id), contact
    domain = address.rsplit("@", 1)[-1]
    if not domain or domain in FREE_MAIL:
        return None, None
    acc = (await db.execute(select(Account).where(or_(func.lower(Account.domain) == domain, json_array_has(Account.alt_domains, domain)))
                            .order_by(Account.created_at))).scalars().first()
    return acc, None


async def _queue_for(db: AsyncSession, to: list[str]) -> SupportQueue | None:
    if to:
        q = (await db.execute(select(SupportQueue).where(func.lower(SupportQueue.email_address).in_(to)))).scalars().first()
        if q is not None:
            return q
    return await cases.default_queue(db)


async def _acknowledge(case: SupportTicket, m: Inbound) -> None:
    if not mailer.configured():
        return
    try:
        await mailer.send(m.from_email, f"[{case.case_number}] {case.subject}"[:300],
                          f"Hello{(' ' + m.from_name.split()[0]) if m.from_name else ''},\n\nThanks for getting in touch. We've opened case "
                          f"{case.case_number} and will reply soon. To add anything, just reply to this email and keep "
                          f"[{case.case_number}] in the subject.\n", in_reply_to=m.message_id)
    except mailer.MailError as e:
        log.warning("Acknowledgement for %s failed: %s", case.case_number, e)


async def open_case(db: AsyncSession, m: Inbound, account: Account, contact: Contact | None, queue: SupportQueue | None,
                    follow_up_of: SupportTicket | None = None) -> SupportTicket:
    description = _clean_body(m.text)
    if follow_up_of is not None:
        description = f"Follow-up to {follow_up_of.case_number}.\n\n{description}"
    return await cases.create(db, {
        "account_id": account.id, "contact_id": contact.id if contact else None, "subject": (m.subject or "(no subject)")[:300],
        "description": description, "severity": follow_up_of.severity if follow_up_of else "medium", "channel": "email",
        "queue_id": queue.id if queue else None, "supplied_email": m.from_email if contact is None else None,
        "supplied_name": (m.from_name or None) if contact is None else None}, actor=None)


async def ingest(db: AsyncSession, m: Inbound) -> InboundEmail:
    """File one inbound message (idempotent by Message-ID). The caller commits."""
    existing = (await db.execute(select(InboundEmail).where(InboundEmail.message_id == m.message_id))).scalar_one_or_none()
    if existing is not None:
        return existing
    if not m.from_email or "@" not in m.from_email:
        raise InboundError("The message has no sender address")
    rec = InboundEmail(message_id=m.message_id, from_email=m.from_email, from_name=(m.from_name or None) and m.from_name[:200],
                       to_email=(m.to[0] if m.to else None), subject=(m.subject or "")[:500], body=_clean_body(m.text), status="ignored",
                       received_at=datetime.now(timezone.utc))
    db.add(rec)
    if m.automatic:
        rec.detail = "Automatic reply or bulk mail"
        return rec
    if m.from_email in await _own_addresses(db):
        rec.detail = "Sent from a Cirra address (mail loop protection)"
        return rec
    recent = (await db.execute(select(func.count(InboundEmail.id)).where(func.lower(InboundEmail.from_email) == m.from_email,
                                                                         InboundEmail.received_at > datetime.now(timezone.utc) - timedelta(hours=1)))).scalar_one()
    if recent >= MAX_PER_SENDER_PER_HOUR:
        rec.detail = f"More than {MAX_PER_SENDER_PER_HOUR} messages from this sender in an hour"
        return rec

    case = await _thread_case(db, m)
    if case is not None and case.status != "closed":
        db.add(CaseComment(case_id=case.id, author_id=None, body=_clean_body(m.text) or "(empty message)", internal=False,
                           message_id=m.message_id, from_email=m.from_email))
        if case.status == "pending" or case.status == "resolved":
            await cases.update(db, case, {"status": "open"})  # the customer answered, or the issue is back
        else:
            case.updated_at = datetime.now(timezone.utc)
        notify(db, [case.owner_id] if case.owner_id else [], "case", f"Customer replied on {case.case_number}", _clean_body(m.text)[:200], f"/cases/{case.id}")
        rec.status, rec.case_id, rec.detail = "appended", case.id, f"Added to {case.case_number}"
        await db.flush()
        return rec
    if case is not None:  # closed: the conversation continues on a new case
        account = await db.get(Account, case.account_id)
        contact = await db.get(Contact, case.contact_id) if case.contact_id else None
        new = await open_case(db, m, account, contact, await db.get(SupportQueue, case.queue_id) if case.queue_id else await _queue_for(db, m.to), case)
        rec.status, rec.case_id, rec.detail = "case_created", new.id, f"{new.case_number} opened (follow-up to closed {case.case_number})"
        await db.flush()
        await _acknowledge(new, m)
        return rec

    account, contact = await _match_account(db, m.from_email)
    if account is None:
        rec.status, rec.detail = "unmatched", "No contact or account matches the sender"
        return rec
    new = await open_case(db, m, account, contact, await _queue_for(db, m.to))
    rec.status, rec.case_id, rec.detail = "case_created", new.id, f"{new.case_number} opened on {account.name}"
    await db.flush()
    await _acknowledge(new, m)
    return rec


async def file_unmatched(db: AsyncSession, rec: InboundEmail, account: Account, contact: Contact | None) -> SupportTicket:
    """An agent files an unmatched message under an account."""
    if rec.status != "unmatched":
        raise InboundError("Only unmatched messages can be filed")
    m = Inbound(message_id=rec.message_id, from_email=rec.from_email, from_name=rec.from_name, to=[rec.to_email] if rec.to_email else [],
                subject=rec.subject, text=rec.body)
    case = await open_case(db, m, account, contact, await _queue_for(db, m.to))
    rec.status, rec.case_id, rec.detail = "converted", case.id, f"Filed as {case.case_number} on {account.name}"
    await db.flush()
    return case


async def email_reply(db: AsyncSession, case: SupportTicket, comment: CaseComment) -> str:
    """Email a public reply to the customer when the case came in by email. Returns what happened, for the UI."""
    if comment.internal or case.channel != "email":
        return "not_email"
    contact = await db.get(Contact, case.contact_id) if case.contact_id else None
    to = (contact.email if contact and contact.status != "erased" else None) or case.supplied_email
    if not to:
        return "no_address"
    if not mailer.configured():
        return "not_configured"
    last = (await db.execute(select(CaseComment.message_id).where(CaseComment.case_id == case.id, CaseComment.message_id.is_not(None),
                                                                  CaseComment.id != comment.id).order_by(CaseComment.created_at.desc()).limit(1))).scalar() \
        or (await db.execute(select(InboundEmail.message_id).where(InboundEmail.case_id == case.id).order_by(InboundEmail.received_at).limit(1))).scalar()
    try:
        comment.message_id = await mailer.send(to, f"Re: [{case.case_number}] {case.subject}"[:300],
                                               f"{comment.body}\n\n--\nCase {case.case_number}. Reply to this email to respond.", in_reply_to=last)
    except mailer.MailError as e:
        log.warning("Emailing the reply on %s failed: %s", case.case_number, e)
        return "failed"
    comment.emailed = True
    return "sent"


# ---- support IMAP mailbox ------------------------------------------------------------------------------

def _fetch_support(since_uid: int, limit: int = 100) -> list[tuple[int, bytes]]:
    import imaplib

    client = imaplib.IMAP4_SSL(settings.support_imap_host, settings.support_imap_port)
    try:
        client.login(settings.support_imap_user, settings.support_imap_password or "")
        client.select("INBOX", readonly=True)
        typ, data = client.uid("search", None, f"UID {since_uid + 1}:*")
        out = []
        for uid in (data[0].split() if typ == "OK" and data and data[0] else [])[:limit]:
            if int(uid) <= since_uid:
                continue
            typ, msg = client.uid("fetch", uid, "(RFC822)")
            if typ == "OK" and msg and isinstance(msg[0], tuple):
                out.append((int(uid), msg[0][1]))
        return out
    finally:
        try:
            client.logout()
        except Exception:
            pass


async def poll_support_mailbox(db: AsyncSession) -> dict:
    """Fetch new mail from the support mailbox (SUPPORT_IMAP_*), oldest first, committing after each message."""
    import asyncio

    from app.services import app_settings

    if not settings.support_imap_host:
        return {"status": "not_configured"}
    state = await app_settings.get(db, "support_mail")
    since = int(state.get("last_uid", 0))
    messages = await asyncio.to_thread(_fetch_support, since)
    filed = 0
    for uid, raw in sorted(messages):
        try:
            await ingest(db, parse_raw(raw))
            filed += 1
        except InboundError as e:
            log.info("Support mail %s skipped: %s", uid, e)
        since = max(since, uid)
        await app_settings.put(db, "support_mail", {"last_uid": since})
        await db.commit()
    return {"status": "ok", "fetched": len(messages), "filed": filed}
