"""Sales performance: territory alignment, quota attainment and commission statements.

Territories are checked in priority order (lower first, then the more specific one). Every non-empty
criterion must match; a territory without criteria is a catch-all. Realignment stamps accounts with
their territory; it never changes the account owner, but reports owners who aren't on the territory
("coverage gaps") so a manager can reassign deliberately.

Attainment is closed-won bookings in the quarter (USD) against the seller's quota. Commission is paid
per slice of attainment, like a tax bracket:
    base_rate on bookings up to the first tier threshold (e.g. 100% of quota),
    each tier's rate on the bookings between its threshold and the next.
Without a quota every booking earns the base rate. Each won deal's commission is the increase it
causes, so the statement lines add up to the total.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, CommissionPlan, Quota, Territory, User
from app.services import forecasting, fx

LIST_KEYS = ("regions", "countries", "industries", "tiers")
TIERS = ("SMB", "Mid-Market", "Enterprise")


class PerformanceError(ValueError):
    pass


# ---- territories ---------------------------------------------------------------------------------

def clean_criteria(raw: dict | None) -> dict:
    raw = raw or {}
    out: dict = {}
    for k in LIST_KEYS:
        vals = [str(v).strip() for v in raw.get(k) or [] if str(v).strip()]
        if k == "tiers" and any(v not in TIERS for v in vals):
            raise PerformanceError(f"Tiers are {', '.join(TIERS)}")
        if vals:
            out[k] = list(dict.fromkeys(vals))
    for k in ("min_employees", "max_employees"):
        v = raw.get(k)
        if v not in (None, ""):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise PerformanceError("Employee limits must be whole numbers")
            if v < 0:
                raise PerformanceError("Employee limits can't be negative")
            out[k] = v
    if out.get("min_employees") is not None and out.get("max_employees") is not None and out["min_employees"] > out["max_employees"]:
        raise PerformanceError("Minimum employees is above the maximum")
    return out


def matches(criteria: dict, account: Account) -> bool:
    def within(key: str, value) -> bool:
        wanted = criteria.get(key)
        return not wanted or (value is not None and str(value).lower() in {w.lower() for w in wanted})

    if not (within("regions", account.region) and within("countries", account.country)
            and within("industries", account.industry) and within("tiers", account.tier)):
        return False
    emp = account.employee_count
    if criteria.get("min_employees") is not None and (emp is None or emp < criteria["min_employees"]):
        return False
    if criteria.get("max_employees") is not None and (emp is None or emp > criteria["max_employees"]):
        return False
    return True


def ordered(territories: list[Territory]) -> list[Territory]:
    return sorted(territories, key=lambda t: (t.priority, -len(t.criteria or {}), t.name))


def match(account: Account, territories: list[Territory]) -> Territory | None:
    return next((t for t in ordered(territories) if matches(t.criteria or {}, account)), None)


async def all_territories(db: AsyncSession) -> list[Territory]:
    return list((await db.execute(select(Territory))).scalars().all())


async def assign(db: AsyncSession, account: Account, territories: list[Territory] | None = None) -> Territory | None:
    """Stamp one account (on create / conversion / import). Leaves a manually chosen territory alone.
    Pass ``territories`` when assigning many accounts, to load them once. No autoflush: the account may be
    pending, and its insert (with any unique-domain error) belongs to the caller's own flush."""
    if account.territory_id:
        return None
    with db.no_autoflush:
        t = match(account, territories if territories is not None else await all_territories(db))
    account.territory_id = t.id if t else None
    return t


