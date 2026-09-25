"""Hybrid semantic + keyword retrieval over notes, emails and account records (pillar 6).

Vector similarity (pgvector cosine) and Postgres full-text rank are fused with
Reciprocal Rank Fusion, so "accounts concerned about ERP migration timelines
in Q2" finds both paraphrased notes (vector) and exact terms (keyword).
"""
from __future__ import annotations

from sqlalchemy import Text, cast, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Activity
from app.services import embeddings
from app.services.serializers import activity_out

RRF_K = 60


async def hybrid_search(db: AsyncSession, query: str, limit: int = 10, principal=None, account_id=None) -> list[dict]:
    vector = await embeddings.embed(query)
    tsq = func.websearch_to_tsquery("english", query)
    fused: dict[tuple, dict] = {}

    def add(key, rank, kind, payload, signal):
        entry = fused.setdefault(key, {"score": 0.0, "kind": kind, "payload": payload, "signals": {}})
        entry["score"] += 1.0 / (RRF_K + rank)
        entry["signals"][signal] = rank

    base = select(Activity)
    if account_id:
        base = base.where(Activity.account_id == account_id)
    if principal is not None:
        base = principal.scope_accounts(base, "activities", Activity.account_id)
    if vector is not None:
        rows = (await db.execute(base.add_columns((1 - Activity.embedding.cosine_distance(vector)).label("sim"))
                                 .where(Activity.embedding.is_not(None)).order_by(Activity.embedding.cosine_distance(vector)).limit(limit * 3))).unique().all()
        for rank, (a, sim) in enumerate(rows, 1):
            if sim is not None and sim > 0.05:
                add(("activity", a.id), rank, "activity", (a, float(sim)), "vector")
    rows = (await db.execute(base.add_columns(func.ts_rank(Activity.search_tsv, tsq).label("rank")).where(Activity.search_tsv.op("@@")(tsq))
                             .order_by(literal_column("rank").desc()).limit(limit * 3))).unique().all()
    for rank, (a, _) in enumerate(rows, 1):
        prev = fused.get(("activity", a.id))
        add(("activity", a.id), rank, "activity", prev["payload"] if prev else (a, None), "keyword")

    if not account_id:
        acc_doc = func.to_tsvector("english", func.concat_ws(" ", Account.name, Account.industry, Account.legal_name,
                                                             cast(Account.custom_metadata, Text)))
        acc_base = select(Account)
        if principal is not None:
            acc_base = principal.scope_accounts(acc_base)
        rows = (await db.execute(acc_base.add_columns(func.ts_rank(acc_doc, tsq).label("rank")).where(or_(acc_doc.op("@@")(tsq), Account.name.ilike(f"%{query}%")))
                                 .order_by(literal_column("rank").desc()).limit(limit))).unique().all()
        for rank, (acc, _) in enumerate(rows, 1):
            add(("account", acc.id), rank, "account", acc, "keyword")
        if vector is not None:
            rows = (await db.execute(acc_base.add_columns((1 - Account.embedding.cosine_distance(vector)).label("sim")).where(Account.embedding.is_not(None))
                                     .order_by(Account.embedding.cosine_distance(vector)).limit(limit))).unique().all()
            for rank, (acc, sim) in enumerate(rows, 1):
                if sim and sim > 0.15:
                    add(("account", acc.id), rank, "account", acc, "vector")

    out = []
    for entry in sorted(fused.values(), key=lambda e: -e["score"])[:limit]:
        if entry["kind"] == "activity":
            a, sim = entry["payload"]
            item = {"entity": "activity", **activity_out(a, round(sim, 3) if sim is not None else None)}
        else:
            acc = entry["payload"]
            item = {"entity": "account", "id": acc.id, "name": acc.name, "domain": acc.domain, "industry": acc.industry,
                    "health_score": acc.health_score, "summary": f"{acc.name}: {acc.industry or 'account'} · health {acc.health_score}"}
        item["score"] = round(entry["score"], 5)
        item["matched_by"] = sorted(entry["signals"])
        out.append(item)
    return out


def account_document(acc: Account) -> str:
    fields = " ".join(f"{k} {v}" for k, v in (acc.custom_metadata or {}).items() if k != "health_breakdown")
    locs = " ".join(str(l.get("city", "")) for l in (acc.locations or []) if isinstance(l, dict))
    return f"{acc.name} {acc.legal_name or ''} {acc.industry or ''} {acc.tier} {locs} {fields}"
