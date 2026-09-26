"""Pillars 5 and 6: activity ledger, files, tasks/SLA, email & calendar sync, notifications, AI operations."""
import hashlib
from email.message import EmailMessage

from tests.helpers import login_as


async def _account(client, name):
    return next(a for a in (await client.get("/api/v1/accounts", params={"search": name})).json() if a["name"] == name)


async def test_call_and_meeting_intelligence_fields(client):
    acc = await _account(client, "Orion Financial")
    call = (await client.post("/api/v1/activities", json={"account_id": acc["id"], "activity_type": "call", "direction": "outbound",
                                                          "disposition": "gatekeeper", "duration_seconds": 95, "summary": "Reached the EA; Kevin is travelling."})).json()
    assert call["disposition"] == "gatekeeper" and call["duration_seconds"] == 95 and call["direction"] == "outbound"
    meeting = (await client.post("/api/v1/activities", json={"account_id": acc["id"], "activity_type": "meeting", "attendance": "attended",
                                                             "agenda": "1. Security architecture\n2. Deployment plan", "summary": "Architecture review with CISO.",
                                                             "duration_seconds": 3600})).json()
    assert meeting["agenda"].startswith("1.") and meeting["attendance"] == "attended"


async def test_file_attachments_round_trip(client):
    acc = await _account(client, "Orion Financial")
    payload = b"%PDF-1.4 fake security questionnaire"
    up = (await client.post(f"/api/v1/accounts/{acc['id']}/files", files={"file": ("questionnaire.pdf", payload, "application/pdf")},
                            data={"note": "Completed InfoSec questionnaire"})).json()
    assert up["sha256"] == hashlib.sha256(payload).hexdigest()
    got = await client.get(f"/api/v1/files/{up['id']}")
    assert got.content == payload and "questionnaire.pdf" in got.headers["content-disposition"]
    ledger = (await client.get("/api/v1/activities", params={"account_id": acc["id"], "activity_type": "file"})).json()
    assert ledger[0]["attachments"][0]["filename"] == "questionnaire.pdf"


async def test_task_dependencies_delegation_and_escalation(client):
    tasks = (await client.get("/api/v1/tasks")).json()
    blocked = next(t for t in tasks if t["title"] == "Submit security questionnaire answers")
    assert blocked["blocked"] and blocked["depends_on"]["title"].startswith("Collect InfoSec")
    assert (await client.patch(f"/api/v1/tasks/{blocked['id']}", json={"completed": True})).status_code == 409
    prereq = next(t for t in tasks if t["title"].startswith("Collect InfoSec"))
    assert prereq["escalation_level"] >= 2  # 4 days overdue -> manager notified at seed time
    # dependency cycles are refused
    assert (await client.patch(f"/api/v1/tasks/{prereq['id']}", json={"depends_on_id": blocked["id"]})).status_code == 422
    # delegation notifies the assignee
    t = (await client.post("/api/v1/tasks", json={"title": "Prep QBR deck", "assignee_id": prereq["assignee"]["id"], "priority": "high"})).json()
    assert t["assignee"]["full_name"] == "Priya Raman"
    async with login_as("priya@cirra.demo") as ae:
        notes = (await ae.get("/api/v1/notifications")).json()
        assert any("Prep QBR deck" in n["title"] for n in notes["items"])
        mine = (await ae.get("/api/v1/tasks", params={"assignee": "me"})).json()
        assert any(x["title"] == "Prep QBR deck" for x in mine)
    manager_notes = (await client.get("/api/v1/notifications")).json()
    assert any(n["kind"] == "sla" and "Escalated" in n["title"] for n in manager_notes["items"])


