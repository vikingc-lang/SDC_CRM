"""Hybrid semantic + keyword retrieval over notes, emails and account records (pillar 6).

Vector similarity (pgvector cosine) and Postgres full-text rank are fused with
Reciprocal Rank Fusion, so "accounts concerned about ERP migration timelines
in Q2" finds both paraphrased notes (vector) and exact terms (keyword). On other databases the vector signal is
skipped and the keyword signal uses app.core.dialect.full_text (LIKE matching).
"""
from __future__ import annotations

from sqlalchemy import Text, cast, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dialect import full_text, is_postgres, json_without_keys

from app.models import Account, Activity
from app.services import custom_fields, embeddings
from app.services.serializers import activity_out

RRF_K = 60


async def hybrid_search(db: AsyncSession, query: str, limit: int = 10, principal=None, account_id=None) -> list[dict]:
    vector = await embeddings.embed(query) if is_postgres(db) else None  # similarity search needs pgvector
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
    matches, rank_of = full_text(db, query, [Activity.subject, Activity.summary, Activity.raw_text], vector=Activity.search_tsv)
    rows = (await db.execute(base.add_columns(rank_of.label("rank")).where(matches)
                             .order_by(literal_column("rank").desc()).limit(limit * 3))).unique().all()
    for rank, (a, _) in enumerate(rows, 1):
        prev = fused.get(("activity", a.id))
        add(("activity", a.id), rank, "activity", prev["payload"] if prev else (a, None), "keyword")

    if not account_id:
        hidden = custom_fields.hidden_keys("account", principal.user.role if principal is not None else None)
        meta = json_without_keys(db, Account.custom_metadata, hidden)  # field security; None: leave custom fields out
        fields = [Account.name, Account.industry, Account.legal_name] + ([cast(meta, Text)] if meta is not None else [])
        acc_match, acc_rank = full_text(db, query, fields)
        acc_base = select(Account)
        if principal is not None:
            acc_base = principal.scope_accounts(acc_base)
        rows = (await db.execute(acc_base.add_columns(acc_rank.label("rank")).where(or_(acc_match, Account.name.ilike(f"%{query}%")))
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
    restricted = custom_fields.restricted_keys("account")  # the embedding is shared by every role
    fields = " ".join(f"{k} {v}" for k, v in (acc.custom_metadata or {}).items() if k != "health_breakdown" and k not in restricted)
    locs = " ".join(str(l.get("city", "")) for l in (acc.locations or []) if isinstance(l, dict))
    return f"{acc.name} {acc.legal_name or ''} {acc.industry or ''} {acc.tier} {locs} {fields}"
