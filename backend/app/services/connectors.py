"""Pre-built connectors: Slack and Microsoft Teams (collaboration), Mailchimp (marketing), BambooHR (HR).

Each connector is configured in Admin → Connectors with its credentials (encrypted at rest) and tested before it is
saved. The ``connectors`` job (every minute) runs them:

* **Slack**: posts the CRM events you pick (deal won, case created, …) to a channel; sends people their
  notifications as direct messages when they choose "chat" in their notification settings; and answers the
  ``/cirra <account>`` slash command with the accounts that person can see.
* **Microsoft Teams**: posts the events you pick to a channel as cards (a Teams incoming webhook or Workflows URL).
* **Mailchimp**: mirrors a Cirra segment into an audience (only contacts who may be emailed), and brings
  unsubscribes made in Mailchimp back into Cirra as email opt-outs.
* **BambooHR**: keeps Cirra users in step with the employee directory: new hires in the chosen departments get
  a user, managers follow the HR reporting line, and leavers are deactivated (their sessions end at once).

Every call goes through ``netguard`` (public hosts only) and an httpx transport tests can replace.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import netguard, tenancy
from app.core.audit import log_action
from app.core.security import hash_password
from app.models import USER_ROLES, Account, Connector, Contact, Deal, IntegrationEvent, PipelineStage, Segment, User
from app.services.mail import decrypt_secret, encrypt_secret

log = logging.getLogger(__name__)
transport: httpx.AsyncBaseTransport | None = None  # tests swap in an httpx transport

POSTABLE_EVENTS = {
    "deal.closed_won": "Deal won", "deal.closed_lost": "Deal lost", "deal.created": "New deal", "deal.stage_changed": "Deal stage changed",
    "lead.created": "New lead", "lead.converted": "Lead converted", "case.created": "New case", "case.resolved": "Case resolved",
    "order.created": "Order created", "quote.approved": "Quote approved", "contract.created": "Contract created",
    "contract.renewal_opened": "Renewal opened", "invoice.overdue": "Invoice overdue", "campaign.launched": "Campaign launched",
    "segment.entered": "Entered a segment",
}

CATALOG = {
    "slack": {
        "label": "Slack", "category": "Collaboration",
        "summary": "Post deal and case updates to a channel, send people their notifications, and look up accounts with /cirra.",
        "fields": [
            {"key": "bot_token", "label": "Bot token (xoxb-…)", "secret": True, "required": True},
            {"key": "signing_secret", "label": "Signing secret (for the /cirra command)", "secret": True, "required": False},
            {"key": "channel", "label": "Channel for updates", "placeholder": "#sales-wins", "required": False},
        ],
        "events": True,
    },
    "teams": {
        "label": "Microsoft Teams", "category": "Collaboration",
        "summary": "Post deal and case updates to a Teams channel as cards.",
        "fields": [{"key": "webhook_url", "label": "Channel webhook URL (Incoming Webhook or Workflows)", "secret": True, "required": True}],
        "events": True,
    },
    "mailchimp": {
        "label": "Mailchimp", "category": "Marketing",
        "summary": "Mirror a Cirra segment into a Mailchimp audience and bring unsubscribes back as opt-outs.",
        "fields": [
            {"key": "api_key", "label": "API key (ends in -us1, -us21, …)", "secret": True, "required": True},
            {"key": "audience_id", "label": "Audience ID", "required": True},
            {"key": "segment_id", "label": "Segment to sync", "kind": "segment", "required": True},
        ],
        "events": False,
    },
    "bamboohr": {
        "label": "BambooHR", "category": "HR",
        "summary": "Create users for new hires, follow HR reporting lines, and deactivate leavers.",
        "fields": [
            {"key": "subdomain", "label": "Company subdomain (the part before .bamboohr.com)", "required": True},
            {"key": "api_key", "label": "API key", "secret": True, "required": True},
            {"key": "departments", "label": "Departments that use Cirra (comma-separated; empty = everyone)", "required": False},
            {"key": "default_role", "label": "Role for new users", "kind": "role", "required": False},
            {"key": "create_users", "label": "Create users for new hires", "kind": "bool", "required": False},
        ],
        "events": False,
    },
}


class ConnectorError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20, transport=transport)


def secrets_of(c: Connector) -> dict:
    import json

    return json.loads(decrypt_secret(c.secret)) if c.secret else {}


def set_secrets(c: Connector, values: dict) -> None:
    import json

    c.secret = encrypt_secret(json.dumps(values)) if values else None


def connector_out(c: Connector) -> dict:
    spec = CATALOG.get(c.kind, {})
    have = secrets_of(c)
    return {"id": c.id, "kind": c.kind, "label": spec.get("label", c.kind), "name": c.name, "config": c.config, "active": c.active,
            "secrets_set": sorted(k for k, v in have.items() if v), "state": {k: v for k, v in (c.state or {}).items() if k != "cursor"},
            "last_run_at": c.last_run_at, "last_error": c.last_error, "created_at": c.created_at}


def split(kind: str, values: dict) -> tuple[dict, dict]:
    """Separate a form's values into (config, secrets) and validate required fields."""
    spec = CATALOG.get(kind)
    if spec is None:
        raise ConnectorError(f"Unknown connector: {kind}")
    config, secret = {}, {}
    for f in spec["fields"]:
        v = values.get(f["key"])
        if isinstance(v, str):
            v = v.strip()
        if f.get("kind") == "bool":
            v = bool(v)
        if f.get("secret"):
            if v:
                secret[f["key"]] = v
        elif v not in (None, ""):
            config[f["key"]] = v
    if spec.get("events"):
        events = [e for e in (values.get("events") or []) if e in POSTABLE_EVENTS]
        config["events"] = events
    return config, secret


