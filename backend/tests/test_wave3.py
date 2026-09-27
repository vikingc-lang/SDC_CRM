"""Wave 3 platform flexibility: validation rules, field-level security, account sharing rules, custom objects and
configuration export / import."""
import uuid

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Account, CustomObject
from tests.helpers import login_as

API = "/api/v1"


async def _rule(admin, **body):
    r = await admin.post(f"{API}/admin/validation-rules", json={"applies_on": "both", **body})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_validation_rules_block_saves_by_people_but_not_the_system(client):
    async with login_as("admin@cirra.demo") as admin:
        rid = await _rule(admin, entity="accounts", name="Enterprise needs industry", message="Enterprise accounts need an industry",
                          conditions=[{"field": "tier", "op": "eq", "value": "Enterprise"}, {"field": "industry", "op": "is_empty"}])
        try:
            run = uuid.uuid4().hex[:6]
            bad = await client.post(f"{API}/accounts", json={"name": f"Rulebreaker {run}", "domain": f"rulebreaker-{run}.example.com", "tier": "Enterprise", "force": True})
            assert bad.status_code == 422 and bad.json()["detail"] == "Enterprise accounts need an industry"
            assert bad.json()["validation"][0]["rule"] == "Enterprise needs industry"
            async with SessionLocal() as db:
                assert (await db.execute(select(Account).where(Account.domain == f"rulebreaker-{run}.example.com"))).first() is None
            ok = await client.post(f"{API}/accounts", json={"name": f"Rulekeeper {run}", "domain": f"rulekeeper-{run}.example.com", "tier": "Enterprise",
                                                            "industry": "Retail", "force": True})
            assert ok.status_code == 201, ok.text
            acc = ok.json()["id"]
            cleared = await client.patch(f"{API}/accounts/{acc}", json={"industry": ""})
            assert cleared.status_code == 422
            assert (await client.get(f"{API}/accounts/{acc}/360")).json()["account"]["industry"] == "Retail"  # nothing was written
            async with SessionLocal() as db:  # background jobs and automation act as the system
                a = await db.get(Account, uuid.UUID(acc))
                a.industry = None
                await db.commit()
            count = (await admin.post(f"{API}/admin/validation-rules/test", json={"entity": "accounts", "conditions": [
                {"field": "tier", "op": "eq", "value": "Enterprise"}, {"field": "industry", "op": "is_empty"}]})).json()
            assert count["count"] >= 1
            # create-only rules leave edits alone
            await admin.put(f"{API}/admin/validation-rules/{rid}", json={"entity": "accounts", "name": "Enterprise needs industry",
                                                                        "message": "Enterprise accounts need an industry", "applies_on": "create",
                                                                        "conditions": [{"field": "tier", "op": "eq", "value": "Enterprise"},
                                                                                       {"field": "industry", "op": "is_empty"}]})
            assert (await client.patch(f"{API}/accounts/{acc}", json={"name": f"Rulekeeper Group {run}"})).status_code == 200
            for bad_rule in ({"entity": "accounts", "name": "x", "message": "x", "conditions": [{"field": "nope", "op": "eq", "value": 1}]},
                             {"entity": "quotes", "name": "x", "message": "x", "conditions": [{"field": "status", "op": "eq", "value": "draft"}]},
                             {"entity": "accounts", "name": "x", "message": "x", "conditions": []}):
                assert (await admin.post(f"{API}/admin/validation-rules", json=bad_rule)).status_code == 422
        finally:
            await admin.delete(f"{API}/admin/validation-rules/{rid}")


async def test_a_rule_whose_field_was_deleted_never_blocks(client):
    async with login_as("admin@cirra.demo") as admin:
        fid = (await admin.post(f"{API}/admin/custom-fields", json={"entity": "deal", "key": "doomed_flag", "label": "Doomed", "field_type": "boolean"})).json()["id"]
        rid = await _rule(admin, entity="deals", name="Doomed rule", message="blocked", conditions=[{"field": "cf_doomed_flag", "op": "is_empty"}])
        try:
            deal = (await client.get(f"{API}/deals")).json()[0]
            assert (await client.patch(f"{API}/deals/{deal['id']}", json={"po_number": "PO-RULE-1"})).status_code == 422
            await admin.delete(f"{API}/admin/custom-fields/{fid}")
            assert (await client.patch(f"{API}/deals/{deal['id']}", json={"po_number": "PO-RULE-1"})).status_code == 200
        finally:
            await admin.delete(f"{API}/admin/validation-rules/{rid}")


