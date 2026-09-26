"""Lead and account enrichment connectors (``ENRICHMENT_PROVIDER``).

* ``internal`` (default) - no network: fills firmographics from an existing
  account on the same registrable domain, or from other leads at that company,
  and flags free-mail addresses. Keeps the zero-egress posture.
* ``rest`` - a data provider or internal MDM behind ``ENRICHMENT_REST_URL``
  (``GET ?domain=&email=`` -> canonical record below), e.g. a proxy in front of
  a commercial firmographics / technographics service.
* ``disabled`` - skip enrichment.

Canonical record: {company_name, industry, employee_count, annual_revenue, country,
technographics: [..], email_verified: bool, job_title?}. Only empty lead fields are filled.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Account, Lead
from app.services.dedup import registrable_domain

FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "icloud.com", "me.com",
             "aol.com", "proton.me", "protonmail.com", "gmx.com", "gmx.de", "web.de", "mail.com", "yandex.com", "qq.com"}
FIELDS = ("company_name", "industry", "employee_count", "annual_revenue", "country", "job_title")

REGION_BY_COUNTRY = {
    **{c: "NA" for c in ("us", "usa", "united states", "canada", "ca", "mexico", "mx")},
    **{c: "EMEA" for c in ("uk", "gb", "united kingdom", "england", "ireland", "ie", "germany", "de", "france", "fr", "netherlands", "nl",
                           "belgium", "be", "spain", "es", "italy", "it", "sweden", "se", "norway", "no", "denmark", "dk", "finland", "fi",
                           "switzerland", "ch", "austria", "at", "poland", "pl", "portugal", "pt", "uae", "united arab emirates",
                           "saudi arabia", "south africa", "za", "israel")},
    **{c: "APAC" for c in ("india", "in", "australia", "au", "new zealand", "nz", "japan", "jp", "singapore", "sg", "china", "cn",
                           "hong kong", "hk", "south korea", "kr", "indonesia", "malaysia", "philippines", "vietnam", "thailand")},
    **{c: "LATAM" for c in ("brazil", "br", "argentina", "ar", "chile", "cl", "colombia", "co", "peru", "pe")},
}


def region_for(country: str | None) -> str | None:
    return REGION_BY_COUNTRY.get((country or "").strip().lower())


def email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].strip().lower()


def is_free_mail(domain: str | None) -> bool:
    return (domain or "").lower() in FREE_MAIL


async def _internal(db: AsyncSession, lead: Lead) -> dict:
    reg = registrable_domain(lead.domain)
    if not reg:
        return {}
    for acc in (await db.execute(select(Account).where(Account.domain.ilike(f"%{reg}")).limit(10))).scalars().unique().all():
        if registrable_domain(acc.domain) == reg:
            return {"company_name": acc.name, "industry": acc.industry, "employee_count": acc.employee_count,
                    "annual_revenue": float(acc.annual_revenue) if acc.annual_revenue is not None else None,
                    "country": acc.country, "matched": f"account:{acc.id}"}
    peer = (await db.execute(select(Lead).where(Lead.domain == lead.domain, Lead.id != lead.id, Lead.industry.is_not(None)).limit(1))).scalars().first()
    if peer:
        return {"company_name": peer.company_name, "industry": peer.industry, "employee_count": peer.employee_count,
                "annual_revenue": float(peer.annual_revenue) if peer.annual_revenue is not None else None, "country": peer.country,
                "matched": f"lead:{peer.id}"}
    return {}


async def _rest(lead: Lead) -> dict:
    if not settings.enrichment_rest_url:
        raise RuntimeError("ENRICHMENT_REST_URL is not configured")
    headers = {"Authorization": f"Bearer {settings.enrichment_rest_token}"} if settings.enrichment_rest_token else {}
    async with httpx.AsyncClient(timeout=15, headers=headers) as c:
        r = await c.get(settings.enrichment_rest_url, params={"domain": lead.domain or "", "email": lead.email or ""})
        r.raise_for_status()
        return r.json() or {}


async def enrich(db: AsyncSession, lead: Lead) -> dict:
    """Fill empty firmographic fields; returns the enrichment record stored on the lead."""
    provider = settings.enrichment_provider
    record: dict = {"provider": provider, "free_mail": is_free_mail(email_domain(lead.email))}
    if provider == "disabled":
        return record
    try:
        data = await (_rest(lead) if provider == "rest" else _internal(db, lead))
    except Exception as exc:  # enrichment must never block capture
        record["error"] = str(exc)[:200]
        data = {}
    filled = []
    for f in FIELDS:
        if data.get(f) not in (None, "") and getattr(lead, f) in (None, ""):
            setattr(lead, f, data[f])
            filled.append(f)
    if not lead.region and lead.country:
        lead.region = region_for(lead.country)
        if lead.region:
            filled.append("region")
    record.update({"fields_filled": filled, "technographics": data.get("technographics") or [],
                   "email_verified": data.get("email_verified"), "matched": data.get("matched")})
    lead.enrichment, lead.enriched_at = record, datetime.now(timezone.utc)
    return record
