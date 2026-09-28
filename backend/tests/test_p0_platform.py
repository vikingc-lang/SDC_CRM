"""P0 reach, part 2: pre-built connectors (Slack, Microsoft Teams, Mailchimp, BambooHR) against simulated services, and
tenant workspaces isolated in their own databases."""
import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.models import Connector, IntegrationEvent, Notification, Tenant, User
from tests.helpers import login_as

API = "/api/v1"
pytestmark = pytest.mark.asyncio


class FakeServices:
    """Slack, Teams, Mailchimp and BambooHR, as far as the connectors use them."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.unsubscribed: list[str] = []
        self.employees: list[dict] = []
        self.slack_emails = {"U_SAM": "sam@cirra.demo", "U_ADMIN": "admin@cirra.demo"}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = urlparse(str(request.url))
        if url.hostname == "slack.com":
            method = url.path.rsplit("/", 1)[-1]
            if request.headers.get("authorization") != "Bearer xoxb-good":
                return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
            if method == "auth.test":
                return httpx.Response(200, json={"ok": True, "team": "Cirra Demo", "user": "cirra"})
            if method == "users.lookupByEmail":
                return httpx.Response(200, json={"ok": True, "user": {"id": "U_PRIYA"}})
            if method == "users.info":
                uid = parse_qs(url.query)["user"][0]
                return httpx.Response(200, json={"ok": True, "user": {"profile": {"email": self.slack_emails[uid]}}})
            return httpx.Response(200, json={"ok": True})
        if url.hostname == "hooks.teams.example.test":
            return httpx.Response(200, text="1")
        if url.hostname == "us21.api.mailchimp.com":
            if request.method == "GET" and url.path.endswith("/members"):
                return httpx.Response(200, json={"members": [{"email_address": e, "last_changed": "2026-09-28T10:00:00+00:00"}
                                                             for e in self.unsubscribed]})
            if request.method == "GET":
                return httpx.Response(200, json={"name": "Newsletter", "stats": {"member_count": 3}})
            return httpx.Response(200, json={"id": "x"})
        if url.hostname == "api.bamboohr.com":
            return httpx.Response(200, json={"employees": self.employees})
        return httpx.Response(404)

    def bodies(self, host: str, path_end: str = "") -> list[dict]:
        return [json.loads(r.content or b"{}") for r in self.calls if r.url.host == host and r.url.path.endswith(path_end)]


@pytest.fixture
def fake(monkeypatch):
    from app.services import connectors

    f = FakeServices()
    monkeypatch.setattr(connectors, "transport", httpx.MockTransport(f))
    return f


async def _emit(event_type: str, payload: dict, entity_type="deal") -> None:
    from app.services.notify import emit

    async with SessionLocal() as db:
        emit(db, event_type, entity_type, uuid.uuid4(), payload)
        await db.commit()


async def test_slack_posts_events_dms_notifications_and_answers_the_slash_command(client, fake):
    from app.services import notify

    async with SessionLocal() as db:
        await db.execute(delete(Connector))
        await db.commit()
    async with login_as("admin@cirra.demo") as admin:
        bad = await admin.post(f"{API}/admin/connectors", json={"kind": "slack", "name": "Slack", "values": {"bot_token": "xoxb-wrong", "channel": "#wins"}})
        assert bad.status_code == 422 and "invalid_auth" in bad.json()["detail"]
        r = await admin.post(f"{API}/admin/connectors", json={"kind": "slack", "name": "Sales Slack", "values": {
            "bot_token": "xoxb-good", "signing_secret": "shh-signing", "channel": "#wins", "events": ["deal.closed_won", "made.up"]}})
        assert r.status_code == 201, r.text
        c = r.json()
        assert "Cirra Demo" in c["message"] and c["config"]["events"] == ["deal.closed_won"] and "xoxb" not in r.text
        assert c["secrets_set"] == ["bot_token", "signing_secret"]
        await admin.post(f"{API}/admin/connectors/{c['id']}/run")  # baseline: nothing from before the connector existed
        await _emit("deal.closed_won", {"title": "Bosch rollout", "account": "Bosch", "amount": 120000.0, "currency": "EUR"})
        await _emit("deal.created", {"title": "Not wanted"})
        out = (await admin.post(f"{API}/admin/connectors/{c['id']}/run")).json()
        assert out["result"] == {"posted": 1} and out["last_error"] is None
        post = fake.bodies("slack.com", "chat.postMessage")[-1]
        assert post["channel"] == "#wins" and "Deal won: Bosch rollout" in post["text"] and "120,000 EUR" in post["text"]
        # people who choose "chat" get their notifications as Slack DMs
        async with SessionLocal() as db:
            await notify.deliver_pending(db)
            priya = (await db.execute(select(User).where(User.email == "priya@cirra.demo"))).scalar_one()
            priya.notification_prefs = {"kinds": {"task": {"chat": True}}}
            notify.notify(db, [priya.id], "task", "Send the MSA", None, "/tasks")
            await db.commit()
            assert (await notify.deliver_pending(db)).get("chat:sent") == 1
            priya.notification_prefs = {}
            await db.commit()
        dm = fake.bodies("slack.com", "chat.postMessage")[-1]
        assert dm["channel"] == "U_PRIYA" and "Send the MSA" in dm["text"]
        assert (await admin.get(f"{API}/notifications/preferences")).json()["channels"]["chat"] is True

    # /cirra: signed by Slack, answered within the asker's own scope
    def signed(body: str, secret="shh-signing", ts=None):
        ts = str(ts or int(time.time()))
        sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
        return {"x-slack-request-timestamp": ts, "x-slack-signature": sig, "content-type": "application/x-www-form-urlencoded"}

    acct = (await client.get(f"{API}/accounts")).json()
    name = (acct["items"] if isinstance(acct, dict) else acct)[0]["name"]
    body = f"command=%2Fcirra&text={name.split()[0]}&user_id=U_ADMIN"
    r = await client.post(f"{API}/public/slack/command", content=body, headers=signed(body))
    assert r.status_code == 200 and name.split()[0] in r.json()["text"] and "/accounts/" in r.json()["text"]
    assert (await client.post(f"{API}/public/slack/command", content=body, headers=signed(body, secret="wrong"))).status_code == 401
    assert (await client.post(f"{API}/public/slack/command", content=body, headers=signed(body, ts=int(time.time()) - 900))).status_code == 401
    sdr_body = "command=%2Fcirra&text=zzqx-nothing&user_id=U_SAM"
    assert "No accounts you can see" in (await client.post(f"{API}/public/slack/command", content=sdr_body, headers=signed(sdr_body))).json()["text"]
    async with SessionLocal() as db:
        await db.execute(delete(Connector))
        await db.commit()


async def test_teams_cards_and_internal_addresses_refused(client, fake):
    async with login_as("admin@cirra.demo") as admin:
        blocked = await admin.post(f"{API}/admin/connectors", json={"kind": "teams", "name": "Teams", "values": {"webhook_url": "https://127.0.0.1/hook"}})
        assert blocked.status_code == 422 and "Couldn't reach" not in blocked.json()["detail"]  # says why, not a generic failure
        r = await admin.post(f"{API}/admin/connectors", json={"kind": "teams", "name": "Service channel", "values": {
            "webhook_url": "https://hooks.teams.example.test/workflows/abc", "events": ["case.created"]}})
        assert r.status_code == 201 and "test card" in r.json()["message"]
        cid = r.json()["id"]
        await admin.post(f"{API}/admin/connectors/{cid}/run")
        await _emit("case.created", {"case_number": "CASE-9001", "subject": "Scanner offline", "priority": "high"}, entity_type="case")
        assert (await admin.post(f"{API}/admin/connectors/{cid}/run")).json()["result"] == {"posted": 1}
        card = fake.bodies("hooks.teams.example.test")[-1]["attachments"][0]["content"]
        assert card["body"][0]["text"] == "New case: Scanner offline" and "priority high" in card["body"][1]["text"]
        assert card["actions"][0]["url"].endswith("/cases/" + card["actions"][0]["url"].rsplit("/", 1)[-1])
        # editing keeps the stored secret when the field is left blank
        e = await admin.patch(f"{API}/admin/connectors/{cid}", json={"values": {"webhook_url": "", "events": ["case.created", "case.resolved"]}})
        assert e.status_code == 200 and e.json()["config"]["events"] == ["case.created", "case.resolved"]
        assert (await admin.delete(f"{API}/admin/connectors/{cid}")).status_code == 204
    async with login_as("sam@cirra.demo") as sdr:
        assert (await sdr.get(f"{API}/admin/connectors")).status_code == 403


async def test_mailchimp_mirrors_a_segment_with_consent_and_brings_unsubscribes_back(client, fake):
    tag = uuid.uuid4().hex[:6]
    acc = (await client.post(f"{API}/accounts", json={"name": f"Mailer Co {tag}", "domain": f"mailer{tag}.example.com", "force": True})).json()
    ok = (await client.post(f"{API}/contacts", json={"account_id": acc["id"], "first_name": "Olga", "last_name": "Ok",
                                                     "email": f"olga@mailer{tag}.example.com"})).json()
    gdpr = (await client.post(f"{API}/contacts", json={"account_id": acc["id"], "first_name": "Gert", "last_name": "Gdpr",
                                                       "email": f"gert@mailer{tag}.example.com", "privacy_regime": "GDPR"})).json()
    async with login_as("nina@cirra.demo") as nina:
        seg = (await nina.post(f"{API}/segments", json={"name": f"Mailer {tag}", "object": "contact", "rules": {"conditions": [
            {"type": "field", "field": "account.name", "op": "eq", "value": acc["name"]}]}})).json()
    async with login_as("admin@cirra.demo") as admin:
        r = await admin.post(f"{API}/admin/connectors", json={"kind": "mailchimp", "name": "Newsletter", "values": {
            "api_key": "abc123-us21", "audience_id": "L1", "segment_id": seg["id"]}})
        assert r.status_code == 201 and "Newsletter" in r.json()["message"]
        fake.unsubscribed = [ok["email"].upper()]
        out = (await admin.post(f"{API}/admin/connectors/{r.json()['id']}/run")).json()["result"]
        assert out == {"pushed": 1, "skipped_no_consent": 1, "opted_out": 1}
        put = [x for x in fake.calls if x.method == "PUT"]
        assert len(put) == 1 and put[0].url.path.endswith(hashlib.md5(ok["email"].encode()).hexdigest())
        assert json.loads(put[0].content)["merge_fields"]["COMPANY"] == acc["name"]
        assert put[0].headers["authorization"].startswith("Basic ")
        await admin.delete(f"{API}/admin/connectors/{r.json()['id']}")
    contact = (await client.get(f"{API}/contacts/{ok['id']}")).json()
    assert contact["consent"]["opt_out"]["email"] is True and gdpr["id"]


async def test_bamboohr_provisions_new_hires_follows_managers_and_deactivates_leavers(client, fake):
    tag = uuid.uuid4().hex[:6]
    async with login_as("admin@cirra.demo") as admin:
        leaver = (await admin.post(f"{API}/admin/users", json={"email": f"leaver{tag}@cirra.demo", "full_name": "Lee Leaver",
                                                               "role": "sdr", "password": "leaver-pass-123"})).json()
        async with login_as(f"leaver{tag}@cirra.demo", "leaver-pass-123") as lee:
            assert (await lee.get(f"{API}/users/me")).status_code == 200
            fake.employees = [
                {"id": "1", "workEmail": "marcus@cirra.demo", "firstName": "Marcus", "lastName": "Vance", "department": "Sales", "status": "Active"},
                {"id": "2", "workEmail": f"hire{tag}@cirra.demo", "firstName": "Hana", "lastName": "Hire", "department": "Sales",
                 "status": "Active", "supervisorEId": "1"},
                {"id": "3", "workEmail": f"leaver{tag}@cirra.demo", "department": "Sales", "status": "Inactive"},
                {"id": "4", "workEmail": f"finance{tag}@cirra.demo", "firstName": "Fin", "department": "Finance", "status": "Active"},
            ]
            r = await admin.post(f"{API}/admin/connectors", json={"kind": "bamboohr", "name": "HR", "values": {
                "subdomain": "cirrademo", "api_key": "k", "departments": "Sales", "default_role": "sdr", "create_users": True}})
            assert r.status_code == 201 and "4 employees" in r.json()["message"]
            out = (await admin.post(f"{API}/admin/connectors/{r.json()['id']}/run")).json()["result"]
            assert out["created"] == 1 and out["deactivated"] == 1 and out["managers"] >= 1
            assert (await lee.get(f"{API}/users/me")).status_code == 401  # sessions ended at once
        async with SessionLocal() as db:
            hana = (await db.execute(select(User).where(User.email == f"hire{tag}@cirra.demo"))).scalar_one()
            marcus = (await db.execute(select(User).where(User.email == "marcus@cirra.demo"))).scalar_one()
            assert hana.role == "sdr" and hana.manager_id == marcus.id
            assert (await db.execute(select(User).where(User.email == f"finance{tag}@cirra.demo"))).first() is None
        assert (await admin.post(f"{API}/admin/connectors", json={"kind": "bamboohr", "name": "x", "values": {
            "subdomain": "bad/../path", "api_key": "k"}})).status_code == 422
        await admin.delete(f"{API}/admin/connectors/{r.json()['id']}")
    assert leaver["id"]


# ---- tenancy -------------------------------------------------------------------------------------------------------
async def test_tenant_workspaces_are_isolated(client, monkeypatch):
    from app import tenants
    from app.core import tenancy
    from app.main import app
    from app.services import jobs

    monkeypatch.setattr(tenancy.settings, "multi_tenant", True)
    async with SessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.slug == "acme"))
        await db.commit()
    old = tenancy._engines.pop("acme", None)
    if old is not None:
        await old.dispose()
    tenancy.invalidate()
    made = await tenants.create("acme", "Acme Corp", ["acme.localhost"], "ada@acme.example", "Ada Admin", "acme-pass-123",
                                max_users=2, replace_database=True)
    assert made["database"].endswith("_t_acme")

    def ws(**headers):
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers)

    try:
        async with ws(**{"X-Cirra-Tenant": "acme"}) as t:
            login = await t.post(f"{API}/auth/login", json={"username": "ada@acme.example", "password": "acme-pass-123"})
            assert login.status_code == 200
            token = login.json()["access_token"]
            t.headers["Authorization"] = f"Bearer {token}"
            assert (await t.post(f"{API}/auth/login", json={"username": "marcus@cirra.demo", "password": "cirra123"})).status_code == 401
            acc = (await t.post(f"{API}/accounts", json={"name": "Acme Only Ltd", "domain": "acmeonly.example.com", "force": True})).json()
            assert (await t.get(f"{API}/accounts/{acc['id']}/360")).status_code == 200
            assert len((await t.get(f"{API}/pipelines")).json()) == 1  # a fresh workspace with the standard pipeline
            # the plan's user limit (2 active users)
            assert (await t.post(f"{API}/admin/users", json={"email": "bo@acme.example", "full_name": "Bo", "role": "sdr"})).status_code == 201
            full = await t.post(f"{API}/admin/users", json={"email": "cy@acme.example", "full_name": "Cy", "role": "sdr"})
            assert full.status_code == 409 and "user limit" in full.json()["detail"]
        # the default workspace can't see Acme's records, and tokens don't cross workspaces
        assert (await client.get(f"{API}/accounts/{acc['id']}/360")).status_code == 404
        async with ws(Authorization=f"Bearer {token}") as default_ws:
            assert (await default_ws.get(f"{API}/users/me")).status_code == 401
        async with ws(Authorization=client.headers["Authorization"], **{"X-Cirra-Tenant": "acme"}) as cross:
            assert (await cross.get(f"{API}/users/me")).status_code == 401
        # routing by the web app's host name, and by the /w/<slug> prefix links carry
        async with ws(Authorization=f"Bearer {token}", **{"X-Cirra-Host": "acme.localhost:3000"}) as by_host:
            assert (await by_host.get(f"{API}/users/me")).json()["email"] == "ada@acme.example"
        async with ws(Authorization=f"Bearer {token}") as by_path:
            assert (await by_path.get(f"/w/acme{API}/users/me")).json()["email"] == "ada@acme.example"
        async with ws(**{"X-Cirra-Tenant": "nope"}) as unknown:
            assert (await unknown.get(f"{API}/users/me")).status_code == 404
        # scheduled jobs run once per active tenant, each against its own database
        async with tenancy.use("acme"):
            async with SessionLocal() as db:
                ada = (await db.execute(select(User).where(User.email == "ada@acme.example"))).scalar_one()
                from app.services.notify import notify

                notify(db, [ada.id], "system", "Acme-only notification")
                await db.commit()
            assert tenancy.storage_suffix() == "tenants/acme" and tenancy.rate_key_prefix() == "acme:"
            assert tenancy.api_url().endswith("/w/acme") and tenancy.web_url() == "http://acme.localhost"
        await jobs.run_in_tenants("notifications")
        async with tenancy.use("acme"):
            async with SessionLocal() as db:
                n = (await db.execute(select(Notification).where(Notification.title == "Acme-only notification"))).scalar_one()
                assert n.delivered_at is not None
        async with SessionLocal() as db:  # nothing leaked into the primary database
            assert (await db.execute(select(Notification).where(Notification.title == "Acme-only notification"))).first() is None
            assert (await db.execute(select(IntegrationEvent).where(IntegrationEvent.payload["name"].as_string() == "Acme Only Ltd"))).first() is None
        # suspension takes effect for every request
        await tenants.set_fields("acme", status="suspended")
        async with ws(Authorization=f"Bearer {token}", **{"X-Cirra-Tenant": "acme"}) as s:
            r = await s.get(f"{API}/users/me")
            assert r.status_code == 403 and "suspended" in r.json()["detail"]
        assert "acme" not in await tenancy.active_slugs()
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(Tenant).where(Tenant.slug == "acme"))
            await db.commit()
        eng = tenancy._engines.pop("acme", None)
        if eng is not None:
            await eng.dispose()
        tenancy.invalidate()
