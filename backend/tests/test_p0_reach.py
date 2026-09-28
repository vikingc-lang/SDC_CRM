"""P0 reach, part 1: the notification center (inbox, preferences, email, digests, quiet hours, Web Push), the buying
committee and org chart, and the behavioural event store with dynamic segments."""
import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import BehaviorEvent, IntegrationEvent, Notification, PushSubscription, User
from tests.helpers import login_as

API = "/api/v1"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


async def _user(email: str) -> User:
    async with SessionLocal() as db:
        return (await db.execute(select(User).where(User.email == email))).scalar_one()


async def _notify(email: str, kind: str, title: str, **kw) -> uuid.UUID:
    from app.services.notify import notify

    u = await _user(email)
    async with SessionLocal() as db:
        notify(db, [u.id], kind, title, kw.get("body"), kw.get("link", "/"))
        await db.commit()
        return (await db.execute(select(Notification.id).where(Notification.user_id == u.id, Notification.title == title))).scalar_one()


async def _set_prefs(email: str, prefs: dict) -> None:
    async with SessionLocal() as db:
        u = (await db.execute(select(User).where(User.email == email))).scalar_one()
        u.notification_prefs = prefs
        await db.commit()


# ---- notification center -------------------------------------------------------------------------------------------
async def test_inbox_views_archive_snooze_and_muting(client):
    tag = uuid.uuid4().hex[:6]
    a = await _notify("sam@cirra.demo", "task", f"Task {tag}")
    b = await _notify("sam@cirra.demo", "risk", f"Risk {tag}")
    async with login_as("sam@cirra.demo") as sam:
        inbox = (await sam.get(f"{API}/notifications")).json()
        assert {str(a), str(b)} <= {i["id"] for i in inbox["items"]} and inbox["unread_by_kind"]["task"] >= 1
        assert next(i for i in inbox["items"] if i["id"] == str(a))["kind_label"] == "Tasks"
        assert all(i["kind"] == "risk" for i in (await sam.get(f"{API}/notifications", params={"kind": "risk"})).json()["items"])
        # snooze: gone from the inbox, listed under snoozed, and back unread afterwards
        assert (await sam.post(f"{API}/notifications/snooze", json={"ids": [str(a)], "hours": 2})).status_code == 200
        assert str(a) not in {i["id"] for i in (await sam.get(f"{API}/notifications")).json()["items"]}
        assert str(a) in {i["id"] for i in (await sam.get(f"{API}/notifications", params={"view": "snoozed"})).json()["items"]}
        async with SessionLocal() as db:
            (await db.get(Notification, a)).snoozed_until = datetime.now(timezone.utc) - timedelta(minutes=1)
            await db.commit()
        again = next(i for i in (await sam.get(f"{API}/notifications")).json()["items"] if i["id"] == str(a))
        assert again["read"] is False
        # archive and restore
        await sam.post(f"{API}/notifications/archive", json={"ids": [str(b)]})
        assert str(b) in {i["id"] for i in (await sam.get(f"{API}/notifications", params={"view": "archived"})).json()["items"]}
        await sam.post(f"{API}/notifications/archive", json={"ids": [str(b)], "restore": True})
        assert str(b) in {i["id"] for i in (await sam.get(f"{API}/notifications")).json()["items"]}
        # read / unread, and the old "mark all read" call still works
        await sam.post(f"{API}/notifications/read", json=[str(a)])
        assert next(i for i in (await sam.get(f"{API}/notifications")).json()["items"] if i["id"] == str(a))["read"]
        await sam.post(f"{API}/notifications/unread", json={"ids": [str(a)]})
        assert (await sam.post(f"{API}/notifications/read")).status_code == 200
        assert (await sam.get(f"{API}/notifications", params={"view": "unread"})).json()["items"] == []
        # switching a kind off in the app hides it
        prefs = (await sam.get(f"{API}/notifications/preferences")).json()
        assert prefs["kinds"]["sla"]["email"] is True and any(c["key"] == "risk" for c in prefs["catalog"])
        r = await sam.put(f"{API}/notifications/preferences", json={"kinds": {"risk": {"in_app": False}}})
        assert r.json()["kinds"]["risk"]["in_app"] is False
        assert all(i["kind"] != "risk" for i in (await sam.get(f"{API}/notifications")).json()["items"])
        assert (await sam.put(f"{API}/notifications/preferences", json={"quiet": {"enabled": True, "start": "25:99"}})).status_code == 422
    await _set_prefs("sam@cirra.demo", {})


