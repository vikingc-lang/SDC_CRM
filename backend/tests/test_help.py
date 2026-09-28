"""The help center: role guides from live permissions, search, page context, the live data model, and Aiden
answering how-to questions from the help content."""
import uuid

from app.help import content
from tests.helpers import login_as

API = "/api/v1"


async def test_role_guide_and_permissions_follow_the_role(client):
    async with login_as("sam@cirra.demo") as sdr:
        h = (await sdr.get(f"{API}/help")).json()
        assert h["role"]["key"] == "sdr" and h["role"]["mission"] and h["role"]["day"][0]["href"].startswith("/")
        perms = {p["resource"]: p for p in h["permissions"]}
        assert "create" in perms["leads"]["actions"] and "admin" not in perms  # no admin rights, so no admin row
        assert {a["key"] for a in h["areas"]} >= {"pipeline", "leads", "service", "workflows"}
        assert h["processes"][0]["steps"] and h["glossary"]["MQL"]
    async with login_as("admin@cirra.demo") as admin:
        perms = {p["resource"]: p for p in (await admin.get(f"{API}/help")).json()["permissions"]}
        assert perms["admin"]["scope"] == "Everyone's" and "update" in perms["admin"]["actions"]


async def test_search_context_and_area_pages(client):
    hits = (await client.get(f"{API}/help/search", params={"q": "how do I create a quote"})).json()["results"]
    assert hits[0]["kind"] == "howto" and hits[0]["title"] == "How do I create a quote?" and hits[0]["steps"]
    assert (await client.get(f"{API}/help/search", params={"q": "what is MEDDPICC"})).json()["results"][0]["kind"] == "glossary"
    ctx = (await client.get(f"{API}/help/context", params={"path": f"/deals/{uuid.uuid4()}"})).json()
    assert ctx["areas"][0]["key"] == "pipeline" and ctx["areas"][0]["howto"]
    assert (await client.get(f"{API}/help/context", params={"path": "/"})).json()["areas"][0]["key"] == "home"
    area = (await client.get(f"{API}/help/areas/cpq")).json()
    assert area["title"].startswith("Products") and any(p["key"] == "lead-to-cash" for p in area["processes"])
    assert (await client.get(f"{API}/help/areas/nope")).status_code == 404
    # every link in the content points at a real page of the app
    pages = {"/", "/leads", "/pipeline", "/deals", "/accounts", "/contacts", "/tasks", "/ask", "/products", "/quotes", "/approvals", "/documents",
             "/orders", "/performance", "/reports", "/campaigns", "/cases", "/cases/inbox", "/cases/routing", "/knowledge", "/success", "/finance",
             "/partners", "/portal", "/admin", "/admin/workflows", "/objects", "/settings"}
    links = [p.split("?")[0] for a in content.AREAS for p in a["pages"]] + [s["page"].split("?")[0] for p in content.PROCESSES for s in p["steps"]]
    links += [h.split("?")[0] for r in content.ROLES.values() for _, h in r["day"]]
    assert set(links) <= pages, set(links) - pages


async def test_data_model_is_live_and_respects_field_security(client):
    key = f"help_secret_{uuid.uuid4().hex[:6]}"
    async with login_as("admin@cirra.demo") as admin:
        r = await admin.post(f"{API}/admin/custom-fields", json={"entity": "deal", "key": key, "label": "Margin note", "field_type": "text",
                                                                   "access": {"sdr": "hidden"}})
        assert r.status_code in (200, 201), r.text
        model = (await admin.get(f"{API}/help/data-model")).json()
        deal = next(e for e in model["entities"] if e["label"] == "Opportunity")
        assert "Account" in deal["links"] and any(f["key"] == "amount" for f in deal["fields"])
        assert any(f["key"] == key for f in deal["custom_fields"])
        assert not any(f["key"] in ("embedding", "search_tsv") for e in model["entities"] for f in e["fields"])
    async with login_as("sam@cirra.demo") as sdr:
        deal = next(e for e in (await sdr.get(f"{API}/help/data-model")).json()["entities"] if e["label"] == "Opportunity")
        assert not any(f["key"] == key for f in deal["custom_fields"])  # hidden from this role, so not described either


async def test_aiden_answers_how_to_questions_from_help(client):
    r = (await client.post(f"{API}/ai/ask", json={"question": "How do I add products to a deal?"})).json()
    assert r["engine"] == "help" and "1. Open the deal" in r["answer"] and r["help"][0]["href"].startswith("/help/areas/pipeline")
    data = (await client.post(f"{API}/ai/ask", json={"question": "Which deals are at risk?"})).json()
    assert data["engine"] != "help"  # a question about the data is still answered from the data
    async with login_as("sofia@cirra.demo") as agent:  # the help drawer works for every role
        r = (await agent.post(f"{API}/help/ask", json={"question": "how do I take new cases", "page": "/cases"})).json()
        assert r["help"] and "Available" in r["answer"]
        r = (await agent.post(f"{API}/help/ask", json={"question": "zzqx", "page": "/cases"})).json()
        assert r["help"] and r["help"][0]["href"] == "/help/areas/service"  # falls back to this page's help


async def test_help_answers_use_the_model_when_configured(client, monkeypatch):
    from types import SimpleNamespace

    from app.services import llm

    sent = []

    class Fake:
        messages = None

        async def create(self, **kw):
            sent.append(kw)
            return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=50, output_tokens=20),
                                   content=[SimpleNamespace(type="text", text="Open Settings → Calendar sync and connect [H1].")])

    fake = Fake()
    fake.messages = fake
    monkeypatch.setattr(llm.settings, "llm_provider", "anthropic")
    monkeypatch.setattr(llm, "_get_claude_client", lambda: fake)
    r = (await client.post(f"{API}/help/ask", json={"question": "How do I connect my calendar?"})).json()
    assert r["answer"].startswith("Open Settings") and r["help"][0]["title"] == "How do I connect my calendar?"
    assert "HELP:" in sent[0]["messages"][0]["content"] and "Don't invent features" in sent[0]["system"]
