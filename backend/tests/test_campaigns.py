"""Marketing campaigns: capture attribution, list building, responses, consent-checked email, unsubscribe, ROI."""
from app.services import campaigns
from tests.helpers import login_as

API = "/api/v1/campaigns"


def test_slug_and_rendering():
    assert campaigns.slug("  Q4 Pipeline — Webinar! ") == "q4-pipeline-webinar"
    out = campaigns.render("Hi {{ first_name }} at {{company}}", {"first_name": "Ana", "company": "Acme"}, "tok")
    assert out.startswith("Hi Ana at Acme") and out.rstrip().endswith("/unsubscribe/tok")
    explicit = campaigns.render("Bye: {{unsubscribe_url}}", {}, "tok")
    assert explicit.endswith("/unsubscribe/tok") and "--" not in explicit


async def test_seeded_campaigns_attribute_leads_and_compute_roi(client):
    async with login_as("nina@cirra.demo") as mkt:
        data = (await mkt.get(API)).json()
        by_code = {c["code"]: c for c in data["campaigns"]}
        assert {"emea-nurture", "saastr-2026", "q4-pipeline-webinar"} <= set(by_code)
        saastr = by_code["saastr-2026"]["metrics"]
        assert saastr["leads"] >= 1 and saastr["responses"] == saastr["by_status"]["responded"]
        if saastr["cost"]:
            assert saastr["roi_pct"] == round((saastr["influenced_won"] - saastr["cost"]) / saastr["cost"] * 100, 1)
        assert data["totals"]["members"] == sum(c["metrics"]["members"] for c in data["campaigns"])
        # marketing can't reach finance or admin
        assert (await mkt.get("/api/v1/performance/plans")).status_code == 403


async def test_captured_lead_joins_campaign_and_conversion_is_sourced(client):
    async with login_as("nina@cirra.demo") as mkt:
        r = await mkt.post(API, json={"name": "Ops Leaders Dinner", "campaign_type": "event", "status": "active", "actual_cost": 5000})
        assert r.status_code == 201 and r.json()["code"] == "ops-leaders-dinner"
        cid = r.json()["id"]
        assert (await mkt.post(API, json={"name": "ops leaders dinner"})).status_code == 409
        lead = (await mkt.post("/api/v1/leads", json={"first_name": "Ola", "last_name": "Berg", "email": "ola.berg@fjordfreight.example.no",
                                                      "company_name": "Fjord Freight", "domain": "fjordfreight.example.no", "country": "Norway",
                                                      "source": "campaign", "campaign": "Ops Leaders Dinner", "consent": "granted"})).json()
        members = (await mkt.get(f"{API}/{cid}/members")).json()
        assert members["total"] == 1 and members["members"][0]["status"] == "responded" and members["members"][0]["source"] == "capture"
    lead_id = lead["id"] if "id" in lead else lead["lead"]["id"]
    conv = await client.post(f"/api/v1/leads/{lead_id}/convert", json={"create_deal": True, "amount": 40000, "override": True})
    assert conv.status_code == 200, conv.text
    detail = (await client.get(f"{API}/{cid}")).json()
    assert detail["metrics"]["sourced_deals"] == 1 and detail["metrics"]["converted_leads"] == 1
    assert detail["metrics"]["deals"][0]["attribution"] == "sourced"
    assert detail["metrics"]["cost_per_lead"] == 5000


async def test_list_building_responses_email_and_unsubscribe(client):
    async with login_as("nina@cirra.demo") as mkt:
        cid = (await mkt.post(API, json={"name": "Retail Roundtable", "campaign_type": "webinar", "email_subject": "Hi {{first_name}}",
                                         "email_body": "Join us, {{first_name}} from {{company}}."})).json()["id"]
        bad = await mkt.post(f"{API}/{cid}/members/from-filter", json={"source": "contacts", "filters": [{"field": "nope", "op": "eq", "value": 1}]})
        assert bad.status_code == 422
        added = (await mkt.post(f"{API}/{cid}/members/from-filter", json={"source": "contacts", "filters": []})).json()
        assert added["added"] == added["matched"] > 0
        again = (await mkt.post(f"{API}/{cid}/members/from-filter", json={"source": "contacts", "filters": []})).json()
        assert again["added"] == 0 and again["skipped"] == again["matched"]
        preview = (await mkt.get(f"{API}/{cid}/email/preview")).json()
        assert preview["eligible"] + sum(preview["blocked"].values()) == added["added"]
        assert "/unsubscribe/" in preview["sample"]["body"] and preview["sample"]["subject"].startswith("Hi ")
        sent = (await mkt.post(f"{API}/{cid}/email/send")).json()
        assert sent["sent"] == preview["eligible"] and sent["skipped"] == preview["blocked"]
        assert (await mkt.get(f"{API}/{cid}/email/preview")).json()["eligible"] == 0  # nobody is emailed twice
        members = (await mkt.get(f"{API}/{cid}/members", params={"status": "sent"})).json()["members"]
        first = members[0]
        r = await mkt.patch(f"{API}/{cid}/members/{first['id']}", json={"status": "attended"})
        assert r.status_code == 200 and r.json()["responded_at"]
        # the person unsubscribes through their link
        from sqlalchemy import select

        from app.core.database import SessionLocal
        from app.models import CampaignMember, Contact
        async with SessionLocal() as db:
            m = await db.get(CampaignMember, __import__("uuid").UUID(members[1]["id"]))
            token, contact_id = m.token, m.contact_id
        assert (await client.get(f"/api/v1/public/unsubscribe/{token}")).json()["unsubscribed"] is False
        assert (await client.post(f"/api/v1/public/unsubscribe/{token}")).json()["unsubscribed"] is True
        assert (await client.post("/api/v1/public/unsubscribe/not-a-token")).status_code == 404
        async with SessionLocal() as db:
            assert (await db.execute(select(Contact.opt_out_email).where(Contact.id == contact_id))).scalar() is True
        r = await mkt.patch(f"{API}/{cid}/members/{members[1]['id']}", json={"status": "responded"})
        assert r.status_code == 422  # can't re-add an unsubscribed person
        stats = (await mkt.get(f"{API}/{cid}")).json()["metrics"]
        assert stats["by_status"]["attended"] == 1 and stats["by_status"]["unsubscribed"] == 1


async def test_sellers_read_campaigns_but_only_see_their_own_members(client):
    async with login_as("priya@cirra.demo") as ae:
        rows = (await ae.get(API)).json()["campaigns"]
        webinar = next(c for c in rows if c["code"] == "q4-pipeline-webinar")
        seen = (await ae.get(f"{API}/{webinar['id']}/members")).json()["total"]
        assert seen <= webinar["metrics"]["members"]
        assert (await ae.post(API, json={"name": "Rogue"})).status_code == 403
        assert (await ae.post(f"{API}/{webinar['id']}/email/send")).status_code == 403
    async with login_as("sam@cirra.demo") as sdr:
        assert (await sdr.post(f"{API}/{webinar['id']}/members/from-filter", json={"source": "leads", "filters": []})).status_code == 403
