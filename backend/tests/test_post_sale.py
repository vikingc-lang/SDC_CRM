"""Pillars 7, 8 and 9: CS handoff, churn warning, ERP fabric, ecosystem feed, partner portal and commissions."""
import json

import pytest

from tests.helpers import login_as


async def _account(client, name):
    return next(a for a in (await client.get("/api/v1/accounts", params={"search": name})).json() if a["name"] == name)


async def test_closed_won_provisions_onboarding_workspace(client):
    acc = (await client.post("/api/v1/accounts", json={"name": "Handoff Health", "domain": "handoffhealth.example.com", "force": True})).json()
    await client.post("/api/v1/contacts", json={"account_id": acc["id"], "first_name": "Mia", "last_name": "Ross", "buying_role": "Champion", "job_title": "CIO"})
    pipeline = next(p for p in (await client.get("/api/v1/pipelines")).json() if p["kind"] == "direct")
    deal = (await client.post("/api/v1/deals", json={"title": "Handoff rollout", "account_id": acc["id"], "amount": 88000, "pipeline_id": pipeline["id"]})).json()
    await client.post("/api/v1/activities", json={"account_id": acc["id"], "deal_id": deal["id"], "activity_type": "email",
                                                  "summary": "Signed MSA and PO received. Priorities: go live before open enrolment; replace spreadsheets."})
    won = next(s["id"] for s in pipeline["stages"] if s["name"] == "Closed-Won")
    r = await client.patch(f"/api/v1/deals/{deal['id']}/stage", json={"stage_id": won, "override_gates": True, "win_debrief": "Fast time-to-value and private cloud."})
    assert r.status_code == 200 and "onboarding" in r.json()["triggered_action"]
    projects = [p for p in (await client.get("/api/v1/success/onboarding")).json() if p["deal_id"] == deal["id"]]
    assert len(projects) == 1
    proj = projects[0]
    assert len(proj["milestones"]) == 6 and proj["scope"]["stakeholders"][0]["name"] == "Mia Ross"
    assert any("open enrolment" in s for s in proj["scope"]["pre_sales_summary"])
    tasks = (await client.get("/api/v1/tasks", params={"account_id": acc["id"]})).json()
    assert sum(1 for t in tasks if t["milestone_id"]) == 3
    # completing a milestone task completes the milestone
    first = next(t for t in tasks if t["title"].startswith("Kickoff"))
    await client.patch(f"/api/v1/tasks/{first['id']}", json={"completed": True})
    proj = next(p for p in (await client.get("/api/v1/success/onboarding")).json() if p["deal_id"] == deal["id"])
    assert proj["milestones"][0]["status"] == "done" and proj["progress"] == 17
    events = (await client.get("/api/v1/integrations/events", params={"target": "yield"})).json()["events"]
    assert any(e["type"] == "deal.closed_won" and e["payload"]["title"] == "Handoff rollout" for e in events)


async def test_churn_early_warning(client):
    watch = (await client.get("/api/v1/success/churn")).json()
    blue = next(a for a in watch if a["name"] == "Bluepeak Health")
    assert blue["churn_risk"] >= 60 and blue is watch[0]
    f = blue["churn_factors"]
    assert f["utilization_pct"] < 50 and f["champions_departed"] == ["Ian Moss"] and f["open_critical_tickets"] == 1
    view = (await client.get(f"/api/v1/accounts/{blue['id']}/360")).json()
    assert view["account"]["health_breakdown"]["support"] < 60 and view["account"]["health_breakdown"]["overdue_items"] >= 1


async def test_erp_customer_master_ar_aging_and_credit_hold(client):
    aging = (await client.get("/api/v1/finance/ar-aging")).json()
    orion = next(r for r in aging["accounts"] if r["account"]["name"] == "Orion Asset Management")
    assert orion["credit_hold"] and orion["buckets"]["90_plus"] >= 66000 and orion["credit_limit"]
    assert (await client.post("/api/v1/integrations/erp/sync")).status_code == 403  # managers read finance; admins run syncs
    async with login_as("admin@relate.demo") as admin:
        run = (await admin.post("/api/v1/integrations/erp/sync")).json()
    assert run["status"] == "succeeded" and run["stats"]["customers_matched"] >= 3
    # quotes for accounts on credit hold route to finance
    oam = await _account(client, "Orion Asset Management")
    pipeline = next(p for p in (await client.get("/api/v1/pipelines")).json() if p["kind"] == "direct")
    deal = (await client.post("/api/v1/deals", json={"title": "OAM expansion", "account_id": oam["id"], "pipeline_id": pipeline["id"]})).json()
    plat = next(p for p in (await client.get("/api/v1/products")).json() if p["sku"] == "REL-PLAT")
    q = (await client.post(f"/api/v1/deals/{deal['id']}/quotes", json={"lines": [{"product_id": plat["id"], "quantity": 10}]})).json()
    q = (await client.post(f"/api/v1/quotes/{q['id']}/submit")).json()
    assert q["status"] == "pending_approval" and any("credit hold" in a["reason"] for a in q["approvals"])