async def test_email_ingest_threads_and_feeds_relationship_strength(client):
    msg = EmailMessage()
    msg["From"] = "Hannah Weiss <hannah.weiss@orionfinancial.com>"
    msg["To"] = "marcus@cirra.demo"
    msg["Subject"] = "Re: Revenue intelligence rollout plan"
    msg["Message-ID"] = "<abc123@orionfinancial.com>"
    msg["In-Reply-To"] = msg["References"] = "<root-thread@cirra.demo>"
    msg["Date"] = "Wed, 23 Sep 2026 10:00:00 +0000"
    msg.set_content("Thanks Marcus, this looks great and the team is excited. Let's lock the rollout plan.\n\nOn Tue, Marcus wrote:\n> earlier text")
    raw = bytes(msg)
    r = await client.post("/api/v1/email/ingest", files={"file": ("m.eml", raw, "message/rfc822")})
    assert r.status_code == 201
    act = r.json()
    assert act["direction"] == "inbound" and act["contact"]["name"] == "Hannah Weiss" and "earlier text" not in act["summary"]
    assert act["sentiment"] == "positive"
    assert (await client.post("/api/v1/email/ingest", files={"file": ("m.eml", raw, "message/rfc822")})).status_code == 422  # de-duplicated
    contact = (await client.get(f"/api/v1/contacts/{act['contact']['id']}")).json()
    assert contact["rsi_factors"]["inbound_30d"] >= 1


async def test_calendar_import_and_ical_feed(client):
    ics = (b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\nBEGIN:VEVENT\r\nUID:evt-1@test\r\n"
           b"DTSTART:20261015T150000Z\r\nDTEND:20261015T160000Z\r\nSUMMARY:Orion security deep-dive\r\n"
           b"ATTENDEE:mailto:hannah.weiss@orionfinancial.com\r\nDESCRIPTION:Agenda: SSO, audit logging\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    r = (await client.post("/api/v1/calendar/import", files={"file": ("invite.ics", ics, "text/calendar")})).json()
    assert r == {"created": 1, "skipped": 0}
    assert (await client.post("/api/v1/calendar/import", files={"file": ("invite.ics", ics, "text/calendar")})).json()["skipped"] == 1
    feed_path = (await client.post("/api/v1/calendar/token")).json()["feed_path"]
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        feed = await anon.get(feed_path)
    assert feed.headers["content-type"].startswith("text/calendar")
    assert b"BEGIN:VCALENDAR" in feed.content and b"Orion security deep-dive" in feed.content and b"VEVENT" in feed.content


async def test_hybrid_rag_search(client):
    hits = (await client.post("/api/v1/search/semantic", json={"query": "accounts worried about ERP integration effort", "limit": 8})).json()
    assert any("ERP" in (h.get("summary") or "") for h in hits)
    assert any("vector" in h["matched_by"] for h in hits)
    accounts = (await client.post("/api/v1/search/semantic", json={"query": "Helios", "limit": 8})).json()
    assert any(h["entity"] == "account" and h["name"].startswith("Helios") for h in accounts)


async def test_risk_copilot_alerts_and_next_best_actions(client):
    alerts = (await client.get("/api/v1/alerts")).json()
    kinds = {(a["deal"]["title"], a["kind"]) for a in alerts}
    assert ("Fleet Telemetry Rollout", "close_date_pushed") in kinds and ("Fleet Telemetry Rollout", "stagnant") in kinds
    async with login_as("admin@cirra.demo") as admin:
        stats = (await admin.post("/api/v1/admin/jobs/risk_scan")).json()
        assert stats["deals_scanned"] > 5
    nw = next(d for d in (await client.get("/api/v1/deals", params={"search": "Fleet"})).json())
    detail = (await client.get(f"/api/v1/deals/{nw['id']}")).json()
    kinds = {a["kind"] for a in detail["next_best_actions"]}
    assert {"cadence", "slippage", "missing_role"} <= kinds
    assert all(a["message"] for a in detail["next_best_actions"] if a["kind"] in ("cadence", "missing_role"))


async def test_voice_transcription(client, monkeypatch):
    r = await client.post("/api/v1/ai/transcribe", files={"file": ("note.webm", b"\x1a\x45\xdf\xa3", "audio/webm")})
    assert r.status_code == 503 and "TRANSCRIPTION_PROVIDER" in r.json()["detail"]
    from app.services import voice

    async def fake(data, filename, content_type=None):
        return "Call with Hannah Weiss at Orion Financial. She is excited, budget is $410k, and wants a security review next week."

    monkeypatch.setattr(voice, "transcribe", fake)
    out = (await client.post("/api/v1/ai/transcribe", files={"file": ("note.webm", b"...", "audio/webm")})).json()
    assert out["signals"]["transcript"].startswith("Call with Hannah") and out["account_name"] == "Orion Financial" and out["matched_account_id"]
    assert out["deal"]["amount"] == 410000
