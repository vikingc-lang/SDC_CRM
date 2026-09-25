"""Multi-currency conversion for forecasting (pillar 3/4)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FxRate

DEFAULT_RATES = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "CAD": 0.73, "AUD": 0.66, "INR": 0.012}


async def rates(db: AsyncSession) -> dict[str, float]:
    rows = (await db.execute(select(FxRate))).scalars().all()
    return {r.currency: float(r.rate_to_usd) for r in rows} or dict(DEFAULT_RATES)


def to_usd(amount: float, currency: str | None, table: dict[str, float]) -> float:
    return round(float(amount or 0) * table.get((currency or "USD").upper(), 1.0), 2)
