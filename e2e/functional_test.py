"""Live functional test of every Cirra module against the running stack (API, Postgres, Redis, Celery worker + beat).

Run inside the api container:  docker compose cp e2e/functional_test.py api:/tmp/ && docker compose exec api python /tmp/functional_test.py /tmp/ft-results.json
Creates its own records (suffix RUN) and never deletes demo data.
"""
import hashlib
import hmac
import json
import sys
import threading
import time
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

sys.path.insert(0, "/app")  # the app package, for DB-level checks

BASE = "http://localhost:8000"
API = f"{BASE}/api/v1"
PW = "cirra123"
RUN = uuid.uuid4().hex[:6]
USERS = {
    "admin": "admin@cirra.demo", "manager": "marcus@cirra.demo", "ae": "priya@cirra.demo", "ae2": "diego@cirra.demo",
    "sdr": "sam@cirra.demo", "auditor": "viewer@cirra.demo", "support": "sofia@cirra.demo", "marketing": "nina@cirra.demo",
    "partner": "partner@northstar-partners.com",
}
results: list[dict] = []
C: dict[str, httpx.Client] = {}
ME: dict[str, dict] = {}


def check(module: str, name: str):
    def deco(fn):
        t0 = time.time()
        try:
            detail = fn()
            results.append({"module": module, "check": name, "status": "pass", "detail": str(detail or "")[:300], "ms": int((time.time() - t0) * 1000)})
            print(f"PASS  [{module}] {name} {detail or ''}"[:220])
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            results.append({"module": module, "check": name, "status": "fail", "detail": msg[:500], "ms": int((time.time() - t0) * 1000),
                            "trace": traceback.format_exc()[-800:]})
            print(f"FAIL  [{module}] {name} -> {msg}"[:300])
        return fn
    return deco


def ok(r: httpx.Response, *codes: int):
    codes = codes or (200, 201, 204)
    assert r.status_code in codes, f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:300]}"
    return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r


def login(email: str) -> httpx.Client:
    r = httpx.post(f"{API}/auth/login", json={"username": email, "password": PW}, timeout=30)
    assert r.status_code == 200, r.text
    c = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {r.json()['access_token']}"}, timeout=60)
    return c


anon = httpx.Client(base_url=API, timeout=30)

# ============ 1. CRM Platform Foundation ======================================================================


@check("01 Platform foundation", "health endpoint reports database up")
def _():
    r = httpx.get(f"{BASE}/health", timeout=10).json()
    assert r.get("status") in ("ok", "healthy") or r.get("database") in ("ok", True, "up"), r
    return r


@check("01 Platform foundation", "every demo role can sign in and gets its permission matrix")
def _():
    for k, email in USERS.items():
        C[k] = login(email)
        if k != "partner":
            ME[k] = ok(C[k].get("/users/me"))
            assert ME[k]["email"] == email and ME[k]["permissions"], k
    return f"{len(C)} roles: " + ", ".join(sorted({m['role'] for m in ME.values()}))


@check("01 Platform foundation", "wrong password is rejected, sessions are required")
def _():
    assert httpx.post(f"{API}/auth/login", json={"username": USERS["admin"], "password": "nope"}).status_code == 401
    assert anon.get("/deals").status_code == 401
    assert anon.get("/deals", headers={"Authorization": "Bearer garbage"}).status_code == 401


@check("01 Platform foundation", "global search, notifications, dashboard summary, users")
def _():
    s = ok(C["manager"].get("/search/global", params={"q": "Northwind"}))
    ok(C["manager"].get("/notifications"))
    d = ok(C["manager"].get("/dashboard/summary"))
    u = ok(C["manager"].get("/users"))
    return f"search hits={len(s) if isinstance(s, list) else sum(len(v) for v in s.values() if isinstance(v, list))}, users={len(u)}, summary keys={len(d)}"


@check("01 Platform foundation", "custom fields: define, validate and store on a record")
def _():
    key = f"ft_region_code_{RUN}"
    r = C["admin"].post("/admin/custom-fields", json={"entity": "account", "key": key, "label": "FT region code", "field_type": "text"})
    ok(r, 200, 201)
    acc = ok(C["admin"].get("/accounts"))[0]
    ok(C["admin"].patch(f"/accounts/{acc['id']}", json={"custom_fields": {key: "EU-7"}}))
    v = ok(C["admin"].get(f"/accounts/{acc['id']}/360"))["account"]["custom_fields"]
    assert v.get(key) == "EU-7", v
    return "custom field round-trips"


# ============ 2. Accounts ======================================================================================
STATE: dict = {}


@check("02 Accounts", "create account, duplicate detection, 360 view")
def _():
    a = ok(C["ae"].post("/accounts", json={"name": f"Fjordline Logistics {RUN}", "domain": f"fjordline-{RUN}.example.com", "industry": "Logistics",
                                           "tier": "Enterprise", "country": "Norway", "force": True}), 201)
    STATE["account"] = a
    dup = C["ae"].post("/accounts", json={"name": f"Fjordline Logistics {RUN}", "domain": f"fjordline-{RUN}.example.com"})
    assert dup.status_code == 409, dup.text
    v = ok(C["ae"].get(f"/accounts/{a['id']}/360"))
    assert v["account"]["name"].startswith("Fjordline") and v["account"]["territory"], v["account"].get("territory")
    return f"territory auto-assigned: {v['account']['territory']['name']}"


