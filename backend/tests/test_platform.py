"""Pillars 1, 2 and 10: RBAC, audit trail, privacy, custom fields, dedup, hierarchy, import/export."""
import pytest
from sqlalchemy import text

from tests.helpers import login_as


async def _account(client, name):
    rows = (await client.get("/api/v1/accounts", params={"search": name, "limit": 50})).json()
    return next(a for a in rows if a["name"] == name)


async def test_row_level_ownership_isolation(client):
    apex = await _account(client, "Apex Industrial Supply")  # owned by Marcus
    async with login_as("priya@cirra.demo") as ae:
        names = {a["name"] for a in (await ae.get("/api/v1/accounts", params={"limit": 200})).json()}
        assert "Northwind Logistics" in names and "Apex Industrial Supply" not in names
        assert (await ae.get(f"/api/v1/accounts/{apex['id']}/360")).status_code == 404  # not 403: existence isn't leaked
        assert (await ae.delete(f"/api/v1/accounts/{apex['id']}")).status_code == 403  # AEs cannot delete accounts
    async with login_as("sam@cirra.demo") as sdr:
        deals = (await sdr.get("/api/v1/deals")).json()
        assert {d["title"] for d in deals} == {"Quarry Labs: 25 seats"}  # only the SDR's own records
        assert (await sdr.patch(f"/api/v1/deals/{deals[0]['id']}", json={"title": "x"})).status_code == 403  # SDR: create/read only


async def test_partner_users_are_confined_to_portal(client):
    async with login_as("partner@northstar-partners.com") as partner:
        assert (await partner.get("/api/v1/accounts")).status_code == 403
        me = (await partner.get("/api/v1/portal/me")).json()
        assert me["partner"]["name"] == "Northstar Partners"


async def test_permission_matrix_is_editable_and_enforced(client):
    matrix = (await client.get("/api/v1/admin/permissions")).json()
    assert set(r["key"] for r in matrix["roles"]) >= {"super_admin", "sales_manager", "account_executive", "sdr", "auditor"}
    async with login_as("admin@cirra.demo") as admin:
        row = {"role": "sdr", "resource": "reports", "can_read": False, "scope": "own"}
        assert (await admin.put("/api/v1/admin/permissions", json=[row])).status_code == 200
        async with login_as("sam@cirra.demo") as sdr:
            assert (await sdr.get("/api/v1/reports/win-loss")).status_code == 403
        await admin.put("/api/v1/admin/permissions", json=[{**row, "can_read": True}])
        # lock-out protection
        bad = {"role": "super_admin", "resource": "admin", "can_read": False, "can_update": False, "scope": "all"}
        assert (await admin.put("/api/v1/admin/permissions", json=[bad])).status_code == 422


async def test_audit_trail_is_field_level_and_append_only(client):
    acc = await _account(client, "Vertex Manufacturing")
    await client.patch(f"/api/v1/accounts/{acc['id']}", json={"industry": "Precision Manufacturing"})
    rows = (await client.get("/api/v1/admin/audit", params={"record_id": acc["id"], "field": "industry"})).json()["items"]
    assert rows[0]["old_value"] == "Manufacturing" and rows[0]["new_value"] == "Precision Manufacturing"
    assert rows[0]["user"] == "Marcus Vance"
    from app.core.database import SessionLocal

    async with SessionLocal() as db:
        with pytest.raises(Exception, match="append-only"):
            await db.execute(text("UPDATE audit_log SET new_value = 'tampered' WHERE id = :id"), {"id": rows[0]["id"]})
        await db.rollback()
        with pytest.raises(Exception, match="append-only"):
            await db.execute(text("DELETE FROM audit_log"))


