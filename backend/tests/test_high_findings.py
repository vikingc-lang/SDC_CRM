"""Regression tests for the six high findings of the external build review: merges losing data, outbound calls
to internal hosts, Quick-Log naming invisible accounts, e-signature links that never expire and public
countersigning, the ERP demo default, and unmasked notes sent to AI services."""
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings, enforce_secure_settings, security_problems
from app.core.database import SessionLocal
from app.models import (
    Account, Campaign, CampaignMember, Contact, Deal, Lead, Order, PipelineStage, PriceBook, SignatureRequest, SupportTicket, User,
)
from app.services import developer, embeddings
from tests.helpers import login_as

API = "/api/v1"


# ---- 1. merges keep every related record ---------------------------------------------------------------------------

async def test_account_merge_moves_orders_price_books_and_leads(client):
    tag = uuid.uuid4().hex[:6]
    async with login_as("admin@cirra.demo") as admin:
        a = (await admin.post(f"{API}/accounts", json={"name": f"Merge Keep {tag}", "domain": f"keep-{tag}.example"})).json()
        b = (await admin.post(f"{API}/accounts", json={"name": f"Merge Gone {tag}", "domain": f"gone-{tag}.example"})).json()
    keep, gone = uuid.UUID(a["id"]), uuid.UUID(b["id"])
    async with SessionLocal() as db:
        stage = (await db.execute(select(PipelineStage).where(PipelineStage.is_closed_won.is_(False)).limit(1))).scalars().first()
        deal = Deal(title=f"Gone deal {tag}", account_id=gone, pipeline_id=stage.pipeline_id, stage_id=stage.id, amount=1000, risk_factors={}, ai_insights={})
        db.add(deal)
        await db.flush()
        db.add(Order(order_number=f"ORD-T-{tag}", account_id=gone, deal_id=deal.id, currency="USD", payment_terms="NET30", total=1000))  # RESTRICT
        db.add(PriceBook(name=f"Gone book {tag}", kind="customer", account_id=gone))  # CASCADE
        db.add(Lead(first_name="Lee", last_name=f"Conv{tag}", company_name=f"Merge Gone {tag}", email=f"lee-{tag}@gone-{tag}.example",
                    status="converted", converted_account_id=gone))  # SET NULL
        await db.commit()
    async with login_as("admin@cirra.demo") as admin:
        r = await admin.post(f"{API}/admin/dedup/merge", json={"entity": "account", "survivor_id": str(keep), "merged_id": str(gone)})
        assert r.status_code == 200, r.text
    async with SessionLocal() as db:
        assert await db.get(Account, gone) is None
        assert (await db.execute(select(Order.account_id).where(Order.order_number == f"ORD-T-{tag}"))).scalar() == keep
        assert (await db.execute(select(PriceBook.account_id).where(PriceBook.name == f"Gone book {tag}"))).scalar() == keep
        assert (await db.execute(select(Lead.converted_account_id).where(Lead.last_name == f"Conv{tag}"))).scalar() == keep
        assert (await db.execute(select(Deal.account_id).where(Deal.title == f"Gone deal {tag}"))).scalar() == keep


async def test_contact_merge_keeps_campaign_history_and_cases(client):
    tag = uuid.uuid4().hex[:6]
    async with SessionLocal() as db:
        acc = (await db.execute(select(Account).limit(1))).scalars().first()
        keep = Contact(account_id=acc.id, first_name="Kim", last_name=f"Keep{tag}", email=f"kim-{tag}@x.example", buying_role="Evaluator")
        gone = Contact(account_id=acc.id, first_name="Kim", last_name=f"Gone{tag}", email=f"kim2-{tag}@x.example", buying_role="Champion")
        both = Campaign(name=f"Both {tag}", code=f"both-{tag}")
        only = Campaign(name=f"Only {tag}", code=f"only-{tag}")
        db.add_all([keep, gone, both, only])
        await db.flush()
        for camp, c, status in ((both, keep, "sent"), (both, gone, "responded"), (only, gone, "responded")):
            db.add(CampaignMember(campaign_id=camp.id, contact_id=c.id, status=status, token=secrets.token_urlsafe(24)))
        db.add(SupportTicket(account_id=acc.id, contact_id=gone.id, subject=f"Case {tag}", severity="low", status="open"))
        await db.commit()
        keep_id, gone_id, only_id = keep.id, gone.id, only.id
    async with login_as("admin@cirra.demo") as admin:
        r = await admin.post(f"{API}/admin/dedup/merge", json={"entity": "contact", "survivor_id": str(keep_id), "merged_id": str(gone_id)})
        assert r.status_code == 200, r.text
    async with SessionLocal() as db:
        members = (await db.execute(select(CampaignMember.campaign_id).where(CampaignMember.contact_id == keep_id))).scalars().all()
        assert len(members) == 2 and only_id in members  # the membership only the merged contact had is kept
        assert (await db.execute(select(SupportTicket.contact_id).where(SupportTicket.subject == f"Case {tag}"))).scalar() == keep_id
        assert (await db.get(Contact, keep_id)).buying_role == "Champion"


