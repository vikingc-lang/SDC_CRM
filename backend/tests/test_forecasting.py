"""Forecast calls: categories, quarterly roll-ups, overrides, rep submissions and manager adjustments."""
from datetime import date

import pytest

from app.services import forecasting
from tests.helpers import login_as

API = "/api/v1/forecast"


def test_periods_and_ranges():
    assert forecasting.period_range("2026-Q4") == (date(2026, 10, 1), date(2026, 12, 31))
    assert forecasting.period_range("2027-Q1") == (date(2027, 1, 1), date(2027, 3, 31))
    assert forecasting.period_of(date(2026, 9, 26)) == "2026-Q3"
    ps = forecasting.periods(date(2026, 1, 15))
    assert [p["period"] for p in ps] == ["2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3"] and ps[1]["current"]
    with pytest.raises(forecasting.ForecastError):
        forecasting.period_range("2026-Q5")


async def _busiest_period(c) -> tuple[str, dict]:
    best = None
    for p in (await c.get(f"{API}/periods")).json()["periods"]:
        view = (await c.get(f"{API}/me", params={"period": p["period"]})).json()
        if best is None or len(view["deals"]) > len(best[1]["deals"]):
            best = (p["period"], view)
    return best


async def test_rollup_matches_deals_and_calls_are_cumulative(client):
    async with login_as("priya@cirra.demo") as ae:
        period, view = await _busiest_period(ae)
        assert view["deals"], "seed should give Priya deals in some quarter"
        t = view["totals"]
        for cat in forecasting.CATEGORIES:
            assert abs(t[cat] - sum(d["amount_usd"] for d in view["deals"] if d["category"] == cat)) < 0.01
        assert t["commit_call"] == pytest.approx(t["closed"] + t["commit"])
        assert t["best_case_call"] == pytest.approx(t["closed"] + t["commit"] + t["best_case"])


async def test_rep_overrides_category_and_can_reset_it(client):
    async with login_as("priya@cirra.demo") as ae:
        period, view = await _busiest_period(ae)
        deal = next(d for d in view["deals"] if not d["is_closed"])
        target = "omitted" if deal["category"] != "omitted" else "commit"
        r = await ae.put(f"{API}/deals/{deal['id']}/category", json={"category": target})
        assert r.status_code == 200 and r.json()["category"] == target
        after = (await ae.get(f"{API}/me", params={"period": period})).json()
        moved = next(d for d in after["deals"] if d["id"] == deal["id"])
        assert moved["category"] == target and moved["overridden"]
        assert after["totals"][target] == pytest.approx(view["totals"][target] + deal["amount_usd"])
        # the deal's effective category is reportable
        rep = (await ae.post("/api/v1/analytics/run", json={"definition": {"source": "deals", "columns": ["title", "forecast_category"],
                                                                           "filters": [{"field": "forecast_category", "op": "eq", "value": target}]}})).json()
        assert deal["title"] in [row[0] for row in rep["rows"]]
        back = await ae.put(f"{API}/deals/{deal['id']}/category", json={"category": None})
        assert back.json()["category"] == deal["stage_category"]
    async with login_as("diego@cirra.demo") as other:  # someone else's deal is out of scope
        assert (await other.put(f"{API}/deals/{deal['id']}/category", json={"category": "commit"})).status_code == 404


async def test_submission_adjustment_and_team_rollup(client):
    async with login_as("priya@cirra.demo") as ae:
        period, view = await _busiest_period(ae)
        bad = await ae.post(f"{API}/submissions", json={"period": period, "commit": 100, "best_case": 50})
        assert bad.status_code == 422  # best case below commit
        ok = await ae.post(f"{API}/submissions", json={"period": period, "commit": 120000, "best_case": 180000, "note": "Two deals slipping"})
        assert ok.status_code == 201
        mine = (await ae.get(f"{API}/me", params={"period": period})).json()
        assert mine["submission"]["commit"] == 120000 and mine["submission"]["calculated"]["commit_call"] == view["totals"]["commit_call"]
        assert (await ae.get(f"{API}/team", params={"period": period})).status_code == 403
        priya_id = (await ae.get("/api/v1/users/me")).json()["id"]

    team = (await client.get(f"{API}/team", params={"period": period})).json()  # client = Marcus, Priya's manager
    row = next(r for r in team["rows"] if r["user"]["id"] == priya_id)
    assert row["final"] == {"commit": 120000, "best_case": 180000, "source": "submitted"}
    r = await client.put(f"{API}/adjustments", json={"period": period, "rep_id": priya_id, "commit": 90000, "best_case": 150000, "note": "Haircut"})
    assert r.status_code == 200
    team2 = (await client.get(f"{API}/team", params={"period": period})).json()
    row2 = next(r for r in team2["rows"] if r["user"]["id"] == priya_id)
    assert row2["final"]["source"] == "adjusted" and row2["final"]["commit"] == 90000
    assert team2["team"]["adjusted_commit"] == pytest.approx(team["team"]["adjusted_commit"] - 30000)
    assert sum(r["final"]["commit"] for r in team2["rows"]) == pytest.approx(team2["team"]["adjusted_commit"])
    call = await client.post(f"{API}/submissions", json={"period": period, "scope": "team", "commit": 800000, "best_case": 1100000})
    assert call.status_code == 201
    assert (await client.get(f"{API}/team", params={"period": period})).json()["submission"]["commit"] == 800000
    # a manager can only adjust people on their team, and not themselves
    me_id = (await client.get("/api/v1/users/me")).json()["id"]
    assert (await client.put(f"{API}/adjustments", json={"period": period, "rep_id": me_id, "commit": 1, "best_case": 1})).status_code == 422
    await client.delete(f"{API}/adjustments", params={"period": period, "rep_id": priya_id})
    team3 = (await client.get(f"{API}/team", params={"period": period})).json()
    assert next(r for r in team3["rows"] if r["user"]["id"] == priya_id)["final"]["source"] == "submitted"


async def test_new_stage_gets_category_from_probability(client):
    async with login_as("admin@cirra.demo") as admin:
        pipe = (await admin.get("/api/v1/pipelines")).json()[0]
        hi = (await admin.post(f"/api/v1/pipelines/{pipe['id']}/stages", json={"name": "Verbal yes", "default_probability": 85})).json()
        lo = (await admin.post(f"/api/v1/pipelines/{pipe['id']}/stages", json={"name": "Nurture", "default_probability": 10})).json()
        assert hi["forecast_category"] == "commit" and lo["forecast_category"] == "pipeline"
        r = await admin.patch(f"/api/v1/pipelines/stages/{lo['id']}", json={"forecast_category": "best_case"})
        assert r.json()["forecast_category"] == "best_case"
        for s in (hi, lo):
            await admin.delete(f"/api/v1/pipelines/stages/{s['id']}")
