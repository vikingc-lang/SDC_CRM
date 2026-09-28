"""Bi-directional email sync over IMAP/SMTP (pillar 5).

IMAP/SMTP works with any private mail server and with Google Workspace and
Microsoft 365 (both expose IMAP/SMTP with app passwords or OAuth app tokens).
Messages are matched to contacts by address, threaded by Message-ID /
In-Reply-To / References, de-duplicated by Message-ID and written to the
activity ledger with direction (inbound/outbound) so reply latency feeds the
Relationship Strength Index. Outbound mail respects channel opt-outs.
"""
from __future__ import annotations

import base64
import email
import email.policy
import hashlib
import imaplib
import re
import smtplib
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid, parsedate_to_datetime

from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import netguard
from app.core.config import settings
from app.models import Activity, Contact, MailboxConnection, User
from app.services.privacy import can_contact


def _fernet() -> Fernet:
    secret = settings.data_encryption_key or settings.jwt_secret
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def _body_text(msg) -> str:
    part = msg.get_body(preferencelist=("plain", "html")) if hasattr(msg, "get_body") else None
    text = part.get_content() if part is not None else (msg.get_payload(decode=True) or b"").decode(errors="replace")
    if part is not None and part.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
    # drop quoted replies to keep the ledger readable
    text = re.split(r"\n(?:On .+wrote:|-----Original Message-----|>)", text, maxsplit=1)[0]
    return re.sub(r"\s+\n", "\n", text).strip()[:8000]