async def test_field_security_hides_and_locks_custom_fields_everywhere(client):
    async with login_as("admin@cirra.demo") as admin:
        fid = (await admin.post(f"{API}/admin/custom-fields", json={"entity": "account", "key": "discount_cap", "label": "Discount cap",
                                                                    "field_type": "number", "access": {"account_executive": "hidden", "sdr": "read"}})).json()["id"]
        await admin.post(f"{API}/admin/custom-fields", json={"entity": "account", "key": "fls_note", "label": "FLS note", "field_type": "text"})
        sid = (await admin.post(f"{API}/admin/custom-fields", json={"entity": "account", "key": "secret_code", "label": "Secret code",
                                                                    "field_type": "text", "access": {"account_executive": "hidden"}})).json()["id"]
    try:
        async with login_as("priya@cirra.demo") as ae:
            acc = (await ae.get(f"{API}/accounts")).json()[0]["id"]
        assert (await client.patch(f"{API}/accounts/{acc}", json={"custom_fields": {"discount_cap": 12, "secret_code": "zebracorn"}})).status_code == 200
        hits = (await client.post(f"{API}/search/semantic", json={"query": "zebracorn"})).json()
        assert any(h["entity"] == "account" and str(h["id"]) == acc for h in hits)
        mgr = (await client.get(f"{API}/accounts/{acc}/360")).json()
        assert mgr["account"]["custom_fields"]["discount_cap"] == 12
        async with login_as("priya@cirra.demo") as ae:
            view = (await ae.get(f"{API}/accounts/{acc}/360")).json()
            assert "discount_cap" not in view["account"]["custom_fields"]
            assert all(d["key"] != "discount_cap" for d in view["custom_field_definitions"])
            denied = await ae.patch(f"{API}/accounts/{acc}", json={"custom_fields": {"discount_cap": 50}})
            assert denied.status_code == 422 and "can't change" in denied.json()["detail"]
            assert (await ae.patch(f"{API}/accounts/{acc}", json={"custom_fields": {"fls_note": "kept"}})).status_code == 200
            cat = (await ae.get(f"{API}/analytics/sources")).json()
            assert not any(f["key"] == "cf_discount_cap" for s in cat["sources"] if s["key"] == "accounts" for f in s["fields"])
            r = await ae.post(f"{API}/analytics/run", json={"definition": {"source": "accounts", "columns": ["name"],
                                                                           "filters": [{"field": "cf_discount_cap", "op": "gt", "value": 1}]}})
            assert r.status_code == 422 and "can't see" in r.json()["detail"]
            listed = (await ae.post(f"{API}/analytics/run", json={"definition": {"source": "accounts", "columns": ["name", "cf_discount_cap"]}})).json()
            assert [c["key"] for c in listed["columns"]] == ["name"]
            hits = (await ae.post(f"{API}/search/semantic", json={"query": "zebracorn"})).json()
            assert not any(h["entity"] == "account" and str(h["id"]) == acc for h in hits)  # a hidden value can't be searched for
        assert (await client.get(f"{API}/accounts/{acc}/360")).json()["account"]["custom_fields"]["discount_cap"] == 12  # kept on Priya's save
        async with login_as("sam@cirra.demo") as sdr:  # read-only: visible, flagged, not editable
            defs = {d["key"]: d for d in (await sdr.get(f"{API}/accounts/{acc}/360")).json().get("custom_field_definitions", [])}
            if defs:  # only if the SDR can see this account at all
                assert defs["discount_cap"]["read_only"] is True
    finally:
        async with login_as("admin@cirra.demo") as admin:
            await admin.put(f"{API}/admin/custom-fields/{fid}", json={"label": "Discount cap", "access": {}})
            await admin.delete(f"{API}/admin/custom-fields/{sid}")


