"""Lead-to-order steps 1-3: capture, hygiene, scoring, routing, qualification, conversion; stage administration."""
import pytest

from tests.helpers import login_as

pytestmark = pytest.mark.asyncio


async def _key(kind="web_form", source="web_form", campaign="Q4 ERP webinar"):
    async with login_as("admin@cirra.demo") as admin:
        r = await admin.post("/api/v1/admin/intake-keys", json={"name": f"{kind} test", "kind": kind, "source": source, "campaign": campaign})
        assert r.status_code == 201
        return r.json()["key"]


async def test_web_form_capture_dedup_consent_and_mql(client):
    key = await _key()
    form = {"first_name": "Greta", "last_name": "Vogel", "email": "Greta.Vogel@stahlwerk-demo.de", "company": "Stahlwerk Demo GmbH",
            "title": "VP Operations", "country": "Germany", "employees": "850", "industry": "Manufacturing", "revenue": "120000000",
            "consent": "true", "consent_text": "I agree to receive product updates"}
    r = await client.post("/api/v1/intake/leads", json=form, headers={"X-Cirra-Key": key})
    assert r.status_code == 201 and r.json()["merged"] is False
    lead_id = r.json()["lead_id"]
    lead = (await client.get(f"/api/v1/leads/{lead_id}")).json()
    assert lead["email"] == "greta.vogel@stahlwerk-demo.de" and lead["domain"] == "stahlwerk-demo.de"
    assert lead["region"] == "EMEA" and lead["privacy_regime"] == "GDPR" and lead["consent_email"] == "granted"
    assert lead["fit_score"] == 100  # industry, size, revenue, geography and seniority all match the ICP
    assert lead["owner"] is not None  # routed on capture
    assert [e["event_type"] for e in lead["events"]] == ["form_submit"]

    # same person submits again: merged into the open lead, not duplicated
    r2 = await client.post("/api/v1/intake/leads", json={**form, "message": "Pricing for 400 users?"}, headers={"X-Cirra-Key": key})
    assert r2.json()["merged"] is True and r2.json()["lead_id"] == lead_id

    # engagement webhook pushes it over the MQL threshold
    hook = await _key("webhook", "campaign")
    r = await client.post("/api/v1/intake/events", headers={"X-Cirra-Key": hook}, json=[
        {"email": "greta.vogel@stahlwerk-demo.de", "event_type": "pricing_page_visit", "detail": "/pricing"},
        {"email": "greta.vogel@stahlwerk-demo.de", "event_type": "webinar_attended", "detail": "ERP-native CRM webinar"}])
    assert r.status_code == 202 and r.json()["results"][-1]["status"] == "mql"
    lead = (await client.get(f"/api/v1/leads/{lead_id}")).json()
    assert lead["score"] >= lead["score_breakdown"]["mql_threshold"] and lead["mql_at"]
    assert set(lead["score_breakdown"]["engagement"]) == {"form_submit", "pricing_page_visit", "webinar_attended"}

    # honeypot submissions are accepted silently and create nothing
    r = await client.post("/api/v1/intake/leads", json={"email": "bot@spam.example", "website_url_confirm": "x"}, headers={"X-Cirra-Key": key})
    assert r.status_code == 201 and "lead_id" not in r.json()
    assert (await client.post("/api/v1/intake/leads", json=form, headers={"X-Cirra-Key": "nope"})).status_code == 401


async def test_dedup_against_contacts_and_named_account_routing(client):
    async with login_as("admin@cirra.demo") as admin:
        await admin.post("/api/v1/admin/assignment-rules", json={"name": "Named accounts go to the account owner", "priority": 1,
                                                                 "method": "account_owner", "criteria": {"existing_account": True}})
    contacts = (await client.get("/api/v1/contacts", params={"q": "Elena"})).json()
    elena = next(c for c in contacts if c["email"])
    r = await client.post("/api/v1/leads", json={"email": elena["email"], "first_name": "Elena", "last_name": "Rostova",
                                                "company_name": "Apex Industrial Supply", "source": "trade_show"})
    assert r.status_code == 201
    lead = r.json()
    kinds = {m["type"] for m in lead["duplicate_matches"]}
    assert {"contact", "account"} <= kinds
    account = (await client.get(f"/api/v1/accounts/{lead['account_match']['id']}/360")).json()["account"]
    assert lead["owner"]["id"] == account["owner"]["id"]  # named-account rule routes to the account owner


async def test_round_robin_rule_and_sdr_scope(client):
    users = {u["email"]: u["id"] for u in (await client.get("/api/v1/users")).json()}
    async with login_as("admin@cirra.demo") as admin:
        await admin.post("/api/v1/admin/assignment-rules", json={
            "name": "APAC round robin", "priority": 5, "method": "round_robin", "criteria": {"regions": ["APAC"]},
            "assignee_ids": [users["priya@cirra.demo"], users["diego@cirra.demo"]]})
    owners = []
    for i in range(2):
        r = await client.post("/api/v1/leads", json={"email": f"buyer{i}@kiwi-foods-{i}.co.nz", "last_name": f"Buyer{i}",
                                                    "company_name": f"Kiwi Foods {i}", "country": "New Zealand", "source": "campaign"})
        owners.append(r.json()["owner"]["id"])
    assert set(owners) == {users["priya@cirra.demo"], users["diego@cirra.demo"]}
    async with login_as("sam@cirra.demo") as sdr:
        visible = (await sdr.get("/api/v1/leads")).json()
        assert all(l["owner"] is None or l["owner"]["id"] == users["sam@cirra.demo"] for l in visible)
    async with login_as("viewer@cirra.demo") as auditor:
        assert (await auditor.post("/api/v1/leads", json={"email": "x@y.com"})).status_code == 403


