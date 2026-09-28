"""Regression tests for the medium findings of the external build review: money roll-ups across currencies,
campaign sends outside the web request, mailbox sync skipping sent mail, workflow triggers lost on restart,
and an unprotected Redis."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import Settings, security_problems
from app.core.database import SessionLocal
from app.models import (
    Account, Activity, Campaign, Deal, FxRate, Invoice, MailboxConnection, PipelineStage, Task, User, WorkflowEvent, WorkflowRun,
)
from app.services import erp, hierarchy, mail, workflows
from tests.helpers import login_as

API = "/api/v1"


# ---- 1. roll-ups convert currencies -------------------------------------------------------------------------------

async def test_hierarchy_and_receivables_convert_to_usd(client):
    tag = uuid.uuid4().hex[:6]
    async with SessionLocal() as db:
        eur = float((await db.get(FxRate, "EUR")).rate_to_usd)
        parent = Account(name=f"FX Parent {tag}", domain=f"fxp-{tag}.example", erp_customer_id=f"FXP-{tag}")
        db.add(parent)
        await db.flush()
        child = Account(name=f"FX Child {tag}", domain=f"fxc-{tag}.example", parent_id=parent.id)
        db.add(child)
        stage = (await db.execute(select(PipelineStage).where(PipelineStage.is_closed_won.is_(False),
                                                              PipelineStage.is_closed_lost.is_(False)).limit(1))).scalars().first()
        await db.flush()
        for acc, cur in ((parent, "USD"), (child, "EUR")):
            db.add(Deal(title=f"FX {cur} {tag}", account_id=acc.id, pipeline_id=stage.pipeline_id, stage_id=stage.id, amount=Decimal("1000"),
                        currency=cur, risk_factors={}, ai_insights={}))
        today = date.today()
        for n, cur in ((1, "USD"), (2, "EUR")):
            db.add(Invoice(account_id=parent.id, erp_invoice_id=f"INV-{tag}-{n}", invoice_number=f"INV-{tag}-{n}", issue_date=today,
                           due_date=today + timedelta(days=30), currency=cur, amount=Decimal("100"), balance=Decimal("100"), status="open"))
        await db.commit()
        tree = await hierarchy.hierarchy(db, parent.id)
        assert tree["root"]["rollup"]["open_pipeline"] == pytest.approx(1000 + 1000 * eur, abs=0.02)
        assert tree["root"]["open_pipeline"] == 1000.0  # own deals only, still in USD
        ar = await erp.ar_summary(db, await db.get(Account, parent.id))
        assert ar["currency"] == "USD" and ar["open_balance"] == pytest.approx(100 + 100 * eur, abs=0.02)
        assert {i["currency"] for i in ar["invoices"]} == {"USD", "EUR"}  # invoices keep their own currency
        from sqlalchemy import delete  # leave no territory-less accounts behind for other tests

        await db.execute(delete(Invoice).where(Invoice.account_id == parent.id))
        await db.execute(delete(Deal).where(Deal.account_id.in_([parent.id, child.id])))
        await db.execute(delete(Account).where(Account.id == child.id))
        await db.execute(delete(Account).where(Account.id == parent.id))
        await db.commit()


# ---- 2. campaign email is sent by a background job ------------------------------------------------------------------

async def test_campaign_send_is_queued_and_not_run_twice(client, monkeypatch):
    ran = []

    async def fake_run(db, campaign_id, user_id):  # stands in for the job so the request's own behaviour is visible
        ran.append(campaign_id)

    from app.services import campaigns

    monkeypatch.setattr(campaigns, "run_send", fake_run)
    tag = uuid.uuid4().hex[:6]
    async with login_as("nina@cirra.demo") as mkt:
        cid = (await mkt.post(f"{API}/campaigns", json={"name": f"Queued {tag}", "code": f"q-{tag}", "campaign_type": "email",
                                                          "email_subject": "Hello", "email_body": "Hi {{first_name}}"})).json()["id"]
        r = await mkt.post(f"{API}/campaigns/{cid}/email/send")
        assert r.status_code == 202 and ran == [uuid.UUID(cid)]
        detail = (await mkt.get(f"{API}/campaigns/{cid}")).json()
        assert detail["send_status"] == "queued" and detail["send_requested_at"]
        again = await mkt.post(f"{API}/campaigns/{cid}/email/send")
        assert again.status_code == 422 and "already being sent" in again.json()["detail"]
    async with SessionLocal() as db:  # a send stuck past the stale window may start again
        c = await db.get(Campaign, uuid.UUID(cid))
        c.send_requested_at = datetime.now(timezone.utc) - campaigns.SEND_STALE_AFTER - timedelta(minutes=1)
        await db.commit()
    async with login_as("nina@cirra.demo") as mkt:
        assert (await mkt.post(f"{API}/campaigns/{cid}/email/send")).status_code == 202


# ---- 3. mailbox sync keeps a cursor per folder ----------------------------------------------------------------------

def _raw(n: int, folder: str, frm: str, to: str) -> bytes:
    return (f"Message-ID: <{folder}-{n}-{uuid.uuid4().hex[:6]}@test>\r\nFrom: {frm}\r\nTo: {to}\r\nSubject: {folder} {n}\r\n"
            f"Date: Mon, 28 Sep 2026 10:00:00 +0000\r\n\r\nBody {n}\r\n").encode()


class FakeImap:
    """A tiny IMAP server: INBOX with high UIDs, a sent folder flagged \\Sent with low UIDs."""
    folders: dict = {}
    validity: dict = {}

    def __init__(self, host, port):
        self.current = None

    def login(self, user, password):
        return "OK", [b""]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Sent) "/" "Sent Items"', b'(\\HasNoChildren) "/" "Archive"']

    def select(self, folder, readonly=True):
        self.current = folder.strip('"')
        return ("OK", [b"1"]) if self.current in self.folders else ("NO", [b""])

    def response(self, code):
        return code, [str(self.validity.get(self.current, 1)).encode()]

    def uid(self, cmd, *args):
        msgs = self.folders[self.current]
        if cmd == "search":
            lo = int(args[1].split()[1].split(":")[0])
            return "OK", [" ".join(str(u) for u in sorted(msgs) if u >= lo).encode()]
        return "OK", [(b"RFC822", msgs[int(args[0])])]

    def logout(self):
        return "BYE", [b""]


async def test_mail_sync_reads_sent_items_with_their_own_cursor(client, monkeypatch):
    async with SessionLocal() as db:
        priya = (await db.execute(select(User).where(User.email == "priya@cirra.demo"))).scalar_one()
        from app.models import Contact

        contact = (await db.execute(select(Contact).where(Contact.email.is_not(None), Contact.status == "active").limit(1))).scalars().first()
        contact_email = contact.email
        conn = MailboxConnection(user_id=priya.id, email_address="priya@cirra.demo", imap_host="imap.example.com", imap_port=993,
                                 username="priya", secret_encrypted=mail.encrypt_secret("pw"), last_uid=900)
        db.add(conn)
        await db.commit()
        conn_id = conn.id
    inbox = {900 + i: _raw(i, "in", contact_email, "priya@cirra.demo") for i in range(1, 3)}
    sent = {i: _raw(i, "sent", "priya@cirra.demo", contact_email) for i in range(1, 251)}  # low UIDs, and more than one batch
    FakeImap.folders, FakeImap.validity = {"INBOX": inbox, "Sent Items": sent}, {"INBOX": 7, "Sent Items": 3}
    monkeypatch.setattr(mail, "IMAP_CLIENT", FakeImap)
    monkeypatch.setattr(mail.netguard, "check_mail_server", lambda *a, **k: None)

    async def sync():
        async with SessionLocal() as db:
            return await mail.sync_mailbox(db, await db.get(MailboxConnection, conn_id))

    first = await sync()
    assert first["folders"] == {"INBOX": 2, "Sent Items": mail.BATCH}  # the legacy INBOX cursor (900) doesn't hide sent mail
    second = await sync()
    assert second["folders"] == {"Sent Items": 250 - mail.BATCH}  # the backlog continues oldest-first, nothing skipped
    assert (await sync())["fetched"] == 0
    async with SessionLocal() as db:
        state = (await db.get(MailboxConnection, conn_id)).folder_state
        assert state["Sent Items"] == {"uidvalidity": 3, "last_uid": 250} and state["INBOX"]["last_uid"] == 902
        logged = (await db.execute(select(Activity).where(Activity.subject.like("sent %"), Activity.direction == "outbound"))).scalars().all()
        assert len(logged) >= 250
    FakeImap.validity["Sent Items"] = 4  # the server renumbered the folder: start it over (duplicates are ignored)
    FakeImap.folders["Sent Items"] = {1: _raw(999, "sent", "priya@cirra.demo", contact_email)}
    assert (await sync())["folders"] == {"Sent Items": 1}


# ---- 4. workflow triggers survive a restart -------------------------------------------------------------------------

async def test_workflow_triggers_are_queued_durably(client):
    tag = uuid.uuid4().hex[:6]
    async with login_as("admin@cirra.demo") as admin:
        rule = (await admin.post(f"{API}/workflows", json={
            "name": f"Durable {tag}", "enabled": True, "source": "tasks", "trigger": {"type": "created"},
            "conditions": [{"field": "title", "op": "contains", "value": f"Durable {tag}"}],
            "actions": [{"type": "notify", "to": ["owner"], "title": "Queued {{title}}"}]})).json()
        admin_id = (await admin.get(f"{API}/users/me")).json()["id"]
    async with SessionLocal() as db:  # a rolled-back change leaves no queued event
        gone = Task(title=f"Durable {tag} rolled back", owner_id=uuid.UUID(admin_id), assignee_id=uuid.UUID(admin_id))
        db.add(gone)
        await db.flush()
        gone_id = gone.id
        assert (await db.execute(select(WorkflowEvent).where(WorkflowEvent.record_id == gone_id))).scalars().first()  # queued in-transaction
        await db.rollback()
    async with SessionLocal() as db:
        assert not (await db.execute(select(WorkflowEvent).where(WorkflowEvent.record_id == gone_id))).scalars().all()
    # simulate a process that committed the change and died before evaluating it: the event sits in the queue
    async with SessionLocal() as db:
        task = Task(title=f"Durable {tag} orphan", owner_id=uuid.UUID(admin_id), assignee_id=uuid.UUID(admin_id))
        db.add(task)
        await db.flush()
        db.info.pop("wf_event_ids", None)  # this "process" dies before dispatching: only the queued row remains
        await db.commit()
        orphan = task.id
    async with SessionLocal() as db:
        queued = (await db.execute(select(WorkflowEvent).where(WorkflowEvent.record_id == orphan))).scalars().all()
        assert len(queued) == 1 and queued[0].kind == "created"  # written in the same transaction as the task
        queued[0].created_at = datetime.now(timezone.utc) - timedelta(minutes=5)  # ...a while ago
        await db.commit()
    await workflows.drain()
    assert (await workflows.run_stale_events())["recovered"] >= 1
    async with SessionLocal() as db:
        runs = (await db.execute(select(WorkflowRun).where(WorkflowRun.rule_id == uuid.UUID(rule["id"]), WorkflowRun.record_id == orphan))).scalars().all()
        assert len(runs) == 1
        assert not (await db.execute(select(WorkflowEvent).where(WorkflowEvent.record_id == orphan))).scalars().all()
    async with login_as("admin@cirra.demo") as admin:  # the normal path still runs straight after commit and leaves the queue empty
        t = (await admin.post(f"{API}/tasks", json={"title": f"Durable {tag} live"})).json()
        await workflows.drain()
        async with SessionLocal() as db:
            assert (await db.execute(select(WorkflowRun).where(WorkflowRun.record_id == uuid.UUID(t["id"])))).scalars().first()
            assert not (await db.execute(select(WorkflowEvent).where(WorkflowEvent.record_id == uuid.UUID(t["id"])))).scalars().all()
        await admin.post(f"{API}/workflows/{rule['id']}/toggle")


# ---- 5. Redis needs a password outside development ------------------------------------------------------------------

@pytest.mark.parametrize("url,unsafe", [("redis://redis:6379/0", True), ("redis://:cirra_redis_dev_password@redis:6379/0", True),
                                        ("redis://:short@redis:6379/0", True), ("redis://:" + "r" * 32 + "@redis:6379/0", False)])
def test_redis_needs_a_real_password(monkeypatch, url, unsafe):
    for var in ("REDIS_URL", "ERP_CONNECTOR", "JWT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None, environment="production", jwt_secret="k" * 48, redis_url=url)
    assert any(p.startswith("REDIS_URL") for p in security_problems(s)) is unsafe

