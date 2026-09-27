"""Wave 5 operations: quick-log scope, API rate limits, request tracing, security headers and health checks."""
import random
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core import ratelimit
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Contact, Deal
from tests.helpers import login_as

API = "/api/v1"


# ---- quick-log commit: permissions and row-level scope ----------------------------------------------------

async def test_quick_log_commit_stays_inside_the_callers_scope(client):
    async with login_as("diego@cirra.demo") as diego:
        his_deal = (await diego.get(f"{API}/deals")).json()[0]
    async with login_as("priya@cirra.demo") as ae:
        mine = (await ae.get(f"{API}/accounts")).json()[0]
        # an opportunity she can't see is refused when named explicitly...
        r = await ae.post(f"{API}/ai/commit-log", json={"summary": "Call", "account_id": mine["id"], "deal_id": his_deal["id"],
                                                         "deal": {"amount": 1, "title": "x"}})
        assert r.status_code == 404
        # ...and ignored when it's only the AI's suggested match: a new opportunity is opened on her account instead
        r = (await ae.post(f"{API}/ai/commit-log", json={"summary": "Call", "account_id": mine["id"], "matched_deal_id": his_deal["id"],
                                                          "deal": {"amount": 1234, "title": f"Scoped {uuid.uuid4().hex[:4]}"}})).json()
        assert r["deal_id"] and r["deal_id"] != his_deal["id"]
    async with SessionLocal() as db:
        assert float((await db.get(Deal, uuid.UUID(his_deal["id"]))).amount) == float(his_deal["amount"])  # untouched
        other = (await db.execute(select(Contact).where(Contact.account_id != uuid.UUID(mine["id"]), Contact.email.is_not(None),
                                                        Contact.status == "active").limit(1))).scalar_one()
        other_title = other.job_title
    async with login_as("priya@cirra.demo") as ae:
        # a contact email that belongs to another account is not matched (or edited) there
        r = (await ae.post(f"{API}/ai/commit-log", json={"summary": "Met them", "account_id": mine["id"], "create_deal": False,
                                                          "contacts": [{"first_name": "Same", "last_name": "Email", "email": other.email,
                                                                        "job_title": "Changed Title", "buying_role": "Champion"}]})).json()
        assert r["contacts_created"] == 1
    async with SessionLocal() as db:
        assert (await db.get(Contact, other.id)).job_title == other_title
        twin = (await db.execute(select(Contact).where(Contact.account_id == uuid.UUID(mine["id"]), Contact.first_name == "Same",
                                                       Contact.last_name == "Email").order_by(Contact.created_at.desc()).limit(1))).scalar_one()
        assert twin.email is None  # the clashing address isn't copied (emails are unique across the CRM)
    async with login_as("sofia@cirra.demo") as agent:  # support agents read accounts but can't create them
        r = await agent.post(f"{API}/ai/commit-log", json={"summary": "x", "account_name": f"Brand New {uuid.uuid4().hex[:5]} Ltd"})
        assert r.status_code == 403


# ---- rate limits --------------------------------------------------------------------------------------------

def _ip() -> str:
    return f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"


@pytest.fixture
def limited(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 3)
    monkeypatch.setattr(settings, "rate_limit_per_minute", 5)
    monkeypatch.setattr(settings, "rate_limit_api_key_per_minute", 2)
    return settings


async def _client(ip: str) -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 5000)), base_url="http://test")


async def test_sign_in_attempts_are_limited_per_ip(client, limited):
    async with await _client(_ip()) as anon:
        codes = [(await anon.post(f"{API}/auth/login", json={"username": "nobody@cirra.demo", "password": "wrong"})).status_code for _ in range(4)]
        assert codes[:3] == [401, 401, 401] and codes[3] == 429
        r = await anon.post(f"{API}/auth/login", json={"username": "marcus@cirra.demo", "password": "cirra123"})
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and "Too many requests" in r.json()["detail"]
    async with await _client(_ip()) as elsewhere:  # another address has its own budget
        assert (await elsewhere.post(f"{API}/auth/login", json={"username": "marcus@cirra.demo", "password": "cirra123"})).status_code == 200