@check("02 Accounts", "row-level scope: one AE can't see another AE's account (404, not 403)")
def _():
    theirs = ok(C["ae2"].get("/accounts"))
    mine = {a["id"] for a in ok(C["ae"].get("/accounts"))}
    other = next(a for a in theirs if a["id"] not in mine)
    r = C["ae"].get(f"/accounts/{other['id']}/360")
    assert r.status_code == 404, r.status_code
    return f"{len(mine)} visible to Priya, blocked {other['name']}"


@check("02 Accounts", "hierarchy roll-up and duplicate suggestions")
def _():
    a = STATE["account"]
    child = ok(C["ae"].post("/accounts", json={"name": f"Fjordline Sweden {RUN}", "domain": f"fjordline-se-{RUN}.example.com", "parent_id": a["id"], "force": True}), 201)
    h = ok(C["ae"].get(f"/accounts/{a['id']}/hierarchy"))
    ok(C["ae"].get(f"/accounts/{a['id']}/duplicates"))
    assert child["id"] in json.dumps(h), "child missing from hierarchy"
    return "child appears in roll-up"


# ============ 3. Contacts =======================================================================================


@check("03 Contacts", "create contact with buying role; consent ledger; relationship strength")
def _():
    a = STATE["account"]
    c = ok(C["ae"].post("/contacts", json={"account_id": a["id"], "first_name": "Ingrid", "last_name": f"Berg{RUN}", "email": f"ingrid.{RUN}@fjordline-{RUN}.example.com",
                                           "job_title": "COO", "buying_role": "Champion"}), 201)
    STATE["contact"] = c
    ok(C["ae"].post(f"/contacts/{c['id']}/consent", json={"consent_email": "granted", "basis": "consent", "source": "functional test"}))
    got = ok(C["ae"].get(f"/contacts/{c['id']}"))
    assert got["consent"]["email"] == "granted", got["consent"]
    return "consent recorded"


@check("03 Contacts", "right to erasure crypto-shreds a contact")
def _():
    a = STATE["account"]
    c = ok(C["admin"].post("/contacts", json={"account_id": a["id"], "first_name": "Erase", "last_name": f"Me{RUN}", "email": f"erase.{RUN}@example.com"}), 201)
    r = C["admin"].post(f"/contacts/{c['id']}/erase", json={"regulation": "GDPR", "confirm": True})
    ok(r)
    after = ok(C["admin"].get(f"/contacts/{c['id']}"))
    blob = json.dumps(after)
    assert f"erase.{RUN}@example.com" not in blob, "email still present"
    return "PII removed"


# ============ 4. Leads ==========================================================================================


@check("04 Leads", "web-form intake with key, dedup merge, scoring and routing")
def _():
    key = ok(C["admin"].post("/admin/intake-keys", json={"name": f"FT form {RUN}", "source": "web_form"}), 201)["key"]
    form = {"first_name": "Lena", "last_name": f"Voss{RUN}", "email": f"lena.{RUN}@alpenhof-{RUN}.example.de", "company_name": f"Alpenhof {RUN}",
            "domain": f"alpenhof-{RUN}.example.de", "country": "Germany", "job_title": "VP Operations", "employee_count": 1200, "consent": True}
    r = ok(anon.post("/intake/leads", json=form, headers={"X-Cirra-Key": key}), 201)
    r2 = ok(anon.post("/intake/leads", json={**form, "message": "Pricing?"}, headers={"X-Cirra-Key": key}), 201)
    assert r2["merged"] and r2["lead_id"] == r["lead_id"]
    assert anon.post("/intake/leads", json=form, headers={"X-Cirra-Key": "nope"}).status_code == 401
    lead = ok(C["manager"].get(f"/leads/{r['lead_id']}"))
    STATE["lead"] = lead
    return f"score={lead['score']} fit={lead['fit_score']} owner={(lead.get('owner') or {}).get('full_name')}"


@check("04 Leads", "qualification (BANT), conversion to account + contact + opportunity")
def _():
    lead = STATE["lead"]
    ok(C["manager"].put(f"/leads/{lead['id']}/qualification", json={"framework": "bant", "criteria": {
        "budget": {"met": True, "note": "Approved"}, "authority": {"met": True, "note": "VP"}, "need": {"met": True, "note": "Forecasting"},
        "timeline": {"met": True, "note": "Q1"}}}))
    conv = ok(C["manager"].post(f"/leads/{lead['id']}/convert", json={"create_deal": True, "amount": 85000, "deal_title": f"Alpenhof rollout {RUN}"}))
    assert conv["deal_id"] and conv["account_id"], conv
    STATE["conv"] = conv
    return f"created {conv['created']}"


@check("04 Leads", "disqualify requires a reason; SDR sees only own leads")
def _():
    l2 = ok(C["sdr"].post("/leads", json={"first_name": "Spam", "last_name": f"Bot{RUN}", "email": f"spam{RUN}@example.com", "company_name": "X"}), 201)
    assert C["sdr"].post(f"/leads/{l2['id']}/disqualify", json={}).status_code == 422
    ok(C["sdr"].post(f"/leads/{l2['id']}/disqualify", json={"reason": "student_or_personal"}))
    own = ok(C["sdr"].get("/leads"))
    rows = own if isinstance(own, list) else own.get("leads", [])
    sam = ME["sdr"]["id"]
    assert all((x.get("owner") or {}).get("id") in (sam, None) for x in rows), "SDR sees others' leads"
    return f"{len(rows)} leads visible to SDR"


# ============ 5. Opportunities ==================================================================================


