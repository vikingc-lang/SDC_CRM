"""No-code workflows: validation, record triggers, field-change triggers, loop protection, schedules and dry runs."""
from app.services import workflows
from tests.helpers import login_as

API = "/api/v1/workflows"


async def _rule(admin, **over):
    body = {"name": "rule", "enabled": True, "source": "tasks", "trigger": {"type": "created"}, "conditions": [],
            "actions": [{"type": "notify", "to": ["owner"], "title": "hi"}], **over}
    r = await admin.post(API, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def test_rules_are_validated(client):
    async with login_as("admin@cirra.demo") as admin:
        bad = [
            {"trigger": {"type": "updated"}},                                                   # no watched fields
            {"trigger": {"type": "updated", "fields": ["title"]}},                              # not watchable
            {"trigger": {"type": "schedule"}},                                                  # schedule needs conditions
            {"conditions": [{"field": "priority", "op": "gt", "value": 1}]},                    # op does not fit type
            {"actions": []},
            {"actions": [{"type": "update_field", "field": "title", "value": "x"}]},            # not settable
            {"actions": [{"type": "update_field", "field": "priority", "value": "sky-high"}]},  # bad enum
            {"actions": [{"type": "notify", "to": ["role:emperor"], "title": "x"}]},
            {"actions": [{"type": "emit_event", "event": "Bad Name!"}]},
            {"actions": [{"type": "run_sql", "sql": "drop table users"}]},
        ]
        for over in bad:
            body = {"name": "x", "source": "tasks", "trigger": {"type": "created"}, "conditions": [],
                    "actions": [{"type": "notify", "to": ["owner"], "title": "hi"}], **over}
            assert (await admin.post(API, json=body)).status_code == 422, over
    async with login_as("marcus@cirra.demo") as mgr:  # managers can read admin settings, not change them
        assert (await mgr.post(API, json={"name": "x", "source": "tasks", "trigger": {"type": "created"}, "actions": []})).status_code == 403


async def test_created_trigger_runs_actions_after_commit(client):
    async with login_as("admin@cirra.demo") as admin:
        rule = await _rule(admin, name="Web-form lead follow-up", source="leads",
                           conditions=[{"field": "source", "op": "eq", "value": "web_form"}],
                           actions=[{"type": "create_task", "title": "Call {{name}} at {{company}}", "due_in_days": 1, "priority": "high", "assign_to": "owner"},
                                    {"type": "notify", "to": ["role:sales_manager"], "title": "New web lead: {{company}}"},
                                    {"type": "emit_event", "event": "lead.hot"}])
        try:
            async with login_as("sam@cirra.demo") as sdr:
                r = await sdr.post("/api/v1/leads", json={"first_name": "Wanda", "last_name": "Flow", "email": "wanda@flowtest-co.com",
                                                          "company_name": "Flowtest Co", "source": "web_form"})
                assert r.status_code == 201, r.text
                other = await sdr.post("/api/v1/leads", json={"first_name": "Otto", "last_name": "Bound", "email": "otto@outbound-co.com",
                                                              "company_name": "Outbound Co", "source": "outbound"})
                assert other.status_code == 201
                await workflows.drain()
                tasks = (await sdr.get("/api/v1/tasks")).json()
                assert any(t["title"] == "Call Wanda Flow at Flowtest Co" for t in tasks)
                assert not any("Otto" in t["title"] for t in tasks)  # the condition filtered out the outbound lead
            runs = (await admin.get(f"{API}/{rule['id']}/runs")).json()
            assert len(runs) == 1 and runs[0]["status"] == "done" and runs[0]["record"] == "Wanda Flow"
            assert [d["action"] for d in runs[0]["detail"]] == ["create_task", "notify", "emit_event"]
            notes = (await client.get("/api/v1/notifications")).json()  # client = Marcus, a sales manager
            items = notes["items"] if isinstance(notes, dict) else notes
            assert any(n["title"] == "New web lead: Flowtest Co" for n in items)
        finally:
            await admin.delete(f"{API}/{rule['id']}")


async def test_field_change_trigger_and_loop_protection(client):
    async with login_as("admin@cirra.demo") as admin:
        # two rules that would ping-pong a task's priority forever
        a = await _rule(admin, name="urgent->high", trigger={"type": "updated", "fields": ["priority"]},
                        conditions=[{"field": "priority", "op": "eq", "value": "urgent"}],
                        actions=[{"type": "update_field", "field": "priority", "value": "high"}])
        b = await _rule(admin, name="high->urgent", trigger={"type": "updated", "fields": ["priority"]},
                        conditions=[{"field": "priority", "op": "eq", "value": "high"}],
                        actions=[{"type": "update_field", "field": "priority", "value": "urgent"}])
        try:
            task = (await admin.post("/api/v1/tasks", json={"title": "Ping-pong"})).json()
            await admin.patch(f"/api/v1/tasks/{task['id']}", json={"title": "Ping-pong (renamed)"})  # unwatched field: nothing fires
            await workflows.drain()
            assert (await admin.get(f"{API}/{a['id']}/runs")).json() == []
            await admin.patch(f"/api/v1/tasks/{task['id']}", json={"priority": "urgent"})
            await workflows.drain()
            ra, rb = (await admin.get(f"{API}/{a['id']}/runs")).json(), (await admin.get(f"{API}/{b['id']}/runs")).json()
            assert 1 <= len(ra) + len(rb) <= workflows.MAX_DEPTH  # the chain stops instead of looping
        finally:
            await admin.delete(f"{API}/{a['id']}")
            await admin.delete(f"{API}/{b['id']}")


async def test_disabled_rule_never_fires(client):
    async with login_as("admin@cirra.demo") as admin:
        rule = await _rule(admin, name="off", enabled=False)
        await admin.post("/api/v1/tasks", json={"title": "Should not trigger"})
        await workflows.drain()
        assert (await admin.get(f"{API}/{rule['id']}/runs")).json() == []
        await admin.delete(f"{API}/{rule['id']}")


async def test_schedule_acts_once_per_record_and_dry_run_changes_nothing(client):
    async with login_as("admin@cirra.demo") as admin:
        rule = await _rule(admin, name="Risky deals", source="deals", trigger={"type": "schedule"},
                           conditions=[{"field": "status", "op": "eq", "value": "Open"}, {"field": "risk_score", "op": "gte", "value": 60}],
                           actions=[{"type": "create_task", "title": "Rescue plan for {{title}}", "assign_to": "manager", "due_in_days": 2}])
        try:
            dry = (await admin.post(f"{API}/{rule['id']}/test", json={})).json()
            assert dry["matching_count"] >= 1 and dry["sample"][0]["results"][0]["ok"]
            assert (await admin.get(f"{API}/{rule['id']}/runs")).json() == []  # a dry run records nothing
            first = (await admin.post("/api/v1/admin/jobs/workflows")).json()
            assert first["actions_run"] == dry["matching_count"]
            second = (await admin.post("/api/v1/admin/jobs/workflows")).json()
            assert second["actions_run"] == 0  # once per record unless a repeat interval is set
            titles = [t["title"] for t in (await admin.get("/api/v1/tasks")).json()]
            assert sum(t.startswith("Rescue plan for ") for t in titles) == dry["matching_count"]
        finally:
            await admin.delete(f"{API}/{rule['id']}")
