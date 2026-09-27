"""Developer platform: API keys (scope, read-only, revoke) and signed webhooks with retries."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services import developer
from tests.helpers import login_as

API = "/api/v1/developer"


def test_signatures_round_trip_and_patterns():
    body = b'{"type":"deal.created"}'
    header = developer.sign("whsec_x", int(datetime.now(timezone.utc).timestamp()), body)
    assert developer.verify("whsec_x", header, body)
    assert not developer.verify("whsec_y", header, body)
    assert not developer.verify("whsec_x", header, body + b" ")
    assert developer.clean_patterns(["Deal.*", "deal.*", " lead.created "]) == ["deal.*", "lead.created"]
    with pytest.raises(developer.DeveloperError):
        developer.clean_patterns(["deal/*"])
    with pytest.raises(developer.DeveloperError):
        developer.clean_url("ftp://example.com/hook")


async def _user_id(email: str) -> str:
    async with login_as(email) as c:
        return (await c.get("/api/v1/users/me")).json()["id"]


async def test_api_key_acts_as_its_user_and_respects_scope(client):
    priya = await _user_id("priya@cirra.demo")
    async with login_as("admin@cirra.demo") as admin:
        r = await admin.post(f"{API}/api-keys", json={"name": "Priya BI export", "user_id": priya})
        assert r.status_code == 201 and r.json()["key"].startswith("ck_")
        raw, key_id = r.json()["key"], r.json()["id"]
        listed = next(k for k in (await admin.get(f"{API}/api-keys")).json() if k["id"] == key_id)
        assert listed["prefix"] == raw[:11] and "key" not in listed and listed["state"] == "active"
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        assert (await admin.post(f"{API}/api-keys", json={"name": "Old", "user_id": priya, "expires_at": past})).status_code == 422
    async with login_as("priya@cirra.demo") as ae:
        mine = {d["id"] for d in (await ae.get("/api/v1/deals")).json()}
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        via_header = (await anon.get("/api/v1/deals", headers={"X-API-Key": raw})).json()
        via_bearer = await anon.get("/api/v1/deals", headers={"Authorization": f"Bearer {raw}"})
        assert {d["id"] for d in via_header} == mine and via_bearer.status_code == 200
        assert (await anon.get(f"{API}/api-keys", headers={"X-API-Key": raw})).status_code == 403
        assert (await anon.post("/api/v1/auth/mfa/recovery-codes", json={"code": "123456"}, headers={"X-API-Key": raw})).status_code == 403
        assert (await anon.get("/api/v1/deals", headers={"X-API-Key": "ck_nope"})).status_code == 401
        async with login_as("admin@cirra.demo") as admin:
            assert (await admin.post(f"{API}/api-keys/{key_id}/revoke")).status_code == 200
        assert (await anon.get("/api/v1/deals", headers={"X-API-Key": raw})).status_code == 401


async def test_read_only_key_cannot_write_and_admin_keys_cannot_manage_keys(client):
    admin_id = await _user_id("admin@cirra.demo")
    async with login_as("admin@cirra.demo") as admin:
        ro = (await admin.post(f"{API}/api-keys", json={"name": "Warehouse sync", "user_id": admin_id, "read_only": True})).json()["key"]
        rw = (await admin.post(f"{API}/api-keys", json={"name": "Automation", "user_id": admin_id})).json()["key"]
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/v1/accounts", headers={"X-API-Key": ro})).status_code == 200
        r = await anon.post("/api/v1/accounts", json={"name": "RO Co", "domain": "ro-co.example.com", "force": True}, headers={"X-API-Key": ro})
        assert r.status_code == 403 and "read-only" in r.json()["detail"]
        r = await anon.post("/api/v1/accounts", json={"name": "RW Co", "domain": "rw-co.example.com", "force": True}, headers={"X-API-Key": rw})
        assert r.status_code == 201
        assert (await anon.post(f"{API}/api-keys", json={"name": "Escalate", "user_id": admin_id}, headers={"X-API-Key": rw})).status_code == 403
    async with login_as("priya@cirra.demo") as ae:
        assert (await ae.get(f"{API}/api-keys")).status_code == 403


async def test_webhooks_deliver_signed_matching_events_and_retry(client, monkeypatch):
    received: list[httpx.Request] = []
    failing = {"on": False}

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(500, text="boom") if failing["on"] else httpx.Response(204)

    monkeypatch.setattr(developer, "transport", httpx.MockTransport(handler))
    async with login_as("admin@cirra.demo") as admin:
        assert (await admin.post(f"{API}/webhooks", json={"name": "Bad", "url": "not a url"})).status_code == 422
        r = await admin.post(f"{API}/webhooks", json={"name": "Deals to BI", "url": "https://bi.example.com/hooks/cirra", "event_types": ["deal.*"]})
        assert r.status_code == 201
        sub_id, secret = r.json()["id"], r.json()["secret"]
        ping = (await admin.post(f"{API}/webhooks/{sub_id}/test")).json()
        assert ping["status"] == "success" and received[-1].headers["X-Cirra-Event"] == "ping"
        received.clear()

        acct = (await admin.post("/api/v1/accounts", json={"name": "Hook Test Ltd", "domain": "hooktest.example.com", "force": True})).json()
        pipeline = next(p for p in (await admin.get("/api/v1/pipelines")).json() if p["kind"] == "direct")
        deal = (await admin.post("/api/v1/deals", json={"title": "Hook deal", "account_id": acct["id"], "amount": 1000,
                                                        "pipeline_id": pipeline["id"]})).json()
        run = (await admin.post(f"{API}/webhooks/run")).json()
        assert run["queued"] >= 1 and run["succeeded"] >= 1
        types = [q.headers["X-Cirra-Event"] for q in received]
        assert "deal.created" in types and "account.created" not in types  # pattern filter
        req = next(q for q in received if q.headers["X-Cirra-Event"] == "deal.created")
        assert developer.verify(secret, req.headers["X-Cirra-Signature"], req.content)
        payload = json.loads(req.content)
        assert payload["data"]["deal_id"] == deal["id"] and payload["type"] == "deal.created"

        failing["on"] = True
        await admin.patch(f"/api/v1/deals/{deal['id']}", json={"amount": 2000})
        second = pipeline["stages"][1]["id"]
        await admin.patch(f"/api/v1/deals/{deal['id']}/stage", json={"stage_id": second, "override_gates": True})
        await admin.post(f"{API}/webhooks/run")
        failed = (await admin.get(f"{API}/webhooks/{sub_id}/deliveries", params={"status": "failed"})).json()
        assert failed and failed[0]["event_type"] == "deal.stage_changed" and failed[0]["attempts"] == 1
        assert failed[0]["response_code"] == 500 and failed[0]["next_attempt_at"]
        failing["on"] = False
        retried = (await admin.post(f"{API}/deliveries/{failed[0]['id']}/retry")).json()
        assert retried["status"] == "success" and retried["attempts"] == 2
        assert (await admin.post(f"{API}/deliveries/{failed[0]['id']}/retry")).status_code == 422
        hooks = {h["id"]: h for h in (await admin.get(f"{API}/webhooks")).json()}
        assert hooks[sub_id]["consecutive_failures"] == 0 and hooks[sub_id]["active"]


async def test_failing_endpoint_is_switched_off(client, monkeypatch):
    monkeypatch.setattr(developer, "transport", httpx.MockTransport(lambda r: httpx.Response(503)))
    monkeypatch.setattr(developer, "DISABLE_AFTER", 2)
    async with login_as("admin@cirra.demo") as admin:
        sub_id = (await admin.post(f"{API}/webhooks", json={"name": "Flaky", "url": "https://flaky.example.com/h", "event_types": ["contact.created"]})).json()["id"]
        acct = (await admin.get("/api/v1/accounts")).json()[0]
        for i in range(2):
            r = await admin.post("/api/v1/contacts", json={"account_id": acct["id"], "first_name": "Hook", "last_name": f"Person{i}",
                                                           "email": f"hook.person{i}@example.com"})
            assert r.status_code == 201, r.text
        await admin.post(f"{API}/webhooks/run")
        hook = next(h for h in (await admin.get(f"{API}/webhooks")).json() if h["id"] == sub_id)
        assert hook["active"] is False and "Switched off" in hook["disabled_reason"]
        r = await admin.put(f"{API}/webhooks/{sub_id}", json={"name": "Flaky", "url": "https://flaky.example.com/h",
                                                              "event_types": ["contact.created"], "active": True})
        assert r.json()["active"] and r.json()["consecutive_failures"] == 0
        assert (await admin.delete(f"{API}/webhooks/{sub_id}")).status_code == 204
