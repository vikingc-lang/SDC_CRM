"""Regression tests for the code-review findings on the sign-in security, analytics, workflow, forecasting and
service phases."""
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import IntegrationEvent, SupportTicket, User, WorkflowRun
from app.services import identity, workflows
from tests.helpers import login_as

API = "/api/v1"


def _anon():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _new_user(admin, email, role="account_executive", password="s3cure-pass!"):
    r = await admin.post(f"{API}/admin/users", json={"email": email, "full_name": "Review Test", "role": role, "password": password})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_sso_links_existing_accounts_only_on_proof_of_email(client):
    claims = lambda **kw: {"iss": "https://idp.example.com", "sub": kw.pop("sub"), **kw}  # noqa: E731
    async with SessionLocal() as db:
        with pytest.raises(identity.IdentityError, match="didn't mark it verified"):  # UPN only, no verified email
            await identity._link_user(db, {"allowed_domains": []}, claims(sub="attacker-1", preferred_username="admin@cirra.demo"))
        with pytest.raises(identity.IdentityError, match="didn't mark it verified"):  # email claim but no email_verified
            await identity._link_user(db, {"allowed_domains": []}, claims(sub="attacker-2", email="admin@cirra.demo"))
        linked = await identity._link_user(db, {"allowed_domains": []}, claims(sub="ok-1", email="admin@cirra.demo", email_verified=True))
        assert linked.email == "admin@cirra.demo"
        await db.rollback()
        trusted = await identity._link_user(db, {"allowed_domains": ["cirra.demo"]}, claims(sub="ok-2", preferred_username="admin@cirra.demo"))
        assert trusted.email == "admin@cirra.demo"
        await db.rollback()


def test_return_path_rejects_off_site_forms():
    for bad in ("//evil.com", "/\\evil.com", "/\\\\evil.com", "https://evil.com", "evil.com", "/\x00x", None, ""):
        assert not identity.safe_return_path(bad), bad
    for good in ("/deals/123", "/reports?tab=call", "/"):
        assert identity.safe_return_path(good), good


async def test_second_factor_and_password_guessing_lock_out(client):
    async with login_as("admin@cirra.demo") as admin:
        await _new_user(admin, "lockout.mfa@cirra.demo")
        await _new_user(admin, "lockout.pwd@cirra.demo")
    async with _anon() as c:
        tok = (await c.post(f"{API}/auth/login", json={"username": "lockout.mfa@cirra.demo", "password": "s3cure-pass!"})).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        start = (await c.post(f"{API}/auth/mfa/enroll/start", json={})).json()
        assert (await c.post(f"{API}/auth/mfa/enroll/confirm", json={"code": identity.totp_now(start["secret"])})).status_code == 200
    async with _anon() as c:
        pending = (await c.post(f"{API}/auth/login", json={"username": "lockout.mfa@cirra.demo", "password": "s3cure-pass!"})).json()["mfa_token"]
        for _ in range(5):
            assert (await c.post(f"{API}/auth/mfa/verify", json={"mfa_token": pending, "code": "000001"})).status_code == 401
        r = await c.post(f"{API}/auth/mfa/verify", json={"mfa_token": pending, "code": identity.totp_now(start["secret"])})
        assert r.status_code == 429, r.text  # even the right code is refused during the lockout
        for _ in range(10):
            assert (await c.post(f"{API}/auth/login", json={"username": "lockout.pwd@cirra.demo", "password": "wrong"})).status_code == 401
        assert (await c.post(f"{API}/auth/login", json={"username": "lockout.pwd@cirra.demo", "password": "s3cure-pass!"})).status_code == 429


async def test_password_reset_ends_existing_sessions(client):
    async with login_as("admin@cirra.demo") as admin:
        uid = await _new_user(admin, "reset.sessions@cirra.demo")
        async with _anon() as c:
            tok = (await c.post(f"{API}/auth/login", json={"username": "reset.sessions@cirra.demo", "password": "s3cure-pass!"})).json()["access_token"]
            c.headers["Authorization"] = f"Bearer {tok}"
            assert (await c.get(f"{API}/users/me")).status_code == 200
            assert (await admin.patch(f"{API}/admin/users/{uid}", json={"password": "n3w-pass-after-breach"})).status_code == 200
            assert (await c.get(f"{API}/users/me")).status_code == 401


async def test_stale_bearer_does_not_block_forced_enrolment(client):
    async with login_as("admin@cirra.demo") as admin:
        await _new_user(admin, "stale.bearer@cirra.demo", role="sdr")
        await admin.put(f"{API}/admin/security", json={"mfa_required_roles": ["sdr"]})
        try:
            async with _anon() as c:
                step1 = (await c.post(f"{API}/auth/login", json={"username": "stale.bearer@cirra.demo", "password": "s3cure-pass!"})).json()
                c.headers["Authorization"] = "Bearer expired.or.revoked.token"
                r = await c.post(f"{API}/auth/mfa/enroll/start", json={"mfa_token": step1["mfa_token"]})
                assert r.status_code == 200 and r.json()["secret"]
        finally:
            await admin.put(f"{API}/admin/security", json={"mfa_required_roles": []})


