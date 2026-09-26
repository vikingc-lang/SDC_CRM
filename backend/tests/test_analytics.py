"""Self-service analytics: field catalogue, compiled reports, row-level scope, saved reports, dashboards, CSV export."""
from tests.helpers import login_as

API = "/api/v1/analytics"


async def test_catalogue_follows_permissions(client):
    marcus = {s["key"] for s in (await client.get(f"{API}/sources")).json()["sources"]}
    assert {"deals", "accounts", "leads", "quotes", "orders"} <= marcus
    async with login_as("sam@cirra.demo") as sdr:
        body = (await sdr.get(f"{API}/sources")).json()
        keys = {s["key"] for s in body["sources"]}
        assert "quotes" not in keys and "orders" not in keys  # SDRs can't read quotes or orders
        assert body["can_share"] is False
        r = await sdr.post(f"{API}/run", json={"definition": {"source": "quotes", "measures": [{"agg": "count"}]}})
        assert r.status_code == 422


async def test_grouped_report_matches_direct_count(client):
    defn = {"source": "deals", "group_by": [{"field": "status"}], "measures": [{"agg": "count"}, {"agg": "sum", "field": "amount_usd"}]}
    res = (await client.post(f"{API}/run", json={"definition": defn})).json()
    assert [c["key"] for c in res["columns"]] == ["status", "count", "sum_amount_usd"]
    counts = {r[0]: r[1] for r in res["rows"]}
    for status in ("Open", "Won", "Lost"):
        direct = (await client.get("/api/v1/deals", params={"status": status.lower()})).json()
        assert counts.get(status, 0) == len(direct), status


async def test_filters_buckets_and_validation(client):
    defn = {"source": "deals", "filters": [{"field": "status", "op": "eq", "value": "Open"}, {"field": "amount_usd", "op": "gte", "value": 0},
                                           {"field": "close_date", "op": "within", "value": "this_year"}],
            "group_by": [{"field": "close_date", "bucket": "month"}], "measures": [{"agg": "sum", "field": "weighted_usd"}]}
    res = (await client.post(f"{API}/run", json={"definition": defn})).json()
    months = [r[0] for r in res["rows"]]
    assert months == sorted(months) and all(m.endswith("-01") for m in months)  # date buckets ascend by month start
    bad = [
        {"source": "deals", "group_by": [{"field": "title"}]},                         # not groupable
        {"source": "deals", "measures": [{"agg": "sum", "field": "stage"}]},           # sum of text
        {"source": "deals", "filters": [{"field": "stage", "op": "gt", "value": 1}]},  # op doesn't fit type
        {"source": "deals; DROP TABLE users", "columns": ["title"]},                   # unknown source
        {"source": "deals", "columns": ["title", "password_hash"]},                    # not in the catalogue
    ]
    for d in bad:
        assert (await client.post(f"{API}/run", json={"definition": d})).status_code == 422, d


async def test_shared_report_runs_in_each_viewers_scope(client):
    defn = {"source": "accounts", "columns": ["name", "owner"], "limit": 500}
    created = await client.post(f"{API}/reports", json={"name": "All accounts", "definition": defn, "visibility": "shared"})
    assert created.status_code == 201
    rid = created.json()["id"]
    mgr_names = {r[0] for r in (await client.post(f"{API}/reports/{rid}/run")).json()["rows"]}
    async with login_as("priya@cirra.demo") as ae:
        mine = {a["name"] for a in (await ae.get("/api/v1/accounts", params={"limit": 200})).json()}
        rows = (await ae.post(f"{API}/reports/{rid}/run")).json()["rows"]
        assert {r[0] for r in rows} == mine and mine < mgr_names  # same report, only Priya's accounts
        # AEs can't edit someone else's report or share their own
        assert (await ae.put(f"{API}/reports/{rid}", json={"name": "x", "definition": defn})).status_code == 403
        assert (await ae.post(f"{API}/reports", json={"name": "p", "definition": defn, "visibility": "shared"})).status_code == 403
        own = (await ae.post(f"{API}/reports", json={"name": "Mine", "definition": defn})).json()
    # private reports are invisible to others
    assert (await client.get(f"{API}/reports/{own['id']}")).status_code == 404


async def test_dashboard_and_export(client):
    rep = (await client.post(f"{API}/reports", json={"name": "Leads by status", "visibility": "shared",
                                                    "definition": {"source": "leads", "group_by": [{"field": "status"}], "measures": [{"agg": "count"}]}})).json()
    private = (await client.post(f"{API}/reports", json={"name": "Private one", "definition": {"source": "leads", "columns": ["name"]}})).json()
    bad = await client.post(f"{API}/dashboards", json={"name": "Team", "visibility": "shared", "tiles": [{"report_id": private["id"]}]})
    assert bad.status_code == 422  # a shared dashboard can't show a private report
    d = (await client.post(f"{API}/dashboards", json={"name": "Team", "visibility": "shared",
                                                     "tiles": [{"report_id": rep["id"], "size": "full"}]})).json()
    full = (await client.get(f"{API}/dashboards/{d['id']}")).json()
    assert full["tiles"][0]["report"]["name"] == "Leads by status"
    csv = await client.get(f"{API}/reports/{rep['id']}/export")
    assert csv.status_code == 200 and csv.text.splitlines()[0] == "Status,Number of leads"
    async with login_as("sam@cirra.demo") as sdr:  # SDRs have no export right on reports
        assert (await sdr.get(f"{API}/reports/{rep['id']}/export")).status_code == 403


async def test_starter_dashboard_is_seeded_and_runs(client):
    boards = (await client.get(f"{API}/dashboards")).json()
    overview = next(b for b in boards if b["name"] == "Sales overview")
    full = (await client.get(f"{API}/dashboards/{overview['id']}")).json()
    for t in full["tiles"]:
        r = await client.post(f"{API}/reports/{t['report_id']}/run")
        assert r.status_code == 200, (t["report"]["name"], r.text)
