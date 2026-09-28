"""Multi-currency conversion with dated exchange rates.

``fx_rates`` holds today's rate per currency (USD per unit); ``fx_rate_history`` holds every rate with the date
it took effect. ``rates()`` returns a ``Rates`` table: it works as the plain {currency: rate} dict every caller
already uses (today's rates), and ``to_usd(..., on=date)`` converts at the rate in effect on that date. Closed
business converts at its close date, so last year's bookings don't move when the dollar does; open pipeline
converts at today's rate.

Rates are entered by finance (Admin → Tax & currency) or loaded from a daily reference feed (``FX_FEED_URL``,
ECB euro reference rates format), off by default because the default install has no internet egress.
"""
from __future__ import annotations

import bisect
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FxRate, FxRateHistory

DEFAULT_RATES = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "CAD": 0.73, "AUD": 0.66, "INR": 0.012}
http_transport = None  # tests swap in an httpx transport


class FxError(ValueError):
    pass


class Rates(dict):
    """Today's {currency: USD rate}, plus the dated history for ``on=`` conversions."""

    def __init__(self, current: dict[str, float], history: dict[str, list[tuple[date, float]]] | None = None):
        super().__init__(current)
        self.history = history or {}

    def rate(self, currency: str | None, on: date | None = None) -> float:
        cur = (currency or "USD").upper()
        if on is not None and cur in self.history:
            rows = self.history[cur]
            i = bisect.bisect_right([d for d, _ in rows], on) - 1
            if i >= 0:
                return rows[i][1]
            return rows[0][1]  # before the first known rate: the earliest one
        return self.get(cur, 1.0)


async def rates(db: AsyncSession) -> Rates:
    current = {r.currency: float(r.rate_to_usd) for r in (await db.execute(select(FxRate))).scalars().all()} or dict(DEFAULT_RATES)
    history: dict[str, list[tuple[date, float]]] = {}
    for h in (await db.execute(select(FxRateHistory).order_by(FxRateHistory.currency, FxRateHistory.effective_date))).scalars().all():
        history.setdefault(h.currency, []).append((h.effective_date, float(h.rate_to_usd)))
    return Rates(current, history)


def to_usd(amount: float, currency: str | None, table: dict[str, float], on: date | datetime | None = None) -> float:
    if isinstance(on, datetime):
        on = on.date()
    rate = table.rate(currency, on) if isinstance(table, Rates) else table.get((currency or "USD").upper(), 1.0)
    return round(float(amount or 0) * rate, 2)


def closed_on(deal) -> date | None:
    """The date closed business converts at (None for open deals: today's rate)."""
    stage = getattr(deal, "stage", None)
    if stage is not None and (stage.is_closed_won or stage.is_closed_lost) and getattr(deal, "closed_at", None):
        return deal.closed_at.date()
    return None


async def set_rate(db: AsyncSession, currency: str, rate: float, effective: date | None = None, source: str = "manual") -> FxRateHistory:
    cur = (currency or "").strip().upper()
    if len(cur) != 3 or not cur.isalpha():
        raise FxError("Use a three-letter ISO currency code")
    if cur == "USD" and float(rate) != 1.0:
        raise FxError("USD is the base currency; its rate is always 1")
    if not 0 < float(rate) < 1_000_000:
        raise FxError("Enter a positive rate (US dollars per one unit of the currency)")
    effective = effective or date.today()
    if effective > date.today():
        raise FxError("Rates can't take effect in the future")
    value = Decimal(str(rate)).quantize(Decimal("0.000001"))
    row = await db.get(FxRateHistory, (cur, effective))
    if row is None:
        row = FxRateHistory(currency=cur, effective_date=effective, rate_to_usd=value, source=source)
        db.add(row)
    else:
        row.rate_to_usd, row.source = value, source
    latest = (await db.execute(select(FxRateHistory.effective_date).where(FxRateHistory.currency == cur)
                               .order_by(FxRateHistory.effective_date.desc()).limit(1))).scalar()
    if latest is None or effective >= latest:  # the newest rate is today's rate
        current = await db.get(FxRate, cur)
        if current is None:
            db.add(FxRate(currency=cur, rate_to_usd=value))
        else:
            current.rate_to_usd, current.updated_at = value, datetime.now(timezone.utc)
    await db.flush()
    return row


def parse_ecb(xml: bytes | str) -> tuple[date, dict[str, float]]:
    """ECB daily reference rates (units of currency per 1 EUR) -> (date, USD per unit of each currency)."""
    root = ET.fromstring(xml)
    day, per_eur = None, {}
    for el in root.iter():
        if el.tag.endswith("Cube") and el.get("time"):
            day = date.fromisoformat(el.get("time"))
        if el.tag.endswith("Cube") and el.get("currency"):
            per_eur[el.get("currency")] = float(el.get("rate"))
    if day is None or "USD" not in per_eur:
        raise FxError("The feed didn't contain a dated USD reference rate")
    usd_per_eur = per_eur["USD"]
    out = {"EUR": round(usd_per_eur, 6)}
    for cur, r in per_eur.items():
        if cur != "USD" and r > 0:
            out[cur] = round(usd_per_eur / r, 6)
    return day, out


async def import_feed(db: AsyncSession, url: str, currencies: set[str] | None = None) -> dict:
    """Load the reference feed; by default only currencies already in use are updated."""
    import httpx

    async with httpx.AsyncClient(timeout=15, transport=http_transport) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    day, table = parse_ecb(resp.content)
    wanted = currencies or {r.currency for r in (await db.execute(select(FxRate))).scalars().all()}
    updated = []
    for cur, rate in sorted(table.items()):
        if cur in wanted:
            await set_rate(db, cur, rate, min(day, date.today()), source="feed")
            updated.append(cur)
    return {"date": day.isoformat(), "updated": updated}
