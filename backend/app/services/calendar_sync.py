"""Two-way sync between a user's Google Calendar or Microsoft 365 calendar and their Cirra meetings.

Connecting: OAuth 2.0 authorization code with PKCE; the state, verifier and user are kept server-side
(``sso_login_states``); tokens are stored encrypted and refreshed when they expire.

Each sync (every ten minutes, or "Sync now"):

1. **Pull** changes since the provider's incremental cursor (Google ``syncToken``, Graph ``deltaLink``; the first
   run reads 30 days back to 180 days ahead):
   * a new event with an attendee who is a CRM contact becomes a meeting on that contact's account;
   * a changed event updates its meeting (subject, time, duration, agenda); if the meeting was also edited in
     Cirra since the last sync, the calendar wins and the conflict is counted;
   * a cancelled or deleted event marks its meeting ``cancelled``.
2. **Push** the user's upcoming Cirra meetings: new ones are created in the calendar (the contact invited as an
   attendee, no invitation mail sent), edited ones are updated with the event's etag (a concurrent calendar edit
   makes the update wait for the next pull), cancelled or deleted ones are removed from the calendar.

Echoes are avoided by remembering each event's etag and a hash of the meeting's synced fields at the last sync.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Activity, CalendarConnection, CalendarLink, Contact, SsoLoginState, User
from app.services.mail import decrypt_secret, encrypt_secret

log = logging.getLogger(__name__)
http_transport = None  # tests swap in an httpx transport
PAST_DAYS, FUTURE_DAYS = 30, 180
STATE_PREFIX = "cal:"


class CalendarError(ValueError):
    pass


class Conflict(Exception):
    """The remote event changed since we last saw it (etag mismatch)."""


class ResyncNeeded(Exception):
    """The incremental cursor expired; start over with a full read."""


@dataclass
class RemoteEvent:
    id: str
    etag: str | None
    cancelled: bool = False
    summary: str = ""
    description: str = ""
    start: datetime | None = None
    end: datetime | None = None
    attendees: list[str] = field(default_factory=list)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20, transport=http_transport)


def _dt(value: str | None, tz: str | None = None) -> datetime | None:
    if not value:
        return None
    if len(value) == 10:  # all-day date
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)  # Graph returns naive UTC when asked for UTC
    return d.astimezone(timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


# ---- providers ---------------------------------------------------------------------------------------------------

class Google:
    name, label = "google", "Google Calendar"

    @staticmethod
    def authorize_endpoint() -> str:
        return "https://accounts.google.com/o/oauth2/v2/auth"

    @staticmethod
    def token_endpoint() -> str:
        return "https://oauth2.googleapis.com/token"

    api = "https://www.googleapis.com/calendar/v3"
    scope = "openid email https://www.googleapis.com/auth/calendar.events"

    @staticmethod
    def configured() -> bool:
        return bool(settings.google_calendar_client_id and settings.google_calendar_client_secret)

    @staticmethod
    def client() -> tuple[str, str]:
        return settings.google_calendar_client_id or "", settings.google_calendar_client_secret or ""

    @classmethod
    def authorize_params(cls) -> dict:
        return {"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"}

    @classmethod
    async def account_email(cls, token: str, tokens: dict) -> str | None:
        idt = tokens.get("id_token")
        if idt:
            try:
                payload = idt.split(".")[1]
                return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("email")
            except (IndexError, ValueError):
                return None
        return None

    @classmethod
    def _event(cls, e: dict) -> RemoteEvent:
        start, end = e.get("start") or {}, e.get("end") or {}
        return RemoteEvent(id=e["id"], etag=e.get("etag"), cancelled=e.get("status") == "cancelled", summary=e.get("summary") or "",
                           description=e.get("description") or "", start=_dt(start.get("dateTime") or start.get("date")),
                           end=_dt(end.get("dateTime") or end.get("date")),
                           attendees=[a["email"].lower() for a in e.get("attendees") or [] if a.get("email")])

    @classmethod
    async def changes(cls, token: str, calendar: str, cursor: str | None) -> tuple[list[RemoteEvent], str | None]:
        url = f"{cls.api}/calendars/{calendar}/events"
        params = {"syncToken": cursor} if cursor else {"timeMin": _iso(datetime.now(timezone.utc) - timedelta(days=PAST_DAYS)),
                                                         "singleEvents": "true"}
        params.update({"showDeleted": "true", "maxResults": "250"})
        out, page = [], None
        async with _client() as c:
            while True:
                r = await c.get(url, params={**params, **({"pageToken": page} if page else {})}, headers={"Authorization": f"Bearer {token}"})
                if r.status_code == 410:
                    raise ResyncNeeded()
                r.raise_for_status()
                data = r.json()
                out += [cls._event(e) for e in data.get("items") or []]
                page = data.get("nextPageToken")
                if not page:
                    return out, data.get("nextSyncToken")

    @classmethod
    def _body(cls, m: dict) -> dict:
        return {"summary": m["summary"], "description": m["description"], "start": {"dateTime": _iso(m["start"])},
                "end": {"dateTime": _iso(m["end"])}, "attendees": [{"email": e} for e in m["attendees"]]}

    @classmethod
    async def create(cls, token: str, calendar: str, m: dict) -> tuple[str, str | None]:
        async with _client() as c:
            r = await c.post(f"{cls.api}/calendars/{calendar}/events", params={"sendUpdates": "none"}, json=cls._body(m),
                             headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return r.json()["id"], r.json().get("etag")

    @classmethod
    async def update(cls, token: str, calendar: str, remote_id: str, etag: str | None, m: dict) -> str | None:
        headers = {"Authorization": f"Bearer {token}", **({"If-Match": etag} if etag else {})}
        async with _client() as c:
            r = await c.patch(f"{cls.api}/calendars/{calendar}/events/{remote_id}", params={"sendUpdates": "none"}, json=cls._body(m), headers=headers)
        if r.status_code == 412:
            raise Conflict()
        r.raise_for_status()
        return r.json().get("etag")

    @classmethod
    async def delete(cls, token: str, calendar: str, remote_id: str) -> None:
        async with _client() as c:
            r = await c.delete(f"{cls.api}/calendars/{calendar}/events/{remote_id}", params={"sendUpdates": "none"},
                               headers={"Authorization": f"Bearer {token}"})
        if r.status_code not in (404, 410):
            r.raise_for_status()


class Microsoft:
    name, label = "microsoft", "Microsoft 365 / Outlook"
    api = "https://graph.microsoft.com/v1.0"
    scope = "offline_access openid email User.Read Calendars.ReadWrite"

    @staticmethod
    def configured() -> bool:
        return bool(settings.microsoft_calendar_client_id and settings.microsoft_calendar_client_secret)

    @staticmethod
    def client() -> tuple[str, str]:
        return settings.microsoft_calendar_client_id or "", settings.microsoft_calendar_client_secret or ""

    @classmethod
    def _base(cls) -> str:
        return f"https://login.microsoftonline.com/{settings.microsoft_calendar_tenant}/oauth2/v2.0"

    @classmethod
    def authorize_endpoint(cls) -> str:
        return f"{cls._base()}/authorize"

    @classmethod
    def token_endpoint(cls) -> str:
        return f"{cls._base()}/token"

    @classmethod
    def authorize_params(cls) -> dict:
        return {"response_mode": "query"}

    @classmethod
    async def account_email(cls, token: str, tokens: dict) -> str | None:
        async with _client() as c:
            r = await c.get(f"{cls.api}/me", headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            return None
        return r.json().get("mail") or r.json().get("userPrincipalName")

    @classmethod
    def _event(cls, e: dict) -> RemoteEvent:
        if "@removed" in e:
            return RemoteEvent(id=e["id"], etag=None, cancelled=True)
        return RemoteEvent(id=e["id"], etag=e.get("@odata.etag") or e.get("changeKey"), cancelled=bool(e.get("isCancelled")),
                           summary=e.get("subject") or "", description=(e.get("body") or {}).get("content") or e.get("bodyPreview") or "",
                           start=_dt((e.get("start") or {}).get("dateTime")), end=_dt((e.get("end") or {}).get("dateTime")),
                           attendees=[(a.get("emailAddress") or {}).get("address", "").lower() for a in e.get("attendees") or []
                                      if (a.get("emailAddress") or {}).get("address")])

    @classmethod
    async def changes(cls, token: str, calendar: str, cursor: str | None) -> tuple[list[RemoteEvent], str | None]:
        now = datetime.now(timezone.utc)
        url = cursor or (f"{cls.api}/me/calendarView/delta?" + urlencode({"startDateTime": _iso(now - timedelta(days=PAST_DAYS)),
                                                                         "endDateTime": _iso(now + timedelta(days=FUTURE_DAYS))}))
        headers = {"Authorization": f"Bearer {token}", "Prefer": 'outlook.timezone="UTC", odata.maxpagesize=100', "Accept": "application/json"}
        out = []
        async with _client() as c:
            while True:
                r = await c.get(url, headers=headers)
                if r.status_code == 410:
                    raise ResyncNeeded()
                r.raise_for_status()
                data = r.json()
                out += [cls._event(e) for e in data.get("value") or []]
                if data.get("@odata.nextLink"):
                    url = data["@odata.nextLink"]
                    continue
                return out, data.get("@odata.deltaLink")

    @classmethod
    def _body(cls, m: dict) -> dict:
        return {"subject": m["summary"], "body": {"contentType": "text", "content": m["description"]},
                "start": {"dateTime": _iso(m["start"])[:-1], "timeZone": "UTC"}, "end": {"dateTime": _iso(m["end"])[:-1], "timeZone": "UTC"},
                "attendees": [{"emailAddress": {"address": e}, "type": "required"} for e in m["attendees"]]}

    @classmethod
    async def create(cls, token: str, calendar: str, m: dict) -> tuple[str, str | None]:
        async with _client() as c:
            r = await c.post(f"{cls.api}/me/events", json=cls._body(m), headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return r.json()["id"], r.json().get("@odata.etag")

    @classmethod
    async def update(cls, token: str, calendar: str, remote_id: str, etag: str | None, m: dict) -> str | None:
        headers = {"Authorization": f"Bearer {token}", **({"If-Match": etag} if etag else {})}
        async with _client() as c:
            r = await c.patch(f"{cls.api}/me/events/{remote_id}", json=cls._body(m), headers=headers)
        if r.status_code == 412:
            raise Conflict()
        r.raise_for_status()
        return r.json().get("@odata.etag")

    @classmethod
    async def delete(cls, token: str, calendar: str, remote_id: str) -> None:
        async with _client() as c:
            r = await c.delete(f"{cls.api}/me/events/{remote_id}", headers={"Authorization": f"Bearer {token}"})
        if r.status_code not in (404, 410):
            r.raise_for_status()


PROVIDERS = {"google": Google, "microsoft": Microsoft}


# ---- OAuth ------------------------------------------------------------------------------------------------------

def redirect_uri() -> str:
    return f"{settings.public_api_url.rstrip('/')}/api/v1/calendar/oauth/callback"


async def start_connect(db: AsyncSession, user: User, provider: str) -> str:
    prov = PROVIDERS.get(provider)
    if prov is None or not prov.configured():
        raise CalendarError("That calendar provider isn't set up on this server")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    db.add(SsoLoginState(state=state, nonce=f"{STATE_PREFIX}{provider}:{user.id}"[:64], code_verifier=verifier, return_to="/settings"))
    await db.flush()
    client_id, _ = prov.client()
    return f"{prov.authorize_endpoint()}?" + urlencode({"client_id": client_id, "redirect_uri": redirect_uri(), "response_type": "code", "scope": prov.scope,
                                            "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
                                            **prov.authorize_params()})


async def _token_request(prov, data: dict) -> dict:
    client_id, secret = prov.client()
    async with _client() as c:
        r = await c.post(prov.token_endpoint(), data={"client_id": client_id, "client_secret": secret, **data},
                         headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise CalendarError(f"{prov.label} refused the sign-in (HTTP {r.status_code})")
    return r.json()


def _store(conn: CalendarConnection, tokens: dict, previous: dict | None = None) -> str:
    access = tokens["access_token"]
    kept = {"access_token": access, "refresh_token": tokens.get("refresh_token") or (previous or {}).get("refresh_token")}
    conn.token_encrypted = encrypt_secret(json.dumps(kept))
    conn.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(tokens.get("expires_in") or 3600) - 60)
    return access


async def finish_connect(db: AsyncSession, state: str, code: str) -> CalendarConnection:
    row = await db.get(SsoLoginState, state)
    if row is None or not row.nonce.startswith(STATE_PREFIX):
        raise CalendarError("This sign-in link has expired; connect the calendar again")
    await db.delete(row)
    if row.created_at and row.created_at < datetime.now(timezone.utc) - timedelta(minutes=15):
        raise CalendarError("This sign-in link has expired; connect the calendar again")
    _, provider, user_id = row.nonce.split(":", 2)
    prov = PROVIDERS[provider]
    tokens = await _token_request(prov, {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(),
                                         "code_verifier": row.code_verifier})
    uid = uuid.UUID(user_id)
    conn = (await db.execute(select(CalendarConnection).where(CalendarConnection.user_id == uid, CalendarConnection.provider == provider))).scalar_one_or_none()
    if conn is None:
        conn = CalendarConnection(id=uuid.uuid4(), user_id=uid, provider=provider, token_encrypted="")
        db.add(conn)
    access = _store(conn, tokens)
    conn.account_email = await prov.account_email(access, tokens)
    conn.status, conn.last_error, conn.sync_token = "active", None, None
    await db.flush()
    return conn


async def access_token(db: AsyncSession, conn: CalendarConnection) -> str:
    stored = json.loads(decrypt_secret(conn.token_encrypted))
    if conn.token_expires_at and conn.token_expires_at > datetime.now(timezone.utc):
        return stored["access_token"]
    if not stored.get("refresh_token"):
        raise CalendarError("The calendar connection expired; connect it again")
    tokens = await _token_request(PROVIDERS[conn.provider], {"grant_type": "refresh_token", "refresh_token": stored["refresh_token"]})
    return _store(conn, tokens, stored)


# ---- sync --------------------------------------------------------------------------------------------------------

def meeting_hash(a: Activity) -> str:
    raw = json.dumps([a.subject or "", a.occurred_at.isoformat() if a.occurred_at else "", a.duration_seconds or 0, a.agenda or "",
                      a.attendance or ""], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


async def _remote_payload(db: AsyncSession, a: Activity) -> dict:
    email = (await db.execute(select(Contact.email).where(Contact.id == a.contact_id))).scalar() if a.contact_id else None
    start = a.occurred_at
    return {"summary": a.subject or (a.summary or "Meeting")[:120], "description": a.agenda or "", "start": start,
            "end": start + timedelta(seconds=a.duration_seconds or 1800), "attendees": [email.lower()] if email else []}


async def sync_connection(db: AsyncSession, conn: CalendarConnection) -> dict:
    prov = PROVIDERS[conn.provider]
    stats = {"created": 0, "updated": 0, "cancelled": 0, "pushed": 0, "push_updated": 0, "removed": 0, "conflicts": 0, "skipped": 0}
    try:
        token = await access_token(db, conn)
        try:
            events, cursor = await prov.changes(token, conn.calendar_id, conn.sync_token)
        except ResyncNeeded:
            events, cursor = await prov.changes(token, conn.calendar_id, None)
        await _pull(db, conn, events, stats)
        conn.sync_token = cursor or conn.sync_token
        await _push(db, conn, prov, token, stats)
        conn.status, conn.last_error, conn.last_synced_at = "active", None, datetime.now(timezone.utc)
    except (CalendarError, httpx.HTTPError) as e:
        conn.status, conn.last_error = "error", str(e)[:500] if isinstance(e, CalendarError) else f"{prov.label}: {e.__class__.__name__}"
        log.warning("Calendar sync %s failed: %s", conn.id, e)
    await db.commit()
    return stats


async def _pull(db: AsyncSession, conn: CalendarConnection, events: list[RemoteEvent], stats: dict) -> None:
    user = await db.get(User, conn.user_id)
    own = {e.lower() for e in ((user.email if user else ""), conn.account_email or "") if e}
    links = {l.remote_id: l for l in (await db.execute(select(CalendarLink).where(CalendarLink.connection_id == conn.id))).scalars()}
    for ev in events:
        link = links.get(ev.id)
        if ev.cancelled:
            if link is not None:
                act = await db.get(Activity, link.activity_id) if link.activity_id else None
                if act is not None and act.attendance != "cancelled":
                    act.attendance = "cancelled"
                    stats["cancelled"] += 1
                await db.delete(link)
            continue
        if ev.start is None:
            continue
        if link is not None:
            act = await db.get(Activity, link.activity_id) if link.activity_id else None
            if act is None or link.remote_etag == ev.etag:
                continue
            if meeting_hash(act) != link.local_hash:
                stats["conflicts"] += 1  # edited on both sides: the calendar wins
            act.subject = (ev.summary or act.subject or "Meeting")[:500]
            act.occurred_at = ev.start
            act.duration_seconds = int((ev.end - ev.start).total_seconds()) if ev.end else act.duration_seconds
            act.agenda = ev.description or act.agenda
            link.remote_etag, link.local_hash, link.synced_at = ev.etag, meeting_hash(act), datetime.now(timezone.utc)
            stats["updated"] += 1
            continue
        emails = [e.lower() for e in ev.attendees if e.lower() not in own]
        contact = (await db.execute(select(Contact).where(func.lower(Contact.email).in_(emails), Contact.status != "erased"))).scalars().first() if emails else None
        if contact is None:
            stats["skipped"] += 1
            continue
        act = Activity(account_id=contact.account_id, contact_id=contact.id, user_id=conn.user_id, activity_type="meeting",
                       subject=(ev.summary or "Meeting")[:500], summary=ev.summary or "Meeting", agenda=ev.description or None,
                       occurred_at=ev.start, duration_seconds=int((ev.end - ev.start).total_seconds()) if ev.end else None,
                       attendance="scheduled" if ev.start > datetime.now(timezone.utc) else "attended", sentiment="neutral", source="calendar")
        db.add(act)
        await db.flush()
        db.add(CalendarLink(connection_id=conn.id, remote_id=ev.id, activity_id=act.id, remote_etag=ev.etag, local_hash=meeting_hash(act)))
        stats["created"] += 1
    await db.flush()


async def _push(db: AsyncSession, conn: CalendarConnection, prov, token: str, stats: dict) -> None:
    now = datetime.now(timezone.utc)
    links = list((await db.execute(select(CalendarLink).where(CalendarLink.connection_id == conn.id))).scalars())
    by_activity = {l.activity_id: l for l in links if l.activity_id}
    for l in links:  # meetings deleted in Cirra
        if l.activity_id is None:
            await prov.delete(token, conn.calendar_id, l.remote_id)
            await db.delete(l)
            stats["removed"] += 1
    meetings = (await db.execute(select(Activity).where(Activity.user_id == conn.user_id, Activity.activity_type == "meeting",
                                                        Activity.occurred_at >= now - timedelta(days=1),
                                                        Activity.occurred_at <= now + timedelta(days=FUTURE_DAYS)))).scalars().unique().all()
    for m in meetings:
        link = by_activity.get(m.id)
        if m.attendance == "cancelled":
            if link is not None:
                await prov.delete(token, conn.calendar_id, link.remote_id)
                await db.delete(link)
                stats["removed"] += 1
            continue
        h = meeting_hash(m)
        if link is None:
            remote_id, etag = await prov.create(token, conn.calendar_id, await _remote_payload(db, m))
            db.add(CalendarLink(connection_id=conn.id, remote_id=remote_id, activity_id=m.id, remote_etag=etag, local_hash=h))
            stats["pushed"] += 1
        elif h != link.local_hash:
            try:
                link.remote_etag = await prov.update(token, conn.calendar_id, link.remote_id, link.remote_etag, await _remote_payload(db, m))
            except Conflict:
                stats["conflicts"] += 1
                continue
            link.local_hash, link.synced_at = h, now
            stats["push_updated"] += 1
    await db.flush()


async def sync_all(db: AsyncSession) -> dict:
    conns = (await db.execute(select(CalendarConnection).where(CalendarConnection.status != "disabled"))).scalars().all()
    total = {"connections": len(conns)}
    for c in conns:
        for k, v in (await sync_connection(db, c)).items():
            total[k] = total.get(k, 0) + v
    return total


def connection_out(c: CalendarConnection) -> dict:
    return {"id": c.id, "provider": c.provider, "provider_label": PROVIDERS[c.provider].label, "account_email": c.account_email,
            "status": c.status, "last_synced_at": c.last_synced_at, "last_error": c.last_error, "created_at": c.created_at}