async def test_email_instant_digest_and_quiet_hours(client, monkeypatch):
    from app.services import mailer, notify

    sent = []

    async def fake_send(to, subject, text, *a, **kw):
        sent.append((to, subject, text))
        return "<id>"

    monkeypatch.setattr(mailer.settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(mailer, "send", fake_send)
    async with SessionLocal() as db:  # everything queued before this test counts as handled
        await notify.deliver_pending(db)
    sent.clear()

    await _set_prefs("priya@cirra.demo", {"email_mode": "instant", "kinds": {"task": {"email": True}}})
    await _notify("priya@cirra.demo", "task", "Call Bosch back", link="/tasks")
    async with SessionLocal() as db:
        stats = await notify.deliver_pending(db)
    assert stats.get("email:sent") == 1 and sent[0][0] == "priya@cirra.demo" and "Call Bosch back" in sent[0][1] and "/tasks" in sent[0][2]

    # digest: held, then one email at the digest hour listing only what is still unread
    sent.clear()
    await _set_prefs("priya@cirra.demo", {"email_mode": "digest", "digest_hour": 9, "kinds": {"task": {"email": True}}})
    keep = await _notify("priya@cirra.demo", "task", "Digest item one")
    read = await _notify("priya@cirra.demo", "task", "Digest item two")
    async with SessionLocal() as db:
        await notify.deliver_pending(db)
        (await db.get(Notification, read)).read_at = datetime.now(timezone.utc)
        await db.commit()
    assert sent == []
    async with SessionLocal() as db:
        assert await notify.send_digests(db, now=datetime(2026, 9, 29, 3, tzinfo=timezone.utc)) == 0  # not 9:00 in UTC
        assert await notify.send_digests(db, now=datetime(2026, 9, 29, 9, tzinfo=timezone.utc)) == 1
        assert (await db.get(Notification, keep)).delivery["email"] == "digested"
    assert "Digest item one" in sent[0][2] and "Digest item two" not in sent[0][2]

    # quiet hours hold normal notifications but not SLA breaches
    sent.clear()
    await _set_prefs("priya@cirra.demo", {"quiet": {"enabled": True, "start": "20:00", "end": "07:00"},
                                          "kinds": {"task": {"email": True}}})
    held = await _notify("priya@cirra.demo", "task", "Quiet task")
    await _notify("priya@cirra.demo", "sla", "Case past SLA")
    night = datetime(2026, 9, 29, 23, tzinfo=timezone.utc)
    async with SessionLocal() as db:
        await notify.deliver_pending(db, now=night)
        assert (await db.get(Notification, held)).delivered_at is None
        await notify.deliver_pending(db, now=night.replace(hour=8) + timedelta(days=1))
        assert (await db.get(Notification, held)).delivered_at is not None
    assert [s[1] for s in sent] == ["[Cirra] Case past SLA", "[Cirra] Quiet task"]
    await _set_prefs("priya@cirra.demo", {})


def _decrypt(body: bytes, ua_key: ec.EllipticCurvePrivateKey, auth: bytes) -> dict:
    salt, idlen = body[:16], body[20]
    as_pub, ct = body[21:21 + idlen], body[21 + idlen:]
    ua_pub = ua_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + ua_pub + as_pub).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    plain = AESGCM(cek).decrypt(nonce, ct, None)
    assert plain.endswith(b"\x02")
    return json.loads(plain[:-1])


