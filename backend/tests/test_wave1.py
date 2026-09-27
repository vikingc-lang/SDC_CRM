"""Wave 1 maturity pack: custom fields in reports, dashboard filters, drill-down, webhook / Slack / Teams workflow
actions, integration upsert by external id, and bulk actions on list selections."""
import json
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import SupportTicket, WorkflowRun
from app.services import workflows
from tests.helpers import login_as

API = "/api/v1"


async def test_custom_fields_are_reportable_and_usable_in_workflows(client):
    async with login_as("admin@cirra.demo") as admin:
        for body in ({"entity": "deal", "key": "margin_pct", "label": "Margin %", "field_type": "number"},
                     {"entity": "deal", "key": "deal_source_tool", "label": "Source tool", "field_type": "select", "options": ["Web", "Event", "Referral"]}):
            assert (await admin.post(f"{API}/admin/custom-fields", json=body)).status_code in (201, 409)
        deals = (await admin.get(f"{API}/deals")).json()[:3]
        for i, d in enumerate(deals):
            r = await admin.patch(f"{API}/deals/{d['id']}", json={"custom_fields": {"margin_pct": 20 + i * 10, "deal_source_tool": ["Web", "Event", "Web"][i]}})
            assert r.status_code == 200, r.text
        cat = (await admin.get(f"{API}/analytics/sources")).json()
        fields = {f["key"]: f for f in next(s for s in cat["sources"] if s["key"] == "deals")["fields"]}
        assert fields["cf_margin_pct"]["type"] == "number" and fields["cf_deal_source_tool"]["options"] == ["Web", "Event", "Referral"]
        res = (await admin.post(f"{API}/analytics/run", json={"definition": {
            "source": "deals", "group_by": [{"field": "cf_deal_source_tool"}], "measures": [{"agg": "count"}, {"agg": "avg", "field": "cf_margin_pct"}],
            "filters": [{"field": "cf_margin_pct", "op": "gte", "value": 20}]}})).json()
        by = {r[0]: r for r in res["rows"]}
        assert by["Web"][1] == 2 and by["Web"][2] == pytest.approx(30) and by["Event"][1] == 1
        rule = await admin.post(f"{API}/workflows", json={"name": "High margin", "enabled": False, "source": "deals", "trigger": {"type": "created"},
                                                           "conditions": [{"field": "cf_margin_pct", "op": "gte", "value": 40}],
                                                           "actions": [{"type": "notify", "to": ["owner"], "title": "High margin"}]})
        assert rule.status_code == 201, rule.text


async def test_dashboard_filters_apply_per_tile_and_report_what_did_not_apply(client):
    mgr_reports = [{"source": "deals", "measures": [{"agg": "count"}]}, {"source": "orders", "measures": [{"agg": "count"}]}]
    ids = []
    for d in mgr_reports:
        ids.append((await client.post(f"{API}/analytics/reports", json={"name": f"tile {d['source']}", "definition": d})).json()["id"])
    everything = (await client.post(f"{API}/analytics/reports/{ids[0]}/run")).json()["rows"][0][0]
    priya = (await client.post(f"{API}/analytics/reports/{ids[0]}/run", json={"owner": "Priya Raman"})).json()
    assert priya["rows"][0][0] < everything and priya["skipped_filters"] == []
    orders = (await client.post(f"{API}/analytics/reports/{ids[1]}/run", json={"owner": "Priya Raman", "period": "this_year"})).json()
    assert orders["skipped_filters"] == ["owner"]
    assert (await client.post(f"{API}/analytics/reports/{ids[0]}/run", json={"period": "someday"})).status_code == 422


