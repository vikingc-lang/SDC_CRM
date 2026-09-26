"""Lead-to-order step 7 end to end: lead -> conversion -> solution-sale stages -> approved primary quote -> signed Order Form
-> Closed-Won validation -> order -> ERP sales order acknowledged."""
from datetime import date

import pytest

from tests.helpers import login_as

pytestmark = pytest.mark.asyncio


async def _stage(client, deal_id, name, **extra):
    deal = (await client.get(f"/api/v1/deals/{deal_id}")).json()
    stage = next(s for s in deal["stages"] if s["name"] == name)
    return await client.patch(f"/api/v1/deals/{deal_id}/stage", json={"stage_id": stage["id"], **extra})


async def test_lead_to_order_end_to_end(client):
    users = {u["email"]: u["id"] for u in (await client.get("/api/v1/users")).json()}
    products = {p["sku"]: p for p in (await client.get("/api/v1/products")).json()}
    solution = next(p for p in (await client.get("/api/v1/pipelines")).json() if p["name"] == "Enterprise Solution Sale")

    # 1-3: capture, qualify (BANT 4/4) and convert into the solution-selling pipeline
    lead = (await client.post("/api/v1/leads", json={
        "email": "cio@northwind-rail-demo.com", "first_name": "Hana", "last_name": "Sato", "job_title": "CIO", "company_name": "Northwind Rail",
        "domain": "northwind-rail-demo.com", "industry": "Transportation", "employee_count": 2400, "country": "US", "consent": "granted",
        "source": "trade_show"})).json()
    await client.put(f"/api/v1/leads/{lead['id']}/qualification", json={"framework": "bant", "criteria": {
        k: {"met": True} for k in ("budget", "authority", "need", "timeline")}})
    conv = (await client.post(f"/api/v1/leads/{lead['id']}/convert", json={
        "deal_title": "Northwind Rail: Revenue platform", "amount": 60000, "pipeline_id": solution["id"], "buying_role": "Economic Buyer",
        "owner_id": users["priya@cirra.demo"]})).json()
    deal_id, account_id = conv["deal_id"], conv["account_id"]

    # 4: buying committee and evidence for each stage gate
    for first, role in (("Kenji", "Champion"), ("Ava", "Evaluator"), ("Lucas", "Legal Counsel")):
        await client.post("/api/v1/contacts", json={"account_id": account_id, "first_name": first, "last_name": "Northwind",
                                                    "email": f"{first.lower()}@northwind-rail-demo.com", "buying_role": role})
    await client.post("/api/v1/activities", json={
        "account_id": account_id, "deal_id": deal_id, "activity_type": "meeting", "subject": "Solution design workshop",
        "summary": "Pain: manual quote-to-cash, forecast bottleneck. Agreed PoC scope for SAP integration; business case shows 14-month payback."})
    await client.patch(f"/api/v1/deals/{deal_id}", json={"target_close_date": date.today().isoformat()})
    for stage in ("Solution Design / Demo", "Technical Evaluation / PoC", "Business Case Validation"):
        r = await _stage(client, deal_id, stage)
        assert r.status_code == 200, (stage, r.text)

    # 5: CPQ -> approved primary quote
    q = (await client.post(f"/api/v1/deals/{deal_id}/quotes", json={
        "billing_frequency": "quarterly", "term_months": 12,
        "lines": [{"product_id": products["CIR-PLAT"]["id"], "quantity": 50}, {"product_id": products["CIR-AI"]["id"], "quantity": 50}]})).json()
    assert q["is_primary"] is True
    assert (await client.post(f"/api/v1/quotes/{q['id']}/submit")).json()["status"] == "approved"
    r = await _stage(client, deal_id, "Negotiation & Legal")
    assert r.status_code == 200, r.text

    # 6: Order Form signed by both parties
    doc = (await client.post("/api/v1/documents", json={"doc_type": "order_form", "deal_id": deal_id, "quote_id": q["id"]})).json()
    sent = (await client.post(f"/api/v1/documents/{doc['id']}/send", json={"signers": [
        {"name": "Hana Sato", "email": "cio@northwind-rail-demo.com", "party": "customer"},
        {"name": "Marcus Vance", "email": "marcus@cirra.demo", "party": "company"}]}))
    assert sent.status_code == 200, sent.text
    for s in sent.json()["signers"]:
        token = s["sign_url"].rsplit("/", 1)[1]
        assert (await client.post(f"/api/v1/sign/{token}", json={"signature_text": s["name"], "agree": True})).status_code == 200

    # 7: Closed-Won validation blocks until the ERP has what it needs
    r = await _stage(client, deal_id, "Closed-Won", win_debrief="Won on ERP-native quote-to-cash")
    assert r.status_code == 409
    unmet = {g["criterion"] for g in r.json()["gates"] if not g["met"]}
    assert unmet == {"Customer PO number", "Billing address", "Shipping / delivery address"}
    ready = (await client.get(f"/api/v1/deals/{deal_id}/order-readiness")).json()
    assert ready["ready"] is False and len(ready["checks"]) == 6

    addr = {"line1": "1 Harbour Way", "city": "Seattle", "region": "WA", "postal_code": "98101", "country": "US"}
    await client.patch(f"/api/v1/deals/{deal_id}", json={"po_number": "PO-778812", "bill_to": addr, "ship_to": addr, "tax_exempt": True,
                                                        "incoterms": "DAP", "requested_delivery_date": date.today().isoformat()})
    r = await _stage(client, deal_id, "Closed-Won")
    assert r.status_code == 409 and {g["criterion"] for g in r.json()["gates"] if not g["met"]} == {"Tax-exempt certificate on file (if exempt)"}
    other = (await client.get("/api/v1/accounts")).json()
    stranger = next(a for a in other if a["id"] != account_id)
    wrong = (await client.post(f"/api/v1/accounts/{stranger['id']}/files", files={"file": ("cert.pdf", b"%PDF-1.4 cert", "application/pdf")})).json()
    assert (await client.patch(f"/api/v1/deals/{deal_id}", json={"tax_exempt_cert_id": wrong["id"]})).status_code == 422
    cert = (await client.post(f"/api/v1/accounts/{account_id}/files", data={"deal_id": deal_id},
                              files={"file": ("wa-resale-certificate.pdf", b"%PDF-1.4 exemption", "application/pdf")})).json()
    assert (await client.patch(f"/api/v1/deals/{deal_id}", json={"tax_exempt_cert_id": cert["id"]})).status_code == 200

    r = await _stage(client, deal_id, "Closed-Won", win_debrief="Won on ERP-native quote-to-cash")
    assert r.status_code == 200, r.text
    deal = (await client.get(f"/api/v1/deals/{deal_id}")).json()
    assert all(c["met"] for c in deal["order_readiness"]) and len(deal["orders"]) == 1
    order = deal["orders"][0]
    assert order["status"] == "submitted" and order["po_number"] == "PO-778812" and order["billing_frequency"] == "quarterly"
    assert order["incoterms"] == "DAP" and order["tax_exempt"] is True and order["total"] == pytest.approx(q["tcv"])
    assert [l["sku"] for l in order["lines"]] == ["CIR-PLAT", "CIR-AI"] and [l["line_no"] for l in order["lines"]] == [10, 20]
    plat = order["lines"][0]
    assert len(plat["billing_schedule"]) == 4 and sum(i["amount"] for i in plat["billing_schedule"]) == pytest.approx(plat["line_total"])

    # the primary quote is locked; a second order is refused
    locked = await client.put(f"/api/v1/quotes/{q['id']}", json={"lines": [{"product_id": products["CIR-PLAT"]["id"], "quantity": 1}]})
    assert locked.status_code == 409
    assert (await client.post(f"/api/v1/deals/{deal_id}/orders")).status_code == 422

    # async hand-off: the erp_orders job pushes it and the ERP acknowledges with a sales-order number
    async with login_as("admin@cirra.demo") as admin:
        stats = (await admin.post("/api/v1/admin/jobs/erp_orders")).json()
    assert stats["acknowledged"] >= 1
    done = (await client.get(f"/api/v1/orders/{order['id']}")).json()
    assert done["status"] == "acknowledged" and done["erp_order_id"].startswith("SO-45") and done["account"]["erp_customer_id"]
    assert done["erp_payload"]["sold_to"]["erp_customer_id"] == done["account"]["erp_customer_id"]
    assert (await client.post(f"/api/v1/orders/{order['id']}/cancel")).status_code == 422
    listed = (await client.get("/api/v1/orders", params={"status": "acknowledged"})).json()
    assert any(o["id"] == order["id"] for o in listed)
    timeline = (await client.get(f"/api/v1/deals/{deal_id}")).json()["activities"]
    assert any("ERP sales order" in a["summary"] for a in timeline)


async def test_file_connector_and_failed_push_retry(client, monkeypatch, tmp_path):
    import json

    from sqlalchemy import select

    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models import Order
    from app.services import orders

    monkeypatch.setattr(settings, "erp_connector", "file")
    monkeypatch.setattr(settings, "erp_exchange_dir", str(tmp_path))
    async with SessionLocal() as db:
        order = (await db.execute(select(Order).where(Order.status == "acknowledged"))).scalars().first()
        order.status, order.erp_order_id, order.erp_attempts = "submitted", None, 0
        number = order.order_number
        await db.commit()
        stats = await orders.process_queue(db)
    assert stats["pushed"] >= 1
    payload = json.loads((tmp_path / "outbound" / f"sales-order-{number}.json").read_text())
    assert payload["order_number"] == number and payload["lines"]

    (tmp_path / "inbound").mkdir()
    (tmp_path / "inbound" / "order-acks.json").write_text(json.dumps([{"order_number": number, "erp_order_id": "SO-4599001"}]))
    async with SessionLocal() as db:
        assert (await orders.process_queue(db))["acknowledged"] >= 1
        order = (await db.execute(select(Order).where(Order.order_number == number))).scalars().one()
        assert order.status == "acknowledged" and order.erp_order_id == "SO-4599001"

    # REST connector without a URL fails, stays retryable, and the manual submit re-pushes it
    monkeypatch.setattr(settings, "erp_connector", "rest")
    monkeypatch.setattr(settings, "erp_rest_url", "")
    async with SessionLocal() as db:
        order = (await db.execute(select(Order).where(Order.order_number == number))).scalars().one()
        order.status = "submitted"
        await orders.push(db, order)
        assert order.status == "failed" and "ERP_REST_URL" in order.erp_message
        await db.commit()
        oid = order.id
    monkeypatch.setattr(settings, "erp_connector", "demo")
    r = await client.post(f"/api/v1/orders/{oid}/submit")
    assert r.status_code == 200 and r.json()["status"] == "acknowledged"


def test_billing_schedule_periods():
    from decimal import Decimal

    from app.services.orders import billing_schedule

    s = billing_schedule("recurring", Decimal("10"), Decimal("5"), 12, "annual", date(2027, 1, 31))
    assert s == [{"invoice_date": "2027-01-31", "period": "2027-01-31 to 2028-01-30", "amount": 600.0}]
    s = billing_schedule("recurring", Decimal("10"), Decimal("1"), 7, "quarterly", date(2027, 1, 31))
    assert [i["amount"] for i in s] == [30.0, 30.0, 10.0] and s[1]["invoice_date"] == "2027-04-30"
    assert billing_schedule("one_time", Decimal("250"), Decimal("2"), 12, "monthly", date(2027, 1, 1))[0]["amount"] == 500.0


async def test_only_managers_can_override_stage_gates(client):
    """Regression: a rep could force Closed-Won past the order checks, leaving a won deal with no order."""
    async with login_as("diego@cirra.demo") as ae:
        deal = next(d for d in (await ae.get("/api/v1/deals", params={"status": "all"})).json() if d["title"].startswith("Crescent"))
        stages = {s["name"]: s["id"] for s in (await ae.get(f"/api/v1/deals/{deal['id']}")).json()["stages"]}
        r = await ae.patch(f"/api/v1/deals/{deal['id']}/stage", json={"stage_id": stages["Closed-Won"], "override_gates": True})
        assert r.status_code == 403 and r.json()["gates"]
        assert (await ae.get(f"/api/v1/deals/{deal['id']}")).json()["stage"] != "Closed-Won"
    # the manager may still override; the override is recorded on the stage history
    r = await client.patch(f"/api/v1/deals/{deal['id']}/stage", json={"stage_id": stages["Solution Design / Demo"], "override_gates": True})
    assert r.status_code == 200
    history = (await client.get(f"/api/v1/deals/{deal['id']}")).json()["history"]
    assert history[-1]["gate_overridden"] is True


async def test_account_360_with_legal_and_procurement_contacts(client):
    """Regression: the Account 360 crashed when a contact had one of the new buying roles."""
    acc = (await client.post("/api/v1/accounts", json={"name": "Role Sort Co", "domain": "rolesort.example.com", "force": True})).json()
    for first, role in (("Lee", "Legal Counsel"), ("Pat", "Procurement"), ("Cam", "Champion")):
        await client.post("/api/v1/contacts", json={"account_id": acc["id"], "first_name": first, "last_name": "Sort",
                                                    "email": f"{first.lower()}@rolesort.example.com", "buying_role": role})
    r = await client.get(f"/api/v1/accounts/{acc['id']}/360")
    assert r.status_code == 200
    assert [c["buying_role"] for c in r.json()["contacts"]][:3] == ["Champion", "Legal Counsel", "Procurement"]