async def test_web_push_is_encrypted_signed_and_cleans_up(client, monkeypatch):
    from app.services import notify, webpush

    ua_key = ec.generate_private_key(ec.SECP256R1())
    auth = b"0123456789abcdef"
    p256dh = _b64(ua_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    endpoint = f"https://push.example.test/send/{uuid.uuid4().hex}"
    got = []

    def handler(request: httpx.Request) -> httpx.Response:
        got.append(request)
        return httpx.Response(410 if len(got) > 1 else 201)

    monkeypatch.setattr(webpush, "transport", httpx.MockTransport(handler))
    async with login_as("marcus@cirra.demo") as m:
        public = (await m.get(f"{API}/notifications/push/key")).json()["public_key"]
        bad = await m.post(f"{API}/notifications/push/subscribe", json={"endpoint": "https://127.0.0.1/push", "keys": {"p256dh": p256dh, "auth": _b64(auth)}})
        assert bad.status_code == 422
        assert (await m.post(f"{API}/notifications/push/subscribe", json={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": _b64(auth)},
                                                                         "user_agent": "Edge on Windows"})).status_code == 201
        assert any(d["user_agent"] == "Edge on Windows" for d in (await m.get(f"{API}/notifications/preferences")).json()["devices"])
    async with SessionLocal() as db:
        await notify.deliver_pending(db)
    await _notify("marcus@cirra.demo", "sla", "CASE-1 breached", body="Resolve within the hour", link="/cases")
    async with SessionLocal() as db:
        stats = await notify.deliver_pending(db)
    assert stats.get("push:sent") == 1
    req = got[0]
    assert req.headers["content-encoding"] == "aes128gcm" and req.headers["urgency"] == "high"
    msg = _decrypt(req.content, ua_key, auth)
    assert msg["title"] == "CASE-1 breached" and msg["url"] == "/cases"
    token, key = req.headers["authorization"].removeprefix("vapid t=").split(", k=")
    assert key == public
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.urlsafe_b64decode(key + "=" * (-len(key) % 4)))
    assert jwt.decode(token, pub, algorithms=["ES256"], audience="https://push.example.test")["sub"].startswith("mailto:")
    # the push service says the device is gone: the subscription is removed
    await _notify("marcus@cirra.demo", "sla", "CASE-2 breached")
    async with SessionLocal() as db:
        assert (await notify.deliver_pending(db)).get("push:failed") == 1  # 410 Gone: nothing delivered
        assert (await db.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint))).first() is None


# ---- buying committee and org chart --------------------------------------------------------------------------------
async def _account_with_people(c, name: str) -> tuple[dict, dict]:
    slug = f"{name.lower().replace(' ', '')}{uuid.uuid4().hex[:4]}"
    acc = (await c.post(f"{API}/accounts", json={"name": f"{name} {slug[-4:]}", "domain": f"{slug}.example.com", "force": True})).json()
    people = {}
    for first, title, role in [("Vera", "Chief Financial Officer", "Economic Buyer"), ("Tom", "Director of Operations", "Decision Maker"),
                               ("Ana", "Operations Manager", "Champion"), ("Ben", "IT Analyst", "Evaluator")]:
        people[first] = (await c.post(f"{API}/contacts", json={"account_id": acc["id"], "first_name": first, "last_name": name.split()[0],
                                                               "email": f"{first.lower()}@{slug}.example.com", "job_title": title,
                                                               "buying_role": role})).json()
    return acc, people


