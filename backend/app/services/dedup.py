"""Autonomous deduplication (pillar 1).

Scoring
    * Account names are normalised (case, punctuation, legal suffixes such as
      Inc/LLC/GmbH, a leading "The") and compared with Jaro-Winkler and
      normalised Levenshtein similarity: ``0.6*JW + 0.4*Lev``.
    * Registrable-domain equality (``www.apex.com`` == ``apex.com``, also
      across alternate domains) is decisive evidence.
    * Contacts match on e-mail, or on full name within the same account.

Merge conflict-resolution rules (overridable per field)
    * scalars: survivor value, unless empty -> take the merged record's value
    * lists (locations, alternate domains): union; merged primary domain is
      kept as an alternate domain of the survivor
    * JSON attributes: key-wise union, survivor wins on conflict
    * contacts: the more senior buying role wins; privacy flags take the
      *most restrictive* value (denied > granted > unknown, any opt-out wins)
    * every child record (contacts, deals, activities, tasks, documents,
      contracts, invoices, tickets, usage, files, subsidiaries) is re-parented
      onto the survivor, a snapshot is written to ``merge_log`` and the merged
      record is deleted.

Autonomy: ``auto_merge`` merges only near-certain pairs (same registrable
domain with a similar name, or name score >= 0.97); everything else is queued
as a suggestion for a human.
"""
from __future__ import annotations

import re
import uuid
from itertools import combinations

from rapidfuzz.distance import JaroWinkler, Levenshtein
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import _fmt, log_action
from app.models import (
    Account, Activity, Attachment, Contact, Contract, Deal, DedupDismissal, Document, Invoice, MergeLog, OnboardingProject,
    ProductUsage, SupportTicket, Task,
)

_SUFFIXES = r"\b(incorporated|inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|gmbh|plc|s\.a|sa|ag|bv|pty|srl|holdings?)\b\.?"
_SECOND_LEVEL = {"co.uk", "com.au", "co.jp", "com.br", "co.in", "co.nz", "com.mx", "co.za"}
ACCOUNT_SUGGEST_AT = 0.88
AUTO_MERGE_AT = 0.97
ROLE_RANK = {"Decision Maker": 6, "Economic Buyer": 5, "Champion": 5, "Influencer": 3, "Blocker": 2, "Evaluator": 1}
CONSENT_RANK = {"denied": 2, "granted": 1, "unknown": 0}


def normalize_name(name: str) -> str:
    n = name.lower().replace("&", " and ")
    n = re.sub(r"^the\s+", "", n)
    n = re.sub(_SUFFIXES, " ", n)
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def registrable_domain(domain: str | None) -> str | None:
    if not domain:
        return None
    d = domain.lower().strip().removeprefix("http://").removeprefix("https://").split("/")[0].removeprefix("www.")
    parts = d.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else d


def name_similarity(a: str, b: str) -> dict:
    na, nb = normalize_name(a), normalize_name(b)
    jw = JaroWinkler.normalized_similarity(na, nb)
    lev = Levenshtein.normalized_similarity(na, nb)
    return {"jaro_winkler": round(jw, 4), "levenshtein": round(lev, 4), "score": round(0.6 * jw + 0.4 * lev, 4)}


def account_match(a: Account, b: Account) -> dict | None:
    sim = name_similarity(a.name, b.name)
    domains_a = {registrable_domain(d) for d in [a.domain, *(a.alt_domains or [])]} - {None}
    domains_b = {registrable_domain(d) for d in [b.domain, *(b.alt_domains or [])]} - {None}
    same_domain = bool(domains_a & domains_b)
    reasons = []
    if same_domain:
        reasons.append(f"same domain ({', '.join(sorted(domains_a & domains_b))})")
    if sim["score"] >= ACCOUNT_SUGGEST_AT:
        reasons.append(f"similar name (JW {sim['jaro_winkler']:.2f}, Lev {sim['levenshtein']:.2f})")
    if not reasons:
        return None
    score = max(sim["score"], 0.99 if same_domain and sim["score"] >= 0.6 else 0.9 if same_domain else 0)
    auto = (same_domain and sim["score"] >= 0.85) or sim["score"] >= AUTO_MERGE_AT
    return {"score": round(score, 4), "reasons": reasons, "similarity": sim, "same_domain": same_domain, "auto_mergeable": auto}


def contact_match(a: Contact, b: Contact) -> dict | None:
    if a.email and b.email and a.email.lower() == b.email.lower():
        return {"score": 1.0, "reasons": ["same email"], "auto_mergeable": True}
    full_a, full_b = f"{a.first_name} {a.last_name}", f"{b.first_name} {b.last_name}"
    jw = JaroWinkler.normalized_similarity(full_a.lower(), full_b.lower())
    if a.account_id == b.account_id and jw >= 0.92:
        auto = jw >= 0.97 and not (a.email and b.email)
        return {"score": round(jw, 4), "reasons": [f"similar name in same account (JW {jw:.2f})"], "auto_mergeable": auto}
    return None