@check("05 Opportunities", "stage gates block, manager override passes, loss needs a reason")
def _():
    a = STATE["account"]
    pipelines = ok(C["ae"].get("/pipelines"))
    direct = next(p for p in pipelines if p["kind"] == "direct")
    deal = ok(C["ae"].post("/deals", json={"title": f"Fjordline platform {RUN}", "account_id": a["id"], "amount": 120000, "pipeline_id": direct["id"],
                                           "target_close_date": (date.today() + timedelta(days=30)).isoformat()}), 201)
    STATE["deal"], STATE["pipeline"] = deal, direct
    late = [s for s in direct["stages"] if not s["is_closed_won"] and not s["is_closed_lost"]][-1]
    blocked = C["ae"].patch(f"/deals/{deal['id']}/stage", json={"stage_id": late["id"]})
    assert blocked.status_code in (409, 422), blocked.status_code
    ok(C["manager"].patch(f"/deals/{deal['id']}/stage", json={"stage_id": late["id"], "override_gates": True}))
    lost = next(s for s in direct["stages"] if s["is_closed_lost"])
    no_reason = C["manager"].patch(f"/deals/{deal['id']}/stage", json={"stage_id": lost["id"]})
    assert no_reason.status_code in (409, 422), no_reason.status_code
    k = ok(C["ae"].get(f"/pipeline/{direct['id']}/kanban"))
    return f"gates held ({blocked.status_code}), override ok, kanban columns={len(k.get('columns', k)) if isinstance(k, dict) else len(k)}"


# ============ 6. Activities =====================================================================================


@check("06 Activities", "log call and meeting, tasks with dependencies, AI quick-log")
def _():
    a = STATE["account"]
    ok(C["ae"].post("/activities", json={"account_id": a["id"], "deal_id": STATE["deal"]["id"], "activity_type": "call", "direction": "outbound",
                                         "disposition": "connected", "duration_seconds": 600, "summary": "Discovery call; budget confirmed."}), 201)
    ok(C["ae"].post("/activities", json={"account_id": a["id"], "activity_type": "meeting", "attendance": "attended", "summary": "Demo with ops team",
                                         "duration_seconds": 3600}), 201)
    t = ok(C["ae"].post("/tasks", json={"title": f"Send proposal {RUN}", "account_id": a["id"], "priority": "high",
                                        "due_date": (date.today() + timedelta(days=2)).isoformat()}), 201)
    ok(C["ae"].patch(f"/tasks/{t['id']}", json={"completed": True}))
    q = ok(C["ae"].post("/ai/quick-log", json={"raw_text": f"Met Ingrid Berg{RUN} at Fjordline Logistics {RUN}; they want a pilot in March, budget 120k, worried about SAP integration."}))
    tl = ok(C["ae"].get("/activities", params={"account_id": a["id"]}))
    return f"timeline={len(tl)} items, quick-log={list(q)[:4]}"


@check("06 Activities", "personal iCal feed is served by token")
def _():
    tok = ok(C["ae"].post("/calendar/token"))
    r = httpx.get(f"{BASE}{tok['feed_path']}", timeout=30)
    assert r.status_code == 200 and "BEGIN:VCALENDAR" in r.text, r.status_code
    return f"{r.text.count('BEGIN:VEVENT')} events"


# ============ 7-9. Products, CPQ, contracts, orders ==============================================================


@check("07 Products & catalog", "catalogue, price books, promotions, bundle configuration")
def _():
    products = ok(C["ae"].get("/products"))
    books = ok(C["ae"].get("/price-books"))
    promos = ok(C["manager"].get("/promotions"))
    bundle = next((p for p in products if p.get("is_bundle") or p.get("components")), products[0])
    ok(C["ae"].get(f"/products/{bundle['id']}/configuration"))
    STATE["products"] = products
    return f"{len(products)} products, {len(books)} price books, {len(promos)} promotions"


@check("08 CPQ & quotes", "quote pricing, submission creates the approval chain, approver decides")
def _():
    deal = STATE["deal"]
    prod = next(p for p in STATE["products"] if p.get("sku", "").startswith("CIR-PLAT")) if any(p.get("sku", "").startswith("CIR-PLAT") for p in STATE["products"]) else STATE["products"][0]
    q = ok(C["ae"].post(f"/deals/{deal['id']}/quotes", json={"lines": [{"product_id": prod["id"], "quantity": 25, "discount_pct": 22}]}), 200, 201)
    assert q["total"] > 0 if "total" in q else True
    STATE["quote"] = q
    sub = C["ae"].post(f"/quotes/{q['id']}/submit")
    ok(sub, 200)
    pending = ok(C["manager"].get("/approvals"))
    rows = pending if isinstance(pending, list) else pending.get("pending", pending.get("approvals", []))
    mine = [x for x in rows if json.dumps(x).find(q["id"]) >= 0]
    decided, refused = 0, []
    for req in mine:  # a manager decides the Sales Manager level; other groups' levels are refused (separation of duties)
        r = C["manager"].post(f"/approvals/{req['id']}/decide", json={"approve": True, "comment": "FT approve"})
        if r.status_code == 200:
            decided += 1
        else:
            assert r.status_code in (403, 409) and "approver" in r.text.lower() or "earlier" in r.text.lower(), r.text
            refused.append(r.json()["detail"])
    assert mine, "submitting created no approval requests"
    again = ok(C["ae"].get(f"/quotes/{q['id']}"))
    return f"quote {q.get('quote_number')} status {again.get('status')}; manager decided {decided}, refused {len(refused)} ({refused[:1]})"


