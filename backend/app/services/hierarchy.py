"""Parent-child-subsidiary hierarchies with revenue roll-ups in US dollars (pillar 1)."""
from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

_TREE = text(
    """
    WITH RECURSIVE up AS (
        SELECT id, parent_id, 0 AS depth FROM accounts WHERE id = :id
        UNION ALL
        SELECT a.id, a.parent_id, up.depth + 1 FROM accounts a JOIN up ON a.id = up.parent_id WHERE up.depth < 20
    ),
    root AS (SELECT id FROM up ORDER BY depth DESC LIMIT 1),
    down AS (
        SELECT a.id, a.parent_id, a.name, a.domain, a.health_score, a.lifecycle_stage, 0 AS depth
        FROM accounts a WHERE a.id = (SELECT id FROM root)
        UNION ALL
        SELECT a.id, a.parent_id, a.name, a.domain, a.health_score, a.lifecycle_stage, down.depth + 1
        FROM accounts a JOIN down ON a.parent_id = down.id WHERE down.depth < 20
    )
    SELECT d.*
    FROM down d
    """
)


async def hierarchy(db: AsyncSession, account_id: uuid.UUID) -> dict:
    """The full tree containing ``account_id`` with own and rolled-up (self + descendants) totals."""
    rows = [dict(r._mapping) for r in (await db.execute(_TREE, {"id": account_id})).all()]
    if not rows:
        return {"root": None, "nodes": []}
    metrics = ("open_pipeline", "won_revenue", "contract_spend", "active_acv")
    totals = await _totals_usd(db, [r["id"] for r in rows])
    nodes = {r["id"]: {**r, **totals.get(r["id"], dict.fromkeys(metrics, 0.0)), "children": []} for r in rows}
    for n in nodes.values():
        if n["parent_id"] in nodes and n["id"] != n["parent_id"]:
            nodes[n["parent_id"]]["children"].append(n)

    def roll(n):
        totals = {m: n[m] for m in metrics}
        for c in n["children"]:
            sub = roll(c)
            for m in metrics:
                totals[m] += sub[m]
        n["rollup"] = {m: round(v, 2) for m, v in totals.items()}
        n["descendants"] = sum(1 + c["descendants"] for c in n["children"])
        return totals

    root = next(n for n in nodes.values() if n["depth"] == 0)
    roll(root)
    flat = sorted(nodes.values(), key=lambda n: n["depth"])
    for n in flat:
        n["is_current"] = n["id"] == account_id
    return {"root": root, "current": nodes.get(account_id)}


async def _totals_usd(db: AsyncSession, account_ids: list) -> dict:
    """Per-account money in US dollars, converting each deal and contract from its own currency (won deals at
    their close-date rate, open pipeline and contracts at today's), so a tree mixing currencies adds up."""
    from app.models import Contract, Deal, PipelineStage
    from app.services import fx

    rates = await fx.rates(db)
    out: dict = {a: {"open_pipeline": 0.0, "won_revenue": 0.0, "contract_spend": 0.0, "active_acv": 0.0} for a in account_ids}
    deals = await db.execute(select(Deal.account_id, Deal.amount, Deal.currency, Deal.closed_at, PipelineStage.is_closed_won,
                                    PipelineStage.is_closed_lost).join(PipelineStage, PipelineStage.id == Deal.stage_id)
                             .where(Deal.account_id.in_(account_ids)))
    for acc, amount, cur, closed_at, won, lost in deals:
        if won:
            out[acc]["won_revenue"] += fx.to_usd(float(amount or 0), cur, rates, on=closed_at)
        elif not lost:
            out[acc]["open_pipeline"] += fx.to_usd(float(amount or 0), cur, rates)
    for acc, tcv, acv, cur, status in await db.execute(select(Contract.account_id, Contract.tcv, Contract.acv, Contract.currency, Contract.status)
                                                        .where(Contract.account_id.in_(account_ids))):
        out[acc]["contract_spend"] += fx.to_usd(float(tcv or 0), cur, rates)
        if status == "active":
            out[acc]["active_acv"] += fx.to_usd(float(acv or 0), cur, rates)
    return {a: {k: round(v, 2) for k, v in m.items()} for a, m in out.items()}


async def would_create_cycle(db: AsyncSession, account_id: uuid.UUID, new_parent_id: uuid.UUID) -> bool:
    if account_id == new_parent_id:
        return True
    rows = await db.execute(
        text(
            "WITH RECURSIVE up AS (SELECT id, parent_id, 0 AS d FROM accounts WHERE id = :p "
            "UNION ALL SELECT a.id, a.parent_id, up.d + 1 FROM accounts a JOIN up ON a.id = up.parent_id WHERE up.d < 50) "
            "SELECT 1 FROM up WHERE id = :a LIMIT 1"
        ),
        {"p": new_parent_id, "a": account_id},
    )
    return rows.first() is not None
