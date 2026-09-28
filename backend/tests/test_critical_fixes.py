"""Regression tests for the three critical findings of the external build review (Cirra_CRM_Build_Review.pptx):
a forgeable default token secret, quote documents that could load another deal's quote, and duplicate
review / merge without row-level scope."""
import uuid

import pytest
from sqlalchemy import select

from app.core.config import Settings, enforce_secure_settings, security_problems
from app.core.database import SessionLocal
from app.models import Account, Contact, Deal, Quote, User
from tests.helpers import login_as

API = "/api/v1"
OLD_DEFAULT = "super_secret_jwt_key_change_in_production"
STRONG = "k" * 20 + uuid.uuid4().hex  # 52 characters


# ---- 1. the token secret ---------------------------------------------------------------------------------------

def _settings(monkeypatch, **kw) -> Settings:
    for var in ("JWT_SECRET", "DATA_ENCRYPTION_KEY", "ENVIRONMENT", "ERP_CONNECTOR", "REDIS_URL"):
        monkeypatch.delenv(var, raising=False)
    return Settings(_env_file=None, **kw)


def test_no_usable_secret_ships_in_code(monkeypatch):
    assert _settings(monkeypatch).jwt_secret == ""


@pytest.mark.parametrize("secret", [OLD_DEFAULT, "", "short-but-random-1234", "changeme"])
def test_production_refuses_to_start_with_a_weak_or_published_secret(monkeypatch, secret):
    s = _settings(monkeypatch, environment="production", jwt_secret=secret)
    assert security_problems(s)
    with pytest.raises(RuntimeError, match="Refusing to start"):
        enforce_secure_settings(s)


def test_production_starts_with_a_strong_secret_and_checks_the_encryption_key(monkeypatch):
    enforce_secure_settings(_settings(monkeypatch, environment="production", jwt_secret=STRONG, data_encryption_key=STRONG[::-1]))
    with pytest.raises(RuntimeError, match="DATA_ENCRYPTION_KEY"):
        enforce_secure_settings(_settings(monkeypatch, environment="production", jwt_secret=STRONG, data_encryption_key="tiny"))


def test_development_warns_and_generates_a_throwaway_secret(monkeypatch, caplog):
    s = _settings(monkeypatch, environment="development")
    enforce_secure_settings(s)
    assert len(s.jwt_secret) >= 32 and s.jwt_secret != OLD_DEFAULT
    enforce_secure_settings(_settings(monkeypatch, environment="development", jwt_secret=OLD_DEFAULT))
    assert "INSECURE CONFIGURATION" in caplog.text


# ---- 2. quote documents ------------------------------------------------------------------------------------------

async def _quote_on_other_deal(owner_email: str):
    """A deal owned by ``owner_email`` and a quote that belongs to a different deal."""
    async with SessionLocal() as db:
        owner = (await db.execute(select(User).where(User.email == owner_email))).scalar_one()
        quote = (await db.execute(select(Quote).join(Deal, Deal.id == Quote.deal_id).where(Deal.owner_id != owner.id).limit(1))).scalar_one()
        deal = (await db.execute(select(Deal).where(Deal.owner_id == owner.id, Deal.id != quote.deal_id).limit(1))).scalar_one()
        return deal, quote


async def test_a_document_cannot_carry_another_deals_quote(client):
    deal, quote = await _quote_on_other_deal("marcus@cirra.demo")
    r = await client.post(f"{API}/documents", json={"doc_type": "proposal", "deal_id": str(deal.id), "quote_id": str(quote.id)})
    assert r.status_code == 422 and "another opportunity" in r.json()["detail"]
    own = await client.post(f"{API}/documents", json={"doc_type": "proposal", "deal_id": str(quote.deal_id), "quote_id": str(quote.id)})
    assert own.status_code == 201, own.text  # the quote's own deal still works


async def test_a_rep_cannot_load_a_quote_outside_their_scope(client):
    deal, quote = await _quote_on_other_deal("priya@cirra.demo")
    async with login_as("priya@cirra.demo") as ae:
        r = await ae.post(f"{API}/documents", json={"doc_type": "proposal", "deal_id": str(deal.id), "quote_id": str(quote.id)})
        assert r.status_code == 404  # not "wrong deal": the quote's existence isn't revealed


# ---- 3. duplicate review and merge -------------------------------------------------------------------------------

async def _duplicates_owned_by(owner_email: str, client) -> tuple[dict, dict, str]:
    """Two near-duplicate accounts and two duplicate contacts, all owned by ``owner_email``."""
    tag = uuid.uuid4().hex[:6]
    owner = next(u["id"] for u in (await client.get(f"{API}/users")).json() if u["email"] == owner_email)
    a = (await client.post(f"{API}/accounts", json={"name": f"Zephyr Dupe {tag} Inc", "domain": f"zephyr-{tag}.example.com",
                                                    "owner_id": owner, "force": True})).json()
    b = (await client.post(f"{API}/accounts", json={"name": f"Zephyr Dupe {tag}", "domain": f"zephyr-{tag}-emea.example.com",
                                                    "owner_id": owner, "force": True})).json()
    for _ in range(2):
        await client.post(f"{API}/contacts", json={"account_id": a["id"], "first_name": "Dana", "last_name": f"Twin{tag}"})
    return a, b, tag


def _ids(pairs):
    return {frozenset((c["a"]["id"], c["b"]["id"])) for c in pairs}


async def test_duplicate_review_only_shows_and_touches_records_in_scope(client):
    a, b, tag = await _duplicates_owned_by("diego@cirra.demo", client)
    async with SessionLocal() as db:
        twins = [str(c.id) for c in (await db.execute(select(Contact).where(Contact.last_name == f"Twin{tag}"))).scalars()]
    everyone = (await client.get(f"{API}/admin/dedup")).json()  # a sales manager sees them all
    assert frozenset((a["id"], b["id"])) in _ids(everyone["accounts"]) and frozenset(twins) in _ids(everyone["contacts"])
    assert everyone["can_merge"] == {"account": True, "contact": True}

    async with login_as("priya@cirra.demo") as ae:  # another rep: Diego's records are invisible and untouchable
        mine = (await ae.get(f"{API}/admin/dedup")).json()
        assert frozenset((a["id"], b["id"])) not in _ids(mine["accounts"]) and frozenset(twins) not in _ids(mine["contacts"])
        assert mine["can_merge"] == {"account": False, "contact": True}
        r = await ae.post(f"{API}/admin/dedup/merge", json={"entity": "account", "survivor_id": a["id"], "merged_id": b["id"]})
        assert r.status_code == 403 and "can't delete accounts" in r.json()["detail"]
        r = await ae.post(f"{API}/admin/dedup/merge", json={"entity": "contact", "survivor_id": twins[0], "merged_id": twins[1]})
        assert r.status_code == 404
        r = await ae.post(f"{API}/admin/dedup/dismiss", json={"entity": "account", "id_a": a["id"], "id_b": b["id"]})
        assert r.status_code == 404
    async with SessionLocal() as db:
        assert await db.get(Account, uuid.UUID(b["id"])) is not None and await db.get(Contact, uuid.UUID(twins[1])) is not None

    r = await client.post(f"{API}/admin/dedup/merge", json={"entity": "account", "survivor_id": a["id"], "merged_id": b["id"]})
    assert r.status_code == 200, r.text  # the manager can still merge
