from datetime import date

from app.services.ai_extractor import heuristic_extract

NOTES = """Great meeting today with Elena Rostova (VP Procurement) and James Cole, IT Director at Apex Industrial Supply.
Elena is our champion and is excited about the Supply Chain Analytics Platform. James raised concerns about on-prem
security and wants a SOC 2 report. Budget is around $75k, they want to sign by end of Q4. Currently on Salesforce.
Next steps:
- Send SOC 2 report to james.cole@apexindustrial.com by Friday
- Schedule pricing review with CFO Mark Diaz next week"""


def test_extracts_the_full_contract():
    r = heuristic_extract(NOTES, [("Apex Industrial Supply", "apexindustrial.com")], date(2026, 9, 25))
    assert r.account_name == "Apex Industrial Supply"
    assert r.domain == "apexindustrial.com"
    roles = {c.first_name: c.buying_role for c in r.contacts}
    assert roles == {"Elena": "Champion", "James": "Blocker", "Mark": "Economic Buyer"}
    james = next(c for c in r.contacts if c.first_name == "James")
    assert james.job_title == "IT Director" and james.email == "james.cole@apexindustrial.com"
    assert r.deal.amount == 75000
    assert r.deal.title == "Supply Chain Analytics Platform"
    assert r.deal.target_close_date == date(2026, 12, 31)
    assert r.activity_type == "meeting"
    assert r.sentiment == "positive"
    assert len(r.action_items) == 2
    assert all(a.due_date for a in r.action_items)


def test_new_account_from_domain_and_no_hallucination():
    r = heuristic_extract("Quick call with Priya Nair, CTO at Northwind Logistics (northwind.io). No budget discussed yet.", [], date(2026, 9, 25))
    assert r.account_name == "Northwind Logistics"
    assert r.domain == "northwind.io"
    assert r.contacts[0].job_title == "CTO"
    assert r.deal is None
    assert r.activity_type == "call"


def test_amount_units():
    r = heuristic_extract("Proposal for Acme at $1.2M total contract value.", [], date(2026, 9, 25))
    assert r.deal.amount == 1_200_000


async def test_unreachable_llm_falls_back_to_heuristic(monkeypatch):
    from app.core.config import settings
    from app.services import ai_extractor

    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_endpoint", "http://127.0.0.1:9")  # nothing listens here
    monkeypatch.setattr(settings, "llm_timeout_seconds", 2.0)
    result = await ai_extractor.extract("Call with Priya Nair, CTO at Northwind Logistics about a $50k pilot.", [], date(2026, 9, 25))
    assert result.engine == "heuristic"
    assert result.account_name == "Northwind Logistics"
    assert result.deal.amount == 50000


def test_llm_payload_is_validated_into_contract():
    from app.services.ai_extractor import _LLMQuickLog, _from_llm

    raw = {
        "account_name": "Apex Industrial Supply", "domain": "ApexIndustrial.com",
        "contacts": [{"first_name": "Elena", "last_name": "Rostova", "job_title": "VP Procurement", "email": "not-an-email", "buying_role": "Champion"}],
        "deal": {"title": "Analytics", "amount": 75000, "target_close_date": "2026-12-31", "suggested_stage": "Proposal/InfoSec"},
        "activity": {"activity_type": "meeting", "summary": "Pricing review", "action_items": [{"task": "Send MSA", "due_date": "bad-date"}], "sentiment": "positive"},
    }
    out = _from_llm(_LLMQuickLog.model_validate(raw))
    assert out.domain == "apexindustrial.com"
    assert out.contacts[0].email is None  # invalid email dropped, not hallucinated
    assert out.action_items[0].due_date is None
    assert out.deal.target_close_date == date(2026, 12, 31)