# ---- 2. no webhooks or mailboxes inside the network -----------------------------------------------------------

@pytest.mark.parametrize("url", ["http://127.0.0.1:8080/x", "http://169.254.169.254/latest/meta-data", "http://10.1.2.3/hook",
                                 "http://192.168.1.10/hook", "https://localhost/x", "http://[::1]/x", "http://metadata.internal/x",
                                 "http://user:pw@example.com/x"])
async def test_webhooks_refuse_internal_destinations(client, url):
    async with login_as("admin@cirra.demo") as admin:
        r = await admin.post(f"{API}/developer/webhooks", json={"name": "internal", "url": url})
        assert r.status_code == 422, (url, r.text)


async def test_allowlisted_internal_host_and_no_response_bodies_in_the_log(client, monkeypatch):
    monkeypatch.setattr(developer.netguard.settings, "outbound_allowed_hosts", "10.9.0.0/16")
    async with login_as("admin@cirra.demo") as admin:
        r = await admin.post(f"{API}/developer/webhooks", json={"name": "erp hub", "url": "http://10.9.1.5/hooks/cirra"})
        assert r.status_code == 201, r.text
        sub_id = r.json()["id"]
        monkeypatch.setattr(developer, "transport", httpx.MockTransport(lambda req: httpx.Response(500, text="SECRET internal stack trace")))
        await admin.post(f"{API}/developer/webhooks/{sub_id}/test")
        deliveries = (await admin.get(f"{API}/developer/webhooks/{sub_id}/deliveries")).json()
        assert deliveries and all("SECRET" not in (d.get("error") or "") for d in deliveries)
        assert deliveries[0]["error"] == "HTTP 500"
        await admin.delete(f"{API}/developer/webhooks/{sub_id}")


@pytest.mark.parametrize("body", [{"imap_host": "127.0.0.1"}, {"imap_host": "10.0.0.8"}, {"imap_host": "imap.example.com", "imap_port": 22},
                                  {"imap_host": "imap.example.com", "smtp_host": "169.254.169.254"},
                                  {"imap_host": "imap.example.com", "smtp_host": "smtp.example.com", "smtp_port": 6379}])
async def test_mailboxes_refuse_internal_hosts_and_odd_ports(client, body):
    r = await client.post(f"{API}/email/mailboxes", json={"email_address": "me@example.com", "password": "x", **body})
    assert r.status_code == 422, r.text


# ---- 3. Quick-Log never names accounts the user can't see ---------------------------------------------------------

async def test_quick_log_does_not_reveal_invisible_accounts(client):
    async with SessionLocal() as db:
        diego = (await db.execute(select(User).where(User.email == "diego@cirra.demo"))).scalar_one()
        hidden = (await db.execute(select(Account).where(Account.owner_id != diego.id, ~Account.id.in_(
            select(Deal.account_id).where(Deal.owner_id == diego.id))).limit(1))).scalars().first()
    async with login_as("diego@cirra.demo") as rep:
        assert (await rep.get(f"{API}/accounts/{hidden.id}/360")).status_code == 404
        r = (await rep.post(f"{API}/ai/quick-log", json={"raw_text": f"Call with the team at {hidden.name} ({hidden.domain}) about renewal pricing.",
                                                          "account_id": str(hidden.id)})).json()
        assert r["matched_account_id"] is None and r["matched_deal_id"] is None
        assert "fuzzy_account_match" not in r.get("signals", {})


# ---- 4. signing links expire; the company countersigns inside Cirra ------------------------------------------------

async def _order_form(client):
    deal = next(d for d in (await client.get(f"{API}/deals")).json() if not d["is_won"] and not d["is_lost"] and d["currency"] == "USD")
    plat = next(p for p in (await client.get(f"{API}/products")).json() if p["sku"] == "CIR-PLAT")
    q = (await client.post(f"{API}/deals/{deal['id']}/quotes", json={"lines": [{"product_id": plat["id"], "quantity": 5}]})).json()
    return (await client.post(f"{API}/documents", json={"doc_type": "nda", "deal_id": deal["id"], "quote_id": q["id"]})).json()


