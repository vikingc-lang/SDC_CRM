"""Regressions found by the live functional run: AI endpoints honour row-level scope, partners stay out of
internal endpoints, and workflow toggle/update return the rule (they used to 500 on the expired updated_at)."""
from tests.helpers import login_as


async def _foreign_deal(owner_email: str, viewer_email: str) -> dict:
    async with login_as(owner_email) as owner, login_as(viewer_email) as viewer:
        mine = {d["id"] for d in (await viewer.get("/api/v1/deals")).json()}
        return next(d for d in (await owner.get("/api/v1/deals")).json() if d["id"] not in mine)


async def test_briefing_only_shows_what_the_role_may_see(client):
    async with login_as("priya@cirra.demo") as ae:
        visible = {d["id"] for d in (await ae.get("/api/v1/deals")).json()}
        me = (await ae.get("/api/v1/users/me")).json()["id"]
        b = (await ae.get("/api/v1/ai/briefing")).json()
        deal_ids = {p["id"] for p in b["priorities"] if p["kind"] in ("risk", "closing")}
        assert deal_ids <= visible, "briefing leaked another seller's deals"
        tasks = {t["id"]: t for t in (await ae.get("/api/v1/tasks")).json()}
        assert all(p["id"] in tasks for p in b["priorities"] if p["kind"] == "task"), me
    async with login_as("sofia@cirra.demo") as agent:  # no deal access at all
        b = (await agent.get("/api/v1/ai/briefing")).json()
        assert not [p for p in b["priorities"] if p["kind"] in ("risk", "closing")]
    async with login_as("partner@northstar-partners.com") as partner:
        assert (await partner.get("/api/v1/ai/briefing")).status_code == 403
        assert (await partner.get("/api/v1/users")).status_code == 403


async def test_ask_and_draft_email_respect_scope(client):
    other = await _foreign_deal("diego@cirra.demo", "priya@cirra.demo")
    async with login_as("priya@cirra.demo") as ae:
        assert (await ae.post("/api/v1/ai/ask", json={"question": "what are the risks?", "deal_id": other["id"]})).status_code == 404
        assert (await ae.post("/api/v1/ai/ask", json={"question": "who is the champion?", "account_id": (other.get("account") or {}).get("id") or other["account_id"]})).status_code == 404
        assert (await ae.post(f"/api/v1/ai/deals/{other['id']}/draft-email")).status_code == 404
        visible_accounts = {a["id"] for a in (await ae.get("/api/v1/accounts")).json()}
        ans = (await ae.post("/api/v1/ai/ask", json={"question": "Which deals need attention and what is the pipeline?"})).json()
        assert all((s.get("account") or {}).get("id") in visible_accounts for s in ans["sources"] if s.get("account"))
        assert other["title"] not in ans["answer"]


async def test_workflow_toggle_and_update_return_the_rule(client):
    async with login_as("admin@cirra.demo") as admin:
        body = {"name": "Toggle me", "enabled": False, "source": "tasks", "trigger": {"type": "created"}, "conditions": [],
                "actions": [{"type": "notify", "to": ["owner"], "title": "hi"}]}
        rule = (await admin.post("/api/v1/workflows", json=body)).json()
        r = await admin.post(f"/api/v1/workflows/{rule['id']}/toggle")
        assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["updated_at"]
        r = await admin.put(f"/api/v1/workflows/{rule['id']}", json={**body, "name": "Toggle me (renamed)"})
        assert r.status_code == 200 and r.json()["name"] == "Toggle me (renamed)"
        assert (await admin.delete(f"/api/v1/workflows/{rule['id']}")).status_code in (200, 204)