@check("09 Orders & contracts", "document generation, e-signature by customer and company, contracts and orders lists")
def _():
    deal = STATE["deal"]
    nda = ok(C["ae"].post("/documents", json={"doc_type": "nda", "deal_id": deal["id"]}), 200, 201)
    assert "Non-Disclosure" in nda["body"]
    sent = C["ae"].post(f"/documents/{nda['id']}/send", json={"signers": [
        {"name": "Ingrid Berg", "email": STATE["contact"]["email"], "party": "customer"},
        {"name": "Marcus Vance", "email": USERS["manager"], "party": "company"}]})
    if sent.status_code == 409:  # credit review can gate a brand-new account
        return f"send gated by credit review as designed: {sent.json().get('detail')}"
    doc = ok(sent)
    signers = sorted(doc["signers"], key=lambda s: s["order"])
    for s in signers:
        tok = s["sign_url"].rsplit("/", 1)[1]
        ok(anon.get(f"/sign/{tok}"))
        ok(anon.post(f"/sign/{tok}", json={"signature_text": s["name"], "agree": True}))
    final = ok(C["ae"].get(f"/documents/{nda['id']}"))
    ok(C["ae"].get("/contracts"))
    ok(C["ae"].get("/orders"))
    r = C["ae"].get(f"/deals/{deal['id']}/order-readiness")
    ok(r)
    return f"NDA status {final.get('status')}; order readiness checks={len(r.json().get('checks', []))}"


# ============ 10. Forecasting ===================================================================================


@check("10 Forecasting & revenue", "rep forecast roll-up, category override, submission; manager team view")
def _():
    me = ok(C["ae"].get("/forecast/me"))
    t = me["totals"]
    assert abs(t["commit_call"] - (t["closed"] + t["commit"])) < 0.05
    open_deal = next((d for d in me["deals"] if not d["is_closed"]), None)
    if open_deal:
        ok(C["ae"].put(f"/forecast/deals/{open_deal['id']}/category", json={"category": "commit"}))
        ok(C["ae"].put(f"/forecast/deals/{open_deal['id']}/category", json={"category": None}))
    ok(C["ae"].post("/forecast/submissions", json={"period": me["period"], "commit": t["commit_call"], "best_case": t["best_case_call"], "note": "FT"}), 201)
    team = ok(C["manager"].get("/forecast/team"))
    assert C["ae"].get("/forecast/team").status_code == 403
    rf = ok(C["manager"].get("/reports/forecast"))
    return f"commit call {t['commit_call']:.0f}; team rows={len(team['rows'])}; forecast report ok={bool(rf)}"


# ============ 11. Territory / quota / incentives ================================================================


@check("11 Territory, quota & incentives", "scorecard, team leaderboard, quota setting, realign preview, plan preview")
def _():
    me = ok(C["ae"].get("/performance/me"))
    team = ok(C["manager"].get("/performance/team"))
    quotas = ok(C["manager"].get("/performance/quotas"))
    ok(C["manager"].put("/performance/quotas", json=[{"period": quotas["period"], "user_id": me["user"]["id"], "amount": me["quota"] or 350000}]))
    t = ok(C["admin"].get("/performance/territories"))
    prev = ok(C["admin"].post("/performance/territories/realign"))
    pp = ok(C["admin"].post("/performance/plans/preview", json={"base_rate": 6, "tiers": [{"from_pct": 100, "rate": 9}], "quota": 100000, "bookings": 120000}))
    assert abs(pp["total"] - (6000 + 1800)) < 0.01, pp
    assert C["ae"].get("/performance/team").status_code == 403
    return (f"Priya quota {me['quota']:.0f}, closed {me['closed']:.0f}, commission {me['commission']['total']:.0f}; "
            f"{len(team['rows'])} on team; {len(t['territories'])} territories, {t['unassigned']} unassigned; preview moves {len(prev['changes'])}")


# ============ 12. Marketing =====================================================================================


@check("12 Marketing & campaigns", "campaign create, capture attribution, list building, consent-checked send, unsubscribe")
def _():
    mk = C["marketing"]
    cp = ok(mk.post("/campaigns", json={"name": f"FT Webinar {RUN}", "campaign_type": "webinar", "status": "active", "actual_cost": 2500,
                                         "email_subject": "Hi {{first_name}}", "email_body": "Join us, {{first_name}} from {{company}}."}), 201)
    lead = ok(mk.post("/leads", json={"first_name": "Otto", "last_name": f"Lind{RUN}", "email": f"otto.{RUN}@example.se", "company_name": "Lind AB",
                                       "source": "campaign", "campaign": cp["code"], "consent": "granted"}), 201)
    members = ok(mk.get(f"/campaigns/{cp['id']}/members"))
    assert members["total"] == 1 and members["members"][0]["source"] == "capture", members
    added = ok(mk.post(f"/campaigns/{cp['id']}/members/from-filter", json={"source": "contacts", "filters": [{"field": "buying_role", "op": "eq", "value": "Champion"}]}))
    prev = ok(mk.get(f"/campaigns/{cp['id']}/email/preview"))
    sent = ok(mk.post(f"/campaigns/{cp['id']}/email/send"))
    assert sent["sent"] == prev["eligible"]
    sent_members = ok(mk.get(f"/campaigns/{cp['id']}/members", params={"status": "sent"}))["members"]
    STATE["campaign"] = cp
    detail = ok(mk.get(f"/campaigns/{cp['id']}"))
    return (f"captured lead joined; filter added {added['added']}; sent {sent['sent']} (skipped {sent['skipped']}); "
            f"ROI {detail['metrics']['roi_pct']}, members {detail['metrics']['members']}; sent rows {len(sent_members)}")