async def test_file_based_erp_connector(client, tmp_path, monkeypatch):
    from app.core.config import settings

    helios = await _account(client, "Helios Energy")
    inbound = tmp_path / "inbound"
    inbound.mkdir()
    (inbound / "customers.json").write_text(json.dumps([{"erp_customer_id": "SAP-100200", "crm_account_id": helios["id"], "legal_name": "Helios Energy AG",
                                                         "tax_id": "DE811223344", "credit_limit": 250000, "credit_hold": False, "payment_terms": "NET45",
                                                         "billing_address": {"line1": "Leopoldstr. 1", "city": "Munich", "country": "DE"}}]))
    (inbound / "invoices.json").write_text(json.dumps([{"erp_invoice_id": "SAP-INV-1", "erp_customer_id": "SAP-100200", "invoice_number": "9000001",
                                                        "issue_date": "2026-07-01", "due_date": "2026-07-31", "currency": "EUR", "amount": 12000,
                                                        "balance": 12000, "status": "open"}]))
    monkeypatch.setattr(settings, "erp_connector", "file")
    monkeypatch.setattr(settings, "erp_exchange_dir", str(tmp_path))
    async with login_as("admin@relate.demo") as admin:
        run = (await admin.post("/api/v1/integrations/erp/sync")).json()
        assert run["status"] == "succeeded" and run["stats"]["invoices_upserted"] == 1
        out = (await admin.post("/api/v1/integrations/erp/sync", params={"direction": "outbound"})).json()
    ar = (await client.get(f"/api/v1/finance/accounts/{helios['id']}/ar")).json()
    assert ar["erp_customer_id"] == "SAP-100200" and ar["credit_limit"] == 250000 and ar["buckets"]["31_60"] + ar["buckets"]["61_90"] == 12000
    assert out["status"] == "succeeded"
    exported = json.loads(next((tmp_path / "outbound").glob("customers-*.json")).read_text())
    assert any(c["erp_customer_id"] == "SAP-100200" and c["tax_id"] == "DE811223344" for c in exported)


async def test_partner_portal_registration_conflicts_and_collateral(client):
    async with login_as("partner@northstar-partners.com") as partner:
        r = (await partner.post("/api/v1/portal/registrations", json={"company_name": "Cobalt Retail", "domain": "www.cobaltretail.com",
                                                                     "estimated_amount": 50000, "territory": "NA-East"})).json()
        assert r["conflict_detected"]
        bad = await partner.post("/api/v1/portal/registrations", json={"company_name": "Far Away", "domain": "faraway.example.com", "territory": "APAC"})
        assert bad.status_code == 422  # outside the partner's territories
        mine = (await partner.get("/api/v1/portal/registrations")).json()
        assert all("account_id" not in c for reg in mine for c in reg["conflicts"])  # internal details are not exposed
        titles = {c["title"] for c in (await partner.get("/api/v1/portal/collateral")).json()}
        assert "Channel price list (Gold and above)" in titles and "EMEA case study: utilities" not in titles
        item = next(c for c in (await partner.get("/api/v1/portal/collateral")).json() if c["category"] == "battlecard")
        dl = await partner.get(f"/api/v1/portal/collateral/{item['id']}/download")
        assert dl.content.startswith(b"%PDF")
    lib = {c["title"]: c for c in (await client.get("/api/v1/partners/collateral")).json()}
    assert lib[item["title"]]["downloads"] >= 1


async def test_registration_approval_and_commission_attribution(client):
    regs = (await client.get("/api/v1/partners/registrations", params={"status": "submitted"})).json()
    lumina = next(r for r in regs if r["company_name"] == "Lumina Retail")
    assert not lumina["conflicts"]
    out = (await client.post(f"/api/v1/partners/registrations/{lumina['id']}/decide", json={"approve": True, "note": "Welcome aboard"})).json()
    assert out["status"] == "approved" and out["deal_id"] and out["exclusivity_expires_at"]
    deal = (await client.get(f"/api/v1/deals/{out['deal_id']}")).json()
    assert deal["pipeline"]["kind"] == "partner" and deal["partners"][0]["partner"]["name"] == "Northstar Partners"
    # a second registration for the same domain now conflicts on exclusivity
    async with login_as("partner@northstar-partners.com") as partner:
        again = (await partner.post("/api/v1/portal/registrations", json={"company_name": "Lumina", "domain": "lumina-retail.com", "territory": "NA-East"})).json()
        assert again["conflict_detected"]
    report = (await client.get("/api/v1/partners/commissions")).json()
    keystone = next(p for p in report["partners"] if p["partner"] == "Keystone Advisors")
    blue_line = next(l for l in report["lines"] if l["partner"] == "Keystone Advisors")
    assert blue_line["status"] == "earned" and blue_line["rate_pct"] == 7.0
    assert keystone["commission_earned"] == pytest.approx(blue_line["attributed_usd"] * 0.07, rel=1e-3)
    brightpath = next(l for l in report["lines"] if l["partner"] == "BrightPath Agency")
    assert brightpath["split_pct"] == 30 and brightpath["status"] == "pipeline"
    # splits across partners cannot exceed 100%
    northstar = next(p for p in (await client.get("/api/v1/partners")).json() if p["name"] == "Northstar Partners")
    r = await client.post(f"/api/v1/deals/{brightpath['deal_id']}/partners", json={"partner_id": northstar["id"], "role": "co_sell", "split_pct": 80})
    assert r.status_code == 422
