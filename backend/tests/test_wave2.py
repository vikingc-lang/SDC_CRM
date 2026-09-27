"""Wave 2 everyday usability: saved list views with in-place editing, period-over-period comparison and
scheduled report deliveries."""
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Notification, ReportSubscription
from app.services import bulk, cases, mailer, reporting, subscriptions
from tests.helpers import login_as

API = "/api/v1"


def test_previous_range_steps_back_a_calendar_unit_or_the_window_length():
    pr = reporting.previous_range
    assert pr("this_month", date(2026, 3, 31))[:2] == (date(2026, 2, 1), date(2026, 3, 1))
    assert pr("this_quarter", date(2026, 2, 10))[:2] == (date(2025, 10, 1), date(2026, 1, 1))
    assert pr("this_year", date(2026, 6, 1)) == (date(2025, 1, 1), date(2026, 1, 1), "last year")
    assert pr("last_month", date(2026, 1, 15))[:2] == (date(2025, 11, 1), date(2025, 12, 1))
    assert pr("this_week", date(2026, 9, 27))[:2] == (date(2026, 9, 14), date(2026, 9, 21))
    assert pr("last_30_days", date(2026, 9, 27)) == (date(2026, 7, 30), date(2026, 8, 29), "previous 30 days")


async def test_compare_with_previous_period(client):
    defn = {"source": "deals", "measures": [{"agg": "count"}, {"agg": "sum", "field": "amount_usd"}],
            "filters": [{"field": "created", "op": "within", "value": "last_90_days"}], "compare": "previous_period"}
    res = (await client.post(f"{API}/analytics/run", json={"definition": defn})).json()
    cmp = res["comparison"]
    assert cmp["label"] == "previous 90 days" and len(cmp["rows"]) == 1
    direct = (await client.post(f"{API}/analytics/run", json={"definition": {
        "source": "deals", "measures": [{"agg": "count"}], "filters": [{"field": "created", "op": "between", "value": [cmp["start"], cmp["end"]]}]}})).json()
    assert cmp["rows"][0][0] == direct["rows"][0][0]
    grouped = (await client.post(f"{API}/analytics/run", json={"definition": {**defn, "group_by": [{"field": "status"}]}})).json()
    assert {r[0] for r in grouped["comparison"]["rows"]} <= {"Open", "Won", "Lost"}
    for bad in ({**defn, "filters": []}, {**defn, "group_by": [{"field": "created", "bucket": "month"}]},
                {"source": "deals", "columns": ["title"], "filters": defn["filters"], "compare": "previous_period"}):
        assert (await client.post(f"{API}/analytics/run", json={"definition": bad})).status_code == 422
    # a dashboard period supplies the date window for a compared tile that has none of its own
    rid = (await client.post(f"{API}/analytics/reports", json={"name": "Won vs last period", "definition": {
        "source": "deals", "measures": [{"agg": "count"}], "filters": [{"field": "status", "op": "eq", "value": "Won"},
                                                                       {"field": "close_date", "op": "within", "value": "this_year"}],
        "compare": "previous_period"}})).json()["id"]
    tile = (await client.post(f"{API}/analytics/reports/{rid}/run", json={"period": "this_quarter"})).json()
    assert tile["comparison"]["label"] == "last quarter"


