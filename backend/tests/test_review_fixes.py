"""Regression tests for the code-review findings on the territories / campaigns / developer-platform commit."""
import smtplib
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models import CampaignMember, IntegrationEvent, WebhookDelivery, WebhookSubscription
from app.services import campaigns, developer
from tests.helpers import login_as


async def _uid(email):
    async with login_as(email) as c:
        return (await c.get("/api/v1/users/me")).json()["id"]


async def test_admin_api_keys_cannot_manage_identity_or_access(client):
    admin_id = await _uid("admin@cirra.demo")
    async with login_as("admin@cirra.demo") as admin:
        key = (await admin.post("/api/v1/developer/api-keys", json={"name": "Sync tool", "user_id": admin_id})).json()["key"]
        users = (await admin.get("/api/v1/admin/users")).json()
    from app.main import app

    h = {"X-API-Key": key}
    target = next(u for u in users if u["email"] == "sam@cirra.demo")["id"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/v1/accounts", headers=h)).status_code == 200  # ordinary automation still works
        for method, path, body in (("PUT", "/api/v1/admin/security", {"mfa_required_roles": []}),
                                   ("PATCH", f"/api/v1/admin/users/{target}", {"role": "super_admin"}),
                                   ("POST", f"/api/v1/admin/users/{target}/reset-mfa", None),
                                   ("PUT", "/api/v1/admin/permissions", []),
                                   ("POST", "/api/v1/admin/users", {"email": "x@cirra.demo", "full_name": "X", "role": "super_admin"}),
                                   ("GET", "/api/v1/admin/security", None)):
            r = await anon.request(method, path, json=body, headers=h)
            assert r.status_code == 403, (method, path, r.status_code)


async def test_duplicate_account_domain_is_409_not_500(client):
    body = {"name": "Dup Domain Co", "domain": "dup-domain-review.example.com", "force": True, "country": "Germany"}
    assert (await client.post("/api/v1/accounts", json=body)).status_code == 201
    r = await client.post("/api/v1/accounts", json={**body, "name": "Dup Domain Again"})
    assert r.status_code == 409 and "already exists" in r.json()["detail"]


def test_render_inserts_values_literally_and_preview_link_is_inert():
    out = campaigns.render("Hi {{first_name}} at {{company}}", {"first_name": r"J\g<0>", "company": r"Acme\Labs\1"}, "tok")
    assert out.startswith(r"Hi J\g<0> at Acme\Labs\1")
    preview = campaigns.render("Body", {}, None)
    assert "/unsubscribe/<personal-link>" in preview and "/unsubscribe/tok" not in preview


async def test_returning_lead_is_credited_to_the_new_campaign(client):
    async with login_as("nina@cirra.demo") as mkt:
        cid = (await mkt.post("/api/v1/campaigns", json={"name": "Second Touch Webinar", "status": "active"})).json()["id"]
        base = {"first_name": "Rita", "last_name": "Return", "email": "rita.return@returning.example.com", "company_name": "Returning Co",
                "source": "web_form", "consent": "granted"}
        await mkt.post("/api/v1/leads", json={**base, "campaign": "Website"})
        await mkt.post("/api/v1/leads", json={**base, "source": "campaign", "campaign": "second-touch-webinar"})
        members = (await mkt.get(f"/api/v1/campaigns/{cid}/members")).json()
        assert members["total"] == 1 and members["members"][0]["status"] == "responded"


async def test_campaign_deal_list_follows_row_scope(client):
    async with login_as("nina@cirra.demo") as mkt:
        rows = (await mkt.get("/api/v1/campaigns")).json()["campaigns"]
    async with login_as("priya@cirra.demo") as ae:
        mine = {d["id"] for d in (await ae.get("/api/v1/deals", params={"status": "all"})).json()}
        for c in rows:
            detail = (await ae.get(f"/api/v1/campaigns/{c['id']}")).json()
            assert {d["id"] for d in detail["metrics"]["deals"]} <= mine


async def test_campaign_send_keeps_progress_when_smtp_fails(client, monkeypatch):
    async with login_as("nina@cirra.demo") as mkt:
        cid = (await mkt.post("/api/v1/campaigns", json={"name": "Flaky SMTP", "email_subject": "Hi", "email_body": "Body"})).json()["id"]
        added = (await mkt.post(f"/api/v1/campaigns/{cid}/members/from-filter", json={"source": "contacts", "filters": []})).json()["added"]
        eligible = (await mkt.get(f"/api/v1/campaigns/{cid}/email/preview")).json()["eligible"]
        assert added and eligible >= 3
        from app.services import mail

        calls = {"n": 0}

        async def flaky(db, user, to, subject, body, in_reply_to=None, html=None):
            calls["n"] += 1
            if calls["n"] == 3:
                raise smtplib.SMTPServerDisconnected("connection lost")
            return f"<m{calls['n']}@test>", True

        monkeypatch.setattr(mail, "deliver", flaky)
        r = await mkt.post(f"/api/v1/campaigns/{cid}/email/send")
        assert r.status_code == 202
        detail = (await mkt.get(f"/api/v1/campaigns/{cid}")).json()
        assert detail["send_status"] == "failed" and "after 2 email" in detail["send_result"]["error"]
    async with SessionLocal() as db:
        sent = (await db.execute(select(func.count()).select_from(CampaignMember).where(CampaignMember.campaign_id == uuid.UUID(cid),
                                                                                         CampaignMember.status == "sent"))).scalar()
    assert sent == 2  # the two delivered emails stay recorded, so a retry won't resend them


async def test_webhook_fan_out_sees_late_commits_and_never_duplicates(client):
    async with SessionLocal() as db:
        sub = WebhookSubscription(name="Late commit", url="https://late.example.com/h", event_types=["late.*"], secret_enc=developer.new_secret()[1],
                                  active=True, cursor_event_id=await developer.latest_event_id(db))
        db.add(sub)
        e1 = IntegrationEvent(event_type="late.first", entity_type="test", payload={}, targets=[])
        e2 = IntegrationEvent(event_type="late.second", entity_type="test", payload={}, targets=[])
        db.add_all([e1, e2])
        await db.flush()
        first_id = e1.id
        await db.delete(e1)  # e1's transaction "hasn't committed yet"
        await db.commit()
        await developer.fan_out(db)
        await db.commit()
        # e1 now commits with its (lower) id
        db.add(IntegrationEvent(id=first_id, event_type="late.first", entity_type="test", payload={}, targets=[]))
        await db.commit()
        await developer.fan_out(db)
        await developer.fan_out(db)  # overlapping / repeated runs don't duplicate
        await db.commit()
        rows = (await db.execute(select(WebhookDelivery.event_type).where(WebhookDelivery.subscription_id == sub.id))).scalars().all()
        assert sorted(rows) == ["late.first", "late.second"]
        await db.delete(sub)
        await db.commit()


@pytest.mark.parametrize("n", [1])
async def test_concurrent_webhook_runs_skip_instead_of_double_sending(client, n):
    from sqlalchemy import text

    async with SessionLocal() as a, SessionLocal() as b:
        got = (await a.execute(text("SELECT pg_try_advisory_xact_lock(hashtext('cirra.webhooks'))"))).scalar()
        assert got
        out = await developer.run(b)  # a second run while the first holds the lock
        assert out.get("skipped")
        await a.rollback()