async def test_sharing_rules_open_matching_accounts_to_a_role(client):
    found = (await client.post(f"{API}/analytics/run", json={"definition": {"source": "accounts", "columns": ["name"], "with_ids": True, "limit": 2000,
                                                                            "filters": [{"field": "region", "op": "eq", "value": "EMEA"}]}})).json()
    emea = [{"id": i} for i in found["ids"]]
    async with login_as("priya@cirra.demo") as ae:
        mine = {a["id"] for a in (await ae.get(f"{API}/accounts", params={"limit": 500})).json()}
        hidden = next(a for a in emea if a["id"] not in mine)
        assert (await ae.get(f"{API}/accounts/{hidden['id']}/360")).status_code == 404
    async with login_as("admin@cirra.demo") as admin:
        preview = (await admin.post(f"{API}/admin/sharing-rules/test", json={"criteria": [{"field": "region", "op": "eq", "value": "EMEA"}]})).json()
        assert preview["count"] >= len(emea) > 0
        assert (await admin.post(f"{API}/admin/sharing-rules", json={"name": "x", "criteria": [{"field": "region", "op": "eq", "value": "EMEA"}],
                                                                    "roles": ["super_admin"]})).status_code == 422
        rule = (await admin.post(f"{API}/admin/sharing-rules", json={"name": "EMEA to AEs", "criteria": [{"field": "region", "op": "eq", "value": "EMEA"}],
                                                                    "roles": ["account_executive"]})).json()
    try:
        async with login_as("priya@cirra.demo") as ae:
            assert (await ae.get(f"{API}/accounts/{hidden['id']}/360")).status_code == 200
            now = {a["id"] for a in (await ae.get(f"{API}/accounts", params={"limit": 500})).json()}
            assert {a["id"] for a in emea} <= now
        async with login_as("sam@cirra.demo") as sdr:  # a role the rule doesn't name is unaffected
            assert (await sdr.get(f"{API}/accounts/{hidden['id']}/360")).status_code == 404
        async with login_as("admin@cirra.demo") as admin:
            await admin.put(f"{API}/admin/sharing-rules/{rule['id']}", json={**{k: rule[k] for k in ("name", "criteria", "roles")}, "active": False})
        async with login_as("priya@cirra.demo") as ae:
            assert (await ae.get(f"{API}/accounts/{hidden['id']}/360")).status_code == 404
    finally:
        async with login_as("admin@cirra.demo") as admin:
            await admin.delete(f"{API}/admin/sharing-rules/{rule['id']}")