async def realign(db: AsyncSession, apply: bool) -> dict:
    """Recompute every account's territory. Reads only the matching columns (no ORM objects or relationships)
    and writes one UPDATE per destination territory."""
    territories = await all_territories(db)
    names = {t.id: t.name for t in territories}
    rows = (await db.execute(select(Account.id, Account.name, Account.region, Account.country, Account.industry, Account.tier,
                                    Account.employee_count, Account.territory_id).order_by(Account.name))).all()
    changes, counts = [], {t.id: 0 for t in territories}
    moves: dict = {}
    unassigned = 0
    for a in rows:
        t = match(a, territories)
        new = t.id if t else None
        if new:
            counts[new] += 1
        else:
            unassigned += 1
        if new != a.territory_id:
            changes.append({"account_id": a.id, "account": a.name, "from": names.get(a.territory_id), "to": names.get(new)})
            moves.setdefault(new, []).append(a.id)
    if apply:
        for territory_id, ids in moves.items():
            await db.execute(update(Account).where(Account.id.in_(ids)).values(territory_id=territory_id).execution_options(synchronize_session=False))
        await db.flush()
    return {"applied": apply, "changes": changes, "unassigned": unassigned,
            "by_territory": [{"id": tid, "name": names[tid], "accounts": n} for tid, n in counts.items()]}


async def coverage_gaps(db: AsyncSession, territory: Territory, limit: int = 50) -> list[dict]:
    covered = {str(m) for m in territory.member_ids or []} | ({str(territory.manager_id)} if territory.manager_id else set())
    rows = (await db.execute(select(Account).where(Account.territory_id == territory.id).order_by(Account.name))).scalars().unique().all()
    return [{"account_id": a.id, "account": a.name, "owner": a.owner.full_name if a.owner else None}
            for a in rows if str(a.owner_id) not in covered][:limit]


async def detach(db: AsyncSession, territory_id: uuid.UUID) -> None:
    await db.execute(update(Account).where(Account.territory_id == territory_id).values(territory_id=None))


# ---- commission ----------------------------------------------------------------------------------

def clean_tiers(raw: list | None, base_rate: float) -> list[dict]:
    tiers = []
    for t in raw or []:
        try:
            frm, rate = float(t["from_pct"]), float(t["rate"])
        except (KeyError, TypeError, ValueError):
            raise PerformanceError("Each tier needs a threshold (% of quota) and a rate")
        if frm <= 0 or not 0 <= rate <= 100:
            raise PerformanceError("Tier thresholds are above 0% of quota and rates are between 0 and 100%")
        tiers.append({"from_pct": frm, "rate": rate})
    tiers.sort(key=lambda t: t["from_pct"])
    if len({t["from_pct"] for t in tiers}) != len(tiers):
        raise PerformanceError("Two tiers start at the same attainment")
    if not 0 <= base_rate <= 100:
        raise PerformanceError("The base rate is between 0 and 100%")
    return tiers


def plan_for(plans: list[CommissionPlan], user: User) -> CommissionPlan | None:
    active = [p for p in plans if p.active]
    return (next((p for p in active if str(user.id) in {str(m) for m in p.member_ids or []}), None)
            or next((p for p in sorted(active, key=lambda p: p.name) if user.role in (p.roles or [])), None))


def commission(bookings: float, quota: float, base_rate: float, tiers: list[dict]) -> dict:
    """Bracketed commission on ``bookings``. Returns the total and one line per bracket used."""
    bookings = max(0.0, float(bookings))
    if quota <= 0 or not tiers:
        amount = round(bookings * base_rate / 100, 2)
        return {"total": amount, "lines": [{"from_pct": 0, "to_pct": None, "rate": base_rate, "bookings": round(bookings, 2), "amount": amount}]
                if bookings else []}
    edges = [0.0] + [t["from_pct"] for t in tiers]
    rates = [base_rate] + [t["rate"] for t in tiers]
    lines, total = [], 0.0
    for i, rate in enumerate(rates):
        lo = quota * edges[i] / 100
        hi = quota * edges[i + 1] / 100 if i + 1 < len(edges) else None
        slice_ = max(0.0, (min(bookings, hi) if hi is not None else bookings) - lo)
        if slice_ <= 0:
            continue
        amt = slice_ * rate / 100
        total += amt
        lines.append({"from_pct": edges[i], "to_pct": edges[i + 1] if i + 1 < len(edges) else None, "rate": rate,
                      "bookings": round(slice_, 2), "amount": round(amt, 2)})
    return {"total": round(total, 2), "lines": lines}