async def _dismissed(db: AsyncSession, entity: str) -> set[tuple]:
    rows = (await db.execute(select(DedupDismissal.id_a, DedupDismissal.id_b).where(DedupDismissal.entity == entity))).all()
    return {tuple(sorted((str(a), str(b)))) for a, b in rows}


async def account_candidates(db: AsyncSession, limit: int = 100) -> list[dict]:
    """Blocking with pg_trgm + registrable domain, then precise scoring."""
    accounts = {a.id: a for a in (await db.execute(select(Account))).scalars().unique().all()}
    pairs: set[tuple] = set()
    rows = await db.execute(
        text(
            "SELECT a.id, b.id FROM accounts a JOIN accounts b ON a.id < b.id "
            "WHERE similarity(lower(a.name), lower(b.name)) > 0.35"
        )
    )
    pairs.update(tuple(sorted((x, y), key=str)) for x, y in rows.all())
    by_domain: dict[str, list] = {}
    for a in accounts.values():
        for d in {registrable_domain(x) for x in [a.domain, *(a.alt_domains or [])]} - {None}:
            by_domain.setdefault(d, []).append(a.id)
    for ids in by_domain.values():
        pairs.update(tuple(sorted(p, key=str)) for p in combinations(ids, 2))
    dismissed = await _dismissed(db, "account")
    out = []
    for x, y in pairs:
        if tuple(sorted((str(x), str(y)))) in dismissed or x not in accounts or y not in accounts:
            continue
        m = account_match(accounts[x], accounts[y])
        if m:
            a, b = accounts[x], accounts[y]
            out.append({**m, "entity": "account",
                        "a": {"id": a.id, "name": a.name, "domain": a.domain, "created_at": a.created_at},
                        "b": {"id": b.id, "name": b.name, "domain": b.domain, "created_at": b.created_at}})
    return sorted(out, key=lambda m: -m["score"])[:limit]


async def contact_candidates(db: AsyncSession, limit: int = 100) -> list[dict]:
    contacts = (await db.execute(select(Contact).where(Contact.status != "erased"))).scalars().all()
    by_account: dict = {}
    for c in contacts:
        by_account.setdefault(c.account_id, []).append(c)
    dismissed = await _dismissed(db, "contact")
    out = []
    for group in by_account.values():
        for a, b in combinations(group, 2):
            if tuple(sorted((str(a.id), str(b.id)))) in dismissed:
                continue
            m = contact_match(a, b)
            if m:
                out.append({**m, "entity": "contact",
                            "a": {"id": a.id, "name": a.full_name, "email": a.email, "created_at": a.created_at},
                            "b": {"id": b.id, "name": b.full_name, "email": b.email, "created_at": b.created_at}})
    return sorted(out, key=lambda m: -m["score"])[:limit]


def _snapshot(obj) -> dict:
    from sqlalchemy import inspect

    return {c.key: _fmt(getattr(obj, c.key)) for c in inspect(obj).mapper.column_attrs if c.key not in ("embedding", "search_tsv")}


_ACCOUNT_SCALARS = ("industry", "industry_code", "tier", "annual_revenue", "employee_count", "owner_id", "legal_name", "tax_id",
                    "credit_limit", "erp_customer_id", "parent_id", "lifecycle_stage")


async def merge_accounts(db: AsyncSession, survivor: Account, merged: Account, overrides: dict | None = None,
                         user_id=None, score: float | None = None, automatic: bool = False) -> MergeLog:
    if survivor.id == merged.id:
        raise ValueError("Cannot merge a record into itself")
    overrides = overrides or {}
    snapshot = _snapshot(merged)
    resolution: dict = {}
    for f in _ACCOUNT_SCALARS:
        choice = overrides.get(f)
        s_val, m_val = getattr(survivor, f), getattr(merged, f)
        if f == "parent_id" and m_val == survivor.id:
            m_val = None
        if choice == "merged" or (choice is None and s_val in (None, "") and m_val not in (None, "")):
            setattr(survivor, f, m_val)
            resolution[f] = "merged" if choice == "merged" else "merged (survivor empty)"
        else:
            resolution[f] = "survivor"
    if overrides.get("name") == "merged":
        survivor.name, resolution["name"] = merged.name, "merged"
    survivor.locations = list({str(l): l for l in [*(survivor.locations or []), *(merged.locations or [])]}.values())
    alt = [*(survivor.alt_domains or []), *(merged.alt_domains or []), merged.domain]
    survivor.alt_domains = sorted({d for d in alt if d and d != survivor.domain})
    survivor.custom_metadata = {**(merged.custom_metadata or {}), **(survivor.custom_metadata or {})}
    survivor.billing_address = survivor.billing_address or merged.billing_address
    survivor.credit_hold = survivor.credit_hold or merged.credit_hold
    resolution.update({"locations": "union", "alt_domains": "union", "custom_metadata": "union (survivor wins)", "credit_hold": "most restrictive"})

    await db.flush()
    # usage rows are unique per (account, day): the survivor's measurement wins on colliding days
    await db.execute(
        delete(ProductUsage).where(
            ProductUsage.account_id == merged.id,
            ProductUsage.metric_date.in_(select(ProductUsage.metric_date).where(ProductUsage.account_id == survivor.id)),
        )
    )
    for model in (Contact, Deal, Activity, Task, Document, Contract, Invoice, SupportTicket, ProductUsage, Attachment, OnboardingProject):
        await db.execute(update(model).where(model.account_id == merged.id).values(account_id=survivor.id))
    await db.execute(update(Account).where(Account.parent_id == merged.id, Account.id != survivor.id).values(parent_id=survivor.id))
    # Reload collections so the ORM's delete-orphan cascade doesn't remove the re-parented contacts.
    await db.refresh(merged, ["contacts"])
    await db.refresh(survivor, ["contacts"])

    log = MergeLog(entity="account", survivor_id=survivor.id, merged_id=merged.id, snapshot=snapshot, field_resolution=resolution,
                   score=score, automatic=automatic, merged_by=user_id)
    db.add(log)
    log_action(db, "merge", "accounts", survivor.id, f"merged {merged.name} ({merged.id})")
    await db.delete(merged)
    await db.flush()
    return log


