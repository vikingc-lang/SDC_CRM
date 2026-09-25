"""Parent-child-subsidiary hierarchies with revenue roll-ups (pillar 1)."""
from __future__ import annotations

import uuid

from sqlalchemy import text
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
    SELECT d.*,
        COALESCE((SELECT SUM(x.amount) FROM deals x JOIN pipeline_stages s ON s.id = x.stage_id
                  WHERE x.account_id = d.id AND NOT s.is_closed_won AND NOT s.is_closed_lost), 0) AS open_pipeline,
        COALESCE((SELECT SUM(x.amount) FROM deals x JOIN pipeline_stages s ON s.id = x.stage_id
                  WHERE x.account_id = d.id AND s.is_closed_won), 0) AS won_revenue,
        COALESCE((SELECT SUM(c.tcv) FROM contracts c WHERE c.account_id = d.id), 0) AS contract_spend,
        COALESCE((SELECT SUM(c.acv) FROM contracts c WHERE c.account_id = d.id AND c.status = 'active'), 0) AS active_acv
    FROM down d
    """
)


async def hierarchy(db: AsyncSession, account_id: uuid.UUID) -> dict:
    """The full tree containing ``account_id`` with own and rolled-up (self + descendants) totals."""
    rows = [dict(r._mapping) for r in (await db.execute(_TREE, {"id": account_id})).all()]
    if not rows:
        return {"root": None, "nodes": []}
    metrics = ("open_pipeline", "won_revenue", "contract_spend", "active_acv")
    nodes = {r["id"]: {**{k: (float(v) if k in metrics else v) for k, v in r.items()}, "children": []} for r in rows}
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