async def test_org_chart_reporting_lines_refuse_cycles(client):
    acc, p = await _account_with_people(client, "Kestrel Mills")
    assert (await client.patch(f"{API}/contacts/{p['Tom']['id']}", json={"reports_to_id": p["Vera"]["id"]})).status_code == 200
    r = await client.patch(f"{API}/contacts/{p['Ana']['id']}", json={"reports_to_id": p["Tom"]["id"], "influence": "medium", "stance": "champion"})
    assert r.json()["reports_to_id"] == p["Tom"]["id"] and r.json()["stance"] == "champion"
    cycle = await client.patch(f"{API}/contacts/{p['Vera']['id']}", json={"reports_to_id": p["Ana"]["id"]})
    assert cycle.status_code == 422 and "already reports" in cycle.json()["detail"]
    assert (await client.patch(f"{API}/contacts/{p['Vera']['id']}", json={"reports_to_id": p["Vera"]["id"]})).status_code == 422
    other, q = await _account_with_people(client, "Elsewhere Corp")
    assert (await client.patch(f"{API}/contacts/{p['Ben']['id']}", json={"reports_to_id": q["Vera"]["id"]})).status_code == 422
    chart = (await client.get(f"{API}/accounts/{acc['id']}/org-chart")).json()
    nodes = {n["name"].split()[0]: n for n in chart["nodes"]}
    assert nodes["Ana"]["reports_to_id"] == p["Tom"]["id"] and p["Vera"]["id"] in chart["roots"]
    assert (await client.patch(f"{API}/contacts/{p['Ana']['id']}", json={"stance": "friendly"})).status_code == 422


async def test_buying_committee_coverage_gaps_and_suggestions(client):
    acc, p = await _account_with_people(client, "Lumen Freight")
    await client.patch(f"{API}/contacts/{p['Ana']['id']}", json={"reports_to_id": p["Tom"]["id"]})
    deal = (await client.post(f"{API}/deals", json={"title": "Lumen rollout", "account_id": acc["id"], "amount": 50000})).json()
    detail = (await client.get(f"{API}/deals/{deal['id']}")).json()
    stage2 = next(s for s in detail["stages"] if s["stage_order"] == 2)
    await client.patch(f"{API}/deals/{deal['id']}/stage", json={"stage_id": stage2["id"], "override_gates": True})
    r = await client.put(f"{API}/deals/{deal['id']}/committee", json={"contact_id": p["Ana"]["id"], "role": "Champion", "influence": "medium",
                                                                     "stance": "champion", "is_primary": True})
    com = r.json()
    assert r.status_code == 200 and [m["name"].split()[0] for m in com["members"]] == ["Ana"]
    kinds = {g["kind"] for g in com["gaps"]}
    assert {"missing_role", "single_threaded", "no_power_sponsor"} <= kinds
    assert any(g.get("role") == "Economic Buyer" for g in com["gaps"])
    boss = next(s for s in com["suggestions"] if s["contact_id"] == p["Tom"]["id"])
    assert "one level up" in boss["why"]
    # the deal's committee (not the account's contacts) now drives next best actions
    nba = {a["kind"] for a in (await client.get(f"{API}/deals/{deal['id']}")).json()["next_best_actions"]}
    assert "missing_role" in nba
    await client.put(f"{API}/deals/{deal['id']}/committee", json={"contact_id": p["Vera"]["id"], "role": "Economic Buyer", "influence": "high",
                                                                 "stance": "supporter"})
    com = (await client.put(f"{API}/deals/{deal['id']}/committee", json={"contact_id": p["Tom"]["id"], "role": "Decision Maker",
                                                                        "influence": "high", "stance": "blocker"})).json()
    assert not any(g["kind"] == "missing_role" for g in com["gaps"]) and any(g["kind"] == "blocker" for g in com["gaps"])
    assert com["coverage"] > 50
    assert (await client.put(f"{API}/deals/{deal['id']}/committee", json={"contact_id": p["Ben"]["id"], "role": "Wizard"})).status_code == 422
    other, q = await _account_with_people(client, "Other Party")
    assert (await client.put(f"{API}/deals/{deal['id']}/committee", json={"contact_id": q["Ben"]["id"], "role": "User"})).status_code == 422
    after = (await client.delete(f"{API}/deals/{deal['id']}/committee/{p['Tom']['id']}")).json()
    assert p["Tom"]["id"] not in {m["contact_id"] for m in after["members"]}
    chart = (await client.get(f"{API}/accounts/{acc['id']}/org-chart")).json()
    vera = next(n for n in chart["nodes"] if n["id"] == p["Vera"]["id"])
    assert vera["deal_roles"][0]["role"] == "Economic Buyer"
    async with login_as("sam@cirra.demo") as sdr:  # outside an SDR's scope: not found
        assert (await sdr.get(f"{API}/deals/{deal['id']}/committee")).status_code == 404