async def test_custom_objects_end_to_end(client):
    async with login_as("admin@cirra.demo") as admin:
        assert (await admin.post(f"{API}/objects", json={"key": "deals", "label": "x", "plural_label": "x"})).status_code == 422
        obj = (await admin.post(f"{API}/objects", json={"key": "equipment", "label": "Equipment item", "plural_label": "Equipment",
                                                        "description": "Installed hardware at customer sites"})).json()
        assert obj["source"] == "obj_equipment"
        for f in ({"key": "serial", "label": "Serial number", "field_type": "text", "required": True},
                  {"key": "status", "label": "Status", "field_type": "select", "options": ["Active", "Retired"]},
                  {"key": "warranty_end", "label": "Warranty end", "field_type": "date"}):
            assert (await admin.post(f"{API}/admin/custom-fields", json={"entity": "object:equipment", **f})).status_code == 201
        assert (await admin.post(f"{API}/admin/custom-fields", json={"entity": "object:nothing", "key": "x", "label": "x", "field_type": "text"})).status_code == 422
    async with login_as("priya@cirra.demo") as ae:
        acc = (await ae.get(f"{API}/accounts")).json()[0]
        objs = (await ae.get(f"{API}/objects")).json()
        assert [o["key"] for o in objs if o["key"] == "equipment"] and len(next(o for o in objs if o["key"] == "equipment")["fields"]) == 3
        missing = await ae.post(f"{API}/objects/equipment/records", json={"name": "Scanner", "account_id": acc["id"], "data": {"status": "Active"}})
        assert missing.status_code == 422 and "Serial number" in missing.json()["detail"]
        rec = (await ae.post(f"{API}/objects/equipment/records", json={"name": "Scanner X1", "account_id": acc["id"],
                                                                       "data": {"serial": "SN-001", "status": "Active", "warranty_end": "2027-03-31"}})).json()
        assert rec["account"]["name"] == acc["name"] and rec["owner"]["full_name"] == "Priya Raman"
        await ae.post(f"{API}/objects/equipment/records", json={"name": "Printer P2", "account_id": acc["id"], "data": {"serial": "SN-002", "status": "Retired"}})
        assert (await ae.patch(f"{API}/objects/equipment/records/{rec['id']}", json={"data": {"status": "Broken"}})).status_code == 422
        upd = (await ae.patch(f"{API}/objects/equipment/records/{rec['id']}", json={"data": {"status": "Retired"}})).json()
        assert upd["data"] == {"serial": "SN-001", "status": "Retired", "warranty_end": "2027-03-31"}
        on_acc = (await ae.get(f"{API}/objects/equipment/records", params={"account_id": acc["id"]})).json()["records"]
        assert {r["name"] for r in on_acc} >= {"Scanner X1", "Printer P2"}
        report = (await ae.post(f"{API}/analytics/run", json={"definition": {"source": "obj_equipment", "group_by": [{"field": "cf_status"}],
                                                                              "measures": [{"agg": "count"}]}})).json()
        assert dict((r[0], r[1]) for r in report["rows"])["Retired"] >= 2
        grid = (await ae.post(f"{API}/views/run", json={"source": "obj_equipment", "columns": ["name", "cf_serial", "account"]})).json()
        assert grid["link"] == "/objects/equipment/{id}" and len(grid["ids"]) >= 2
    async with login_as("diego@cirra.demo") as other:  # another rep's records on an account he can't see
        assert (await other.get(f"{API}/objects/equipment/records/{rec['id']}")).status_code == 404
        assert all(r["id"] != rec["id"] for r in (await other.get(f"{API}/objects/equipment/records")).json()["records"])
    async with login_as("admin@cirra.demo") as admin:
        rid = await _rule(admin, entity="obj_equipment", name="Retired needs warranty date", message="Retired equipment needs a warranty end date",
                          conditions=[{"field": "cf_status", "op": "eq", "value": "Retired"}, {"field": "cf_warranty_end", "op": "is_empty"}])
    async with login_as("priya@cirra.demo") as ae:
        blocked = await ae.post(f"{API}/objects/equipment/records", json={"name": "Old router", "data": {"serial": "SN-9", "status": "Retired"}})
        assert blocked.status_code == 422 and blocked.json()["detail"] == "Retired equipment needs a warranty end date"
    async with login_as("admin@cirra.demo") as admin:
        await admin.delete(f"{API}/admin/validation-rules/{rid}")
        rep = (await admin.post(f"{API}/analytics/reports", json={"name": "Equipment count", "visibility": "shared",
                                                                  "definition": {"source": "obj_equipment", "measures": [{"agg": "count"}]}})).json()["id"]
        board = (await admin.post(f"{API}/analytics/dashboards", json={"name": "Equipment board", "tiles": [{"report_id": rep}]})).json()["id"]
        assert (await admin.delete(f"{API}/objects/equipment")).status_code == 409  # has records
        assert (await admin.delete(f"{API}/objects/equipment", params={"confirm": "true"})).status_code == 204
        assert all(f["entity"] != "object:equipment" for f in (await admin.get(f"{API}/admin/custom-fields")).json())
        assert (await admin.get(f"{API}/analytics/reports/{rep}")).status_code == 404  # its reports go with it...
        assert (await admin.get(f"{API}/analytics/dashboards/{board}")).json()["tile_count"] == 0  # ...and off dashboards
    assert not any(s["key"] == "obj_equipment" for s in (await client.get(f"{API}/analytics/sources")).json()["sources"])


