import pytest


async def test_login_rejects_bad_password(client):
    resp = await client.post("/api/v1/auth/login", json={"username": "marcus@relate.demo", "password": "nope"})
    assert resp.status_code == 401


async def test_requires_auth(seeded):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/v1/accounts")).status_code == 401


async def test_accounts_and_360(client):
    accounts = [a for a in (await client.get("/api/v1/accounts", params={"search": "apex"})).json() if a["name"] == "Apex Industrial Supply"]
    assert accounts
    assert 0 <= accounts[0]["health"] <= 100
    view = (await client.get(f"/api/v1/accounts/{accounts[0]['id']}/360")).json()
    assert {"account", "contacts", "deals", "recent_activities"} <= view.keys()
    deal = view["deals"][0]
    assert deal["stage"] == "Proposal/InfoSec"
    # the approved quote set the deal's amount to its TCV
    assert deal["weighted_value"] == pytest.approx(deal["amount"] * 0.75 * (1 - deal["risk_score"] / 200))
    assert view["account"]["customer_master"] and view["account"]["annual_revenue"] == 420_000_000
    assert {"contracts", "finance", "onboarding", "usage", "alerts"} <= view.keys()


async def test_kanban_and_stage_gates(client):
    pipelines = (await client.get("/api/v1/pipelines")).json()
    assert [p["kind"] for p in pipelines] == ["direct", "inbound", "renewal", "partner"]
    pipeline = pipelines[0]
    board = (await client.get(f"/api/v1/pipeline/{pipeline['id']}/kanban")).json()
    names = [c["name"] for c in board["columns"]]
    assert names == ["Discovery", "Pain Fit", "Solution Demo", "Proposal/InfoSec", "Closed-Won", "Closed-Lost"]
    cobalt = next(d for c in board["columns"] for d in c["deals"] if d["title"] == "Omnichannel Promotions Engine")
    stage = {c["name"]: c["id"] for c in board["columns"]}

    # forward move with unmet criteria is blocked with a gate checklist
    resp = await client.patch(f"/api/v1/deals/{cobalt['id']}/stage", json={"stage_id": stage["Proposal/InfoSec"]})
    assert resp.status_code == 409 and resp.json()["gates"]
    # closing lost without a taxonomy reason + debrief is always blocked, even with override
    resp = await client.patch(f"/api/v1/deals/{cobalt['id']}/stage", json={"stage_id": stage["Closed-Lost"], "override_gates": True})
    assert resp.status_code == 409 and resp.json()["loss_taxonomy"]["champion_departed"]
    resp = await client.patch(f"/api/v1/deals/{cobalt['id']}/stage", json={"stage_id": stage["Closed-Lost"], "loss_reason": "budget_frozen", "loss_debrief": "short"})
    assert resp.status_code == 409
    # conscious override succeeds and reports a forecast delta + AI action
    resp = await client.patch(f"/api/v1/deals/{cobalt['id']}/stage", json={"stage_id": stage["Pain Fit"], "override_gates": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deal"]["stage"] == "Pain Fit" and body["forecast_delta"] > 0
    assert "competitor" in body["triggered_action"].lower()
    history = (await client.get(f"/api/v1/deals/{cobalt['id']}")).json()["history"]
    assert history[-1]["to"] == "Pain Fit" and history[-1]["gate_overridden"]


async def test_quick_log_preview_then_commit(client):
    notes = (
        "Call with Dana Whitfield, VP Operations at Lumen Robotics (lumenrobotics.com). Dana is the decision maker "
        "and is excited about a warehouse automation platform, budget about $150k, decision by end of Q4. "
        "Next steps: send case studies by Friday; schedule a technical demo next week."
    )
    preview = (await client.post("/api/v1/ai/quick-log", json={"raw_text": notes})).json()
    assert preview["account_name"] == "Lumen Robotics" and preview["matched_account_id"] is None
    assert preview["contacts"][0]["buying_role"] == "Decision Maker"
    assert preview["deal"]["amount"] == 150000

    commit = await client.post("/api/v1/ai/commit-log", json={**preview, "raw_text": notes})
    assert commit.status_code == 200, commit.text
    out = commit.json()
    assert out["status"] == "committed" and out["deal_id"] and out["tasks_created"] == 2

    # logging again for the same account matches the existing account and deal
    again = (await client.post("/api/v1/ai/quick-log", json={"raw_text": "Follow-up email to Dana at Lumen Robotics about the warehouse automation platform."})).json()
    assert again["matched_account_id"] == out["account_id"]
    view = (await client.get(f"/api/v1/accounts/{out['account_id']}/360")).json()
    assert view["account"]["health_score"] > 0 and len(view["tasks"]) == 2


async def test_semantic_search_and_copilot(client):
    results = (await client.post("/api/v1/search/semantic", json={"query": "security questionnaire soc2", "limit": 5})).json()
    assert results and results[0]["score"] > 0 and results[0]["matched_by"]
    assert any("SOC 2" in r["summary"] or "security" in r["summary"].lower() for r in results[:3])
    answer = (await client.post("/api/v1/ai/ask", json={"question": "Which deals are at risk?"})).json()
    assert "Fleet Telemetry Rollout" in answer["answer"]


async def test_dashboard_and_briefing(client):
    summary = (await client.get("/api/v1/dashboard/summary")).json()
    assert summary["weighted_pipeline"] <= summary["total_pipeline"]
    assert summary["by_stage"]
    brief = (await client.get("/api/v1/ai/briefing")).json()
    assert brief["headline"] and brief["priorities"]


async def test_auditor_reads_and_exports_but_cannot_write(client):
    from tests.helpers import login_as

    async with login_as("viewer@relate.demo") as c:
        assert (await c.get("/api/v1/accounts")).status_code == 200
        assert (await c.post("/api/v1/tasks", json={"title": "x"})).status_code == 403
        assert (await c.get("/api/v1/admin/export/accounts")).status_code == 200
        assert (await c.get("/api/v1/admin/audit")).status_code == 200