async def merge_contacts(db: AsyncSession, survivor: Contact, merged: Contact, overrides: dict | None = None,
                         user_id=None, score: float | None = None, automatic: bool = False) -> MergeLog:
    if survivor.id == merged.id:
        raise ValueError("Cannot merge a record into itself")
    overrides = overrides or {}
    snapshot = _snapshot(merged)
    resolution = {}
    for f in ("email", "phone", "mobile", "linkedin_url", "job_title", "department", "timezone"):
        s_val, m_val = getattr(survivor, f), getattr(merged, f)
        if overrides.get(f) == "merged" or (s_val in (None, "") and m_val not in (None, "")):
            if f == "email":
                merged.email = None  # release the unique email before moving it
                await db.flush()
            setattr(survivor, f, m_val)
            resolution[f] = "merged"
        else:
            resolution[f] = "survivor"
    if ROLE_RANK.get(merged.buying_role, 0) > ROLE_RANK.get(survivor.buying_role, 0):
        survivor.buying_role, resolution["buying_role"] = merged.buying_role, "merged (more senior)"
    if CONSENT_RANK[merged.consent_email] > CONSENT_RANK[survivor.consent_email]:
        survivor.consent_email = merged.consent_email
    for flag in ("opt_out_email", "opt_out_phone", "opt_out_sms", "do_not_sell"):
        setattr(survivor, flag, getattr(survivor, flag) or getattr(merged, flag))
    resolution["privacy"] = "most restrictive"
    survivor.custom_fields = {**(merged.custom_fields or {}), **(survivor.custom_fields or {})}
    await db.execute(update(Activity).where(Activity.contact_id == merged.id).values(contact_id=survivor.id))
    await db.execute(update(Deal).where(Deal.primary_contact_id == merged.id).values(primary_contact_id=survivor.id))
    log = MergeLog(entity="contact", survivor_id=survivor.id, merged_id=merged.id, snapshot=snapshot, field_resolution=resolution,
                   score=score, automatic=automatic, merged_by=user_id)
    db.add(log)
    log_action(db, "merge", "contacts", survivor.id, f"merged contact {merged.id}")
    await db.delete(merged)
    await db.flush()
    return log


def pick_survivor(a, b):
    """Older record with more history survives by default."""
    return (a, b) if (a.created_at or 0) <= (b.created_at or 0) else (b, a)


async def auto_merge(db: AsyncSession) -> dict:
    merged = {"accounts": 0, "contacts": 0}
    for cand in await account_candidates(db, limit=500):
        if not cand["auto_mergeable"]:
            continue
        a, b = await db.get(Account, cand["a"]["id"]), await db.get(Account, cand["b"]["id"])
        if a is None or b is None:
            continue
        survivor, loser = pick_survivor(a, b)
        await merge_accounts(db, survivor, loser, score=cand["score"], automatic=True)
        merged["accounts"] += 1
    for cand in await contact_candidates(db, limit=500):
        if not cand["auto_mergeable"]:
            continue
        a, b = await db.get(Contact, cand["a"]["id"]), await db.get(Contact, cand["b"]["id"])
        if a is None or b is None:
            continue
        survivor, loser = pick_survivor(a, b)
        await merge_contacts(db, survivor, loser, score=cand["score"], automatic=True)
        merged["contacts"] += 1
    await db.commit()
    return merged


async def find_account_duplicate(db: AsyncSession, name: str, domain: str | None) -> dict | None:
    """Best existing match for a would-be new account (used on create and in Quick-Log)."""
    probe = Account(name=name, domain=domain or "", alt_domains=[])
    conds = [func.similarity(func.lower(Account.name), name.lower()) > 0.3]
    reg = registrable_domain(domain)
    if reg:
        conds.append(Account.domain.ilike(f"%{reg}"))
    best = None
    for acc in (await db.execute(select(Account).where(or_(*conds)).limit(25))).scalars().unique().all():
        m = account_match(probe, acc)
        if m and (best is None or m["score"] > best["score"]):
            best = {**m, "account": {"id": acc.id, "name": acc.name, "domain": acc.domain}}
    return best


def new_id() -> uuid.UUID:
    return uuid.uuid4()