# ---- behavioural events and segments -------------------------------------------------------------------------------
async def test_website_tracking_identifies_visitors_and_stitches_history(client):
    acc, p = await _account_with_people(client, "Orbit Tools")
    async with login_as("nina@cirra.demo") as nina:
        cfg = (await nina.put(f"{API}/segments-tracking", json={"enabled": True, "domains": ["orbit.example.com"]})).json()
    key = cfg["site_key"]
    assert key in cfg["snippet"]
    js = (await client.get(f"{API}/public/t.js", params={"k": key})).text
    assert "sendBeacon" in js and key in js
    assert "off" in (await client.get(f"{API}/public/t.js", params={"k": "nope"})).text
    anon = uuid.uuid4().hex
    send = lambda body, origin="https://www.orbit.example.com": client.post(  # noqa: E731
        f"{API}/public/events", content=json.dumps(body), headers={"Content-Type": "text/plain", "Origin": origin})
    assert (await send({"site_key": "wrong", "anonymous_id": anon, "events": []})).status_code == 422
    assert (await send({"site_key": key, "anonymous_id": anon, "events": [{"event": "page_view", "url": "https://x"}]},
                       origin="https://evil.example.net")).status_code == 403
    r = await send({"site_key": key, "anonymous_id": anon, "events": [
        {"event": "page_view", "url": "https://www.orbit.example.com/pricing"}, {"event": "page_view", "url": "https://www.orbit.example.com/docs"},
        {"event": "BAD NAME", "url": "https://x"}]})
    assert r.json() == {"stored": 2, "identified": False}
    r = await send({"site_key": key, "anonymous_id": anon, "events": [{"event": "video_play", "properties": {"video": "demo", "nested": {"x": 1}}}],
                    "identify": {"email": p["Ana"]["email"].upper()}})
    assert r.json()["identified"] is True
    tl = (await client.get(f"{API}/contacts/{p['Ana']['id']}/behavior")).json()
    assert tl["last_30_days"]["page_view"] == 2 and tl["last_30_days"]["video_play"] == 1
    assert tl["top_pages"][0]["views"] == 1 and "nested" not in tl["events"][0]["properties"]
    # a later anonymous visit is attributed to the person without identifying again
    await send({"site_key": key, "anonymous_id": anon, "events": [{"event": "page_view", "url": "https://www.orbit.example.com/pricing"}]})
    assert (await client.get(f"{API}/contacts/{p['Ana']['id']}/behavior")).json()["last_30_days"]["page_view"] == 3
    # server-side product events by email
    async with login_as("admin@cirra.demo") as admin:
        out = (await admin.post(f"{API}/events", json=[{"event": "feature_used", "email": p["Ben"]["email"], "properties": {"feature": "forecast"}},
                                                        {"event": "feature_used", "email": "nobody@nowhere.test"},
                                                        {"event": "Bad Event", "email": p["Ben"]["email"]}])).json()
    assert out["stored"] == 1 and out["unknown_person"] == 1 and out["invalid"][0]["index"] == 2
    async with login_as("sam@cirra.demo") as sdr:
        assert (await sdr.post(f"{API}/events", json=[])).status_code == 403  # SDRs can't write contacts