async def test_consent_blocks_outreach_and_erasure_crypto_shreds_pii(client):
    acc = await _account(client, "Summit Foods Co.")
    c = (await client.post("/api/v1/contacts", json={"account_id": acc["id"], "first_name": "Petra", "last_name": "Novak", "email": "petra.novak@summitfoods.com",
                                                      "phone": "+1 612 555 0199", "privacy_regime": "GDPR", "timezone": "Europe/Prague"})).json()
    # GDPR with no consent and no legitimate-interest basis -> email blocked
    resp = await client.post("/api/v1/email/send", json={"contact_id": c["id"], "subject": "Hello", "body": "Hi Petra"})
    assert resp.status_code == 403 and "GDPR" in resp.json()["detail"]
    await client.post(f"/api/v1/contacts/{c['id']}/consent", json={"consent_email": "granted", "basis": "consent", "source": "webinar form"})
    assert (await client.post("/api/v1/email/send", json={"contact_id": c["id"], "subject": "Hello", "body": "Hi Petra"})).status_code == 201
    await client.post(f"/api/v1/contacts/{c['id']}/consent", json={"opt_out_email": True})
    assert (await client.post("/api/v1/email/send", json={"contact_id": c["id"], "subject": "Again", "body": "x"})).status_code == 403

    await client.patch(f"/api/v1/contacts/{c['id']}", json={"phone": "+1 612 555 0100"})
    audit = (await client.get("/api/v1/admin/audit", params={"record_id": c["id"], "field": "phone"})).json()["items"]
    assert audit[0]["encrypted"] and audit[0]["old_value"] == "+1 612 555 0199"  # decryptable while the key exists

    assert (await client.post(f"/api/v1/contacts/{c['id']}/erase", json={"regulation": "GDPR"})).status_code == 422  # needs confirm
    erased = (await client.post(f"/api/v1/contacts/{c['id']}/erase", json={"regulation": "GDPR", "confirm": True})).json()
    assert erased["key_destroyed"] and len(erased["subject_hash"]) == 64
    profile = (await client.get(f"/api/v1/contacts/{c['id']}")).json()
    assert profile["status"] == "erased" and profile["email"] is None and profile["name"] == "Erased Contact"
    audit = (await client.get("/api/v1/admin/audit", params={"record_id": c["id"], "field": "phone"})).json()["items"]
    assert all(r["old_value"] in ("[crypto-shredded]", None) for r in audit)
    events = [e["event_type"] for e in profile["consent_events"]]
    assert {"consent_granted", "opt_out", "erased"} <= set(events)


async def test_custom_fields_are_typed(client):
    async with login_as("admin@cirra.demo") as admin:
        r = await admin.post("/api/v1/admin/custom-fields", json={"entity": "account", "key": "fleet_size", "label": "Fleet size", "field_type": "number"})
        assert r.status_code == 201
    acc = await _account(client, "Northwind Logistics")
    assert (await client.patch(f"/api/v1/accounts/{acc['id']}", json={"custom_fields": {"fleet_size": "lots"}})).status_code == 422
    assert (await client.patch(f"/api/v1/accounts/{acc['id']}", json={"custom_fields": {"fleet_size": "1200", "erp_region": "EMEA"}})).status_code == 200
    view = (await client.get(f"/api/v1/accounts/{acc['id']}/360")).json()
    assert view["account"]["custom_fields"]["fleet_size"] == 1200.0
    assert (await client.patch(f"/api/v1/accounts/{acc['id']}", json={"custom_fields": {"erp_region": "LATAM"}})).status_code == 422


