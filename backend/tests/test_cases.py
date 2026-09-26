"""Customer service: case lifecycle, routing, SLA clocks and breaches, conversations, knowledge base, CSAT and access."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.models import SupportTicket
from tests.helpers import login_as

API = "/api/v1"


async def _account(c, name="Apex Industrial Supply"):
    return next(a for a in (await c.get(f"{API}/accounts", params={"search": name})).json() if a["name"] == name)


async def test_seeded_service_desk(client):
    async with login_as("sofia@cirra.demo") as agent:
        me = (await agent.get(f"{API}/users/me")).json()
        assert me["role"] == "support_agent"
        meta = (await agent.get(f"{API}/cases/meta")).json()
        assert {q["name"] for q in meta["queues"]} >= {"Customer Support", "Billing"}
        cases = (await agent.get(f"{API}/cases", params={"view": "all"})).json()
        assert cases and all(c["case_number"].startswith("CS-") for c in cases)
        assert (await agent.get(f"{API}/deals")).status_code == 403  # agents work cases, not pipeline
        kb = (await agent.get(f"{API}/knowledge", params={"search": "sso login"})).json()
        assert kb["articles"][0]["title"] == "Fix an SSO login loop"


async def test_case_lifecycle_sla_and_csat(client):
    acct = await _account(client)
    async with login_as("sofia@cirra.demo") as agent:
        agent_id = (await agent.get(f"{API}/users/me")).json()["id"]
        r = await agent.post(f"{API}/cases", json={"account_id": acct["id"], "subject": "SSO login loop after IdP change",
                                                   "description": "Users bounce back to the sign-in page.", "severity": "critical", "channel": "phone"})
        assert r.status_code == 201, r.text
        cid = r.json()["id"]
        case = (await agent.get(f"{API}/cases/{cid}")).json()
        assert case["owner_id"] == agent_id  # auto-assigned by the default queue
        assert case["clocks"]["first_response"]["state"] == "running"
        opened = datetime.fromisoformat(case["opened_at"])
        due = datetime.fromisoformat(case["clocks"]["first_response"]["due_at"])
        assert abs((due - opened) - timedelta(hours=1)) < timedelta(minutes=1)  # critical: 1 hour to respond
        assert any(a["title"] == "Fix an SSO login loop" for a in case["suggested_articles"])

        await agent.post(f"{API}/cases/{cid}/comments", json={"body": "Checking the IdP logs.", "internal": True})
        c2 = (await agent.get(f"{API}/cases/{cid}")).json()
        assert c2["clocks"]["first_response"]["state"] == "running"  # internal notes don't stop the clock
        await agent.post(f"{API}/cases/{cid}/comments", json={"body": "We've found the cause; fix going out today.", "internal": False})
        c3 = (await agent.get(f"{API}/cases/{cid}")).json()
        assert c3["clocks"]["first_response"]["state"] == "met" and c3["status"] == "pending"

        await agent.patch(f"{API}/cases/{cid}", json={"status": "resolved"})
        c4 = (await agent.get(f"{API}/cases/{cid}")).json()
        assert c4["clocks"]["resolution"]["state"] == "met" and c4["csat"]["survey_url"]
        token = c4["csat"]["survey_url"].rsplit("/", 1)[1]

    async with login_as("sam@cirra.demo") as anyone:  # the survey is public: no auth needed
        anyone.headers.pop("Authorization")
        info = (await anyone.get(f"{API}/public/csat/{token}")).json()
        assert info["case_number"] == c4["case_number"] and not info["rated"]
        assert (await anyone.post(f"{API}/public/csat/{token}", json={"score": 5, "comment": "Fast fix"})).status_code == 200
        assert (await anyone.post(f"{API}/public/csat/{token}", json={"score": 1})).status_code == 409  # once only
        assert (await anyone.get(f"{API}/public/csat/not-a-token")).status_code == 404
    rep = (await client.post(f"{API}/analytics/run", json={"definition": {"source": "cases", "columns": ["case_number", "csat"],
                                                                         "filters": [{"field": "csat", "op": "eq", "value": 5}]}})).json()
    assert c4["case_number"] in [r[0] for r in rep["rows"]]


async def test_breach_scan_flags_once_and_notifies(client):
    from app.core.database import SessionLocal

    acct = await _account(client)
    async with login_as("sofia@cirra.demo") as agent:
        cid = (await agent.post(f"{API}/cases", json={"account_id": acct["id"], "subject": "Invoices missing PO numbers", "severity": "high"})).json()["id"]
    async with SessionLocal() as db:  # pretend the case was opened a day ago
        past = datetime.now(timezone.utc) - timedelta(days=1)
        await db.execute(update(SupportTicket).where(SupportTicket.id == cid).values(
            opened_at=past, first_response_due_at=past + timedelta(hours=4), resolve_due_at=past + timedelta(hours=24) - timedelta(minutes=5)))
        await db.commit()
    async with login_as("admin@cirra.demo") as admin:
        first = (await admin.post(f"{API}/admin/jobs/case_sla")).json()
        second = (await admin.post(f"{API}/admin/jobs/case_sla")).json()
        assert first["newly_flagged"] >= 1 and second["newly_flagged"] == 0
    async with login_as("sofia@cirra.demo") as agent:
        c = (await agent.get(f"{API}/cases/{cid}")).json()
        assert c["sla_breached"] and c["clocks"]["first_response"]["state"] == "breached"
        breached = [x["id"] for x in (await agent.get(f"{API}/cases", params={"view": "breached"})).json()]
        assert cid in breached
        notes = (await agent.get(f"{API}/notifications")).json()
        items = notes["items"] if isinstance(notes, dict) else notes
        assert any("SLA breached" in n["title"] and "Invoices missing PO numbers" in n["title"] for n in items)


async def test_case_access_rules(client):
    apex = await _account(client)  # owned by Marcus
    async with login_as("sofia@cirra.demo") as agent:
        cid = (await agent.post(f"{API}/cases", json={"account_id": apex["id"], "subject": "Portal outage", "severity": "low"})).json()["id"]
        diego_id = next(u["id"] for u in (await agent.get(f"{API}/users")).json() if u["email"] == "diego@cirra.demo")
        # AEs can work cases but only through accounts they own; assignment needs someone who can work cases
        assert (await agent.patch(f"{API}/cases/{cid}", json={"owner_id": diego_id})).status_code == 200
    async with login_as("priya@cirra.demo") as ae:  # Priya doesn't own Apex
        assert (await ae.get(f"{API}/cases/{cid}")).status_code == 404
    async with login_as("sam@cirra.demo") as sdr:
        assert (await sdr.post(f"{API}/cases", json={"account_id": apex["id"], "subject": "x!", "severity": "low"})).status_code == 403
        assert (await sdr.post(f"{API}/knowledge", json={"title": "Draft", "body": "x"})).status_code == 403
    async with login_as("sofia@cirra.demo") as agent:
        auditor_id = next(u["id"] for u in (await client.get(f"{API}/admin/users")).json() if u["email"] == "viewer@cirra.demo") \
            if (await client.get(f"{API}/admin/users")).status_code == 200 else None
        if auditor_id:  # auditors can read but not work cases
            assert (await agent.patch(f"{API}/cases/{cid}", json={"owner_id": auditor_id})).status_code == 422
