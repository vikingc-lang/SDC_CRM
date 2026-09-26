"""Admin-editable settings stored in ``app_settings`` with code defaults (merged on read)."""
from __future__ import annotations

import copy

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import current_user_id
from app.models import AppSetting

DEFAULTS: dict[str, dict] = {
    "lead_scoring": {
        # Ideal Customer Profile used for the explicit (fit) score
        "icp": {
            "industries": ["Manufacturing", "Industrial Distribution", "Logistics", "Retail", "Consumer Goods", "Food & Beverage",
                           "Healthcare", "Energy & Utilities"],
            "min_employees": 200, "max_employees": 20000,
            "min_revenue": 20_000_000,
            "regions": ["NA", "EMEA"], "countries": [],
            "senior_titles": ["chief", "cxo", "ceo", "cfo", "coo", "cio", "cto", "vp", "vice president", "head", "director", "svp", "evp"],
            "manager_titles": ["manager", "lead", "principal"],
        },
        "fit_weights": {"industry": 30, "size": 25, "revenue": 20, "geography": 15, "seniority": 10},
        # Implicit (intent / engagement) points per event, decayed by half-life
        "event_points": {"form_submit": 10, "content_download": 10, "pricing_page_visit": 15, "webinar_registered": 5,
                         "webinar_attended": 20, "email_open": 2, "email_click": 5, "trade_show_scan": 15, "meeting_booked": 30,
                         "web_visit": 3},
        "engagement_half_life_days": 30,
        "score_weights": {"fit": 0.5, "engagement": 0.5},
        "mql_threshold": 60,
        # Conversion requires this many confirmed criteria in the lead's framework (BANT has 4, MEDDPICC 8)
        "conversion_min_criteria": {"bant": 3, "meddpicc": 5},
    },
    "approval_chain": {"order": ["sales_manager", "deal_desk", "vp_sales", "finance", "legal"]},
    "esign": {"provider": "builtin"},
}


async def get(db: AsyncSession, key: str) -> dict:
    row = await db.get(AppSetting, key)
    value = copy.deepcopy(DEFAULTS.get(key, {}))
    if row:
        _merge(value, row.value)
    return value


async def put(db: AsyncSession, key: str, value: dict) -> dict:
    row = await db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value, updated_by=current_user_id.get()))
    else:
        row.value, row.updated_by = value, current_user_id.get()
    await db.flush()
    return await get(db, key)


def _merge(base: dict, override: dict) -> None:
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