async def test_drill_down_returns_the_records_behind_a_cell(client):
    defn = {"source": "deals", "group_by": [{"field": "status"}], "measures": [{"agg": "count"}]}
    summary = (await client.post(f"{API}/analytics/run", json={"definition": defn})).json()
    won = next(r for r in summary["rows"] if r[0] == "Won")
    drill = (await client.post(f"{API}/analytics/drill", json={"definition": defn, "values": ["Won"]})).json()
    assert drill["row_count"] == won[1] and len(drill["ids"]) == won[1] and drill["link"] == "/deals/{id}"
    monthly = {"source": "deals", "group_by": [{"field": "created", "bucket": "month"}], "measures": [{"agg": "count"}]}
    first = (await client.post(f"{API}/analytics/run", json={"definition": monthly})).json()["rows"][0]
    cell = (await client.post(f"{API}/analytics/drill", json={"definition": monthly, "values": [first[0]]})).json()
    assert cell["row_count"] == first[1]
    assert (await client.post(f"{API}/analytics/drill", json={"definition": {}, "values": []})).status_code == 422


async def test_webhook_and_chat_actions_post_and_log(client, monkeypatch):
    seen = []
    monkeypatch.setattr(workflows, "http_transport", httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200 if "slack" in str(r.url) else 202)))
    async with login_as("admin@cirra.demo") as admin:
        bad = await admin.post(f"{API}/workflows", json={"name": "x", "source": "tasks", "trigger": {"type": "created"},
                                                          "actions": [{"type": "post_message", "channel": "slack", "webhook_url": "http://insecure", "text": "x"}]})
        assert bad.status_code == 422
        rule = (await admin.post(f"{API}/workflows", json={
            "name": "Tell ops", "enabled": True, "source": "tasks", "trigger": {"type": "created"},
            "conditions": [{"field": "title", "op": "contains", "value": "OPS-HOOK"}],
            "actions": [{"type": "http_request", "url": "https://erp.example.com/hooks/cirra"},
                        {"type": "post_message", "channel": "slack", "webhook_url": "https://hooks.slack.com/services/T/B/X", "text": "New task: {{title}}"},
                        {"type": "post_message", "channel": "teams", "webhook_url": "https://example.webhook.office.com/x", "text": "Task {{title}}"}]})).json()
        preview = (await admin.post(f"{API}/workflows/{rule['id']}/test", json={})).json()
        assert not seen  # a test run never calls out
        await admin.post(f"{API}/tasks", json={"title": "OPS-HOOK reconcile"})
        await workflows.drain()
        by_url = {str(r.url): json.loads(r.content) for r in seen}  # sent concurrently after the commit: order varies
        assert set(by_url) == {"https://erp.example.com/hooks/cirra", "https://hooks.slack.com/services/T/B/X", "https://example.webhook.office.com/x"}
        assert by_url["https://erp.example.com/hooks/cirra"]["record"]["title"] == "OPS-HOOK reconcile"
        assert by_url["https://hooks.slack.com/services/T/B/X"] == {"text": "New task: OPS-HOOK reconcile"}
        async with SessionLocal() as db:
            run = (await db.execute(select(WorkflowRun).where(WorkflowRun.rule_id == uuid.UUID(rule["id"])))).scalars().one()
        assert run.status == "done" and "HTTP 202" in run.detail[0]["detail"]
        await admin.post(f"{API}/workflows/{rule['id']}/toggle")
        _ = preview