async def test_list_views_crud_run_and_visibility(client):
    meta = (await client.get(f"{API}/views", params={"source": "leads"})).json()
    assert "status" in meta["inline"] and meta["link"] == "/leads/{id}" and any(f["key"] == "score" for f in meta["fields"])
    body = {"source": "leads", "name": "Hot new leads", "columns": ["name", "company", "score", "status", "owner"],
            "filters": [{"field": "status", "op": "in", "value": ["new", "working"]}, {"field": "score", "op": "gte", "value": 50}],
            "sort": {"by": "score", "dir": "desc"}}
    view = (await client.post(f"{API}/views", json=body)).json()
    res = (await client.post(f"{API}/views/run", json={k: body[k] for k in ("source", "columns", "filters", "sort")})).json()
    scores = [r[2] for r in res["rows"]]
    assert scores == sorted(scores, reverse=True) and all(s >= 50 for s in scores) and len(res["ids"]) == len(res["rows"])
    assert all(r[3] in ("new", "working") for r in res["rows"])
    async with login_as("priya@cirra.demo") as ae:
        assert all(v["id"] != view["id"] for v in (await ae.get(f"{API}/views", params={"source": "leads"})).json()["views"])
        assert (await ae.put(f"{API}/views/{view['id']}", json=body)).status_code == 404
        shared = await ae.post(f"{API}/views", json={**body, "visibility": "shared"})
        assert shared.status_code == 403
        mine = (await ae.post(f"{API}/views/run", json={"source": "leads", "columns": ["name", "owner"]})).json()
        assert {r[1] for r in mine["rows"]} <= {"Priya Raman", None}  # row scope applies to views too
    team = (await client.put(f"{API}/views/{view['id']}", json={**body, "visibility": "shared", "name": "Hot leads (team)"})).json()
    assert team["visibility"] == "shared" and team["name"] == "Hot leads (team)"
    async with login_as("priya@cirra.demo") as ae:
        seen = (await ae.get(f"{API}/views", params={"source": "leads"})).json()["views"]
        assert any(v["id"] == view["id"] and not v["can_edit"] for v in seen)
        assert (await ae.delete(f"{API}/views/{view['id']}")).status_code == 403
    for bad in ({**body, "columns": ["nope"]}, {**body, "sort": {"by": "nope"}}, {**body, "columns": []},
                {**body, "filters": [{"field": "score", "op": "contains", "value": 1}]}):
        assert (await client.post(f"{API}/views", json=bad)).status_code == 422
    assert (await client.delete(f"{API}/views/{view['id']}")).status_code == 204


async def test_views_offer_inline_edits_only_where_the_role_can_update(client):
    async with login_as("sofia@cirra.demo") as agent:
        meta = (await agent.get(f"{API}/views", params={"source": "cases"})).json()
        assert set(meta["inline"]) == {"owner", "status", "priority"}
        res = (await agent.post(f"{API}/views/run", json={"source": "cases", "columns": ["case_number", "priority"]})).json()
        cid = res["ids"][0]
        r = (await agent.post(f"{API}/bulk/cases", json={"ids": [cid], "action": "set_priority", "value": "high"})).json()
        assert r["matched"] == 1
    assert (await client.get(f"{API}/views", params={"source": "contacts"})).json()["inline"] == {}
    assert bulk.CASE_STATUSES == cases.STATUSES and bulk.CASE_PRIORITIES == cases.PRIORITIES