async def test_config_export_import_round_trip_and_all_or_nothing(client):
    sv = f"site_visit_{uuid.uuid4().hex[:5]}"
    async with login_as("admin@cirra.demo") as admin:
        bundle = (await admin.get(f"{API}/admin/config/export")).json()
        assert bundle["format"] == "cirra-config" and all(k in bundle for k in ("custom_fields", "workflows", "role_permissions", "reports"))
        assert not any(p["role"] == "super_admin" for p in bundle["role_permissions"])
        same = (await admin.post(f"{API}/admin/config/import", json={"bundle": bundle})).json()
        assert same["dry_run"] and not same["applied"] and same["summary"]["create"] == 0 and same["summary"]["error"] == 0
        extra = {**bundle,
                 "custom_objects": [*bundle["custom_objects"], {"key": sv, "label": "Site visit", "plural_label": "Site visits"}],
                 "custom_fields": [*bundle["custom_fields"], {"entity": f"object:{sv}", "key": "outcome", "label": "Outcome",
                                                               "field_type": "select", "options": ["Good", "Bad"]}],
                 "validation_rules": [*bundle["validation_rules"], {"entity": f"obj_{sv}", "name": f"Outcome needed {sv}", "message": "Pick an outcome",
                                                                     "conditions": [{"field": "cf_outcome", "op": "is_empty"}]}],
                 "reports": [*bundle["reports"], {"name": f"Site visits by outcome {sv}", "definition": {"source": f"obj_{sv}",
                                                  "group_by": [{"field": "cf_outcome"}], "measures": [{"agg": "count"}]}}],
                 "dashboards": [*bundle["dashboards"], {"name": f"Field ops {sv}", "tiles": [{"report": f"Site visits by outcome {sv}", "size": "half"}]}]}
        preview = (await admin.post(f"{API}/admin/config/import", json={"bundle": extra})).json()
        assert preview["summary"]["create"] == 5 and preview["summary"]["error"] == 0, preview
        async with SessionLocal() as db:  # a dry run writes nothing
            assert (await db.execute(select(CustomObject).where(CustomObject.key == sv))).first() is None
        broken = {**extra, "validation_rules": [*extra["validation_rules"], {"entity": f"obj_{sv}", "name": "Bad", "message": "x",
                                                                             "conditions": [{"field": "cf_missing", "op": "is_empty"}]}]}
        res = (await admin.post(f"{API}/admin/config/import", json={"bundle": broken, "dry_run": False})).json()
        assert not res["applied"] and res["summary"]["error"] == 1
        async with SessionLocal() as db:
            assert (await db.execute(select(CustomObject).where(CustomObject.key == sv))).first() is None
        done = (await admin.post(f"{API}/admin/config/import", json={"bundle": extra, "dry_run": False})).json()
        assert done["applied"] and done["summary"]["create"] == 5
        assert any(o["key"] == sv for o in (await admin.get(f"{API}/objects")).json())
        again = (await admin.post(f"{API}/admin/config/import", json={"bundle": extra})).json()
        assert again["summary"]["create"] == 0 and again["summary"]["update"] == 0
        assert (await admin.post(f"{API}/admin/config/import", json={"bundle": {"format": "other"}})).status_code == 422
        await admin.delete(f"{API}/objects/{sv}", params={"confirm": "true"})
    assert (await client.post(f"{API}/admin/config/import", json={"bundle": bundle})).status_code == 403  # a sales manager


async def test_repeated_names_round_trip_unchanged(client):
    tag = uuid.uuid4().hex[:5]
    ids = []
    for src in ("deals", "leads"):  # two shared reports and two shared dashboards with the same names
        ids.append((await client.post(f"{API}/analytics/reports", json={"name": f"Twin {tag}", "visibility": "shared",
                                                                         "definition": {"source": src, "measures": [{"agg": "count"}]}})).json()["id"])
    for rid in ids:
        await client.post(f"{API}/analytics/dashboards", json={"name": f"Twin board {tag}", "visibility": "shared", "tiles": [{"report_id": rid}]})
    async with login_as("admin@cirra.demo") as admin:
        bundle = (await admin.get(f"{API}/admin/config/export")).json()
        res = (await admin.post(f"{API}/admin/config/import", json={"bundle": bundle})).json()
    changed = [i for i in res["items"] if i["action"] != "unchanged"]
    assert changed == [], changed
