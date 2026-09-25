"""Pillars 3 and 4: multi-pipeline stage gates, loss taxonomy, FX forecast, CPQ, approvals, documents, e-signature, contracts."""
import pytest

from tests.helpers import login_as


async def _pipeline(client, kind):
    return next(p for p in (await client.get("/api/v1/pipelines")).json() if p["kind"] == kind)


async def _new_deal(client, title, pipeline_kind="direct", amount=50000, **kw):
    acc = (await client.post("/api/v1/accounts", json={"name": f"{title} Corp", "domain": f"{title.lower().replace(' ', '')}.example.com", "force": True})).json()
    await client.post("/api/v1/contacts", json={"account_id": acc["id"], "first_name": "Kim", "last_name": title.split()[0], "buying_role": "Champion",
                                                "email": f"kim@{title.lower().replace(' ', '')}.example.com"})
    p = await _pipeline(client, pipeline_kind)
    deal = (await client.post("/api/v1/deals", json={"title": title, "account_id": acc["id"], "amount": amount, "pipeline_id": p["id"], **kw})).json()
    return deal, p


def _stage(p, name):
    return next(s["id"] for s in p["stages"] if s["name"] == name)


async def test_independent_pipelines_have_their_own_gates(client):
    deal, p = await _new_deal(client, "Gatecheck Mid", "inbound")
    assert deal["stage"] == "Lead"
    r = await client.patch(f"/api/v1/deals/{deal['id']}/stage", json={"stage_id": _stage(p, "Proposal Sent")})
    assert r.status_code == 409
    unmet = {g["criterion"] for g in r.json()["gates"] if not g["met"]}
    assert unmet == {"Target close date set", "Approved amount (approved quote)"}
    # Demo Completed requires a logged meeting/call
    r = await client.patch(f"/api/v1/deals/{deal['id']}/stage", json={"stage_id": _stage(p, "Demo Completed")})
    assert r.status_code == 409
    await client.post("/api/v1/activities", json={"account_id": deal["account"]["id"], "deal_id": deal["id"], "activity_type": "meeting",
                                                  "summary": "Product demo for the sales team", "duration_seconds": 2700, "attendance": "attended",
                                                  "agenda": "1. Pipeline views\n2. Quick-Log"})
    r = await client.patch(f"/api/v1/deals/{deal['id']}/stage", json={"stage_id": _stage(p, "Demo Completed")})
    assert r.status_code == 200 and r.json()["deal"]["stage"] == "Demo Completed"


async def test_gate_rules_are_configurable(client):
    p = await _pipeline(client, "inbound")
    lead = next(s for s in p["stages"] if s["name"] == "Lead")
    async with login_as("priya@relate.demo") as ae:
        assert (await ae.put(f"/api/v1/pipelines/stages/{lead['id']}/gates", json={"gate_rules": []})).status_code == 403
    async with login_as("admin@relate.demo") as admin:
        bad = await admin.put(f"/api/v1/pipelines/stages/{lead['id']}/gates", json={"gate_rules": [{"type": "nonsense"}]})
        assert bad.status_code == 422
        ok = await admin.put(f"/api/v1/pipelines/stages/{lead['id']}/gates",
                             json={"gate_rules": [{"type": "min_contacts", "min": 1, "label": "Contact identified"}], "default_probability": 8})
        assert ok.status_code == 200 and ok.json()["default_probability"] == 8