async def test_case_patch_rejects_clearing_required_fields(client):
    async with login_as("sofia@cirra.demo") as agent:
        acc = (await agent.get(f"{API}/accounts")).json()[0]
        cid = (await agent.post(f"{API}/cases", json={"account_id": acc["id"], "subject": "Null patch check"})).json()["id"]
        for field in ("status", "severity", "subject", "channel"):
            r = await agent.patch(f"{API}/cases/{cid}", json={field: None})
            assert r.status_code == 422, (field, r.status_code)


async def test_scheduled_rules_reach_matches_beyond_one_batch(client, monkeypatch):
    monkeypatch.setattr(workflows, "SCHEDULE_BATCH", 2)
    async with login_as("admin@cirra.demo") as admin:
        for i in range(5):
            await admin.post(f"{API}/tasks", json={"title": f"Batch sweep {i}", "priority": "low"})
        rule = (await admin.post(f"{API}/workflows", json={
            "name": "Batch sweep", "enabled": True, "source": "tasks", "trigger": {"type": "schedule"},
            "conditions": [{"field": "title", "op": "contains", "value": "Batch sweep"}],
            "actions": [{"type": "notify", "to": ["owner"], "title": "Sweep {{title}}"}]})).json()
        for _ in range(3):
            async with SessionLocal() as db:
                await workflows.run_scheduled(db)
        async with SessionLocal() as db:
            ran = (await db.execute(select(WorkflowRun.record_id).where(WorkflowRun.rule_id == rule["id"]))).scalars().all()
        assert len(set(ran)) == 5 and len(ran) == 5
        await admin.post(f"{API}/workflows/{rule['id']}/toggle")


async def test_case_priority_rule_retimes_sla_and_events_use_entity_names(client):
    async with login_as("admin@cirra.demo") as admin:
        rule = (await admin.post(f"{API}/workflows", json={
            "name": "Escalate outages", "enabled": True, "source": "cases", "trigger": {"type": "created"},
            "conditions": [{"field": "subject", "op": "contains", "value": "OUTAGE"}],
            "actions": [{"type": "update_field", "field": "priority", "value": "critical"}, {"type": "emit_event", "event": "case_escalated"}]})).json()
        acc = (await admin.get(f"{API}/accounts")).json()[0]
        cid = (await admin.post(f"{API}/cases", json={"account_id": acc["id"], "subject": "OUTAGE: sync down", "severity": "medium"})).json()["id"]
        await workflows.drain()
        async with SessionLocal() as db:
            case = await db.get(SupportTicket, __import__("uuid").UUID(cid))
            assert case.severity == "critical"
            assert case.first_response_due_at - case.opened_at <= timedelta(hours=1, minutes=1)  # critical target, not medium's 8 h
            ev = (await db.execute(select(IntegrationEvent).where(IntegrationEvent.event_type == "workflow.case_escalated")
                                   .order_by(IntegrationEvent.id.desc()).limit(1))).scalar_one()
            assert ev.entity_type == "case"
        await admin.post(f"{API}/workflows/{rule['id']}/toggle")


async def test_one_failing_rule_does_not_skip_the_others(client, monkeypatch):
    real = workflows.execute

    async def flaky(db, rule, record_id, trigger, dry_run=False):
        if rule.name == "Fails first":
            raise RuntimeError("boom")
        return await real(db, rule, record_id, trigger, dry_run=dry_run)

    monkeypatch.setattr(workflows, "execute", flaky)
    async with login_as("admin@cirra.demo") as admin:
        ids = []
        for name in ("Fails first", "Still runs"):
            ids.append((await admin.post(f"{API}/workflows", json={
                "name": name, "enabled": True, "source": "tasks", "trigger": {"type": "created"},
                "conditions": [{"field": "title", "op": "contains", "value": "Isolation"}],
                "actions": [{"type": "notify", "to": ["owner"], "title": "hi"}]})).json()["id"])
        await admin.post(f"{API}/tasks", json={"title": "Isolation check"})
        await workflows.drain()
        async with SessionLocal() as db:
            runs = (await db.execute(select(WorkflowRun.rule_id).where(WorkflowRun.rule_id.in_(ids)))).scalars().all()
        assert [str(r) for r in runs] == [ids[1]]
        for i in ids:
            await admin.post(f"{API}/workflows/{i}/toggle")
    _ = User


async def test_every_report_field_of_every_source_runs(client):
    """Selects, groups and aggregates every catalogue field once, so a field whose SQL fails can't hide."""
    cat = (await client.get(f"{API}/analytics/sources")).json()
    for src in cat["sources"]:
        keys = [f["key"] for f in src["fields"]]
        r = await client.post(f"{API}/analytics/run", json={"definition": {"source": src["key"], "columns": keys}})
        assert r.status_code == 200, (src["key"], r.text[:300])
        for f in src["fields"]:
            if f["type"] in ("number", "money"):
                r = await client.post(f"{API}/analytics/run", json={"definition": {"source": src["key"], "measures": [{"agg": "avg", "field": f["key"]}]}})
                assert r.status_code == 200, (src["key"], f["key"], r.text[:200])
            if f.get("groupable"):
                r = await client.post(f"{API}/analytics/run", json={"definition": {"source": src["key"], "group_by": [{"field": f["key"]}],
                                                                                   "measures": [{"agg": "count"}]}})
                assert r.status_code == 200, (src["key"], f["key"], r.text[:200])