def statement(won: list[dict], quota: float, base_rate: float, tiers: list[dict]) -> list[dict]:
    """Per-deal commission, in close order: each deal earns what it adds to the running total."""
    out, running, paid = [], 0.0, 0.0
    for d in sorted(won, key=lambda d: (d["closed_at"] or "", d["title"])):
        running += d["amount_usd"]
        now = commission(running, quota, base_rate, tiers)["total"]
        earned = round(now - paid, 2)
        paid = now
        out.append({**d, "commission": earned, "effective_rate": round(earned / d["amount_usd"] * 100, 2) if d["amount_usd"] else 0.0,
                    "attainment_after": round(running / quota * 100, 1) if quota else None})
    return out


# ---- attainment ----------------------------------------------------------------------------------

async def quota_of(db: AsyncSession, user_id, period: str) -> float:
    q = (await db.execute(select(Quota.amount).where(Quota.user_id == user_id, Quota.period == period))).scalar()
    return float(q or 0)


def _plan_out(p: CommissionPlan | None) -> dict | None:
    if p is None:
        return None
    return {"id": p.id, "name": p.name, "description": p.description, "base_rate": float(p.base_rate), "tiers": p.tiers or [],
            "roles": p.roles or [], "member_ids": [str(m) for m in p.member_ids or []], "active": p.active}


async def scorecard(db: AsyncSession, user: User, period: str, rates: dict, plans: list[CommissionPlan], deals=None, shares=None) -> dict:
    """Quota attainment and commission. A deal with revenue splits credits each person their share; otherwise the
    owner gets all of it (services/deal_team.py)."""
    from app.services import deal_team

    if deals is None:
        deals = await forecasting.deals_in(db, [user.id], period, with_splits=True)
    if shares is None:
        shares = await deal_team.revenue_shares(db, [d.id for d in deals])
    weights = {d.id: deal_team.credit(d, user.id, shares) for d in deals}
    deals = [d for d in deals if weights[d.id] > 0]
    roll = forecasting.rollup(deals, rates, weights)
    quota = await quota_of(db, user.id, period)
    closed = roll["closed"]
    plan = plan_for(plans, user)
    won = [{"id": d.id, "title": d.title, "account": d.account.name,
            "amount_usd": round(fx.to_usd(float(d.amount or 0), d.currency, rates, on=fx.closed_on(d)) * weights[d.id], 2), "credit_pct": round(weights[d.id] * 100, 2),
            "closed_at": d.closed_at.isoformat() if d.closed_at else None} for d in deals if d.stage.is_closed_won]
    base, tiers = (float(plan.base_rate), plan.tiers or []) if plan else (0.0, [])
    comm = commission(closed, quota, base, tiers) if plan else {"total": 0.0, "lines": []}
    open_pipeline = round(roll["commit"] + roll["best_case"] + roll["pipeline"], 2)
    gap = round(max(0.0, quota - closed), 2)
    return {
        "user": {"id": user.id, "name": user.full_name, "role": user.role},
        "quota": quota, "closed": closed, "attainment_pct": round(closed / quota * 100, 1) if quota else None,
        "commit_call": roll["commit_call"], "best_case_call": roll["best_case_call"],
        "projected_pct": round(roll["commit_call"] / quota * 100, 1) if quota else None,
        "gap": gap, "open_pipeline": open_pipeline, "coverage": round(open_pipeline / gap, 2) if gap else None,
        "won_count": len(won), "plan": _plan_out(plan), "commission": comm,
        "statement": statement(won, quota, base, tiers) if plan else [{**w, "commission": 0.0, "effective_rate": 0.0,
                                                                        "attainment_after": None} for w in won],
    }


async def my_view(db: AsyncSession, user: User, period: str) -> dict:
    plans = list((await db.execute(select(CommissionPlan))).scalars().all())
    return {"period": period, **await scorecard(db, user, period, await fx.rates(db), plans)}