async def test_cpq_tiers_tcv_and_two_level_approval_routing(client):
    deal, _ = await _new_deal(client, "Quote Routing", amount=0)
    products = {p["sku"]: p for p in (await client.get("/api/v1/products")).json()}
    q = (await client.post(f"/api/v1/deals/{deal['id']}/quotes", json={
        "currency": "USD", "term_months": 24, "payment_terms": "NET60",
        "lines": [{"product_id": products["REL-PLAT"]["id"], "quantity": 150, "discount_pct": 30},
                  {"product_id": products["REL-IMPL"]["id"], "quantity": 1}]})).json()
    plat = q["lines"][0]
    assert plat["list_unit_price"] == 58  # volume tier reached at 100 seats
    assert plat["line_total"] == pytest.approx(58 * 0.70 * 150 * 24)
    assert q["tcv"] == pytest.approx(plat["line_total"] + 15000)
    assert q["acv"] == pytest.approx(58 * 0.70 * 150 * 12)
    assert q["discount_total"] == pytest.approx(58 * 150 * 24 + 15000 - q["tcv"])
    q = (await client.post(f"/api/v1/quotes/{q['id']}/submit")).json()
    assert q["status"] == "pending_approval"
    roles = {a["required_role"]: a for a in q["approvals"] if a["status"] == "pending"}
    assert set(roles) == {"sales_manager", "finance"}
    assert "NET60" in roles["finance"]["reason"] and "30%" in roles["finance"]["reason"]
    async with login_as("priya@relate.demo") as ae:
        assert (await ae.post(f"/api/v1/approvals/{roles['sales_manager']['id']}/decide", json={"approve": True})).status_code == 403
    assert (await client.post(f"/api/v1/approvals/{roles['finance']['id']}/decide", json={"approve": True})).status_code == 403  # manager isn't finance
    q = (await client.post(f"/api/v1/approvals/{roles['sales_manager']['id']}/decide", json={"approve": True, "comment": "Strategic logo"})).json()
    assert q["status"] == "pending_approval"
    async with login_as("admin@relate.demo") as finance:
        q = (await finance.post(f"/api/v1/approvals/{roles['finance']['id']}/decide", json={"approve": True})).json()
    assert q["status"] == "approved"
    refreshed = (await client.get(f"/api/v1/deals/{deal['id']}")).json()
    assert refreshed["amount"] == pytest.approx(q["tcv"])  # approved amount flows to the forecast
    # editing an approved quote resets approval
    q2 = (await client.put(f"/api/v1/quotes/{q['id']}", json={"currency": "USD", "term_months": 24, "payment_terms": "NET30",
                                                               "lines": [{"product_id": products["REL-PLAT"]["id"], "quantity": 150, "discount_pct": 5}]})).json()
    assert q2["status"] == "draft"
    assert (await client.post(f"/api/v1/quotes/{q['id']}/submit")).json()["status"] == "approved"  # within policy: auto-approved