async def test_segments_combine_attributes_and_behaviour(client):
    acc, p = await _account_with_people(client, "Segment Steel")
    await client.patch(f"{API}/accounts/{acc['id']}", json={"industry": "Manufacturing"})
    async with SessionLocal() as db:
        now = datetime.now(timezone.utc)
        for i in range(3):
            db.add(BehaviorEvent(event="page_view", contact_id=uuid.UUID(p["Vera"]["id"]), account_id=uuid.UUID(acc["id"]),
                                 url="https://site/pricing", occurred_at=now - timedelta(days=i)))
        db.add(BehaviorEvent(event="page_view", contact_id=uuid.UUID(p["Tom"]["id"]), account_id=uuid.UUID(acc["id"]),
                             url="https://site/pricing", occurred_at=now - timedelta(days=90)))
        await db.commit()
    rules = {"match": "all", "conditions": [
        {"type": "field", "field": "account.name", "op": "eq", "value": acc["name"]},
        {"type": "event", "event": "page_view", "url_contains": "/PRICING", "min_count": 2, "within_days": 30}]}
    async with login_as("nina@cirra.demo") as nina:
        prev = (await nina.post(f"{API}/segments/preview", json={"object": "contact", "rules": rules})).json()
        assert prev["count"] == 1 and prev["sample"][0]["id"] == p["Vera"]["id"]
        never = {"match": "all", "conditions": [rules["conditions"][0],
                                                 {"type": "event", "event": "page_view", "min_count": 1, "within_days": 30, "negate": True}]}
        names = {s["name"].split()[0] for s in (await nina.post(f"{API}/segments/preview", json={"object": "contact", "rules": never})).json()["sample"]}
        assert names == {"Tom", "Ana", "Ben"}
        assert (await nina.post(f"{API}/segments/preview", json={"object": "contact", "rules": {"conditions": [
            {"type": "field", "field": "contact.password", "op": "eq", "value": "x"}]}})).status_code == 422
        seg = (await nina.post(f"{API}/segments", json={"name": f"Pricing-page visitors {uuid.uuid4().hex[:4]}", "object": "contact",
                                                        "rules": rules})).json()
        assert seg["member_count"] == 1
        async with SessionLocal() as db:
            db.add(BehaviorEvent(event="page_view", contact_id=uuid.UUID(p["Ana"]["id"]), url="https://site/pricing"))
            db.add(BehaviorEvent(event="page_view", contact_id=uuid.UUID(p["Ana"]["id"]), url="https://site/pricing/enterprise"))
            await db.commit()
        out = (await nina.post(f"{API}/segments/{seg['id']}/refresh")).json()
        assert out["members"] == 2 and out["entered"] == 1
        async with SessionLocal() as db:
            ev = (await db.execute(select(IntegrationEvent).where(IntegrationEvent.event_type == "segment.entered",
                                                                  IntegrationEvent.entity_id == uuid.UUID(seg["id"])))).scalars().all()
            assert [e.payload["record_id"] for e in ev] == [p["Ana"]["id"]]
        camp = (await nina.post(f"{API}/campaigns", json={"name": f"Pricing follow-up {uuid.uuid4().hex[:4]}", "campaign_type": "email"})).json()
        assert (await nina.post(f"{API}/segments/{seg['id']}/add-to-campaign", json={"campaign_id": camp["id"]})).json()["added"] == 2
        lead_seg = (await nina.post(f"{API}/segments/preview", json={"object": "lead", "rules": {"conditions": [
            {"type": "field", "field": "lead.score", "op": "gte", "value": 0}]}})).json()
        assert lead_seg["count"] >= 1
    async with login_as("sam@cirra.demo") as sdr:
        assert (await sdr.post(f"{API}/segments", json={"name": "x", "object": "contact", "rules": rules})).status_code == 403


pytestmark = pytest.mark.asyncio