async def test_signing_links_expire_and_countersigning_happens_in_cirra(client):
    doc = await _order_form(client)
    bad = await client.post(f"{API}/documents/{doc['id']}/send", json={"signers": [
        {"name": "Buyer", "email": "buyer@customer.example", "party": "customer"}, {"name": "Stranger", "email": "x@elsewhere.example", "party": "company"}]})
    assert bad.status_code == 422 and "active Cirra user" in bad.json()["detail"]
    sent = (await client.post(f"{API}/documents/{doc['id']}/send", json={"signers": [
        {"name": "Buyer", "email": "buyer@customer.example", "party": "customer"}, {"name": "Marcus Vance", "email": "marcus@cirra.demo", "party": "company"}]})).json()
    customer = next(s for s in sent["signers"] if s["party"] == "customer")
    company = next(s for s in sent["signers"] if s["party"] == "company")
    assert company["sign_url"] is None and customer["expires_at"] and not customer["link_expired"]
    async with SessionLocal() as db:  # company tokens exist but are refused on the public route
        co_token = (await db.get(SignatureRequest, uuid.UUID(company["id"]))).token
    assert (await client.get(f"{API}/sign/{co_token}")).status_code == 403
    assert (await client.post(f"{API}/sign/{co_token}", json={"signature_text": "Marcus Vance", "agree": True})).status_code == 403

    old = customer["sign_url"].rsplit("/", 1)[1]
    async with SessionLocal() as db:
        (await db.get(SignatureRequest, uuid.UUID(customer["id"]))).expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db.commit()
    assert (await client.get(f"{API}/sign/{old}")).status_code == 410
    assert (await client.post(f"{API}/sign/{old}", json={"signature_text": "Buyer", "agree": True})).status_code == 410
    assert (await client.post(f"{API}/sign/{old}/comments", json={"body": "Please revise"})).status_code == 410
    renewed = (await client.post(f"{API}/documents/{doc['id']}/signers/{customer['id']}/resend")).json()
    fresh = next(s for s in renewed["signers"] if s["party"] == "customer")["sign_url"].rsplit("/", 1)[1]
    assert fresh != old and (await client.get(f"{API}/sign/{old}")).status_code == 404
    assert (await client.post(f"{API}/sign/{fresh}", json={"signature_text": "Buyer", "agree": True})).status_code == 200

    async with login_as("priya@cirra.demo") as other:  # not the named countersigner
        assert (await other.post(f"{API}/documents/{doc['id']}/countersign", json={"signature_text": "Priya", "agree": True})).status_code in (403, 404)
    r = await client.post(f"{API}/documents/{doc['id']}/countersign", json={"signature_text": "Marcus Vance", "agree": True})
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert (await client.post(f"{API}/documents/{doc['id']}/countersign", json={"signature_text": "Marcus Vance", "agree": True})).status_code == 403


# ---- 5. the ERP connector doesn't invent data by default ------------------------------------------------------------

def test_erp_demo_is_off_by_default_and_refused_in_production(monkeypatch):
    for var in ("ERP_CONNECTOR", "JWT_SECRET", "DATA_ENCRYPTION_KEY", "ENVIRONMENT", "REDIS_URL"):
        monkeypatch.delenv(var, raising=False)
    strong = "k" * 48
    assert Settings(_env_file=None).erp_connector == "disabled"
    prod = Settings(_env_file=None, environment="production", jwt_secret=strong, data_encryption_key=strong[::-1], erp_connector="demo")
    assert any("ERP_CONNECTOR=demo" in p for p in security_problems(prod))
    with pytest.raises(RuntimeError, match="ERP_CONNECTOR=demo"):
        enforce_secure_settings(prod)
    enforce_secure_settings(Settings(_env_file=None, environment="production", jwt_secret=strong, data_encryption_key=strong[::-1],
                                     erp_connector="file"))


async def test_account_finance_says_when_erp_data_is_sample_data(client):
    acc = (await client.get(f"{API}/accounts")).json()[0]
    ar = (await client.get(f"{API}/finance/accounts/{acc['id']}/ar")).json()
    assert ar["demo_data"] is True  # the test workspace runs the demo connector


# ---- 6. notes sent to cloud AI services are masked --------------------------------------------------------------

async def test_cloud_embeddings_get_masked_text(monkeypatch):
    sent = []
    monkeypatch.setattr(embeddings.settings, "embedding_provider", "aws_bedrock")
    monkeypatch.setattr(embeddings, "_titan_embed", lambda text: sent.append(text) or [0.1] * 8)
    await embeddings.embed("Spoke with dana@acme.example on +44 20 7946 0958 about the renewal")
    assert "dana@acme.example" not in sent[0] and "7946" not in sent[0] and "renewal" in sent[0]