async def test_signed_in_users_and_api_keys_have_their_own_budgets(client, limited):
    tok = client.headers["Authorization"].split(" ", 1)[1]
    async with await _client(_ip()) as c:
        c.headers["Authorization"] = f"Bearer {tok}"
        seen = []
        for _ in range(6):
            r = await c.get(f"{API}/users/me")
            seen.append((r.status_code, r.headers.get("x-ratelimit-remaining")))
        # the session token's count is shared with any other test using it this minute, so check the shape, not exact numbers
        assert seen[-1][0] == 429 and all(s in (200, 429) for s, _ in seen)
    async with login_as("admin@cirra.demo") as admin:
        users = (await admin.get(f"{API}/users")).json()
        priya = next(u["id"] for u in users if u["email"] == "priya@cirra.demo")
        key = (await admin.post(f"{API}/developer/api-keys", json={"name": f"Limit test {uuid.uuid4().hex[:4]}", "user_id": priya})).json()
    async with await _client(_ip()) as k:
        k.headers["Authorization"] = f"Bearer {key['key']}"
        codes = [(await k.get(f"{API}/accounts")).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
    async with login_as("admin@cirra.demo") as admin:
        await admin.post(f"{API}/developer/api-keys/{key['id']}/revoke")


async def test_limiter_fails_open_and_skips_health_checks(client, limited, monkeypatch):
    async with await _client(_ip()) as anon:
        for _ in range(5):
            assert (await anon.get("/health/live")).status_code == 200  # never counted
    import redis.asyncio as aioredis

    monkeypatch.setattr(ratelimit, "_redis", aioredis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2, socket_timeout=0.2))
    async with await _client(_ip()) as anon:
        codes = [(await anon.post(f"{API}/auth/login", json={"username": "nobody@cirra.demo", "password": "x"})).status_code for _ in range(5)]
        assert 429 not in codes  # Redis down: requests go through rather than the CRM going down
    monkeypatch.setattr(ratelimit, "_redis", None)


def test_paths_are_charged_to_the_right_bucket():
    c = ratelimit.classify
    assert c("/api/v1/auth/login", "")[0] == "auth"
    assert c("/api/v1/t/o/abc.gif", "")[0] == "tracking"
    assert c("/api/v1/public/csat/x", "")[0] == c("/api/v1/sign/x", "")[0] == c("/api/v1/inbound/email", "")[0] == "public"
    assert c("/api/v1/accounts", "Bearer ck_abc")[2].startswith("key:") and c("/api/v1/accounts", "Bearer eyJ")[2].startswith("user:")
    assert c("/api/v1/accounts", "")[2] is None


# ---- tracing, headers and health ------------------------------------------------------------------------------

async def test_request_ids_and_security_headers(client):
    r = await client.get(f"{API}/users/me")
    assert len(r.headers["x-request-id"]) >= 8 and r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    mine = "trace-" + uuid.uuid4().hex[:12]
    assert (await client.get(f"{API}/users/me", headers={"X-Request-ID": mine})).headers["x-request-id"] == mine
    assert (await client.get(f"{API}/users/me", headers={"X-Request-ID": "bad id\nwith newline"})).headers["x-request-id"] != "bad id\nwith newline"
    docs = await client.get("/docs")
    assert "content-security-policy" not in docs.headers  # Swagger UI needs its scripts


async def test_liveness_and_readiness(client):
    assert (await client.get("/health/live")).json() == {"status": "ok"}
    ready = await client.get("/health/ready")
    assert ready.status_code == 200, ready.text
    assert ready.json()["checks"] == {"database": "ok", "migrations": "ok", "redis": "ok"}