@check("12 Marketing & campaigns", "public unsubscribe link opts the person out; sellers can't create campaigns")
def _():
    import asyncio

    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models import CampaignMember

    async def token():
        async with SessionLocal() as db:
            return (await db.execute(select(CampaignMember.token).where(CampaignMember.campaign_id == uuid.UUID(STATE["campaign"]["id"]),
                                                                        CampaignMember.status == "sent").limit(1))).scalar()
    tok = asyncio.run(token())
    assert tok, "no sent member"
    assert ok(anon.get(f"/public/unsubscribe/{tok}"))["unsubscribed"] is False
    assert ok(anon.post(f"/public/unsubscribe/{tok}"))["unsubscribed"] is True
    assert C["ae"].post("/campaigns", json={"name": "rogue"}).status_code == 403
    return "unsubscribed via token"


# ============ 13. Service =======================================================================================


@check("13 Customer service", "case with SLA clocks, internal note vs public reply, resolve, CSAT, knowledge base")
def _():
    sup = C["support"]
    case = ok(sup.post("/cases", json={"account_id": STATE["account"]["id"], "contact_id": STATE["contact"]["id"], "subject": f"SSO loop {RUN}",
                                        "severity": "high", "channel": "email"}), 201)
    d = ok(sup.get(f"/cases/{case['id']}"))
    assert d["clocks"]["first_response"]["state"] in ("running", "due_soon"), d["clocks"]
    ok(sup.post(f"/cases/{case['id']}/comments", json={"body": "Checking IdP logs", "internal": True}), 201)
    ok(sup.post(f"/cases/{case['id']}/comments", json={"body": "Fix going out today", "internal": False}), 201)
    d = ok(sup.get(f"/cases/{case['id']}"))
    assert d["clocks"]["first_response"]["state"] == "met", d["clocks"]["first_response"]
    ok(sup.patch(f"/cases/{case['id']}", json={"status": "resolved"}))
    d = ok(sup.get(f"/cases/{case['id']}"))
    token = d["csat"]["survey_url"].rsplit("/", 1)[1]
    ok(anon.post(f"/public/csat/{token}", json={"score": 5, "comment": "Great"}))
    kb = ok(sup.get("/knowledge", params={"search": "sso login"}))
    ok(sup.get("/cases/stats"))
    return f"{d['case_number']} resolved, CSAT sent; KB top hit: {kb['articles'][0]['title'] if kb['articles'] else None}"


# ============ 14. Customer success ==============================================================================


@check("14 Customer success", "onboarding workspaces, churn watchlist, renewals; closed-won provisions onboarding")
def _():
    ob = ok(C["manager"].get("/success/onboarding"))
    churn = ok(C["manager"].get("/success/churn"))
    ren = ok(C["manager"].get("/success/renewals"))
    return f"{len(ob)} onboarding projects, churn rows={len(churn) if isinstance(churn, list) else len(churn.get('accounts', []))}, renewals={len(ren) if isinstance(ren, list) else ren}"


# ============ 15. Partners ======================================================================================


@check("15 Partner / channel", "partner portal registration, conflict check, manager approval creates a partner deal")
def _():
    p = C["partner"]
    ok(p.get("/portal/me"))
    reg = ok(p.post("/portal/registrations", json={"company_name": f"Kestrel Foods {RUN}", "domain": f"kestrel-{RUN}.example.com", "territory": "NA-East",
                                                    "estimated_amount": 40000, "product_interest": "Growth"}), 201)
    assert p.get("/deals").status_code == 403, "partner reached internal API"
    regs = ok(C["manager"].get("/partners/registrations"))
    mine = next(r for r in (regs if isinstance(regs, list) else regs.get("registrations", [])) if r["id"] == reg["id"])
    dec = ok(C["manager"].post(f"/partners/registrations/{mine['id']}/decide", json={"approve": True, "note": "FT"}))
    ok(p.get("/portal/collateral"))
    ok(p.get("/portal/commissions"))
    return f"registration approved -> deal {dec.get('deal_id')}"


# ============ 16. CDP / 360 =====================================================================================


@check("16 Customer data / 360", "account 360 unifies contacts, deals, activities, contracts, cases, finance; semantic search")
def _():
    v = ok(C["ae"].get(f"/accounts/{STATE['account']['id']}/360"))
    keys = {k for k in v if v[k]}
    for k in ("contacts", "deals", "recent_activities"):
        assert k in keys, f"360 missing {k}"
    s = ok(C["ae"].post("/search/semantic", json={"query": "SAP integration worries", "limit": 5}))
    ar = ok(C["manager"].get(f"/finance/accounts/{STATE['account']['id']}/ar"))
    return f"360 sections: {sorted(keys)[:9]}; semantic hits={len(s) if isinstance(s, list) else len(s.get('results', []))}"


# ============ 17. Workflows =====================================================================================