async def ingest_message(db: AsyncSession, raw: bytes, mailbox_owner: User | None = None) -> Activity | None:
    """Parse one RFC 822 message into an email activity; None if unrelated to any contact or already stored."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    message_id = (msg.get("Message-ID") or "").strip() or f"<{hashlib.sha256(raw).hexdigest()}@cirra.local>"
    if (await db.execute(select(Activity.id).where(Activity.external_id == message_id))).first():
        return None
    sender = [a.lower() for _, a in getaddresses([msg.get("From", "")]) if a]
    recipients = [a.lower() for _, a in getaddresses(msg.get_all("To", []) + msg.get_all("Cc", [])) if a]
    addresses = set(sender + recipients)
    contacts = (await db.execute(select(Contact).where(func.lower(Contact.email).in_(addresses), Contact.status != "erased"))).scalars().all()
    if not contacts:
        return None
    by_email = {c.email.lower(): c for c in contacts}
    inbound = bool(sender) and sender[0] in by_email
    contact = by_email[sender[0]] if inbound else next(by_email[a] for a in recipients if a in by_email)
    refs = (msg.get("References") or "").split()
    thread = refs[0] if refs else (msg.get("In-Reply-To") or message_id).strip()
    try:
        occurred = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else datetime.now(timezone.utc)
    except (TypeError, ValueError):
        occurred = datetime.now(timezone.utc)
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    body = _body_text(msg)
    subject = str(msg.get("Subject") or "(no subject)")[:500]
    from app.services.ai_extractor import _sentiment

    act = Activity(account_id=contact.account_id, contact_id=contact.id, user_id=mailbox_owner.id if mailbox_owner else None,
                   activity_type="email", direction="inbound" if inbound else "outbound", subject=subject,
                   summary=f"{subject}: {body[:300]}".strip(), raw_text=body, sentiment=_sentiment(body), occurred_at=occurred,
                   external_id=message_id[:500], thread_id=thread[:500], source="email_sync")
    db.add(act)
    await db.flush()
    return act


IMAP_CLIENT = imaplib.IMAP4_SSL  # tests swap in a fake server
BATCH = 200  # messages per folder per sync; the rest follow on the next sync (oldest first, nothing skipped)
SENT_NAMES = ("Sent", "Sent Items", "Sent Messages", "Sent Mail", "[Gmail]/Sent Mail", "INBOX.Sent", "INBOX/Sent")
_LIST_LINE = re.compile(r'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')


def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _folders(client) -> list[str]:
    """INBOX plus the sent folder: the one flagged \\Sent (RFC 6154), else the first of the usual names."""
    names, sent = [], []
    typ, data = client.list()
    for raw in (data or []) if typ == "OK" else []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        m = _LIST_LINE.match(line)
        if not m:
            continue
        name = m.group("name").strip().strip('"')
        names.append(name)
        if "\\sent" in m.group("flags").lower():
            sent.append(name)
    if not sent:
        sent = [n for n in SENT_NAMES if n in names][:1]
    return ["INBOX", *[n for n in sent if n.upper() != "INBOX"]]


def _uidvalidity(client) -> int | None:
    typ, data = client.response("UIDVALIDITY")
    try:
        return int(data[0]) if data and data[0] else None
    except (TypeError, ValueError):
        return None


def _imap_fetch(conn: MailboxConnection, password: str, state: dict, limit: int = BATCH) -> tuple[list[tuple[str, int, bytes]], dict]:
    """New messages per folder since each folder's own UID cursor (UIDs are per folder, so one shared cursor
    skips mail). Returns the messages and each folder's current UIDVALIDITY; a changed UIDVALIDITY restarts that
    folder (ingest de-duplicates by Message-ID)."""
    netguard.check_mail_server(conn.imap_host, conn.imap_port or 993, "imap")  # at use too: DNS may have changed
    client = IMAP_CLIENT(conn.imap_host, conn.imap_port or 993)
    try:
        client.login(conn.username or conn.email_address, password)
        out, validity = [], {}
        for folder in _folders(client):
            if client.select(_quote(folder), readonly=True)[0] != "OK":
                continue
            uv = _uidvalidity(client)
            validity[folder] = uv
            prev = state.get(folder) or {}
            since = int(prev.get("last_uid") or 0) if prev.get("uidvalidity") in (None, uv) else 0
            typ, data = client.uid("search", None, f"UID {since + 1}:*")
            if typ != "OK":
                continue
            uids = sorted(int(u) for u in (data[0].split() if data and data[0] else []) if int(u) > since)
            for uid in uids[:limit]:
                typ, msg_data = client.uid("fetch", str(uid), "(RFC822)")
                if typ == "OK" and msg_data and isinstance(msg_data[0], tuple):
                    out.append((folder, uid, msg_data[0][1]))
        return out, validity
    finally:
        try:
            client.logout()
        except Exception:
            pass


async def sync_mailbox(db: AsyncSession, conn: MailboxConnection) -> dict:
    import asyncio

    owner = await db.get(User, conn.user_id)
    state = {k: dict(v) for k, v in (conn.folder_state or {}).items()}
    if not state and conn.last_uid:  # before per-folder cursors: the old cursor was INBOX's
        state["INBOX"] = {"uidvalidity": None, "last_uid": conn.last_uid}
    try:
        messages, validity = await asyncio.to_thread(_imap_fetch, conn, decrypt_secret(conn.secret_encrypted or ""), state)
    except Exception as exc:
        conn.status, conn.last_error = "error", str(exc)[:500]
        await db.commit()
        return {"status": "error", "error": conn.last_error}
    for folder, uv in validity.items():
        prev = state.get(folder) or {}
        if prev.get("uidvalidity") not in (None, uv):
            prev = {}
        state[folder] = {"uidvalidity": uv, "last_uid": int(prev.get("last_uid") or 0)}
    created, by_folder = 0, {}
    for folder, uid, raw in sorted(messages, key=lambda m: (m[0], m[1])):
        if await ingest_message(db, raw, owner):
            created += 1
        state[folder]["last_uid"] = max(state[folder]["last_uid"], uid)
        by_folder[folder] = by_folder.get(folder, 0) + 1
    conn.folder_state = state
    conn.last_uid = max([conn.last_uid or 0, *(v["last_uid"] for k, v in state.items() if k == "INBOX")])
    conn.status, conn.last_error, conn.last_synced_at = "active", None, datetime.now(timezone.utc)
    await db.commit()
    return {"status": "ok", "fetched": len(messages), "logged": created, "folders": by_folder}


async def deliver(db: AsyncSession, user: User, to: str, subject: str, body: str, in_reply_to: str | None = None,
                  html: str | None = None) -> tuple[str, bool]:
    """Send through the user's connected mailbox (SMTP). Returns (Message-ID, delivered); without SMTP it's recorded only."""
    conn = (await db.execute(select(MailboxConnection).where(MailboxConnection.user_id == user.id, MailboxConnection.status != "disabled"))).scalars().first()
    msg = EmailMessage()
    msg["From"] = conn.email_address if conn else user.email
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=(msg["From"].split("@")[-1] if "@" in msg["From"] else "cirra.local"))
    if in_reply_to:
        msg["In-Reply-To"] = msg["References"] = in_reply_to
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    if not (conn and conn.smtp_host):
        return str(msg["Message-ID"]), False
    import asyncio

    def _send():
        netguard.check_mail_server(conn.smtp_host, conn.smtp_port or 587, "smtp")
        with smtplib.SMTP(conn.smtp_host, conn.smtp_port or 587, timeout=30) as s:
            s.starttls()
            s.login(conn.username or conn.email_address, decrypt_secret(conn.secret_encrypted or ""))
            s.send_message(msg)

    await asyncio.to_thread(_send)
    return str(msg["Message-ID"]), True


async def send_email(db: AsyncSession, user: User, contact: Contact, subject: str, body: str, deal_id=None, in_reply_to: str | None = None) -> Activity:
    allowed, why = can_contact(contact, "email")
    if not allowed:
        raise PermissionError(why)
    message_id, delivered = await deliver(db, user, contact.email, subject, body, in_reply_to)
    act = Activity(account_id=contact.account_id, contact_id=contact.id, deal_id=deal_id, user_id=user.id, activity_type="email",
                   direction="outbound", subject=subject[:500], summary=f"{subject}: {body[:300]}", raw_text=body, sentiment="neutral",
                   external_id=message_id, thread_id=in_reply_to or message_id,
                   source="email_sync" if delivered else "manual")
    db.add(act)
    await db.flush()
    return act


def new_token() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex
