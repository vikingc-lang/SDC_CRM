"""Lead-to-order steps 5-6: price books, promotions, bundles and rules, approval chain, legal documents, redlining,
customer comments, credit checks and external e-signature."""
import pytest

from tests.helpers import login_as

pytestmark = pytest.mark.asyncio


async def _products(client):
    return {p["sku"]: p for p in (await client.get("/api/v1/products")).json()}


async def _deal(client, name, country="US"):
    slug = name.lower().replace(" ", "")
    acc = (await client.post("/api/v1/accounts", json={"name": name, "domain": f"{slug}.example.com", "country": country, "force": True})).json()
    await client.post("/api/v1/contacts", json={"account_id": acc["id"], "first_name": "Buyer", "last_name": name.split()[0],
                                                "email": f"buyer@{slug}.example.com", "buying_role": "Champion"})
    deal = (await client.post("/api/v1/deals", json={"title": f"{name} deal", "account_id": acc["id"], "amount": 0})).json()
    return acc, deal


async def test_price_book_resolution_customer_then_regional_then_list(client):
    products = await _products(client)
    plat = products["CIR-PLAT"]["id"]
    acc, deal = await _deal(client, "Rhein Logistik", country="Germany")
    other, other_deal = await _deal(client, "Harbour Foods", country="Australia")
    async with login_as("admin@cirra.demo") as admin:
        r = await admin.post("/api/v1/price-books", json={"name": "APAC 2027", "kind": "regional", "region": "APAC",
                                                        "entries": [{"product_id": plat, "currency": "EUR", "tiers": [{"min_qty": 1, "unit_price": 55}]}]})
        assert r.status_code == 201
        r = await admin.post("/api/v1/price-books", json={"name": "Rhein framework agreement", "kind": "customer", "account_id": acc["id"],
                                                        "entries": [{"product_id": plat, "currency": "EUR", "tiers": [{"min_qty": 1, "unit_price": 47}]}]})
        assert r.status_code == 201
        assert (await admin.post("/api/v1/price-books", json={"name": "bad", "kind": "customer"})).status_code == 422
    q = (await client.post(f"/api/v1/deals/{deal['id']}/quotes", json={"currency": "EUR", "lines": [{"product_id": plat, "quantity": 10}]})).json()
    assert q["lines"][0]["list_unit_price"] == 47 and q["lines"][0]["price_source"] == "customer:Rhein framework agreement"
    q = (await client.post(f"/api/v1/deals/{other_deal['id']}/quotes", json={"currency": "EUR", "lines": [{"product_id": plat, "quantity": 10}]})).json()
    assert q["lines"][0]["list_unit_price"] == 55 and q["lines"][0]["price_source"] == "regional:APAC 2027"
    _, us_deal = await _deal(client, "Prairie Grain", country="US")
    q = (await client.post(f"/api/v1/deals/{us_deal['id']}/quotes", json={"currency": "EUR", "lines": [{"product_id": plat, "quantity": 10}]})).json()
    assert q["lines"][0]["list_unit_price"] == 60 and q["lines"][0]["price_source"] == "list"


async def test_promotions_bundles_and_product_rules(client):
    products = await _products(client)
    _, deal = await _deal(client, "Promo Metals")
    lines = [{"product_id": products["CIR-PLAT"]["id"], "quantity": 60}, {"product_id": products["CIR-AI"]["id"], "quantity": 60}]
    q = (await client.post(f"/api/v1/deals/{deal['id']}/quotes", json={"promo_code": "launch-ai", "lines": lines})).json()
    ai = next(l for l in q["lines"] if l["sku"] == "CIR-AI")
    assert ai["promo_discount_pct"] == 15 and ai["net_unit_price"] == pytest.approx(20 * 0.85)
    assert q["promo_code"] == "LAUNCH-AI" and q["promo_discount_total"] == pytest.approx(20 * 0.15 * 60 * 12)
    assert q["max_discount_pct"] == 0  # promotions are pre-approved: they do not trigger discount approvals
    assert (await client.post(f"/api/v1/quotes/{q['id']}/submit")).json()["status"] == "approved"
    bad = await client.post(f"/api/v1/deals/{deal['id']}/quotes", json={"promo_code": "NOPE", "lines": lines})
    assert bad.status_code == 422 and "not valid" in bad.json()["detail"]

    # dependency: the AI add-on needs the platform
    r = await client.post(f"/api/v1/deals/{deal['id']}/quotes", json={"lines": [{"product_id": products["CIR-AI"]["id"], "quantity": 10}]})
    assert r.status_code == 422 and "requires the Cirra Platform" in r.json()["detail"]
    # bundle expands into included components, which also satisfy the dependency
    q = (await client.post(f"/api/v1/deals/{deal['id']}/quotes", json={"lines": [{"product_id": products["CIR-GROWTH"]["id"], "quantity": 120}]})).json()
    parent = next(l for l in q["lines"] if l["sku"] == "CIR-GROWTH")
    included = [l for l in q["lines"] if l["is_included"]]
    assert {l["sku"] for l in included} == {"CIR-PLAT", "CIR-AI"} and all(l["line_total"] == 0 and l["parent_line_id"] == parent["id"] for l in included)
    assert q["tcv"] == pytest.approx(69 * 120 * 12)
    # exclusion
    r = await client.post(f"/api/v1/deals/{deal['id']}/quotes", json={"lines": [{"product_id": products["CIR-GROWTH"]["id"], "quantity": 10},
                                                                              {"product_id": products["CIR-SUP"]["id"], "quantity": 1}]})
    assert r.status_code == 422 and "cannot be combined" in r.json()["detail"]


async def test_custom_terms_route_to_legal_last_and_groups(client):
    products = await _products(client)
    _, deal = await _deal(client, "Legal Terms Co")
    q = (await client.post(f"/api/v1/deals/{deal['id']}/quotes", json={
        "custom_terms": "Liability cap raised to 2x annual fees", "payment_terms": "NET60",
        "lines": [{"product_id": products["CIR-PLAT"]["id"], "quantity": 50, "discount_pct": 35}]})).json()
    q = (await client.post(f"/api/v1/quotes/{q['id']}/submit")).json()
    order = [a["required_role"] for a in sorted(q["approvals"], key=lambda a: a["level"])]
    assert order == ["sales_manager", "deal_desk", "vp_sales", "finance", "legal"]
    groups = {g["key"]: g for g in (await client.get("/api/v1/approval-groups")).json()}
    assert [m["full_name"] for m in groups["legal"]["members"]] == ["Lena Kowalski"]


async def test_legal_documents_redlines_comments_and_resend(client):
    _, deal = await _deal(client, "Redline Industries")
    msa = (await client.post("/api/v1/documents", json={"doc_type": "msa", "deal_id": deal["id"]})).json()
    assert msa["current_version"] == 1 and "Master Subscription Agreement" in msa["body"]
    for t in ("sla", "dpa"):
        doc = (await client.post("/api/v1/documents", json={"doc_type": t, "deal_id": deal["id"]})).json()
        assert doc["doc_type"] == t and doc["status"] == "draft"
    assert (await client.post("/api/v1/documents", json={"doc_type": "proposal", "deal_id": deal["id"]})).status_code == 422  # needs a quote

    revised = msa["body"].replace("the fees paid or payable in the twelve (12) months", "two times (2x) the fees paid in the twelve (12) months")
    v = await client.post(f"/api/v1/documents/{msa['id']}/versions", json={"body": revised, "note": "Customer asked for a higher cap"})
    assert v.status_code == 201 and v.json()["version"] == 2
    d = (await client.get(f"/api/v1/documents/{msa['id']}/diff", params={"from_version": 1})).json()
    assert d["stats"] == {"inserted": 1, "deleted": 1} and any("two times (2x)" in l["text"] for l in d["lines"] if l["op"] == "insert")

    c = (await client.post(f"/api/v1/documents/{msa['id']}/comments", json={"clause": "6. Limitation of liability", "body": "Legal to confirm the 2x cap"})).json()
    signers = {"signers": [{"name": "Buyer Redline", "email": "buyer@redlineindustries.example.com", "party": "customer"},
                           {"name": "Marcus Vance", "email": "marcus@cirra.demo", "party": "company"}]}
    blocked = await client.post(f"/api/v1/documents/{msa['id']}/send", json=signers)
    assert blocked.status_code == 422 and "open redline comment" in blocked.json()["detail"]
    await client.post(f"/api/v1/documents/{msa['id']}/comments/{c['id']}/resolve")
    sent = (await client.post(f"/api/v1/documents/{msa['id']}/send", json=signers)).json()
    assert sent["status"] == "sent"
    token = sent["signers"][0]["sign_url"].rsplit("/", 1)[1]

    # the customer asks for a change from the signing page: signing pauses until a revision is issued
    r = await client.post(f"/api/v1/sign/{token}/comments", json={"clause": "7. Term", "body": "Please add a 60-day termination for convenience"})
    assert r.status_code == 201
    view = (await client.get(f"/api/v1/sign/{token}")).json()
    assert view["document"]["status"] == "in_negotiation" and not view["your_turn"] and view["comments"]
    assert (await client.post(f"/api/v1/sign/{token}", json={"signature_text": "Buyer Redline", "agree": True})).status_code == 409

    await client.post(f"/api/v1/documents/{msa['id']}/comments/{r.json()['id']}/resolve")
    v3 = revised + "\nEither party may terminate for convenience on sixty (60) days' written notice.\n"
    await client.post(f"/api/v1/documents/{msa['id']}/versions", json={"body": v3, "note": "Added termination for convenience"})
    doc = (await client.get(f"/api/v1/documents/{msa['id']}")).json()
    assert doc["current_version"] == 3 and doc["signers"] == [] and doc["status"] == "in_negotiation"  # old signatures voided
    sent = (await client.post(f"/api/v1/documents/{msa['id']}/send", json=signers)).json()
    for s in sent["signers"]:
        t = s["sign_url"].rsplit("/", 1)[1]
        assert (await client.post(f"/api/v1/sign/{t}", json={"signature_text": s["name"], "agree": True})).status_code == 200
    final = (await client.get(f"/api/v1/documents/{msa['id']}")).json()
    assert final["status"] == "completed" and final["pdf_attachment_id"]
    neg = (await client.get(f"/api/v1/documents/{msa['id']}/negotiation")).json()
    assert [v["version"] for v in neg["versions"]] == [1, 2, 3] and neg["open_comments"] == 0


async def test_credit_review_blocks_order_form_signature(client):
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models import Account

    products = await _products(client)
    acc, deal = await _deal(client, "Risky Retail")
    q = (await client.post(f"/api/v1/deals/{deal['id']}/quotes", json={"lines": [{"product_id": products["CIR-PLAT"]["id"], "quantity": 20}]})).json()
    assert (await client.post(f"/api/v1/quotes/{q['id']}/submit")).json()["status"] == "approved"
    doc = (await client.post("/api/v1/documents", json={"doc_type": "order_form", "deal_id": deal["id"]})).json()
    async with SessionLocal() as db:  # the ERP puts the customer on credit hold after the quote was approved
        account = (await db.execute(select(Account).where(Account.id == acc["id"]))).scalars().one()
        account.credit_hold = True
        await db.commit()
    r = await client.post(f"/api/v1/documents/{doc['id']}/send", json={"signers": [
        {"name": "Buyer Risky", "email": "buyer@riskyretail.example.com", "party": "customer"},
        {"name": "Marcus Vance", "email": "marcus@cirra.demo", "party": "company"}]})
    assert r.status_code == 409 and "Credit review required" in r.json()["detail"]
    risk = (await client.get(f"/api/v1/finance/accounts/{acc['id']}/credit-risk")).json()
    assert risk["factors"]["credit_hold"] is True and risk["factors"]["no_credit_history"] is True and risk["band"] in ("medium", "high")


async def test_external_esign_provider_and_webhook(client, monkeypatch):
    from app.core.config import settings
    from app.services import contracting

    async def fake_envelope(provider, doc, pdf):
        assert provider == "docusign" and pdf.startswith(b"%PDF")
        return "ENV-TEST-001"

    monkeypatch.setattr(contracting, "_create_envelope", fake_envelope)
    monkeypatch.setattr(settings, "esign_webhook_secret", "s3cret")
    _, deal = await _deal(client, "Envelope Corp")
    nda = (await client.post("/api/v1/documents", json={"doc_type": "nda", "deal_id": deal["id"]})).json()
    sent = (await client.post(f"/api/v1/documents/{nda['id']}/send", json={"provider": "docusign", "signers": [
        {"name": "Buyer Envelope", "email": "buyer@envelopecorp.example.com", "party": "customer"},
        {"name": "Marcus Vance", "email": "marcus@cirra.demo", "party": "company"}]})).json()
    assert sent["esign_provider"] == "docusign" and sent["envelope_id"] == "ENV-TEST-001"
    token = sent["signers"][0]["sign_url"].rsplit("/", 1)[1]
    assert (await client.post(f"/api/v1/sign/{token}", json={"signature_text": "Buyer", "agree": True})).status_code == 409
    assert (await client.post("/api/v1/esign/webhook/docusign", json={"envelopeId": "ENV-TEST-001", "status": "completed"})).status_code == 401
    h = {"X-Cirra-Esign-Secret": "s3cret"}
    r = await client.post("/api/v1/esign/webhook/docusign", headers=h,
                          json={"envelopeId": "ENV-TEST-001", "status": "recipient-completed", "recipientEmail": "buyer@envelopecorp.example.com"})
    assert r.json()["status"] == "partially_signed"
    r = await client.post("/api/v1/esign/webhook/docusign", headers=h, json={"envelopeId": "ENV-TEST-001", "status": "completed"})
    assert r.json()["status"] == "completed"
    doc = (await client.get(f"/api/v1/documents/{nda['id']}")).json()
    assert doc["pdf_attachment_id"] and all(s["status"] == "signed" for s in doc["signers"])
