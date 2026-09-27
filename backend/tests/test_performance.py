"""Territories, quotas, attainment and tiered commission."""
import pytest

from app.services import performance
from tests.helpers import login_as

API = "/api/v1/performance"
AE_TIERS = [{"from_pct": 100, "rate": 8}, {"from_pct": 125, "rate": 10}]


def test_commission_is_bracketed_by_attainment():
    # 100k quota: 100k at 5%, 25k at 8%, 25k at 10%
    out = performance.commission(150_000, 100_000, 5, AE_TIERS)
    assert out["total"] == pytest.approx(5_000 + 2_000 + 2_500)
    assert [line["rate"] for line in out["lines"]] == [5, 8, 10]
    assert performance.commission(80_000, 100_000, 5, AE_TIERS)["total"] == pytest.approx(4_000)
    assert performance.commission(50_000, 0, 5, AE_TIERS)["total"] == pytest.approx(2_500)  # no quota: base rate only
    assert performance.commission(0, 100_000, 5, AE_TIERS) == {"total": 0.0, "lines": []}


def test_statement_lines_add_up_and_show_the_accelerator():
    won = [{"id": i, "title": f"D{i}", "account": "A", "amount_usd": 60_000.0, "closed_at": f"2026-0{i}-01"} for i in (1, 2, 3)]
    lines = performance.statement(won, 100_000, 5, AE_TIERS)
    assert sum(line["commission"] for line in lines) == pytest.approx(performance.commission(180_000, 100_000, 5, AE_TIERS)["total"])
    assert lines[0]["effective_rate"] == 5 and lines[-1]["effective_rate"] > 8  # the last deal lands above 125%
    assert lines[-1]["attainment_after"] == 180.0


def test_territory_criteria_validation_and_matching():
    with pytest.raises(performance.PerformanceError):
        performance.clean_criteria({"tiers": ["Huge"]})
    with pytest.raises(performance.PerformanceError):
        performance.clean_criteria({"min_employees": 500, "max_employees": 10})
    with pytest.raises(performance.PerformanceError):
        performance.clean_tiers([{"from_pct": 100, "rate": 8}, {"from_pct": 100, "rate": 9}], 5)
    c = performance.clean_criteria({"regions": ["EMEA", " "], "min_employees": "1000"})
    assert c == {"regions": ["EMEA"], "min_employees": 1000}

    class A:  # minimal account
        region, country, industry, tier, employee_count = "EMEA", "Germany", "Retail", "Enterprise", 5000
    assert performance.matches(c, A) and performance.matches({}, A)
    A.employee_count = 200
    assert not performance.matches(c, A)


async def test_rep_scorecard_uses_quota_plan_and_closed_bookings(client):
    async with login_as("priya@cirra.demo") as ae:
        card = (await ae.get(f"{API}/me")).json()
        assert card["quota"] == 350000 and card["plan"]["name"] == "Account Executive 2026"
        assert card["closed"] == pytest.approx(sum(s["amount_usd"] for s in card["statement"]))
        assert card["commission"]["total"] == pytest.approx(sum(s["commission"] for s in card["statement"]), abs=0.05)
        if card["closed"]:
            assert card["attainment_pct"] == pytest.approx(round(card["closed"] / 350000 * 100, 1))
        assert (await ae.get(f"{API}/team")).status_code == 403
        assert (await ae.put(f"{API}/quotas", json=[{"period": card["period"], "user_id": str(card["user"]["id"]), "amount": 1}])).status_code == 403