async def team_view(db: AsyncSession, manager: User, period: str) -> dict:
    rates = await fx.rates(db)
    plans = list((await db.execute(select(CommissionPlan))).scalars().all())
    members = await forecasting.team_members(db, manager)
    from app.services import deal_team

    all_deals = await forecasting.deals_in(db, [u.id for u in members], period, with_splits=True) if members else []
    shares = await deal_team.revenue_shares(db, [d.id for d in all_deals])
    rows = []
    for u in members:
        card = await scorecard(db, u, period, rates, plans, [d for d in all_deals if deal_team.credit(d, u.id, shares) > 0], shares)
        card.pop("statement")
        rows.append(card)
    rows.sort(key=lambda r: (r["attainment_pct"] is None, -(r["attainment_pct"] or 0), -r["closed"]))
    quota = round(sum(r["quota"] for r in rows), 2)
    closed = round(sum(r["closed"] for r in rows), 2)
    return {"period": period, "rows": rows, "team": {
        "quota": quota, "closed": closed, "attainment_pct": round(closed / quota * 100, 1) if quota else None,
        "commit_call": round(sum(r["commit_call"] for r in rows), 2), "commission": round(sum(r["commission"]["total"] for r in rows), 2),
        "at_or_above": sum(1 for r in rows if (r["attainment_pct"] or 0) >= 100), "no_quota": sum(1 for r in rows if not r["quota"])}}


# ---- demo content ---------------------------------------------------------------------------------

async def ensure_demo(db: AsyncSession) -> bool:
    """Territories by region and segment, quotas for this and next quarter, and two commission plans. Idempotent."""
    if (await db.execute(select(Territory.id).limit(1))).first():
        return False
    users = {u.email: u for u in (await db.execute(select(User))).scalars()}
    marcus, priya, diego = users.get("marcus@cirra.demo"), users.get("priya@cirra.demo"), users.get("diego@cirra.demo")
    ids = lambda *us: [str(u.id) for u in us if u]  # noqa: E731
    na = Territory(name="North America", description="All North American accounts.", manager_id=marcus.id if marcus else None,
                   member_ids=ids(priya, diego), criteria={"regions": ["NA"]}, priority=200)
    db.add(na)
    await db.flush()
    db.add_all([
        Territory(name="NA Enterprise", description="North American enterprises (1,000+ employees).", parent_id=na.id,
                  manager_id=marcus.id if marcus else None, member_ids=ids(priya), priority=100,
                  criteria={"regions": ["NA"], "min_employees": 1000}),
        Territory(name="EMEA", description="Europe, Middle East and Africa.", manager_id=marcus.id if marcus else None,
                  member_ids=ids(diego), criteria={"regions": ["EMEA"]}, priority=100),
        Territory(name="International", description="Everything not covered by a regional territory.", manager_id=marcus.id if marcus else None,
                  member_ids=ids(diego), criteria={}, priority=900),
    ])
    today = date.today()
    current = forecasting.period_of(today)
    upcoming = forecasting.period_of(forecasting.period_range(current)[1] + timedelta(days=1))
    for period in (current, upcoming):
        for u in (priya, diego):
            if u:
                db.add(Quota(user_id=u.id, period=period, amount=350000, set_by=marcus.id if marcus else None))
        if marcus:
            db.add(Quota(user_id=marcus.id, period=period, amount=900000, set_by=marcus.id))
    db.add_all([
        CommissionPlan(name="Account Executive 2026", description="6% to quota, 9% from 100% of quota, 12% from 125%.",
                       base_rate=6, tiers=[{"from_pct": 100, "rate": 9}, {"from_pct": 125, "rate": 12}], roles=["account_executive"]),
        CommissionPlan(name="Sales Manager override 2026", description="1.5% of own bookings to quota, 2.5% above.",
                       base_rate=1.5, tiers=[{"from_pct": 100, "rate": 2.5}], roles=["sales_manager"]),
    ])
    await db.flush()
    await realign(db, apply=True)
    return True