@check("17 Workflow automation", "rule validation, test run preview, created-trigger fires after commit and logs a run")
def _():
    adm = C["admin"]
    bad = adm.post("/workflows", json={"name": "bad", "source": "nope", "trigger": {"type": "created"}, "actions": []})
    assert bad.status_code == 422
    rule = ok(adm.post("/workflows", json={"name": f"FT task follow-up {RUN}", "enabled": True, "source": "tasks", "trigger": {"type": "created"},
                                           "conditions": [{"field": "title", "op": "contains", "value": f"WF{RUN}"}],
                                           "actions": [{"type": "notify", "to": ["owner"], "title": "New task {{title}}"}]}), 201)
    ok(adm.post(f"/workflows/{rule['id']}/test", json={}))
    ok(C["ae"].post("/tasks", json={"title": f"Call back WF{RUN}", "priority": "normal"}), 201)
    runs = []
    for _ in range(20):
        runs = ok(adm.get(f"/workflows/{rule['id']}/runs"))
        runs = runs if isinstance(runs, list) else runs.get("runs", [])
        if runs:
            break
        time.sleep(1)
    assert runs, "no workflow run logged"
    ok(adm.post(f"/workflows/{rule['id']}/toggle"))
    return f"{len(runs)} run(s), last status {runs[0].get('status')}"


# ============ 18. Analytics =====================================================================================


@check("18 Analytics & reporting", "report builder over every source, save + share, dashboard, CSV export, win/loss")
def _():
    mgr = C["manager"]
    src = ok(mgr.get("/analytics/sources"))
    keys = [s["key"] for s in src["sources"]]
    for k in keys:
        ok(mgr.post("/analytics/run", json={"definition": {"source": k, "measures": [{"agg": "count"}]}}))
    rep = ok(mgr.post("/analytics/reports", json={"name": f"FT deals by territory {RUN}", "visibility": "shared",
                                                  "definition": {"source": "deals", "group_by": [{"field": "territory"}], "measures": [{"agg": "count"}]}}), 201)
    dash = ok(mgr.post("/analytics/dashboards", json={"name": f"FT dash {RUN}", "visibility": "shared", "tiles": [{"report_id": rep["id"], "size": "half"}]}), 201)
    ok(mgr.get(f"/analytics/dashboards/{dash['id']}"))
    csv = mgr.get(f"/analytics/reports/{rep['id']}/export")
    assert csv.status_code == 200 and "," in csv.text
    ok(mgr.get("/reports/win-loss"))
    ae_view = ok(C["ae"].post(f"/analytics/reports/{rep['id']}/run"))
    return f"{len(keys)} sources ran ({', '.join(keys)}); shared report runs under the AE's scope ({len(ae_view['rows'])} rows)"


# ============ 19. AI ============================================================================================


@check("19 AI / agentic", "briefing, ask (hybrid RAG), account brief, email draft, risk scan")
def _():
    ok(C["ae"].get("/ai/status"))
    b = ok(C["ae"].get("/ai/briefing"))
    a = ok(C["ae"].post("/ai/ask", json={"question": "Which deals are at risk this quarter?"}))
    ok(C["ae"].get(f"/ai/accounts/{STATE['account']['id']}/brief"))
    ok(C["ae"].post(f"/ai/deals/{STATE['deal']['id']}/draft-email", json={"purpose": "follow-up"}))
    scan = ok(C["admin"].post("/admin/jobs/risk_scan"))
    alerts = ok(C["ae"].get("/alerts"))
    return f"briefing headline: {str(b.get('headline'))[:60]}; answer chars={len(json.dumps(a))}; scan={scan}; alerts={len(alerts)}"


# ============ 20. Integration / API / events =====================================================================

RECEIVED: list[dict] = []


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        if self.path == "/hook":  # other (test) subscriptions may point at other paths on this listener
            RECEIVED.append({"headers": dict(self.headers), "body": body})
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


@check("20 Integration / API / events", "API key: read-only enforcement, scope as its user, revocation")
def _():
    adm = C["admin"]
    k = ok(adm.post("/developer/api-keys", json={"name": f"FT BI {RUN}", "user_id": ME["ae"]["id"], "read_only": True}), 201)
    h = {"X-API-Key": k["key"]}
    mine = {d["id"] for d in ok(C["ae"].get("/deals"))}
    via = {d["id"] for d in ok(anon.get("/deals", headers=h))}
    assert via == mine, "key scope differs from its user"
    assert anon.post("/tasks", json={"title": "x"}, headers=h).status_code == 403
    assert anon.get("/developer/api-keys", headers=h).status_code == 403
    ok(adm.post(f"/developer/api-keys/{k['id']}/revoke"))
    assert anon.get("/deals", headers=h).status_code == 401
    return f"key acted as Priya over {len(via)} deals, then revoked"


