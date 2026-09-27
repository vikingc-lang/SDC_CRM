"""Wave 4 service and marketing depth: email-to-case, presence-based routing, email tracking and nurture journeys."""
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import CampaignMember, EmailSend, InboundEmail, Lead, SupportTicket
from app.services import journeys, mail, mailer, tracking
from tests.helpers import login_as

API = "/api/v1"
SECRET = "inbound-test-secret"


@pytest.fixture
def outbox(monkeypatch):
    """System email switched on and captured, plus the inbound webhook enabled."""
    sent = []
    monkeypatch.setattr(mailer.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(mailer.settings, "inbound_email_secret", SECRET)
    monkeypatch.setattr(mailer, "_send", lambda msg: sent.append(msg))
    return sent


async def _mail(client, **m):
    return await client.post(f"{API}/inbound/email", headers={"X-Cirra-Inbound-Secret": SECRET}, json=m)


def _mid() -> str:
    return f"<{uuid.uuid4().hex}@customer.example>"


async def test_email_to_case_threading_replies_and_follow_ups(client, outbox):
    async with login_as("admin@cirra.demo") as admin:
        users = (await admin.get(f"{API}/users")).json()
        sofia = next(u["id"] for u in users if u["email"] == "sofia@cirra.demo")
        qname = f"EMEA support {uuid.uuid4().hex[:5]}"
        address = f"support-{uuid.uuid4().hex[:6]}@cirra.example"
        r = await admin.post(f"{API}/service/queues", json={"name": qname, "member_ids": [sofia], "email_address": address})
        assert r.status_code == 201, r.text
        qid = r.json()["id"]
    contact = next(c for c in (await client.get(f"{API}/contacts")).json() if c.get("email") and c["status"] == "active")
    first = _mid()
    r = (await _mail(client, message_id=first, from_email=contact["email"], from_name=contact["name"], to=[address],
                     subject="Printer won't connect", text="Hi, our label printer stopped connecting.\n\nOn Mon, someone wrote:\n> old")).json()
    assert r["status"] == "case_created"
    case = (await client.get(f"{API}/cases/{r['case_id']}")).json()
    assert case["channel"] == "email" and case["queue_id"] == qid and case["contact"]["email"] == contact["email"]
    assert case["owner_id"] == sofia and "old" not in case["description"]  # quoted history is trimmed
    ack = outbox[-1]
    assert ack["To"] == contact["email"] and f"[{case['case_number']}]" in ack["Subject"] and ack["In-Reply-To"] == first
    assert (await _mail(client, message_id=first, from_email=contact["email"], subject="x", text="x")).json()["id"] == r["id"]  # idempotent

    async with login_as("sofia@cirra.demo") as agent:
        reply = (await agent.post(f"{API}/cases/{case['id']}/comments", json={"body": "Please power-cycle it and tell me if it works."})).json()
        assert reply["email"] == "sent"
        out = outbox[-1]
        assert out["To"] == contact["email"] and out["Subject"].startswith(f"Re: [{case['case_number']}]") and out["In-Reply-To"] == first
        assert (await agent.get(f"{API}/cases/{case['id']}")).json()["status"] == "pending"
        # the customer answers the agent's email without the tag: threaded by In-Reply-To
        back = (await _mail(client, message_id=_mid(), from_email=contact["email"], subject="RE: your reply", text="Still broken.",
                            in_reply_to=out["Message-ID"])).json()
        assert back["status"] == "appended" and back["case_id"] == case["id"]
        detail = (await agent.get(f"{API}/cases/{case['id']}")).json()
        assert detail["status"] == "open" and detail["comments"][-1]["from_customer"] and detail["comments"][-1]["body"] == "Still broken."
        assert (await agent.post(f"{API}/cases/{case['id']}/comments", json={"body": "internal only", "internal": True})).json()["email"] == "not_email"
        await agent.patch(f"{API}/cases/{case['id']}", json={"status": "closed"})
    follow = (await _mail(client, message_id=_mid(), from_email=contact["email"], subject=f"Re: [{case['case_number']}] Printer", text="It broke again")).json()
    assert follow["status"] == "case_created" and follow["case_id"] != case["id"]
    new = (await client.get(f"{API}/cases/{follow['case_id']}")).json()
    assert new["description"].startswith(f"Follow-up to {case['case_number']}")


async def test_unknown_senders_domains_loops_and_security(client, outbox):
    acc = (await client.get(f"{API}/accounts", params={"search": "Northwind"})).json()[0]
    domain_hit = (await _mail(client, message_id=_mid(), from_email=f"new.person.{uuid.uuid4().hex[:4]}@{acc['domain']}", from_name="New Person",
                              subject="Access request", text="Please add me")).json()
    assert domain_hit["status"] == "case_created"
    c = (await client.get(f"{API}/cases/{domain_hit['case_id']}")).json()
    assert c["account_id"] == acc["id"] and c["contact"] is None and c["supplied"]["name"] == "New Person"
    stranger = (await _mail(client, message_id=_mid(), from_email="someone@unknown-company.example", subject="Hello", text="Who is this")).json()
    gmail = (await _mail(client, message_id=_mid(), from_email=f"x{uuid.uuid4().hex[:4]}@gmail.com", subject="Hi", text="free mail never matches by domain")).json()
    assert stranger["status"] == gmail["status"] == "unmatched"
    auto = (await _mail(client, message_id=_mid(), from_email="someone@unknown-company.example", subject="Out of office", text="away", automatic=True)).json()
    assert auto["status"] == "ignored"
    own = (await _mail(client, message_id=_mid(), from_email="cirra@localhost", subject="loop", text="loop")).json()
    assert own["status"] == "ignored" and "loop" in own["detail"]
    async with login_as("sofia@cirra.demo") as agent:
        tray = (await agent.get(f"{API}/cases/inbound")).json()
        assert any(m["id"] == stranger["id"] for m in tray["messages"])
        filed = await agent.post(f"{API}/cases/inbound/{stranger['id']}/file", json={"account_id": acc["id"]})
        assert filed.status_code == 201 and filed.json()["case_number"].startswith("CS-")
        assert (await agent.post(f"{API}/cases/inbound/{stranger['id']}/dismiss")).status_code == 422  # already filed
        assert (await agent.post(f"{API}/cases/inbound/{gmail['id']}/dismiss")).json()["status"] == "dismissed"
    bad = await client.post(f"{API}/inbound/email", headers={"X-Cirra-Inbound-Secret": "wrong"}, json={"message_id": _mid(), "from_email": "a@b.co"})
    assert bad.status_code == 401


async def test_inbound_endpoint_is_off_without_a_secret(client, monkeypatch):
    monkeypatch.setattr(mailer.settings, "inbound_email_secret", None)
    r = await client.post(f"{API}/inbound/email", json={"message_id": _mid(), "from_email": "a@b.co"})
    assert r.status_code == 404


async def test_raw_rfc822_and_sender_rate_limit(client, outbox):
    contact = next(c for c in (await client.get(f"{API}/contacts")).json() if c.get("email") and c["status"] == "active")
    raw = (f"From: {contact['name']} <{contact['email']}>\r\nTo: help@cirra.example\r\nSubject: Raw message\r\nMessage-ID: {_mid()}\r\n"
           "Content-Type: text/plain\r\n\r\nSent as raw MIME.\r\n").encode()
    import base64

    r = (await _mail(client, raw=base64.b64encode(raw).decode())).json()
    assert r["status"] == "case_created"
    async with SessionLocal() as db:
        flood = f"flood{uuid.uuid4().hex[:4]}@unknown-company.example"
        for _ in range(20):
            db.add(InboundEmail(message_id=_mid(), from_email=flood, subject="spam", body="", status="unmatched"))
        await db.commit()
    limited = (await _mail(client, message_id=_mid(), from_email=flood, subject="more", text="more")).json()
    assert limited["status"] == "ignored" and "hour" in limited["detail"]


async def test_presence_routing_waits_then_pushes_by_priority(client):
    async with login_as("admin@cirra.demo") as admin:
        users = (await admin.get(f"{API}/users")).json()
        sofia = next(u["id"] for u in users if u["email"] == "sofia@cirra.demo")
        me = next(u["id"] for u in users if u["email"] == "admin@cirra.demo")
        qid = (await admin.post(f"{API}/service/queues", json={"name": f"Live chat {uuid.uuid4().hex[:5]}", "member_ids": [sofia, me],
                                                               "routing": "presence"})).json()["id"]
        await admin.put(f"{API}/cases/presence/me", json={"status": "offline"})
        await admin.put(f"{API}/cases/presence/{sofia}", json={"status": "offline"})
        acc = (await admin.get(f"{API}/accounts")).json()[0]["id"]
        low = (await admin.post(f"{API}/cases", json={"account_id": acc, "subject": "Low one", "severity": "low", "queue_id": qid, "channel": "chat"})).json()
        crit = (await admin.post(f"{API}/cases", json={"account_id": acc, "subject": "Critical one", "severity": "critical", "queue_id": qid, "channel": "chat"})).json()
        console = (await admin.get(f"{API}/cases/routing")).json()
        assert next(q for q in console["queues"] if q["id"] == qid)["waiting"] == 2
        load = next(a for a in console["agents"] if a["id"] == sofia)["open_cases"]
    async with login_as("sofia@cirra.demo") as agent:
        r = await agent.put(f"{API}/cases/presence/me", json={"status": "available", "capacity": load + 1})
        assert r.status_code == 200, r.text  # pushing a waiting case touches the presence row again
        p = r.json()
        assert p["status"] == "available" and p["updated_at"] and p["last_assigned_at"]
        assert (await agent.get(f"{API}/cases/{crit['id']}")).json()["owner_id"] == sofia  # critical first, and she's now full
        assert (await agent.get(f"{API}/cases/{low['id']}")).json()["owner_id"] is None
        await agent.patch(f"{API}/cases/{crit['id']}", json={"status": "resolved"})  # frees capacity: the low case follows
        assert (await agent.get(f"{API}/cases/{low['id']}")).json()["owner_id"] == sofia
        await agent.put(f"{API}/cases/presence/me", json={"status": "offline"})
    async with login_as("marcus@cirra.demo") as mgr:  # a sales manager can't set other agents' presence
        assert (await mgr.put(f"{API}/cases/presence/{sofia}", json={"status": "available"})).status_code == 403


async def test_tracked_nurture_journey_branches_on_engagement(client, monkeypatch):
    delivered = []

    async def fake_deliver(db, user, to, subject, body, in_reply_to=None, html=None):
        delivered.append({"to": to, "subject": subject, "body": body, "html": html})
        return f"<{uuid.uuid4().hex}@cirra.test>", True
    monkeypatch.setattr(mail, "deliver", fake_deliver)
    tag = uuid.uuid4().hex[:6]
    async with login_as("nina@cirra.demo") as mkt:
        camp = (await mkt.post(f"{API}/campaigns", json={"name": f"Nurture {tag}"})).json()
        ids = []
        for n in range(3):
            lead = (await mkt.post(f"{API}/leads", json={"first_name": f"Nia{n}", "last_name": f"Journey{tag}", "email": f"nia{n}.{tag}@journey-test.example",
                                                         "company_name": f"Journey Co {tag}", "consent": "granted", "country": "United States"})).json()
            ids.append(lead["id"])
        await mkt.post(f"{API}/campaigns/{camp['id']}/members", json={"lead_ids": ids})
        steps = [{"type": "email", "subject": "Hi {{first_name}}", "body": "See the demo: https://cirra.example/demo?x=1 {{unsubscribe_url}}"},
                 {"type": "wait", "days": 2},
                 {"type": "email", "subject": "Did you miss this?", "body": "Second chance", "send_if": "not_opened"},
                 {"type": "email", "subject": "Book a call", "body": "Pick a time", "send_if": "clicked"}]
        for bad in ([], [{"type": "wait", "days": 1}], [{"type": "wait", "days": 999}], [{"type": "email", "subject": "x", "body": "y", "send_if": "maybe"}]):
            r = await mkt.post(f"{API}/journeys", json={"name": "bad", "campaign_id": camp["id"], "steps": bad})
            assert (r.status_code == 422) or (bad == [] and r.status_code == 201)
        j = (await mkt.post(f"{API}/journeys", json={"name": f"Welcome {tag}", "campaign_id": camp["id"], "steps": steps})).json()
        act = (await mkt.post(f"{API}/journeys/{j['id']}/status", params={"status": "active"})).json()
        assert act["enrolled"] == 3
        assert (await mkt.put(f"{API}/journeys/{j['id']}", json={"name": "x", "campaign_id": camp["id"], "steps": steps})).status_code == 409
        test = (await mkt.post(f"{API}/journeys/{j['id']}/test", json={"step": 0})).json()
        assert test["sent_to"] == "nina@cirra.demo"
    delivered.clear()
    async with SessionLocal() as db:
        await journeys.run(db)
    assert len(delivered) == 3 and all(d["subject"].startswith("Hi Nia") for d in delivered)
    first = {d["to"]: d for d in delivered}
    # nia0 opens (pixel), nia1 clicks (link), nia2 does nothing
    pixel = re.search(r'src="([^"]+\.gif)"', first[f"nia0.{tag}@journey-test.example"]["html"]).group(1)
    assert (await client.get(urlsplit(pixel).path)).headers["content-type"] == "image/gif"
    link = re.search(r"https?://\S+/t/c/\S+", first[f"nia1.{tag}@journey-test.example"]["body"]).group(0)
    parts = urlsplit(link)
    hit = await client.get(f"{parts.path}?{parts.query}", follow_redirects=False)
    assert hit.status_code == 302 and hit.headers["location"] == "https://cirra.example/demo?x=1"
    assert "/unsubscribe/" in first[f"nia1.{tag}@journey-test.example"]["body"]  # the unsubscribe link stays direct
    tampered = await client.get(f"{parts.path}?u=https%3A%2F%2Fevil.example&s=deadbeef", follow_redirects=False)
    assert tampered.status_code == 404
    assert (await client.get(f"{API}/t/o/not-a-token.gif")).status_code == 200
    async with SessionLocal() as db:
        lead1 = await db.get(Lead, uuid.UUID(ids[1]))
        member1 = (await db.execute(select(CampaignMember).where(CampaignMember.lead_id == lead1.id))).scalar_one()
        assert lead1.engagement_score > 0 and member1.status == "responded"
        later = datetime.now(timezone.utc) + timedelta(days=2, minutes=1)
        delivered.clear()
        await journeys.run(db, now=later)
    got = {d["to"]: d["subject"] for d in delivered}
    assert got == {f"nia2.{tag}@journey-test.example": "Did you miss this?", f"nia1.{tag}@journey-test.example": "Book a call"}
    async with login_as("nina@cirra.demo") as mkt:
        detail = (await mkt.get(f"{API}/journeys/{j['id']}")).json()
        s0 = detail["stats"]["steps"][0]
        assert (s0["sent"], s0["opened"], s0["clicked"]) == (3, 2, 1) and detail["stats"]["by_status"]["completed"] == 3
        email = (await mkt.get(f"{API}/campaigns/{camp['id']}")).json()["metrics"]["email"]
        assert email["sent"] == 5 and email["opened"] == 2 and email["clicked"] == 1
        assert (await mkt.delete(f"{API}/journeys/{j['id']}")).status_code == 409  # ran: archive instead
        assert (await mkt.post(f"{API}/journeys/{j['id']}/status", params={"status": "archived"})).json()["status"] == "archived"


async def test_journey_exits_on_unsubscribe_and_missing_consent(client, monkeypatch):
    async def fake_deliver(db, user, to, subject, body, in_reply_to=None, html=None):
        return "<x@cirra.test>", True
    monkeypatch.setattr(mail, "deliver", fake_deliver)
    tag = uuid.uuid4().hex[:6]
    async with login_as("nina@cirra.demo") as mkt:
        camp = (await mkt.post(f"{API}/campaigns", json={"name": f"Exit test {tag}"})).json()
        ok = (await mkt.post(f"{API}/leads", json={"first_name": "Una", "last_name": f"Sub{tag}", "email": f"una.{tag}@exit-test.example",
                                                   "company_name": f"Exit Co {tag}", "consent": "granted", "country": "United States"})).json()
        gdpr = (await mkt.post(f"{API}/leads", json={"first_name": "Gerd", "last_name": f"Noconsent{tag}", "email": f"gerd.{tag}@exit-test.example",
                                                     "company_name": f"Exit GmbH {tag}", "consent": "unknown", "country": "Germany"})).json()
        await mkt.post(f"{API}/campaigns/{camp['id']}/members", json={"lead_ids": [ok["id"], gdpr["id"]]})
        j = (await mkt.post(f"{API}/journeys", json={"name": "Exits", "campaign_id": camp["id"], "steps": [
            {"type": "wait", "days": 1}, {"type": "email", "subject": "Hello", "body": "Body"}]})).json()
        await mkt.post(f"{API}/journeys/{j['id']}/status", params={"status": "active"})
    async with SessionLocal() as db:
        await journeys.run(db)
        token = (await db.execute(select(CampaignMember.token).where(CampaignMember.lead_id == uuid.UUID(ok["id"])))).scalar_one()
    assert (await client.post(f"{API}/public/unsubscribe/{token}")).status_code == 200
    async with SessionLocal() as db:
        await journeys.run(db, now=datetime.now(timezone.utc) + timedelta(days=1, minutes=1))
        assert not (await db.execute(select(EmailSend).where(EmailSend.journey_id == uuid.UUID(j["id"])))).first()
    async with login_as("nina@cirra.demo") as mkt:
        stats = (await mkt.get(f"{API}/journeys/{j['id']}")).json()["stats"]
        assert stats["by_status"]["exited"] == 2
        assert set(stats["exit_reasons"]) == {"Unsubscribed", "Can't email: GDPR: no recorded consent"}


def test_click_signatures_bind_token_and_url():
    sig = tracking.sign("tok", "https://a.example")
    assert tracking.verify("tok", "https://a.example", sig)
    assert not tracking.verify("tok", "https://b.example", sig) and not tracking.verify("other", "https://a.example", sig)
    html = tracking.tracked_html("Go <here> https://a.example/x?y=1 & more", "tok")
    assert "&lt;here&gt;" in html and "/t/c/tok?u=" in html and "/t/o/tok.gif" in html
    _ = SupportTicket