def check_required(kind: str, config: dict, secret: dict) -> None:
    for f in CATALOG[kind]["fields"]:
        if f.get("required") and not (secret if f.get("secret") else config).get(f["key"]):
            raise ConnectorError(f"{f['label']} is required")
    if kind == "bamboohr" and config.get("default_role") and config["default_role"] not in USER_ROLES:
        raise ConnectorError("Choose a valid role for new users")
    if kind == "bamboohr" and not str(config.get("subdomain", "")).replace("-", "").isalnum():
        raise ConnectorError("The subdomain is letters, digits and hyphens")
    if kind == "mailchimp" and "-" not in secret.get("api_key", "-"):
        raise ConnectorError("A Mailchimp API key ends with its data center, like -us21")


async def _request(method: str, url: str, **kw) -> httpx.Response:
    await asyncio.to_thread(netguard.check_url, url, "The service address")
    async with _client() as client:
        return await client.request(method, url, **kw)


# ---- Slack ---------------------------------------------------------------------------------------------------------
SLACK = "https://slack.com/api"


async def _slack(token: str, method: str, *, get: bool = False, **payload) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    r = await (_request("GET", f"{SLACK}/{method}", params=payload, headers=headers) if get
               else _request("POST", f"{SLACK}/{method}", json=payload, headers=headers))
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not data.get("ok"):
        raise ConnectorError(f"Slack said: {data.get('error', r.status_code)}")
    return data


async def slack_post(c: Connector, text: str, channel: str | None = None) -> None:
    channel = channel or c.config.get("channel")
    if not channel:
        raise ConnectorError("Choose a channel for updates")
    await _slack(secrets_of(c)["bot_token"], "chat.postMessage", channel=channel, text=text, unfurl_links=False)


async def _active(db: AsyncSession, kind: str) -> Connector | None:
    return (await db.execute(select(Connector).where(Connector.kind == kind, Connector.active.is_(True))
                             .order_by(Connector.created_at))).scalars().first()


async def chat_available(db: AsyncSession) -> bool:
    return await _active(db, "slack") is not None


