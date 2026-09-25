"""Back-office & ERP integration fabric (pillar 8).

Connectors (``ERP_CONNECTOR``):
* ``file`` - JSON exchange folder, the lowest-common-denominator pattern for
  SAP S/4HANA (via CPI/IDoc exports) and NetSuite (saved-search exports):
  ``inbound/customers.json`` + ``inbound/invoices.json`` are read,
  ``outbound/customers-<ts>.json`` is written.
* ``rest`` - an integration-middleware API (MuleSoft, Boomi, SAP CPI, a
  NetSuite RESTlet) exposing ``GET /customers``, ``GET /invoices`` and
  ``POST /customers`` in the canonical schema below.
* ``demo`` - deterministic data for the demo workspace.

Canonical customer: {erp_customer_id, crm_account_id?, domain?, tax_id?, legal_name,
billing_address{line1,city,region,postal_code,country}, credit_limit, credit_hold, payment_terms}
Canonical invoice:  {erp_invoice_id, erp_customer_id, invoice_number, issue_date, due_date,
currency, amount, balance, status}
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Account, ErpSyncRun, Invoice
from app.services.dedup import registrable_domain
from app.services.notify import emit

AGING_BUCKETS = (("current", None, 0), ("1_30", 1, 30), ("31_60", 31, 60), ("61_90", 61, 90), ("90_plus", 91, None))


# ---- connectors ---------------------------------------------------------------
async def _pull(kind: str) -> list[dict]:
    connector = settings.erp_connector
    if connector == "file":
        path = Path(settings.erp_exchange_dir) / "inbound" / f"{kind}.json"
        return json.loads(path.read_text()) if path.exists() else []
    if connector == "rest":
        if not settings.erp_rest_url:
            raise RuntimeError("ERP_REST_URL is not configured")
        async with httpx.AsyncClient(timeout=60, headers={"Authorization": f"Bearer {settings.erp_rest_token}"} if settings.erp_rest_token else {}) as c:
            r = await c.get(f"{settings.erp_rest_url.rstrip('/')}/{kind}")
            r.raise_for_status()
            return r.json()
    return []


async def _push_customers(records: list[dict]) -> None:
    connector = settings.erp_connector
    if connector == "file":
        out = Path(settings.erp_exchange_dir) / "outbound"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"customers-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json").write_text(json.dumps(records, indent=2, default=str))
    elif connector == "rest" and settings.erp_rest_url:
        async with httpx.AsyncClient(timeout=60, headers={"Authorization": f"Bearer {settings.erp_rest_token}"} if settings.erp_rest_token else {}) as c:
            (await c.post(f"{settings.erp_rest_url.rstrip('/')}/customers", json=records)).raise_for_status()


def _demo_records(accounts: list[Account]) -> tuple[list[dict], list[dict]]:
    """Stable pseudo-ERP data derived from account ids (demo connector)."""
    customers, invoices = [], []
    today = date.today()
    for a in accounts:
        if a.lifecycle_stage != "customer":
            continue
        h = int(hashlib.sha256(str(a.id).encode()).hexdigest(), 16)
        erp_id = a.erp_customer_id or f"C{100000 + h % 900000}"
        limit = Decimal(50000 + (h % 20) * 25000)
        customers.append({"erp_customer_id": erp_id, "crm_account_id": str(a.id), "legal_name": a.legal_name or f"{a.name}",
                          "tax_id": a.tax_id or f"US-{h % 90000000 + 10000000}", "credit_limit": float(limit),
                          "payment_terms": a.payment_terms or "NET30",
                          "billing_address": a.billing_address or {"line1": f"{100 + h % 800} Commerce Way", "city": "Chicago", "region": "IL",
                                                                   "postal_code": "60601", "country": "US"}})
        for i in range(3 + h % 3):
            issue = today - timedelta(days=15 + i * 31 + (h >> i) % 20)
            amount = Decimal(8000 + ((h >> (i * 3)) % 40) * 500)
            paid = i >= 2 and (h >> i) % 3 != 0
            invoices.append({"erp_invoice_id": f"{erp_id}-INV{i + 1:03d}", "erp_customer_id": erp_id, "invoice_number": f"INV-{(h % 9000) + 1000}-{i + 1}",
                             "issue_date": issue.isoformat(), "due_date": (issue + timedelta(days=30)).isoformat(), "currency": "USD",
                             "amount": float(amount), "balance": 0.0 if paid else float(amount), "status": "paid" if paid else "open"})
    return customers, invoices


# ---- sync ------------------------------------------------------------------------
async def sync_inbound(db: AsyncSession) -> ErpSyncRun:
    run = ErpSyncRun(connector=settings.erp_connector, direction="inbound", status="running")
    db.add(run)
    await db.flush()
    stats = {"customers_matched": 0, "customers_unmatched": 0, "invoices_upserted": 0, "credit_holds": 0}
    try:
        accounts = (await db.execute(select(Account))).scalars().unique().all()
        if settings.erp_connector == "demo":
            customers, invoices = _demo_records(list(accounts))
        else:
            customers, invoices = await _pull("customers"), await _pull("invoices")
        by_erp = {}
        now = datetime.now(timezone.utc)
        for c in customers:
            acc = None
            if c.get("crm_account_id"):
                acc = next((a for a in accounts if str(a.id) == str(c["crm_account_id"])), None)
            acc = acc or next((a for a in accounts if a.erp_customer_id and a.erp_customer_id == c.get("erp_customer_id")), None)
            acc = acc or next((a for a in accounts if c.get("tax_id") and a.tax_id == c.get("tax_id")), None)
            if acc is None and c.get("domain"):
                acc = next((a for a in accounts if registrable_domain(a.domain) == registrable_domain(c["domain"])), None)
            if acc is None:
                stats["customers_unmatched"] += 1
                continue
            acc.erp_customer_id = c.get("erp_customer_id") or acc.erp_customer_id
            acc.legal_name = c.get("legal_name") or acc.legal_name
            acc.tax_id = c.get("tax_id") or acc.tax_id
            acc.billing_address = c.get("billing_address") or acc.billing_address
            if c.get("credit_limit") is not None:
                acc.credit_limit = Decimal(str(c["credit_limit"]))
            acc.payment_terms = c.get("payment_terms") or acc.payment_terms
            acc.erp_synced_at = now
            by_erp[acc.erp_customer_id] = acc
            stats["customers_matched"] += 1
        await db.flush()
        existing = {i.erp_invoice_id: i for i in (await db.execute(select(Invoice).where(Invoice.erp_invoice_id.in_([x["erp_invoice_id"] for x in invoices] or [""])))).scalars().all()}
        for x in invoices:
            acc = by_erp.get(x["erp_customer_id"])
            if acc is None:
                continue
            inv = existing.get(x["erp_invoice_id"]) or Invoice(erp_invoice_id=x["erp_invoice_id"], account_id=acc.id)
            inv.invoice_number, inv.currency = x["invoice_number"], x.get("currency", "USD")
            inv.issue_date, inv.due_date = date.fromisoformat(x["issue_date"]), date.fromisoformat(x["due_date"])
            inv.amount, inv.balance = Decimal(str(x["amount"])), Decimal(str(x["balance"]))
            inv.status, inv.synced_at = x.get("status", "open"), now
            if inv.id is None:
                db.add(inv)
            stats["invoices_upserted"] += 1
        await db.flush()
        for acc in by_erp.values():
            summary = await ar_summary(db, acc)
            explicit = next((c.get("credit_hold") for c in customers if c.get("erp_customer_id") == acc.erp_customer_id and c.get("credit_hold") is not None), None)
            hold = explicit if explicit is not None else (summary["buckets"]["90_plus"] > 0 or (acc.credit_limit is not None and summary["open_balance"] > float(acc.credit_limit)))
            if hold and not acc.credit_hold:
                emit(db, "invoice.overdue", "account", acc.id, {"account": acc.name, "open_balance": summary["open_balance"], "buckets": summary["buckets"]})
            acc.credit_hold = bool(hold)
            stats["credit_holds"] += int(acc.credit_hold)
        run.status, run.stats = "succeeded", stats
    except Exception as exc:  # recorded on the run; the API surfaces it
        run.status, run.error, run.stats = "failed", str(exc)[:1000], stats
    run.finished_at = datetime.now(timezone.utc)
    await db.flush()
    return run


async def sync_outbound(db: AsyncSession, since: datetime | None = None) -> ErpSyncRun:
    run = ErpSyncRun(connector=settings.erp_connector, direction="outbound", status="running")
    db.add(run)
    await db.flush()
    stmt = select(Account).where(or_(Account.lifecycle_stage == "customer", Account.erp_customer_id.is_not(None)))
    if since:
        stmt = stmt.where(Account.updated_at >= since)
    records = [{"crm_account_id": str(a.id), "erp_customer_id": a.erp_customer_id, "name": a.name, "legal_name": a.legal_name, "domain": a.domain,
                "tax_id": a.tax_id, "billing_address": a.billing_address, "payment_terms": a.payment_terms, "industry": a.industry}
               for a in (await db.execute(stmt)).scalars().unique().all()]
    try:
        if settings.erp_connector not in ("demo", "disabled"):
            await _push_customers(records)
        run.status, run.stats = "succeeded", {"customers_exported": len(records)}
    except Exception as exc:
        run.status, run.error = "failed", str(exc)[:1000]
    run.finished_at = datetime.now(timezone.utc)
    await db.flush()
    return run


async def ar_summary(db: AsyncSession, account: Account, today: date | None = None) -> dict:
    today = today or date.today()
    invoices = (await db.execute(select(Invoice).where(Invoice.account_id == account.id).order_by(Invoice.due_date.desc()))).scalars().all()
    buckets = {b[0]: 0.0 for b in AGING_BUCKETS}
    for inv in invoices:
        if inv.status != "open" or float(inv.balance) <= 0:
            continue
        overdue = (today - inv.due_date).days
        for name, lo, hi in AGING_BUCKETS:
            if (lo is None and overdue <= 0) or (lo is not None and overdue >= lo and (hi is None or overdue <= hi)):
                buckets[name] += float(inv.balance)
                break
    open_balance = round(sum(buckets.values()), 2)
    limit = float(account.credit_limit) if account.credit_limit is not None else None
    return {
        "open_balance": open_balance, "overdue_balance": round(open_balance - buckets["current"], 2),
        "buckets": {k: round(v, 2) for k, v in buckets.items()},
        "credit_limit": limit, "credit_available": round(limit - open_balance, 2) if limit is not None else None,
        "credit_hold": account.credit_hold, "erp_customer_id": account.erp_customer_id, "erp_synced_at": account.erp_synced_at,
        "invoices": [{"id": i.id, "invoice_number": i.invoice_number, "issue_date": i.issue_date, "due_date": i.due_date, "currency": i.currency,
                      "amount": float(i.amount), "balance": float(i.balance), "status": i.status,
                      "days_overdue": max(0, (today - i.due_date).days) if i.status == "open" and float(i.balance) > 0 else 0} for i in invoices],
    }


async def deliver_events(db: AsyncSession, limit: int = 200) -> dict:
    """Push pending outbox events to configured SDC module webhooks."""
    from app.models import IntegrationEvent

    hooks = json.loads(settings.ecosystem_webhooks or "{}")
    if not hooks:
        return {"delivered": 0, "note": "No ECOSYSTEM_WEBHOOKS configured; modules can poll /api/v1/integrations/events"}
    events = (await db.execute(select(IntegrationEvent).where(IntegrationEvent.delivered_at.is_(None)).order_by(IntegrationEvent.id).limit(limit))).scalars().all()
    delivered = 0
    async with httpx.AsyncClient(timeout=15) as client:
        for ev in events:
            ok = True
            for target in ev.targets:
                if target in hooks:
                    try:
                        (await client.post(hooks[target], json={"id": ev.id, "type": ev.event_type, "entity": ev.entity_type,
                                                                "entity_id": str(ev.entity_id) if ev.entity_id else None,
                                                                "payload": ev.payload, "created_at": ev.created_at.isoformat()})).raise_for_status()
                    except httpx.HTTPError:
                        ok = False
            if ok:
                ev.delivered_at = datetime.now(timezone.utc)
                delivered += 1
    await db.commit()
    return {"delivered": delivered}