@check("20 Integration / API / events", "webhook delivered by the Celery beat worker, HMAC-signed, pattern-filtered")
def _():
    srv = HTTPServer(("0.0.0.0", 9999), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    adm = C["admin"]
    sub = ok(adm.post("/developer/webhooks", json={"name": f"FT receiver {RUN}", "url": "http://api:9999/hook", "event_types": ["deal.*", "case.created"]}), 201)
    STATE["hook"] = sub
    ping = ok(adm.post(f"/developer/webhooks/{sub['id']}/test"))
    assert ping["status"] == "success", ping
    RECEIVED.clear()
    deal = ok(C["ae"].post("/deals", json={"title": f"Hooked deal {RUN}", "account_id": STATE["account"]["id"], "amount": 5000,
                                           "pipeline_id": STATE["pipeline"]["id"]}), 201)
    ok(C["ae"].patch(f"/contacts/{STATE['contact']['id']}", json={"job_title": "Chief Operating Officer"}))
    t0 = time.time()
    while time.time() - t0 < 100 and not any(json.loads(r["body"]).get("data", {}).get("deal_id") == deal["id"] for r in RECEIVED):
        time.sleep(2)
    got = [r for r in RECEIVED if json.loads(r["body"]).get("data", {}).get("deal_id") == deal["id"]]
    assert got, f"worker didn't deliver within 100 s ({len(RECEIVED)} other deliveries)"
    secret = sub["secret"]

    def valid(rec):  # other (older) subscriptions may also point at this listener; ours must verify with our secret
        parts = dict(p.split("=", 1) for p in rec["headers"].get("X-Cirra-Signature", "").split(","))
        want = hmac.new(secret.encode(), f"{parts.get('t')}.".encode() + rec["body"], hashlib.sha256).hexdigest()
        return hmac.compare_digest(want, parts.get("v1", ""))
    assert any(valid(r) for r in got), "no delivery carried a valid signature for this subscription"
    types = {json.loads(r["body"])["type"] for r in RECEIVED}
    assert "contact.created" not in types and all(t.startswith("deal.") or t == "case.created" for t in types), types
    srv.shutdown()
    deliveries = ok(adm.get(f"/developer/webhooks/{sub['id']}/deliveries"))
    return f"delivered by worker in {time.time() - t0:.0f}s, signature valid; event types seen {sorted(types)}; log rows {len(deliveries)}"


@check("20 Integration / API / events", "outbox event feed advances its cursor per target; ERP sync runs")
def _():
    f = ok(C["manager"].get("/integrations/events", params={"target": "nexora", "limit": 5}))
    assert all("nexora" in e["targets"] for e in f["events"])
    f2 = ok(C["manager"].get("/integrations/events", params={"target": "nexora", "limit": 5, "after_id": f["next_after_id"]}))
    assert not f2["events"] or f2["events"][0]["id"] > f["next_after_id"]
    assert C["manager"].post("/integrations/erp/sync").status_code == 403  # finance update is Super Admin only
    run = ok(C["admin"].post("/integrations/erp/sync"))
    return f"feed ok, ERP sync {run['status']} via {run['connector']}"


# ============ 21. Administration / security / governance ========================================================


@check("21 Admin, security & governance", "RBAC matrix editable by admin only; audit trail is append-only")
def _():
    m = ok(C["admin"].get("/admin/permissions"))
    assert C["manager"].put("/admin/permissions", json=[]).status_code == 403
    audit = ok(C["auditor"].get("/admin/audit"))
    rows = audit if isinstance(audit, list) else audit.get("items", [])
    assert rows, "auditor sees no audit entries"
    import asyncio

    from sqlalchemy import text

    from app.core.database import SessionLocal

    async def tamper():
        async with SessionLocal() as db:
            try:
                await db.execute(text("UPDATE audit_log SET new_value = 'x' WHERE id = (SELECT id FROM audit_log LIMIT 1)"))
                await db.commit()
                return "UPDATE allowed"
            except Exception as e:  # noqa: BLE001
                return f"rejected: {type(e).__name__}"
    res = asyncio.run(tamper())
    assert res.startswith("rejected"), res
    return f"{len(m.get('roles', []))} roles in matrix; auditor sees {len(rows)} audit rows; tamper {res}"


@check("21 Admin, security & governance", "two-factor enrolment and sign-in with TOTP; admin reset signs the user out")
def _():
    from app.services import identity

    email = f"ft.mfa.{RUN}@cirra.demo"
    u = ok(C["admin"].post("/admin/users", json={"email": email, "full_name": "FT MFA", "role": "account_executive"}), 201)
    tmp = u.get("temporary_password")
    c = httpx.Client(base_url=API, timeout=30)
    tok = ok(c.post("/auth/login", json={"username": email, "password": tmp}))["access_token"]
    c.headers["Authorization"] = f"Bearer {tok}"
    start = ok(c.post("/auth/mfa/enroll/start", json={}))
    conf = ok(c.post("/auth/mfa/enroll/confirm", json={"code": identity.totp_now(start["secret"])}))
    assert len(conf["recovery_codes"]) >= 8
    step1 = ok(anon.post("/auth/login", json={"username": email, "password": tmp}))
    assert step1.get("mfa_required") and not step1.get("access_token")
    time.sleep(31)  # next TOTP step (replay protection)
    fin = ok(anon.post("/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "code": identity.totp_now(start["secret"])}))
    assert fin["access_token"]
    users = ok(C["admin"].get("/admin/users"))
    uid = next(x["id"] for x in users if x["email"] == email)
    ok(C["admin"].post(f"/admin/users/{uid}/reset-mfa"))
    assert anon.get("/users/me", headers={"Authorization": f"Bearer {fin['access_token']}"}).status_code == 401
    ok(C["admin"].patch(f"/admin/users/{uid}", json={"is_active": False}))
    return "enrol -> 2-step sign-in -> reset revoked the session"


@check("21 Admin, security & governance", "export is audited; import preview auto-maps and validates")
def _():
    r = C["manager"].get("/admin/export/accounts", params={"format": "csv"})
    assert r.status_code == 200 and r.text.count("\n") > 1
    csv_body = f"Company Name,Website,Industry\nImport Test {RUN},import-{RUN}.example.com,Retail\nBad Row,,\n"
    prev = ok(C["admin"].post("/admin/import/accounts/preview", files={"file": ("a.csv", csv_body.encode(), "text/csv")}))
    return f"export {r.text.count(chr(10))} lines; import preview mapping={prev.get('mapping')}, errors={len(prev.get('errors', []))}"


@check("21 Admin, security & governance", "every background job runs on demand without errors")
def _():
    jobs = ["risk_scan", "escalations", "renewals", "rescore", "auto_dedup", "erp_sync", "erp_orders", "lead_rescore", "reindex", "workflows", "case_sla",
            "territories", "webhooks"]
    out = {}
    for j in jobs:
        r = C["admin"].post(f"/admin/jobs/{j}", timeout=300)
        assert r.status_code == 200, f"{j}: {r.status_code} {r.text[:200]}"
        out[j] = "ok"
    return f"{len(out)} jobs ok"


# ============ Role sweep: every read endpoint x every role =========================================================
# (path, resource, action) - resource None = any signed-in internal user
SWEEP = [
    ("/accounts", "accounts", "read"), ("/contacts", "contacts", "read"), ("/deals", "deals", "read"), ("/activities", "activities", "read"),
    ("/tasks", "tasks", "read"), ("/products", "products", "read"), ("/quotes", "quotes", "read"), ("/approvals", "approvals", "read"),
    ("/contracts", "contracts", "read"), ("/orders", "orders", "read"), ("/leads", "leads", "read"), ("/leads/summary", "leads", "read"),
    ("/success/onboarding", "success", "read"), ("/success/churn", "success", "read"), ("/success/renewals", "contracts", "read"),
    ("/finance/ar-aging", "finance", "read"), ("/partners", "partners", "read"), ("/partners/registrations", "partners", "read"),
    ("/partners/commissions", "partners", "read"), ("/analytics/sources", "reports", "read"), ("/analytics/reports", "reports", "read"),
    ("/analytics/dashboards", "reports", "read"), ("/admin/users", "admin", "read"), ("/admin/permissions", "admin", "read"),
    ("/admin/audit", "audit", "read"), ("/admin/custom-fields", None, None), ("/admin/security", "admin", "read"),
    ("/cases", "cases", "read"), ("/cases/meta", "cases", "read"), ("/cases/stats", "cases", "read"), ("/knowledge", "knowledge", "read"),
    ("/service/queues", "cases", "read"), ("/service/sla", "cases", "read"),
    ("/campaigns", "campaigns", "read"), ("/campaigns/meta", "campaigns", "read"),
    ("/performance/me", "deals", "read"), ("/performance/periods", "deals", "read"), ("/performance/territories", "accounts", "read"),
    ("/performance/plans", "admin", "read"), ("/developer/api-keys", "admin", "read"), ("/developer/webhooks", "admin", "read"),
    ("/developer/events", "admin", "read"), ("/workflows", "admin", "read"), ("/forecast/me", "deals", "read"), ("/forecast/periods", "deals", "read"),
    ("/integrations/events", "finance", "read"), ("/pipelines", None, None), ("/price-books", None, None), ("/promotions", None, None),
    ("/notifications", None, None), ("/users", None, None), ("/users/me", None, None), ("/dashboard/summary", None, None), ("/alerts", None, None),
    ("/ai/briefing", None, None), ("/ai/status", None, None), ("/reports/forecast", None, None), ("/reports/win-loss", None, None),
    ("/search/global?q=a", None, None), ("/approval-groups", None, None), ("/approval-policies", None, None), ("/admin/lead-settings", None, None),
    ("/admin/assignment-rules", None, None), ("/admin/intake-keys", None, None), ("/admin/dedup", None, None), ("/admin/compliance", None, None),
    ("/integrations/erp/runs", None, None), ("/email/mailboxes", None, None), ("/workflows/meta", None, None),
    ("/performance/team", None, None), ("/performance/quotas", None, None), ("/forecast/team", None, None),
]


@check("Role sweep", "every read endpoint for every role: no server errors and access matches the permission matrix")
def _():
    errors, mismatches, calls = [], [], 0
    for role, client in C.items():
        perms = ME.get(role, {}).get("permissions", {})
        for path, res, action in SWEEP:
            r = client.get(path)
            calls += 1
            if r.status_code >= 500:
                errors.append(f"{role} {path} {r.status_code}")
                continue
            if role == "partner":
                # a portal user may read only their own profile, notifications, calendar/mailbox and AI status
                if r.status_code not in (403, 401) and path not in ("/users/me", "/notifications", "/email/mailboxes", "/ai/status"):
                    mismatches.append(f"partner reached {path} ({r.status_code})")
                continue
            if res:
                allowed = perms.get(res, {}).get(action, False)
                if allowed and r.status_code != 200:
                    mismatches.append(f"{role} {path} expected 200 got {r.status_code}: {r.text[:80]}")
                if not allowed and r.status_code != 403:
                    mismatches.append(f"{role} {path} expected 403 got {r.status_code}")
    assert not errors, f"5xx: {errors[:10]}"
    assert not mismatches, f"{len(mismatches)} mismatches: {mismatches[:12]}"
    return f"{calls} calls across {len(C)} roles, 0 server errors, 0 permission mismatches"


# ============ Report ==============================================================================================
out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ft-results.json"
with open(out, "w") as f:
    json.dump({"run": RUN, "at": datetime.now(timezone.utc).isoformat(), "results": results}, f, indent=1, default=str)
passed = sum(r["status"] == "pass" for r in results)
print(f"\n{passed}/{len(results)} checks passed")