async def test_document_assembly_esignature_and_contract(client):
    deal, _ = await _new_deal(client, "Sign Flow", amount=0)
    products = {p["sku"]: p for p in (await client.get("/api/v1/products")).json()}
    assert (await client.post("/api/v1/documents", json={"doc_type": "order_form", "deal_id": deal["id"]})).status_code == 422  # no approved quote
    q = (await client.post(f"/api/v1/deals/{deal['id']}/quotes", json={"lines": [{"product_id": products["REL-PLAT"]["id"], "quantity": 20}]})).json()
    await client.post(f"/api/v1/quotes/{q['id']}/submit")
    doc = (await client.post("/api/v1/documents", json={"doc_type": "order_form", "deal_id": deal["id"]})).json()
    assert q["quote_number"] in doc["body"] and "Sign Flow Corp" in doc["body"] and "<table" in doc["body_html"]
    nda = (await client.post("/api/v1/documents", json={"doc_type": "nda", "deal_id": deal["id"]})).json()
    assert "Mutual Non-Disclosure Agreement" in nda["body"]
    doc = (await client.post(f"/api/v1/documents/{doc['id']}/send", json={"signers": [
        {"name": "Kim Sign", "email": "kim@signflow.example.com", "party": "customer"},
        {"name": "Marcus Vance", "email": "marcus@relate.demo", "party": "company"}]})).json()
    customer, company = sorted(doc["signers"], key=lambda s: s["order"])
    tok_c, tok_co = customer["sign_url"].rsplit("/", 1)[1], company["sign_url"].rsplit("/", 1)[1]
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:  # public, token-only
        view = (await anon.get(f"/api/v1/sign/{tok_co}")).json()
        assert view["your_turn"] is False
        assert (await anon.post(f"/api/v1/sign/{tok_co}", json={"signature_text": "Marcus Vance", "agree": True})).status_code == 409  # out of order
        assert (await anon.post(f"/api/v1/sign/{tok_c}", json={"signature_text": "Kim Sign"})).status_code == 422  # must agree
        r = await anon.post(f"/api/v1/sign/{tok_c}", json={"signature_text": "Kim Sign", "agree": True}, headers={"user-agent": "pytest-browser"})
        assert r.json()["document_status"] == "partially_signed"
        assert (await anon.post(f"/api/v1/sign/{tok_c}", json={"signature_text": "Kim Sign", "agree": True})).status_code == 409  # single use
        r = await anon.post(f"/api/v1/sign/{tok_co}", json={"signature_text": "Marcus Vance", "agree": True})
        assert r.json()["document_status"] == "completed"
    final = (await client.get(f"/api/v1/documents/{doc['id']}")).json()
    assert final["status"] == "completed" and final["pdf_attachment_id"]
    pdf = await client.get(f"/api/v1/documents/{doc['id']}/pdf")
    assert pdf.content.startswith(b"%PDF") and len(pdf.content) > 2000
    contracts = [c for c in (await client.get("/api/v1/contracts")).json() if c["deal_id"] == deal["id"]]
    assert len(contracts) == 1 and contracts[0]["acv"] == pytest.approx(q["acv"]) and contracts[0]["status"] == "active"
    timeline = (await client.get(f"/api/v1/accounts/{deal['account']['id']}/360")).json()["recent_activities"]
    signed = next(a for a in timeline if a["type"] == "document" and "fully executed" in a["summary"])
    assert signed["attachments"][0]["filename"].endswith("(signed).pdf")


async def test_loss_taxonomy_debrief_and_win_loss_attribution(client):
    deal, p = await _new_deal(client, "Lost Cause")
    r = await client.patch(f"/api/v1/deals/{deal['id']}/stage", json={"stage_id": _stage(p, "Closed-Lost"), "loss_reason": "champion_departed",
                                                                       "loss_debrief": "Our champion left for a competitor and the new VP paused all tooling spend."})
    assert r.status_code == 200 and r.json()["deal"]["loss_reason"] == "champion_departed"
    report = (await client.get("/api/v1/reports/win-loss")).json()
    reasons = {r["reason"]: r for r in report["loss_reasons"]}
    assert reasons["champion_departed"]["debriefs"][0]["title"] == "Lost Cause"
    assert any(c["competitor"] == "SAP" for c in report["competitors"])


async def test_multi_currency_forecast(client):
    deals = (await client.get("/api/v1/deals", params={"search": "Ambient AI expansion"})).json()
    eur = deals[0]
    assert eur["currency"] == "EUR" and eur["amount_usd"] == pytest.approx(eur["amount"] * 1.08)
    fc = (await client.get("/api/v1/reports/forecast")).json()
    assert fc["currency"] == "USD" and {p["pipeline"] for p in fc["by_pipeline"]} >= {"Enterprise Direct Sales", "Renewals & Upsells"}


async def test_renewal_engine_links_original_terms(client):
    renewals = [d for d in (await client.get("/api/v1/deals", params={"search": "renewal"})).json() if d["deal_type"] == "renewal"]
    assert renewals and renewals[0]["contract_id"] and renewals[0]["ai_insights"]["prior_terms"]["term_months"] == 12
    assert (await client.post("/api/v1/success/renewals/run")).json()["renewals_created"] == 0  # idempotent
    upcoming = (await client.get("/api/v1/success/renewals", params={"days": 120})).json()
    assert any(c["renewal_deal_id"] for c in upcoming)