async def test_duplicate_detection_and_merge_rules(client):
    # creating an obvious duplicate is intercepted
    r = await client.post("/api/v1/accounts", json={"name": "Apex Industrial Supply Inc.", "domain": "apexindustrial.co"})
    assert r.status_code == 409 and r.json()["duplicate"]["account"]["name"].startswith("Apex Industrial")
    cands = (await client.get("/api/v1/admin/dedup")).json()["accounts"]
    pair = next(c for c in cands if {c["a"]["name"], c["b"]["name"]} == {"Apex Industrial Supply", "Apex Industrial Supplies"})
    assert pair["similarity"]["jaro_winkler"] > 0.95 and not pair["auto_mergeable"]
    survivor = pair["a"] if pair["a"]["name"] == "Apex Industrial Supply" else pair["b"]
    merged = pair["b"] if survivor is pair["a"] else pair["a"]
    res = (await client.post("/api/v1/admin/dedup/merge", json={"entity": "account", "survivor_id": survivor["id"], "merged_id": merged["id"]})).json()
    assert res["status"] == "merged" and res["field_resolution"]["alt_domains"] == "union"
    view = (await client.get(f"/api/v1/accounts/{survivor['id']}/360")).json()
    assert "apexindustrialsupply.com" in view["account"]["alt_domains"]
    # the duplicate Elena (no email) now sits next to the real one -> auto-mergeable contact pair
    contact_pair = next(c for c in (await client.get("/api/v1/admin/dedup")).json()["contacts"] if c["a"]["name"] == c["b"]["name"] == "Elena Rostova")
    assert contact_pair["auto_mergeable"]
    async with login_as("admin@cirra.demo") as admin:
        assert (await admin.post("/api/v1/admin/dedup/auto")).json()["contacts"] >= 1
    elenas = [c for c in (await client.get("/api/v1/contacts", params={"search": "Elena"})).json() if c["name"] == "Elena Rostova"]
    assert len(elenas) == 1 and elenas[0]["email"] == "elena.rostova@apexindustrial.com" and elenas[0]["buying_role"] == "Decision Maker"


async def test_hierarchy_rollups(client):
    helios = await _account(client, "Helios Energy")
    tree = (await client.get(f"/api/v1/accounts/{helios['id']}/hierarchy")).json()
    assert tree["root"]["name"] == "Helios Group"
    energy = tree["root"]["children"][0]
    assert energy["name"] == "Helios Energy" and energy["children"][0]["name"] == "Helios Renewables"
    assert tree["root"]["rollup"]["contract_spend"] >= 138000  # subsidiary contract rolls up to the group
    assert tree["root"]["rollup"]["open_pipeline"] >= energy["open_pipeline"]
    # cycles are refused
    group = await _account(client, "Helios Group")
    renew = await _account(client, "Helios Renewables")
    assert (await client.patch(f"/api/v1/accounts/{group['id']}", json={"parent_id": renew["id"]})).status_code == 422


async def test_import_auto_maps_validates_and_rolls_back(client):
    before = len((await client.get("/api/v1/accounts", params={"limit": 500})).json())
    good = "Company Name,Website,Employees,Annual Revenue,Parent Company\nNova Mining,novamining.com,900,120000000,\nNova Mining Chile,novamining.cl,120,,novamining.com\n"
    prev = (await client.post("/api/v1/admin/import/accounts/preview", files={"file": ("a.csv", good, "text/csv")})).json()
    assert prev["mapping"] == {"Company Name": "name", "Website": "domain", "Employees": "employee_count", "Annual Revenue": "annual_revenue", "Parent Company": "parent_domain"}
    assert prev["rows_valid"] == 2 and not prev["errors"]
    bad = good + "Broken Co,not-a-domain,abc,,\n"
    r = await client.post("/api/v1/admin/import/accounts/commit", files={"file": ("a.csv", bad, "text/csv")})
    assert r.status_code == 422 and r.json()["detail"]["rolled_back"]
    assert len(r.json()["detail"]["errors"][0]["errors"]) == 2  # bad domain + bad employee count on row 4
    assert len((await client.get("/api/v1/accounts", params={"limit": 500})).json()) == before  # nothing written
    ok = (await client.post("/api/v1/admin/import/accounts/commit", files={"file": ("a.csv", good, "text/csv")})).json()
    assert ok["created"] == 2
    chile = await _account(client, "Nova Mining Chile")
    tree = (await client.get(f"/api/v1/accounts/{chile['id']}/hierarchy")).json()
    assert tree["root"]["name"] == "Nova Mining"


async def test_export_respects_scope_and_is_audited(client):
    async with login_as("viewer@cirra.demo") as auditor:
        r = await auditor.get("/api/v1/admin/export/accounts", params={"format": "json"})
        assert r.status_code == 200 and len(r.json()) >= 10
    async with login_as("priya@cirra.demo") as ae:
        assert (await ae.get("/api/v1/admin/export/accounts")).status_code == 403  # AEs have no export right by default
    log = (await client.get("/api/v1/admin/audit", params={"action": "export"})).json()["items"]
    assert log and log[0]["entity"] == "accounts"