async def test_manager_sets_quotas_for_the_team_only(client):
    team = (await client.get(f"{API}/team")).json()
    names = {r["user"]["name"] for r in team["rows"]}
    assert {"Priya Raman", "Diego Alvarez"} <= names
    quotas = (await client.get(f"{API}/quotas", params={"period": team["period"]})).json()
    sam = next(r for r in quotas["rows"] if r["user"]["name"] == "Sam Okoye")
    assert sam["amount"] is None
    r = await client.put(f"{API}/quotas", json=[{"period": team["period"], "user_id": str(sam["user"]["id"]), "amount": 120000}])
    assert r.status_code == 200
    after = (await client.get(f"{API}/team")).json()
    assert next(x for x in after["rows"] if x["user"]["name"] == "Sam Okoye")["quota"] == 120000
    me = (await client.get("/api/v1/users/me")).json()
    assert (await client.put(f"{API}/quotas", json=[{"period": team["period"], "user_id": me["id"], "amount": 5}])).status_code == 404
    assert (await client.put(f"{API}/quotas", json=[{"period": "2026-Q9", "user_id": str(sam["user"]["id"]), "amount": 5}])).status_code == 422


async def test_territories_realign_new_accounts_and_reports(client):
    async with login_as("admin@cirra.demo") as admin:
        before = (await admin.get(f"{API}/territories")).json()
        assert {t["name"] for t in before["territories"]} >= {"NA Enterprise", "EMEA", "International"}
        assert before["unassigned"] == 0  # the catch-all covers everyone
        r = await admin.post(f"{API}/territories", json={"name": "DACH Retail", "priority": 10,
                                                         "criteria": {"countries": ["Germany", "Austria", "Switzerland"], "industries": ["Retail"]}})
        assert r.status_code == 201
        tid = r.json()["id"]
        assert (await admin.post(f"{API}/territories", json={"name": "dach retail"})).status_code == 409
        acc = (await admin.post("/api/v1/accounts", json={"name": "Kaufhaus Nord", "domain": "kaufhaus-nord.example.de", "industry": "Retail",
                                                          "country": "Germany", "force": True})).json()
        detail = (await admin.get(f"/api/v1/accounts/{acc['id']}/360")).json()
        assert detail["account"]["territory"]["name"] == "DACH Retail"
        preview = (await admin.post(f"{API}/territories/realign")).json()
        assert preview["applied"] is False
        res = (await admin.post("/api/v1/analytics/run", json={"definition": {"source": "accounts", "group_by": [{"field": "territory"}],
                                                                             "measures": [{"agg": "count"}]}})).json()
        assert dict((r[0], r[1]) for r in res["rows"])["DACH Retail"] >= 1
        assert (await admin.put(f"{API}/territories/{tid}", json={"name": "DACH Retail", "parent_id": tid})).status_code == 422  # cycle
        assert (await admin.delete(f"{API}/territories/{tid}")).status_code == 204
        detail = (await admin.get(f"/api/v1/accounts/{acc['id']}/360")).json()
        assert detail["account"]["territory"] is None
        applied = (await admin.post(f"{API}/territories/realign", params={"apply": True})).json()
        assert any(c["account"] == "Kaufhaus Nord" for c in applied["changes"])
    async with login_as("priya@cirra.demo") as ae:
        assert (await ae.post(f"{API}/territories", json={"name": "Mine"})).status_code == 403


async def test_commission_plan_editor_validates_and_previews(client):
    async with login_as("admin@cirra.demo") as admin:
        bad = await admin.post(f"{API}/plans", json={"name": "Bad", "base_rate": 5, "tiers": [{"from_pct": -5, "rate": 8}]})
        assert bad.status_code == 422
        assert (await admin.post(f"{API}/plans", json={"name": "X", "base_rate": 5, "roles": ["pilot"]})).status_code == 422
        prev = (await admin.post(f"{API}/plans/preview", json={"base_rate": 5, "tiers": AE_TIERS, "quota": 100000, "bookings": 150000})).json()
        assert prev["total"] == pytest.approx(9500)
        r = await admin.post(f"{API}/plans", json={"name": "SDR pilot", "base_rate": 2, "roles": ["sdr"]})
        assert r.status_code == 201
        assert any(p["name"] == "SDR pilot" for p in (await admin.get(f"{API}/plans")).json())
        assert (await admin.delete(f"{API}/plans/{r.json()['id']}")).status_code == 204