async def test_qualification_gate_and_conversion(client):
    r = await client.post("/api/v1/leads", json={"email": "cfo@orbital-mills-demo.com", "first_name": "Maya", "last_name": "Chen",
                                                "job_title": "CFO", "company_name": "Orbital Mills", "domain": "orbital-mills-demo.com",
                                                "industry": "Manufacturing", "employee_count": 1200, "country": "US", "consent": "granted",
                                                "source": "outbound"})
    lead = r.json()
    await client.post(f"/api/v1/leads/{lead['id']}/events", json={"event_type": "meeting_booked", "detail": "Discovery call booked"})
    blocked = await client.post(f"/api/v1/leads/{lead['id']}/convert", json={"deal_title": "Orbital Mills: CRM", "amount": 90000})
    assert blocked.status_code == 422 and "BANT" in blocked.json()["detail"]

    q = await client.put(f"/api/v1/leads/{lead['id']}/qualification", json={"framework": "bant", "criteria": {
        "budget": {"met": True, "note": "FY27 budget approved"}, "authority": {"met": True, "note": "CFO signs"},
        "need": {"met": True, "note": "Forecast accuracy"}, "timeline": {"met": False}}})
    assert q.json()["status"] == "sql" and q.json()["qualification"]["met"] == 3

    solution = next(p for p in (await client.get("/api/v1/pipelines")).json() if p["name"] == "Enterprise Solution Sale")
    users = {u["email"]: u["id"] for u in (await client.get("/api/v1/users")).json()}
    r = await client.post(f"/api/v1/leads/{lead['id']}/convert", json={
        "deal_title": "Orbital Mills: Revenue platform", "amount": 90000, "pipeline_id": solution["id"], "buying_role": "Economic Buyer",
        "owner_id": users["priya@cirra.demo"]})  # SDR hands the opportunity to an AE
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["created"] == {"account": True, "contact": True, "deal": True}
    deal = (await client.get(f"/api/v1/deals/{out['deal_id']}")).json()
    assert deal["stage"] == "Discovery" and deal["account"]["id"] == str(out["account_id"])
    assert any("Lead converted" in a["summary"] for a in deal["activities"])
    contact = (await client.get(f"/api/v1/contacts/{out['contact_id']}")).json()
    c = contact.get("contact", contact)
    assert c["consent"]["email"] == "granted" and c["buying_role"] == "Economic Buyer"
    converted = (await client.get(f"/api/v1/leads/{lead['id']}")).json()
    assert converted["status"] == "converted" and converted["converted"]["deal_id"] == str(out["deal_id"])
    assert (await client.post(f"/api/v1/leads/{lead['id']}/convert", json={})).status_code == 422


async def test_conversion_links_matched_account(client):
    r = await client.post("/api/v1/leads", json={"email": "new.buyer@apexindustrial.com", "first_name": "Omar", "last_name": "Haddad",
                                                "company_name": "Apex Industrial Supply", "source": "outbound"})
    lead = r.json()
    assert lead["account_match"]
    r = await client.post(f"/api/v1/leads/{lead['id']}/convert", json={"create_deal": False, "override": True})
    assert r.status_code == 200 and r.json()["created"]["account"] is False and r.json()["deal_id"] is None


async def test_stage_administration(client):
    solution = next(p for p in (await client.get("/api/v1/pipelines")).json() if p["name"] == "Enterprise Solution Sale")
    async with login_as("admin@cirra.demo") as admin:
        disc = next(s for s in solution["stages"] if s["name"] == "Discovery")
        r = await admin.post(f"/api/v1/pipelines/{solution['id']}/stages", json={"name": "Mutual Action Plan", "default_probability": 30,
                                                                               "after_stage_id": disc["id"]})
        assert r.status_code == 201 and r.json()["stage_order"] == 2
        stage_id = r.json()["id"]
        r = await admin.patch(f"/api/v1/pipelines/stages/{stage_id}", json={"name": "Mutual Plan Agreed", "move": "down"})
        assert r.json()["name"] == "Mutual Plan Agreed" and r.json()["stage_order"] == 3
        names = [s["name"] for s in next(p for p in (await admin.get("/api/v1/pipelines")).json() if p["id"] == solution["id"])["stages"]]
        assert names[-2:] == ["Closed-Won", "Closed-Lost"]
        assert (await admin.delete(f"/api/v1/pipelines/stages/{stage_id}")).status_code == 204
        assert (await admin.delete(f"/api/v1/pipelines/stages/{disc['id']}")).status_code == 409  # deals are in it
    assert (await client.post(f"/api/v1/pipelines/{solution['id']}/stages", json={"name": "X stage", "default_probability": 5})).status_code == 403