def _sub(**kw):
    base = {"frequency": "daily", "weekday": 0, "day_of_month": 1, "hour": 7, "active": True, "last_sent_at": None,
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    return SimpleNamespace(**{**base, **kw})


def test_schedule_slots_and_catch_up():
    utc = timezone.utc
    now = datetime(2026, 9, 27, 6, 30, tzinfo=utc)  # a Sunday
    assert subscriptions.last_slot(_sub(), now) == datetime(2026, 9, 26, 7, tzinfo=utc)
    assert subscriptions.last_slot(_sub(frequency="weekly", weekday=0), now) == datetime(2026, 9, 21, 7, tzinfo=utc)
    assert subscriptions.last_slot(_sub(frequency="weekly", weekday=6, hour=6), now) == datetime(2026, 9, 27, 6, tzinfo=utc)
    assert subscriptions.last_slot(_sub(frequency="monthly", day_of_month=28), now) == datetime(2026, 8, 28, 7, tzinfo=utc)
    assert subscriptions.last_slot(_sub(frequency="monthly", day_of_month=28), datetime(2026, 1, 5, tzinfo=utc)) == datetime(2025, 12, 28, 7, tzinfo=utc)
    assert subscriptions.is_due(_sub(), now)
    assert not subscriptions.is_due(_sub(last_sent_at=datetime(2026, 9, 26, 7, 0, 5, tzinfo=utc)), now)
    assert not subscriptions.is_due(_sub(created_at=datetime(2026, 9, 26, 9, tzinfo=utc)), now)  # set up after today's slot
    assert not subscriptions.is_due(_sub(active=False), now)


async def test_subscriptions_deliver_per_recipient_with_their_own_access(client, monkeypatch):
    sent = []
    monkeypatch.setattr(mailer.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(mailer, "_send", lambda msg: sent.append(msg))
    users = (await client.get(f"{API}/users")).json()
    priya = next(u["id"] for u in users if u["email"] == "priya@cirra.demo")
    defn = {"source": "deals", "measures": [{"agg": "count"}]}
    private = (await client.post(f"{API}/analytics/reports", json={"name": "Deal count (mine)", "definition": defn})).json()["id"]
    bad = await client.put(f"{API}/analytics/reports/{private}/subscription", json={"frequency": "daily", "recipient_ids": [priya]})
    assert bad.status_code == 422
    rid = (await client.post(f"{API}/analytics/reports", json={"name": "Deal count", "definition": defn, "visibility": "shared"})).json()["id"]
    sub = (await client.put(f"{API}/analytics/reports/{rid}/subscription", json={"frequency": "weekly", "weekday": 2, "hour": 8,
                                                                                 "recipient_ids": [priya]})).json()
    assert sub["email_enabled"] and sub["subscription"]["recipient_ids"] == [priya]
    async with SessionLocal() as db:  # make it due: pretend it was set up long ago
        row = (await db.execute(select(ReportSubscription).where(ReportSubscription.id == uuid.UUID(sub["subscription"]["id"])))).scalar_one()
        row.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        await db.commit()
        assert await subscriptions.deliver_due(db) >= 1
        await db.refresh(row)
        assert row.last_status.startswith("Delivered to 2, emailed 2")
        notes = (await db.execute(select(Notification).where(Notification.kind == "report", Notification.link == f"/reports/builder?id={rid}")
                                  .order_by(Notification.created_at))).scalars().all()
        assert len(notes) == 2
        again = await subscriptions.deliver_due(db)  # not due again until next week's slot
        await db.refresh(row)
        assert again == 0
    counts = {m["To"]: m for m in sent}
    assert set(counts) == {"marcus@cirra.demo", "priya@cirra.demo"}
    mgr_total = (await client.post(f"{API}/analytics/reports/{rid}/run")).json()["rows"][0][0]
    body = lambda m: m.get_body(("plain",)).get_content()  # noqa: E731
    assert f"Number of opportunities: {mgr_total:,}" in body(counts["marcus@cirra.demo"])
    assert f"Number of opportunities: {mgr_total:,}" not in body(counts["priya@cirra.demo"])  # Priya sees only her deals
    assert any(a.get_filename().endswith(".csv") for a in counts["priya@cirra.demo"].iter_attachments())
    before = len(sent)
    now = (await client.post(f"{API}/analytics/reports/{rid}/subscription/send")).json()
    assert now["status"].startswith("Delivered to 1") and [m["To"] for m in sent[before:]] == ["marcus@cirra.demo"]  # never colleagues on demand
    listed = (await client.get(f"{API}/analytics/subscriptions")).json()
    assert any(s["report_name"] == "Deal count" for s in listed["subscriptions"])
    assert (await client.delete(f"{API}/analytics/reports/{rid}/subscription")).status_code == 204
    assert (await client.get(f"{API}/analytics/reports/{rid}/subscription")).json()["subscription"] is None


async def test_mail_failure_is_recorded_without_the_server_reply(client, monkeypatch):
    import smtplib

    def boom(msg):
        raise smtplib.SMTPAuthenticationError(535, b"bad password for secret-user")
    monkeypatch.setattr(mailer.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(mailer, "_send", boom)
    rid = (await client.post(f"{API}/analytics/reports", json={"name": "Failing mail", "definition": {"source": "deals", "measures": [{"agg": "count"}]}})).json()["id"]
    await client.put(f"{API}/analytics/reports/{rid}/subscription", json={"frequency": "daily"})
    status = (await client.post(f"{API}/analytics/reports/{rid}/subscription/send")).json()["status"]
    assert "SMTPAuthenticationError" in status and "secret" not in status and status.startswith("Delivered to 1, emailed 0")


async def test_subscriptions_lapse_with_the_subscriber_and_skip_people_without_reports(client):
    async with login_as("priya@cirra.demo") as ae:
        rid = (await ae.post(f"{API}/analytics/reports", json={"name": "Priya's deals", "definition": {"source": "deals", "measures": [{"agg": "count"}]}})).json()["id"]
        sub_id = (await ae.put(f"{API}/analytics/reports/{rid}/subscription", json={"frequency": "daily"})).json()["subscription"]["id"]
    from app.models import User

    async with SessionLocal() as db:
        priya = (await db.execute(select(User).where(User.email == "priya@cirra.demo"))).scalar_one()
        sub = await db.get(ReportSubscription, uuid.UUID(sub_id))
        priya.is_active = False
        await db.flush()
        status = await subscriptions.deliver(db, sub)
        assert status.startswith("Paused") and sub.active is False
        await db.rollback()  # leave Priya active for the other tests
