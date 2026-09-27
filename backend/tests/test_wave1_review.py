"""Regression tests for the Wave 1 code review."""
import uuid

import httpx

from app.core.database import SessionLocal
from app.models import CustomFieldDefinition, Deal, WorkflowRun
from app.services import reporting, workflows
from sqlalchemy import select
from tests.helpers import login_as

API = "/api/v1"


async def test_lead_upsert_overwrites_sent_fields_and_keeps_the_rest(client):
    await client.put(f"{API}/upsert/leads/LEAD-OW-1", json={"email": "jon.ow@overwrite.example.com", "first_name": "Jon", "last_name": "Ow",
                                                            "phone": "111", "company_name": "Overwrite Co"})
    r = (await client.put(f"{API}/upsert/leads/LEAD-OW-1", json={"first_name": "John", "phone": "222"})).json()
    assert r["status"] == "updated"
    lead = (await client.get(f"{API}/leads/{r['id']}")).json()
    assert lead["first_name"] == "John" and lead["phone"] == "222" and lead["company_name"] == "Overwrite Co"


async def test_lead_upsert_by_email_cannot_reach_another_reps_lead(client):
    users = (await client.get(f"{API}/users")).json()
    diego = next(u["id"] for u in users if u["email"] == "diego@cirra.demo")
    created = (await client.post(f"{API}/leads", json={"first_name": "Dee", "last_name": "Owned", "email": "dee.owned@diegos.example.com",
                                                       "company_name": "Diego Co", "owner_id": diego})).json()
    async with login_as("priya@cirra.demo") as ae:
        r = await ae.put(f"{API}/upsert/leads/PRIYA-NEW-1", json={"email": "dee.owned@diegos.example.com", "first_name": "Hijack"})
        assert r.status_code == 404
    assert (await client.get(f"{API}/leads/{created['id']}")).json()["first_name"] == "Dee"


async def test_records_linked_to_another_systems_id_are_not_rekeyed(client):
    assert (await client.put(f"{API}/upsert/accounts/SF-1", json={"name": "Rekey Co", "domain": "rekey.example.com"})).status_code == 200
    r = await client.put(f"{API}/upsert/accounts/ERP-9", json={"name": "Rekey Co", "domain": "rekey.example.com"})
    assert r.status_code == 422 and "SF-1" in r.json()["detail"]


async def test_bad_custom_values_read_as_empty_instead_of_failing(client):
    deal = (await client.get(f"{API}/deals"))
    d = deal.json()[0]
    await client.patch(f"{API}/deals/{d['id']}", json={"custom_fields": {"late_num": "large", "late_flag": "maybe", "late_day": "soon"}})
    async with login_as("admin@cirra.demo") as admin:  # only admins define fields
        for key, typ in (("late_num", "number"), ("late_flag", "boolean"), ("late_day", "date")):
            assert (await admin.post(f"{API}/admin/custom-fields", json={"entity": "deal", "key": key, "label": key, "field_type": typ})).status_code in (201, 409)
    res = await client.post(f"{API}/analytics/run", json={"definition": {"source": "deals", "columns": ["title", "cf_late_num", "cf_late_flag", "cf_late_day"],
                                                                         "filters": [{"field": "cf_late_num", "op": "is_empty"}]}})
    assert res.status_code == 200, res.text
    grouped = (await client.post(f"{API}/analytics/run", json={"definition": {"source": "deals", "group_by": [{"field": "cf_late_flag"}],
                                                                              "measures": [{"agg": "count"}]}})).json()
    drill = await client.post(f"{API}/analytics/drill", json={"definition": {"source": "deals", "group_by": [{"field": "cf_late_flag"}],
                                                                             "measures": [{"agg": "count"}]}, "values": [None]})
    assert drill.status_code == 200 and drill.json()["row_count"] == next(r[1] for r in grouped["rows"] if r[0] is None)
    async with SessionLocal() as db:  # workflows read every field of the record: must not fail on the bad values
        vals = await reporting.record_values(db, "deals", uuid.UUID(d["id"]))
    assert vals["cf_late_num"] == ""


def test_placeholders_accept_digits():
    assert workflows.render("{{cf_q3_target}} reached", {"cf_q3_target": "90"}) == "90 reached"


async def test_catalogue_follows_definitions_without_an_explicit_invalidate(client):
    await client.get(f"{API}/analytics/sources")  # warm the catalogue on this "replica"
    async with SessionLocal() as db:  # another replica creates a field: this process is never told
        db.add(CustomFieldDefinition(entity="account", key="replica_field", label="Replica field", field_type="text", options=[]))
        await db.commit()
    cat = (await client.get(f"{API}/analytics/sources")).json()
    assert any(f["key"] == "cf_replica_field" for s in cat["sources"] if s["key"] == "accounts" for f in s["fields"])


async def test_outbound_actions_run_after_commit_retry_and_never_log_bodies(client, monkeypatch):
    calls = []

    def handler(r):
        calls.append(str(r.url))
        if "retry" in str(r.url) and calls.count(str(r.url)) == 1:
            return httpx.Response(503, text="try later")
        return httpx.Response(500, text="secret-internal-stacktrace") if "broken" in str(r.url) else httpx.Response(202)

    monkeypatch.setattr(workflows, "http_transport", httpx.MockTransport(handler))
    async with login_as("admin@cirra.demo") as admin:
        for bad in ("http://127.0.0.1:8080/x", "http://169.254.169.254/latest/meta-data", "http://localhost/x"):
            r = await admin.post(f"{API}/workflows", json={"name": "ssrf", "source": "tasks", "trigger": {"type": "created"},
                                                            "actions": [{"type": "http_request", "url": bad}]})
            assert r.status_code == 422, bad
        rule = (await admin.post(f"{API}/workflows", json={
            "name": "After commit", "enabled": True, "source": "tasks", "trigger": {"type": "created"},
            "conditions": [{"field": "title", "op": "contains", "value": "AFTERCOMMIT"}],
            "actions": [{"type": "http_request", "url": "https://retry.example.com/h"}, {"type": "http_request", "url": "https://broken.example.com/h"}]})).json()
        await admin.post(f"{API}/tasks", json={"title": "AFTERCOMMIT check"})
        await workflows.drain()
        async with SessionLocal() as db:
            run = (await db.execute(select(WorkflowRun).where(WorkflowRun.rule_id == uuid.UUID(rule["id"])))).scalars().one()
        assert calls.count("https://retry.example.com/h") == 2 and run.detail[0]["ok"] is True
        assert run.detail[1]["ok"] is False and "secret" not in str(run.detail) and run.status == "failed"
        await admin.post(f"{API}/workflows/{rule['id']}/toggle")


async def test_bulk_set_queue_with_a_malformed_id_is_422(client):
    async with login_as("sofia@cirra.demo") as agent:
        cases = (await agent.get(f"{API}/cases")).json()
        r = await agent.post(f"{API}/bulk/cases", json={"ids": [cases[0]["id"]], "action": "set_queue", "value": "tier-2"})
        assert r.status_code == 422
    _ = Deal