async def direct_message(db: AsyncSession, email: str, text: str) -> str:
    """A Slack DM to the person with this email (their notification, when they chose "chat")."""
    c = await _active(db, "slack")
    if c is None:
        return "not_connected"
    token = secrets_of(c).get("bot_token")
    try:
        cache = dict((c.state or {}).get("slack_users") or {})
        uid = cache.get(email.lower())
        if not uid:
            uid = (await _slack(token, "users.lookupByEmail", get=True, email=email))["user"]["id"]
            cache[email.lower()] = uid
            c.state = {**(c.state or {}), "slack_users": dict(list(cache.items())[-500:])}
        await _slack(token, "chat.postMessage", channel=uid, text=text, unfurl_links=False)
        return "sent"
    except (ConnectorError, httpx.HTTPError, KeyError, netguard.BlockedDestination):
        return "failed"


def verify_slack(signing_secret: str, timestamp: str, body: bytes, signature: str, tolerance: int = 300) -> bool:
    try:
        if abs(time.time() - int(timestamp)) > tolerance:
            return False
    except (TypeError, ValueError):
        return False
    expected = "v0=" + hmac.new(signing_secret.encode(), f"v0:{timestamp}:".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


async def slash_command(db: AsyncSession, form: dict) -> dict:
    """/cirra <account name>: the matching accounts this Slack user may see in Cirra (their own scope)."""
    from app.core.rbac import principal_for
    from app.core.config import settings

    c = await _active(db, "slack")
    if c is None:
        return {"response_type": "ephemeral", "text": "Cirra isn't connected to this Slack workspace."}
    query = (form.get("text") or "").strip()
    try:
        email = (await _slack(secrets_of(c)["bot_token"], "users.info", get=True, user=form.get("user_id", "")))["user"]["profile"]["email"]
    except (ConnectorError, KeyError, httpx.HTTPError):
        return {"response_type": "ephemeral", "text": "Cirra couldn't read your Slack profile's email address."}
    user = (await db.execute(select(User).where(func.lower(User.email) == email.lower(), User.is_active.is_(True)))).scalars().first()
    if user is None:
        return {"response_type": "ephemeral", "text": f"No active Cirra user has the email {email}."}
    p = await principal_for(db, user)
    if not p.can("accounts", "read"):
        return {"response_type": "ephemeral", "text": "Your Cirra role can't view accounts."}
    if len(query) < 2:
        return {"response_type": "ephemeral", "text": "Type part of an account name, like `/cirra acme`."}
    stmt = p.scope_accounts(select(Account).where(Account.name.ilike(f"%{query[:80]}%"))).order_by(Account.name).limit(5)
    accounts = (await db.execute(stmt)).scalars().unique().all()
    if not accounts:
        return {"response_type": "ephemeral", "text": f"No accounts you can see match “{query}”."}
    base = tenancy.web_url()
    lines = []
    for a in accounts:
        open_amt = (await db.execute(p.scope_deals(select(func.coalesce(func.sum(Deal.amount), 0))
                                                   .join(PipelineStage, PipelineStage.id == Deal.stage_id)
                                                   .where(Deal.account_id == a.id, PipelineStage.is_closed_won.is_(False),
                                                          PipelineStage.is_closed_lost.is_(False))))).scalar_one() if p.can("deals", "read") else None
        pipeline = f" · open pipeline {float(open_amt):,.0f}" if open_amt is not None else ""
        lines.append(f"*<{base}/accounts/{a.id}|{a.name}>* · health {a.health_score}{pipeline}"
                     f"{f' · owner {a.owner.full_name}' if a.owner else ''}")
    return {"response_type": "ephemeral", "text": "\n".join(lines)}


# ---- Microsoft Teams -----------------------------------------------------------------------------------------------
async def teams_post(c: Connector, title: str, text: str, link: str | None = None) -> None:
    url = secrets_of(c)["webhook_url"]
    card = {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.4",
            "body": [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True},
                     {"type": "TextBlock", "text": text, "wrap": True}],
            "actions": [{"type": "Action.OpenUrl", "title": "Open in Cirra", "url": link}] if link else []}
    r = await _request("POST", url, json={"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]})
    if r.status_code >= 400:
        raise ConnectorError(f"Teams refused the message ({r.status_code})")


# ---- event posting (Slack and Teams) -------------------------------------------------------------------------------
def describe(ev: IntegrationEvent) -> tuple[str, str, str | None]:
    """(title, text, link path) for an outbox event."""
    p = ev.payload or {}
    amount = f"{p['amount']:,.0f} {p.get('currency', '')}".strip() if isinstance(p.get("amount"), (int, float)) else ""
    name = p.get("title") or p.get("name") or p.get("subject") or p.get("campaign") or p.get("order_number") or p.get("contract_number") or ""
    title = f"{POSTABLE_EVENTS.get(ev.event_type, ev.event_type)}: {name}".rstrip(": ")
    bits = [b for b in (p.get("account"), amount, p.get("to_stage") and f"now in {p['to_stage']}", p.get("case_number"),
                        p.get("priority") and f"priority {p['priority']}", p.get("loss_reason") and f"reason: {p['loss_reason']}",
                        p.get("segment") and f"segment {p['segment']}") if b]
    link = {"deal": f"/deals/{ev.entity_id}", "case": f"/cases/{ev.entity_id}", "account": f"/accounts/{ev.entity_id}",
            "lead": f"/leads/{ev.entity_id}", "order": f"/orders/{ev.entity_id}", "quote": f"/quotes/{ev.entity_id}",
            "campaign": f"/campaigns/{ev.entity_id}"}.get(ev.entity_type) if ev.entity_id else None
    return title, " · ".join(bits) or ev.event_type, link


async def post_events(db: AsyncSession, c: Connector, limit: int = 100) -> int:
    from app.core.config import settings

    wanted = (c.config or {}).get("events") or []
    cursor = int((c.state or {}).get("cursor") or 0)
    rows = (await db.execute(select(IntegrationEvent).where(IntegrationEvent.id > cursor).order_by(IntegrationEvent.id).limit(limit))).scalars().all()
    posted = 0
    for ev in rows:
        if ev.event_type in wanted:
            title, text, link = describe(ev)
            url = f"{tenancy.web_url()}{link}" if link else None
            if c.kind == "slack":
                await slack_post(c, f"*{title}*\n{text}" + (f"\n<{url}|Open in Cirra>" if url else ""))
            else:
                await teams_post(c, title, text, url)
            posted += 1
        cursor = ev.id  # advance only past events handled (a failure above keeps the rest for the next run)
        c.state = {**(c.state or {}), "cursor": cursor, "posted": int((c.state or {}).get("posted", 0)) + (1 if ev.event_type in wanted else 0)}
    return posted


# ---- Mailchimp -----------------------------------------------------------------------------------------------------
def _mc_base(api_key: str) -> str:
    dc = api_key.rsplit("-", 1)[-1]
    if not dc.isalnum():
        raise ConnectorError("A Mailchimp API key ends with its data center, like -us21")
    return f"https://{dc}.api.mailchimp.com/3.0"


async def _mc(c: Connector, method: str, path: str, **kw) -> dict:
    key = secrets_of(c)["api_key"]
    r = await _request(method, f"{_mc_base(key)}{path}", auth=("cirra", key), **kw)
    if r.status_code >= 400:
        detail = r.json().get("title") if r.headers.get("content-type", "").startswith("application/") else None
        raise ConnectorError(f"Mailchimp said: {detail or r.status_code}")
    return r.json() if r.content else {}


async def mailchimp_sync(db: AsyncSession, c: Connector, cap: int = 2000) -> dict:
    """Push the segment's emailable contacts to the audience; pull unsubscribes back as email opt-outs."""
    from app.services import cdp
    from app.services.privacy import can_contact, record_consent

    seg = await db.get(Segment, c.config.get("segment_id")) if c.config.get("segment_id") else None
    if seg is None or seg.object != "contact":
        raise ConnectorError("Choose a contact segment to sync")
    audience = c.config["audience_id"]
    ids = await cdp.member_ids(db, seg)
    rows = (await db.execute(select(Contact, Account.name).join(Account, Account.id == Contact.account_id)
                             .where(Contact.id.in_(ids)))).unique().all() if ids else []
    pushed = skipped = 0
    for ct, company in rows[:cap]:
        ok, _ = can_contact(ct, "email")
        if not ok or not ct.email:
            skipped += 1
            continue
        h = hashlib.md5(ct.email.lower().encode()).hexdigest()  # noqa: S324 - Mailchimp's member id is this hash
        await _mc(c, "PUT", f"/lists/{audience}/members/{h}", json={
            "email_address": ct.email, "status_if_new": "subscribed",
            "merge_fields": {"FNAME": ct.first_name or "", "LNAME": ct.last_name or "", "COMPANY": company or ""}})
        pushed += 1
    since = (c.state or {}).get("unsub_since")
    params = {"status": "unsubscribed", "count": 1000, "fields": "members.email_address,members.last_changed"}
    if since:
        params["since_last_changed"] = since
    opted_out = 0
    for m in (await _mc(c, "GET", f"/lists/{audience}/members", params=params)).get("members", []):
        ct = (await db.execute(select(Contact).where(func.lower(Contact.email) == str(m.get("email_address", "")).lower()))).scalars().first()
        if ct is not None and not ct.opt_out_email:
            await record_consent(db, ct, opt_outs={"email": True}, source="mailchimp")
            opted_out += 1
    c.state = {**(c.state or {}), "unsub_since": _now().strftime("%Y-%m-%dT%H:%M:%S+00:00"), "pushed": pushed, "skipped": skipped,
               "opted_out": int((c.state or {}).get("opted_out", 0)) + opted_out}
    return {"pushed": pushed, "skipped_no_consent": skipped, "opted_out": opted_out}


# ---- BambooHR ------------------------------------------------------------------------------------------------------
BAMBOO_FIELDS = ["firstName", "lastName", "workEmail", "jobTitle", "department", "status", "supervisorEId", "id"]


async def bamboohr_employees(c: Connector) -> list[dict]:
    sub = c.config["subdomain"]
    r = await _request("POST", f"https://api.bamboohr.com/api/gateway.php/{sub}/v1/reports/custom",
                       params={"format": "JSON", "onlyCurrent": "false"}, auth=(secrets_of(c)["api_key"], "x"),
                       headers={"Accept": "application/json"}, json={"title": "Cirra user sync", "fields": BAMBOO_FIELDS})
    if r.status_code >= 400:
        raise ConnectorError(f"BambooHR refused the request ({r.status_code})")
    return r.json().get("employees", [])


async def bamboohr_sync(db: AsyncSession, c: Connector) -> dict:

    staff = await bamboohr_employees(c)
    departments = {d.strip().lower() for d in str(c.config.get("departments") or "").split(",") if d.strip()}
    in_scope = [e for e in staff if e.get("workEmail") and (not departments or str(e.get("department", "")).lower() in departments)]
    users = {u.email.lower(): u for u in (await db.execute(select(User))).scalars().all()}
    by_hr_id = {str(e.get("id")): str(e.get("workEmail")).lower() for e in in_scope}
    stats = {"created": 0, "deactivated": 0, "managers": 0, "unchanged": 0, "over_limit": 0}
    for e in in_scope:
        email = str(e["workEmail"]).lower()
        active = str(e.get("status", "Active")).lower() == "active"
        u = users.get(email)
        if u is None:
            if not active or not c.config.get("create_users"):
                continue
            if not await tenancy.can_add_user(db):
                stats["over_limit"] += 1
                continue
            u = User(email=email, full_name=f"{e.get('firstName', '')} {e.get('lastName', '')}".strip() or email,
                     role=c.config.get("default_role") or "account_executive", password_hash=hash_password(secrets.token_urlsafe(32)))
            db.add(u)
            await db.flush()
            users[email] = u
            log_action(db, "hr_provision", "user", u.id, f"Created from BambooHR ({e.get('department') or 'no department'})")
            stats["created"] += 1
        elif not active and u.is_active:
            u.is_active = False
            u.session_version = (u.session_version or 0) + 1  # end their sessions now
            log_action(db, "hr_deactivate", "user", u.id, "Left the company (BambooHR)")
            stats["deactivated"] += 1
        else:
            stats["unchanged"] += 1
    for e in in_scope:  # reporting lines, once every user exists
        u = users.get(str(e["workEmail"]).lower())
        boss = users.get(by_hr_id.get(str(e.get("supervisorEId") or ""), ""))
        if u is not None and boss is not None and boss.id != u.id and u.manager_id != boss.id:
            u.manager_id = boss.id
            stats["managers"] += 1
    c.state = {**(c.state or {}), "last_sync": stats}
    return stats


# ---- test, run -----------------------------------------------------------------------------------------------------
async def test(db: AsyncSession, c: Connector) -> str:
    """Check the credentials with a harmless call; returns a sentence for the admin."""
    try:
        if c.kind == "slack":
            info = await _slack(secrets_of(c)["bot_token"], "auth.test")
            return f"Connected to the {info.get('team', 'Slack')} workspace as {info.get('user', 'the Cirra app')}."
        if c.kind == "teams":
            await teams_post(c, "Cirra is connected", "Deal and case updates you choose will appear in this channel.")
            return "A test card was posted to the channel."
        if c.kind == "mailchimp":
            data = await _mc(c, "GET", f"/lists/{c.config['audience_id']}", params={"fields": "name,stats.member_count"})
            return f"Connected to the audience “{data.get('name', c.config['audience_id'])}”."
        if c.kind == "bamboohr":
            staff = await bamboohr_employees(c)
            return f"Connected: {len(staff)} employees in the directory."
    except netguard.BlockedDestination as e:  # (a ValueError, so first)
        raise ConnectorError(str(e))
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise ConnectorError(f"Couldn't reach the service ({type(e).__name__})")
    raise ConnectorError(f"Unknown connector: {c.kind}")


async def run_one(db: AsyncSession, c: Connector, *, scheduled: bool = True) -> dict:
    """Run one connector's work. Directory and audience syncs run hourly on the schedule; event posting every minute."""
    out: dict = {}
    try:
        if c.kind in ("slack", "teams"):
            if not (c.state or {}).get("cursor"):
                c.state = {**(c.state or {}), "cursor": (await db.execute(select(func.coalesce(func.max(IntegrationEvent.id), 0)))).scalar_one()}
            out["posted"] = await post_events(db, c)
        elif c.kind in ("mailchimp", "bamboohr"):
            due = not c.last_run_at or (_now() - c.last_run_at).total_seconds() >= 3600
            if scheduled and not due:
                return {"skipped": "ran within the hour"}
            out = await (mailchimp_sync(db, c) if c.kind == "mailchimp" else bamboohr_sync(db, c))
        c.last_error = None
    except (ConnectorError, httpx.HTTPError, KeyError, ValueError, netguard.BlockedDestination) as e:
        c.last_error = str(e)[:500] if isinstance(e, (ConnectorError, netguard.BlockedDestination)) else f"{type(e).__name__}"
        out = {"error": c.last_error}
    c.last_run_at = _now()
    return out


async def run_all(db: AsyncSession) -> dict:
    results = {}
    for c in (await db.execute(select(Connector).where(Connector.active.is_(True)))).scalars().all():
        results[str(c.id)] = await run_one(db, c)
        await db.commit()
    return results
