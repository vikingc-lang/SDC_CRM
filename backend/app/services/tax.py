"""Tax on quotes and orders, from a pluggable engine chosen in Admin → Tax & currency.

* ``none``      – no tax (the default; totals are pre-tax as before).
* ``builtin``   – rates in ``tax_rates`` by ship-to country, optional region and optional product tax code; the
                  most specific matching rate applies to each line.
* ``india_gst`` – GST by product tax code (HSN/SAC) rate: CGST + SGST (half each) when the seller and buyer are in
                  the same state, IGST otherwise (or when the buyer's state is unknown).
* ``avalara``   – Avalara AvaTax ``transactions/create`` (uncommitted SalesOrder) with the seller and ship-to
                  addresses; per-line tax and jurisdictions come back from Avalara.

Tax-exempt deals (with a certificate on file, enforced by the order gate) are never taxed. The taxable amount of
a line is its total over the term. A failing engine never blocks the quote: tax is left at zero and the error is
shown on the quote so it can be recalculated.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Account, Deal, Quote, TaxRate
from app.services import app_settings

log = logging.getLogger(__name__)
POLICY_KEY = "tax"
ENGINES = {"none": "No tax calculation", "builtin": "Built-in rates", "india_gst": "India GST", "avalara": "Avalara AvaTax"}
DEFAULT_POLICY = {"engine": "none", "seller": {"country": "US", "region": "", "postal_code": "", "city": "", "line1": ""},
                  "gst": {"default_rate": 18.0, "rates_by_code": {}}}
_CENT = Decimal("0.01")
http_transport = None  # tests swap in an httpx transport
# name, country, region, tax code, rate: starting points for the built-in engine (migration 018 and the demo seed)
DEFAULT_RATES = [
    ("VAT", "GB", None, None, 20), ("VAT", "IE", None, None, 23), ("VAT", "DE", None, None, 19), ("VAT", "FR", None, None, 20),
    ("VAT", "NL", None, None, 21), ("VAT", "ES", None, None, 21), ("VAT", "IT", None, None, 22), ("GST", "AU", None, None, 10),
    ("GST", "NZ", None, None, 15), ("GST", "SG", None, None, 9), ("Consumption tax", "JP", None, None, 10), ("GST", "CA", None, None, 5),
    ("HST", "CA", "ON", None, 13), ("VAT", "AE", None, None, 5), ("GST", "IN", None, None, 18),
    ("Sales tax", "US", "NY", None, 4), ("Sales tax", "US", "TX", None, 6.25), ("Sales tax", "US", "WA", None, 6.5),
]

COUNTRIES = {"united states": "US", "usa": "US", "united states of america": "US", "united kingdom": "GB", "uk": "GB", "great britain": "GB",
             "england": "GB", "india": "IN", "germany": "DE", "deutschland": "DE", "france": "FR", "netherlands": "NL", "ireland": "IE",
             "spain": "ES", "italy": "IT", "australia": "AU", "new zealand": "NZ", "singapore": "SG", "japan": "JP", "canada": "CA",
             "united arab emirates": "AE", "uae": "AE", "mexico": "MX", "brazil": "BR"}
INDIA_STATES = {"maharashtra": "MH", "karnataka": "KA", "delhi": "DL", "tamil nadu": "TN", "gujarat": "GJ", "telangana": "TS",
                "uttar pradesh": "UP", "west bengal": "WB", "haryana": "HR", "kerala": "KL", "rajasthan": "RJ", "punjab": "PB",
                "andhra pradesh": "AP", "madhya pradesh": "MP", "goa": "GA", "odisha": "OD", "bihar": "BR", "assam": "AS"}
INDIA_CITIES = {"mumbai": "MH", "pune": "MH", "bengaluru": "KA", "bangalore": "KA", "new delhi": "DL", "chennai": "TN",
                "ahmedabad": "GJ", "hyderabad": "TS", "kolkata": "WB", "gurugram": "HR", "gurgaon": "HR", "noida": "UP", "kochi": "KL", "jaipur": "RJ"}


class TaxError(ValueError):
    pass


@dataclass
class Line:
    number: str
    amount: Decimal
    tax_code: str | None = None
    sku: str | None = None
    description: str | None = None
    quantity: Decimal = Decimal(1)


@dataclass
class Result:
    engine: str
    total: Decimal = Decimal(0)
    lines: dict[str, dict] = field(default_factory=dict)       # number -> {tax, rate, jurisdictions}
    summary: dict[tuple, Decimal] = field(default_factory=dict)  # (name, rate) -> amount
    note: str | None = None
    error: str | None = None

    def add(self, number: str, name: str, rate: Decimal, taxable: Decimal) -> None:
        amount = (taxable * rate / Decimal(100)).quantize(_CENT, ROUND_HALF_UP)
        entry = self.lines.setdefault(number, {"tax": Decimal(0), "rate": Decimal(0), "jurisdictions": []})
        entry["tax"] += amount
        entry["rate"] += rate
        entry["jurisdictions"].append({"name": name, "rate": float(rate), "tax": float(amount)})
        self.summary[(name, rate)] = self.summary.get((name, rate), Decimal(0)) + amount
        self.total += amount

    def out(self) -> dict:
        return {"engine": self.engine, "engine_label": ENGINES.get(self.engine, self.engine), "total": float(self.total),
                "summary": [{"name": n, "rate": float(r), "amount": float(a)} for (n, r), a in self.summary.items()],
                "lines": {k: {"tax": float(v["tax"]), "rate": float(v["rate"]), "jurisdictions": v["jurisdictions"]} for k, v in self.lines.items()},
                "note": self.note, "error": self.error}


def country_code(value: str | None) -> str | None:
    v = (value or "").strip()
    if len(v) == 2 and v.isalpha():
        return v.upper()
    return COUNTRIES.get(v.lower())


def region_code(address: dict, country: str | None) -> str | None:
    region = (address.get("region") or address.get("state") or "").strip()
    if country == "IN":
        if region:
            return INDIA_STATES.get(region.lower(), region.upper()[:2])
        return INDIA_CITIES.get((address.get("city") or "").strip().lower())
    return region.upper()[:10] or None


async def policy(db: AsyncSession) -> dict:
    return await app_settings.get(db, POLICY_KEY)


def validate_policy(p: dict) -> dict:
    engine = p.get("engine", "none")
    if engine not in ENGINES:
        raise TaxError("Choose a tax engine")
    seller = {k: str((p.get("seller") or {}).get(k) or "").strip()[:100] for k in ("country", "region", "postal_code", "city", "line1")}
    if engine != "none" and not country_code(seller["country"]):
        raise TaxError("Enter the seller's country (two-letter code, e.g. US, IN, GB)")
    if engine == "india_gst" and not seller["region"]:
        raise TaxError("India GST needs the seller's state (e.g. MH, KA, DL)")
    if engine == "avalara" and not (settings.avalara_account_id and settings.avalara_license_key):
        raise TaxError("Set AVALARA_ACCOUNT_ID and AVALARA_LICENSE_KEY on the server first")
    gst = p.get("gst") or {}
    default_rate = float(gst.get("default_rate", 18))
    codes = {str(k)[:20]: float(v) for k, v in (gst.get("rates_by_code") or {}).items()}
    if not all(0 <= r <= 100 for r in [default_rate, *codes.values()]):
        raise TaxError("GST rates are between 0 and 100%")
    return {"engine": engine, "seller": seller, "gst": {"default_rate": default_rate, "rates_by_code": codes}}


async def calculate(db: AsyncSession, lines: list[Line], ship_to: dict, *, currency: str, exempt: bool = False,
                    customer_code: str = "", doc_code: str = "", on: date | None = None, pol: dict | None = None) -> Result:
    pol = pol or await policy(db)
    engine = pol.get("engine", "none")
    res = Result(engine=engine)
    if engine == "none":
        return res
    if exempt:
        res.note = "Tax exempt (certificate on file)"
        return res
    country = country_code(ship_to.get("country"))
    if engine != "avalara" and country is None:
        res.error = "The ship-to / bill-to address has no country, so no tax could be calculated"
        return res
    try:
        if engine == "builtin":
            await _builtin(db, res, lines, country, region_code(ship_to, country))
        elif engine == "india_gst":
            _gst(res, lines, pol, country, region_code(ship_to, country))
        elif engine == "avalara":
            await _avalara(res, lines, pol, ship_to, currency, customer_code, doc_code, on or date.today(), exempt)
    except TaxError as e:
        res.error = str(e)
    except httpx.HTTPError as e:
        log.warning("Tax engine %s failed: %s", engine, e)
        res.error = f"{ENGINES[engine]} couldn't be reached ({e.__class__.__name__}); tax not calculated"
    return res


async def _builtin(db: AsyncSession, res: Result, lines: list[Line], country: str, region: str | None) -> None:
    rows = (await db.execute(select(TaxRate).where(TaxRate.country == country, TaxRate.active.is_(True)))).scalars().all()
    if not rows:
        res.note = f"No tax rates set up for {country}; no tax applied"
        return
    for line in lines:
        def score(r: TaxRate):
            if r.region and r.region != region:
                return -1
            if r.tax_code and r.tax_code != line.tax_code:
                return -1
            return (2 if r.region else 0) + (1 if r.tax_code else 0)
        best = max(rows, key=score)
        if score(best) >= 0 and line.amount:
            res.add(line.number, best.name, Decimal(str(best.rate)), line.amount)


def _gst(res: Result, lines: list[Line], pol: dict, country: str, buyer_state: str | None) -> None:
    if country != "IN":
        res.note = "Export of services from India: zero-rated (no GST)"
        return
    seller_state = region_code({"region": pol["seller"]["region"]}, "IN")
    intra = buyer_state is not None and buyer_state == seller_state
    if buyer_state is None:
        res.note = "Buyer's state unknown: IGST applied (add the state to the bill-to address)"
    codes = pol["gst"]["rates_by_code"]
    for line in lines:
        rate = Decimal(str(codes.get(line.tax_code or "", pol["gst"]["default_rate"])))
        if not line.amount or not rate:
            continue
        if intra:
            res.add(line.number, "CGST", rate / 2, line.amount)
            res.add(line.number, "SGST", rate / 2, line.amount)
        else:
            res.add(line.number, "IGST", rate, line.amount)


def _avalara_base() -> str:
    return "https://rest.avatax.com" if settings.avalara_environment == "production" else "https://sandbox-rest.avatax.com"


def _addr(a: dict) -> dict:
    return {"line1": a.get("line1") or "", "city": a.get("city") or "", "region": a.get("region") or a.get("state") or "",
            "country": country_code(a.get("country")) or (a.get("country") or ""), "postalCode": a.get("postal_code") or a.get("postalCode") or ""}


async def _avalara(res: Result, lines: list[Line], pol: dict, ship_to: dict, currency: str, customer: str, doc: str, on: date, exempt: bool) -> None:
    if not (settings.avalara_account_id and settings.avalara_license_key):
        raise TaxError("Avalara credentials aren't configured")
    body = {"type": "SalesOrder", "companyCode": settings.avalara_company_code, "date": on.isoformat(), "customerCode": customer or "CIRRA",
            "code": doc or None, "currencyCode": currency, "commit": False,
            "addresses": {"shipFrom": _addr(pol["seller"]), "shipTo": _addr(ship_to)},
            "lines": [{"number": l.number, "quantity": float(l.quantity), "amount": float(l.amount), "taxCode": l.tax_code or "SW054000",
                       "itemCode": l.sku, "description": (l.description or "")[:255]} for l in lines]}
    token = base64.b64encode(f"{settings.avalara_account_id}:{settings.avalara_license_key}".encode()).decode()
    async with httpx.AsyncClient(timeout=20, transport=http_transport) as client:
        r = await client.post(f"{_avalara_base()}/api/v2/transactions/create", json=body,
                              headers={"Authorization": f"Basic {token}", "X-Avalara-Client": "Cirra CRM; 1.0; REST; 2"})
    if r.status_code >= 400:
        msg = ""
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            pass
        raise TaxError(f"Avalara rejected the request (HTTP {r.status_code}){': ' + msg[:200] if msg else ''}")
    data = r.json()
    for line in data.get("lines") or []:
        for d in line.get("details") or []:
            rate = (Decimal(str(d.get("rate") or 0)) * 100).quantize(Decimal("0.001"))
            name = f"{d.get('jurisName') or ''} {d.get('taxName') or 'Tax'}".strip()
            amount = Decimal(str(d.get("tax") or 0)).quantize(_CENT)
            entry = res.lines.setdefault(str(line.get("lineNumber")), {"tax": Decimal(0), "rate": Decimal(0), "jurisdictions": []})
            entry["tax"] += amount
            entry["rate"] += rate
            entry["jurisdictions"].append({"name": name, "rate": float(rate), "tax": float(amount)})
            res.summary[(name, rate)] = res.summary.get((name, rate), Decimal(0)) + amount
    res.total = Decimal(str(data.get("totalTax") or 0)).quantize(_CENT)


def ship_to_of(deal: Deal, account: Account | None) -> dict:
    return (deal.ship_to or None) or (deal.bill_to or None) or ((account.billing_address if account else None) or {})


async def apply_to_quote(db: AsyncSession, quote: Quote, deal: Deal | None = None) -> None:
    """Recalculate ``quote.tax_total`` / ``tax_detail`` from its current lines."""
    deal = deal or await db.get(Deal, quote.deal_id)
    account = await db.get(Account, deal.account_id) if deal else None
    await db.refresh(quote, ["lines"])
    lines = [Line(number=str(i + 1), amount=Decimal(str(l.line_total)), tax_code=l.product.tax_code, sku=l.product.sku,
                  description=l.description, quantity=Decimal(str(l.quantity)))
             for i, l in enumerate(sorted(quote.lines, key=lambda l: l.position)) if not l.is_included]
    res = await calculate(db, lines, ship_to_of(deal, account) if deal else {}, currency=quote.currency,
                          exempt=bool(deal and deal.tax_exempt), customer_code=(account.erp_customer_id or str(account.id)) if account else "",
                          doc_code=quote.quote_number)
    quote.tax_total = res.total.quantize(_CENT)
    quote.tax_detail = res.out()