async def test_upsert_by_external_id_is_idempotent_and_scoped(client):
    r1 = await client.put(f"{API}/upsert/accounts/ERP-9001", json={"name": "Upsert Industries", "domain": "upsert-industries.example.com",
                                                                     "country": "Germany", "tier": "Enterprise"})
    assert r1.status_code == 200 and r1.json()["status"] == "created"
    r2 = await client.put(f"{API}/upsert/accounts/ERP-9001", json={"name": "Upsert Industries AG", "employee_count": 4200})
    assert r2.json() == {**r1.json(), "status": "updated"}
    c = (await client.put(f"{API}/upsert/contacts/CRM-C-1", json={"first_name": "Ute", "last_name": "Sync", "email": "ute.sync@upsert-industries.example.com",
                                                                  "account_external_id": "ERP-9001", "buying_role": "Champion"})).json()
    assert c["status"] == "created"
    d = await client.put(f"{API}/upsert/deals/OPP-77", json={"title": "Upsert rollout", "account_external_id": "ERP-9001", "amount": 50000,
                                                             "currency": "eur", "stage": "Pain Fit", "target_close_date": "2026-12-15"})
    assert d.status_code == 200, d.text
    moved = await client.put(f"{API}/upsert/deals/OPP-77", json={"stage": "Closed-Won"})
    assert moved.status_code == 422 and "stage" in moved.json()["detail"]
    batch = (await client.post(f"{API}/upsert/leads", json={"records": [
        {"external_id": "MKT-1", "email": "first.lead@batchco.example.com", "last_name": "Lead", "company_name": "BatchCo"},
        {"external_id": "MKT-2"},  # no email or name: rejected on its own
        {"external_id": "MKT-1", "email": "first.lead@batchco.example.com", "job_title": "CFO"}]})).json()
    assert [x["status"] for x in batch["results"]] == ["created", "error", "updated"] and batch["error"] == 1
    detail = (await client.get(f"{API}/accounts/{r1.json()['id']}/360")).json()["account"]
    assert detail["name"] == "Upsert Industries AG" and detail["employee_count"] == 4200 and detail["territory"]
    async with login_as("diego@cirra.demo") as other_ae:  # Diego doesn't own it: the record is invisible to him
        r = await other_ae.put(f"{API}/upsert/accounts/ERP-9001", json={"name": "Hijack"})
        assert r.status_code == 404


async def test_bulk_actions_respect_scope_and_side_effects(client):
    async with login_as("priya@cirra.demo") as ae, login_as("diego@cirra.demo") as other:
        mine = [l["id"] for l in (await ae.get(f"{API}/leads")).json()["leads"]] if isinstance((await ae.get(f"{API}/leads")).json(), dict) else \
            [l["id"] for l in (await ae.get(f"{API}/leads")).json()]
        r = await other.post(f"{API}/bulk/leads", json={"ids": mine[:3], "action": "set_status", "value": "working"})
        assert r.status_code == 200 and r.json()["changed"] == 0  # not his leads
    users = (await client.get(f"{API}/users")).json()
    sam = next(u for u in users if u["email"] == "sam@cirra.demo")["id"]
    leads = (await client.get(f"{API}/leads")).json()
    leads = leads["leads"] if isinstance(leads, dict) else leads
    open_ids = [l["id"] for l in leads if l["status"] in ("new", "working", "mql")][:3]
    r = (await client.post(f"{API}/bulk/leads", json={"ids": open_ids, "action": "assign_owner", "value": sam})).json()
    assert r["changed"] >= 1 and r["skipped"] == 0
    assert (await client.post(f"{API}/bulk/leads", json={"ids": open_ids, "action": "explode"})).status_code == 422
    async with login_as("sofia@cirra.demo") as agent:
        acc = (await agent.get(f"{API}/accounts")).json()[0]
        cid = (await agent.post(f"{API}/cases", json={"account_id": acc["id"], "subject": "Bulk priority", "severity": "low"})).json()["id"]
        r = (await agent.post(f"{API}/bulk/cases", json={"ids": [cid], "action": "set_priority", "value": "critical"})).json()
        assert r["changed"] == 1
    async with SessionLocal() as db:
        case = await db.get(SupportTicket, uuid.UUID(cid))
        assert case.severity == "critical" and case.first_response_due_at - case.opened_at <= timedelta(hours=1, minutes=1)
    async with login_as("nina@cirra.demo") as mkt:
        camp = (await mkt.post(f"{API}/campaigns", json={"name": "Bulk add target"})).json()["id"]
        contacts = [c["id"] for c in (await mkt.get(f"{API}/contacts")).json()[:4]]
        r = (await mkt.post(f"{API}/bulk/contacts", json={"ids": contacts, "action": "add_to_campaign", "value": camp})).json()
        assert r["changed"] == 4
        assert (await mkt.get(f"{API}/campaigns/{camp}/members")).json()["total"] == 4
