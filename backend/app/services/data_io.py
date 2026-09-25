"""Import / export engine (pillar 10).

* CSV or JSON input; headers are auto-mapped to fields (exact name, synonym,
  then fuzzy match >= 85) and can be overridden.
* Every row is validated (required, type, enum, e-mail, references) before
  anything is written. Commit runs in one transaction: any failure rolls back
  the whole batch and returns the row-level errors.
* Accounts upsert by domain, contacts by e-mail, products by SKU; deals insert.
* Exports honour row-level ownership and are recorded in the audit trail.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from email_validator import EmailNotValidError, validate_email
from rapidfuzz import fuzz, process
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Contact, Deal, DealStageHistory, Pipeline, PriceBookEntry, Product, User
from app.models import ACCOUNT_TIERS, BUYING_ROLES

F = dict  # field spec shorthand

ENTITY_FIELDS: dict[str, dict[str, dict]] = {
    "accounts": {
        "name": F(type="str", required=True, synonyms=["company", "company name", "account", "account name", "organization"]),
        "domain": F(type="domain", required=True, synonyms=["website", "url", "web", "company domain"]),
        "industry": F(type="str", synonyms=["sector", "vertical"]),
        "industry_code": F(type="str", synonyms=["naics", "sic", "naics code"]),
        "tier": F(type="enum", options=list(ACCOUNT_TIERS), synonyms=["segment", "size"]),
        "annual_revenue": F(type="number", synonyms=["revenue", "arr", "turnover"]),
        "employee_count": F(type="int", synonyms=["employees", "headcount", "staff"]),
        "legal_name": F(type="str", synonyms=["legal entity", "registered name"]),
        "tax_id": F(type="str", synonyms=["vat", "vat id", "ein", "tax number"]),
        "credit_limit": F(type="number", synonyms=["credit"]),
        "payment_terms": F(type="str", synonyms=["terms"]),
        "lifecycle_stage": F(type="enum", options=["prospect", "customer", "churned", "partner"], synonyms=["status", "lifecycle"]),
        "parent_domain": F(type="ref_account", synonyms=["parent", "parent company", "parent website"]),
        "owner_email": F(type="ref_user", synonyms=["owner", "account owner", "rep"]),
    },
    "contacts": {
        "first_name": F(type="str", required=True, synonyms=["first", "given name", "firstname"]),
        "last_name": F(type="str", synonyms=["last", "surname", "family name", "lastname"]),
        "email": F(type="email", synonyms=["email address", "e-mail", "work email"]),
        "phone": F(type="str", synonyms=["direct phone", "work phone", "telephone", "office phone"]),
        "mobile": F(type="str", synonyms=["cell", "mobile phone", "cell phone"]),
        "job_title": F(type="str", synonyms=["title", "position", "role title"]),
        "department": F(type="str", synonyms=["dept", "function"]),
        "timezone": F(type="str", synonyms=["time zone", "tz"]),
        "linkedin_url": F(type="str", synonyms=["linkedin", "linkedin profile"]),
        "buying_role": F(type="enum", options=list(BUYING_ROLES), synonyms=["role", "persona", "buying center role"]),
        "account_domain": F(type="ref_account", required=True, synonyms=["company domain", "account", "company", "website", "domain"]),
    },
    "deals": {
        "title": F(type="str", required=True, synonyms=["deal", "deal name", "opportunity", "opportunity name", "name"]),
        "account_domain": F(type="ref_account", required=True, synonyms=["account", "company", "domain", "company domain"]),
        "amount": F(type="number", synonyms=["value", "deal value", "amount usd"]),
        "currency": F(type="str", synonyms=["ccy"]),
        "pipeline": F(type="str", synonyms=["pipeline name"]),
        "stage": F(type="str", synonyms=["deal stage", "stage name"]),
        "target_close_date": F(type="date", synonyms=["close date", "expected close", "close"]),
        "owner_email": F(type="ref_user", synonyms=["owner", "rep", "deal owner"]),
    },
    "products": {
        "sku": F(type="str", required=True, synonyms=["product code", "code", "item"]),
        "name": F(type="str", required=True, synonyms=["product", "product name"]),
        "family": F(type="str", synonyms=["category", "product family"]),
        "billing_type": F(type="enum", options=["recurring", "one_time"], synonyms=["billing"]),
        "unit": F(type="str", synonyms=["uom", "unit of measure"]),
        "price_usd": F(type="number", synonyms=["price", "list price", "unit price"]),
    },
}


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", h.lower()).strip()


def auto_map(entity: str, headers: list[str]) -> dict[str, str | None]:
    fields = ENTITY_FIELDS[entity]
    vocab = {}
    for name, spec in fields.items():
        vocab[_norm(name)] = name
        for syn in spec.get("synonyms", []):
            vocab.setdefault(_norm(syn), name)
    mapping, used = {}, set()
    for h in headers:
        n = _norm(h)
        target = vocab.get(n)
        if target is None:
            match = process.extractOne(n, list(vocab), scorer=fuzz.ratio)
            target = vocab[match[0]] if match and match[1] >= 85 else None
        mapping[h] = target if target not in used else None
        if target:
            used.add(target)
    return mapping


def parse_file(content: bytes, filename: str) -> tuple[list[str], list[dict]]:
    text = content.decode("utf-8-sig", errors="replace")
    if filename.lower().endswith(".json") or text.lstrip().startswith(("[", "{")):
        data = json.loads(text)
        rows = data if isinstance(data, list) else data.get("rows") or data.get(next(iter(data)), [])
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError("JSON must be an array of objects")
        headers = list(dict.fromkeys(k for r in rows for k in r))
        return headers, [{k: ("" if r.get(k) is None else r.get(k)) for k in headers} for r in rows]
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    return list(reader.fieldnames), [dict(r) for r in reader]


class _Refs:
    def __init__(self, accounts: dict, users: dict, pending_accounts: set):
        self.accounts, self.users, self.pending = accounts, users, pending_accounts


def _coerce(field: str, spec: dict, raw, refs: _Refs):
    value = raw.strip() if isinstance(raw, str) else raw
    if value in (None, ""):
        if spec.get("required"):
            raise ValueError(f"{field} is required")
        return None
    t = spec["type"]
    if t == "str":
        return str(value)[:255]
    if t == "domain":
        d = str(value).lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").split("/")[0]
        if "." not in d:
            raise ValueError(f"{field} '{value}' is not a domain")
        return d
    if t in ("number", "int"):
        try:
            num = Decimal(str(value).replace(",", "").replace("$", ""))
        except InvalidOperation:
            raise ValueError(f"{field} '{value}' is not a number")
        return int(num) if t == "int" else num
    if t == "date":
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            raise ValueError(f"{field} '{value}' is not a YYYY-MM-DD date")
    if t == "enum":
        match = next((o for o in spec["options"] if o.lower() == str(value).lower().replace(" ", "_") or o.lower() == str(value).lower()), None)
        if match is None:
            raise ValueError(f"{field} must be one of {', '.join(spec['options'])}")
        return match
    if t == "email":
        try:
            return validate_email(str(value), check_deliverability=False).normalized.lower()
        except EmailNotValidError as exc:
            raise ValueError(f"{field}: {exc}")
    if t == "ref_account":
        d = str(value).lower().removeprefix("www.")
        if d not in refs.accounts and d not in refs.pending:
            raise ValueError(f"{field}: no account with domain '{value}'")
        return d
    if t == "ref_user":
        u = refs.users.get(str(value).lower())
        if u is None:
            raise ValueError(f"{field}: no user '{value}'")
        return u
    return value


async def _refs(db: AsyncSession) -> _Refs:
    accounts = {a.domain.lower(): a for a in (await db.execute(select(Account))).scalars().unique().all()}
    users = {u.email.lower(): u for u in (await db.execute(select(User))).scalars().all()}
    return _Refs(accounts, users, set())


async def validate_rows(db: AsyncSession, entity: str, rows: list[dict], mapping: dict[str, str | None]) -> tuple[list[dict], list[dict]]:
    fields = ENTITY_FIELDS[entity]
    refs = await _refs(db)
    if entity == "accounts":  # accounts in the same file may be referenced as parents
        refs.pending = {str(r.get(h, "")).lower().removeprefix("www.") for r in rows for h, f in mapping.items() if f == "domain"}
    mapped_fields = {f for f in mapping.values() if f}
    missing = [f for f, s in fields.items() if s.get("required") and f not in mapped_fields]
    if missing:
        return [], [{"row": None, "errors": [f"Required column not mapped: {', '.join(missing)}"]}]
    clean, errors = [], []
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        out, errs = {}, []
        for header, field in mapping.items():
            if not field:
                continue
            try:
                out[field] = _coerce(field, fields[field], row.get(header), refs)
            except ValueError as exc:
                errs.append(str(exc))
        for f, spec in fields.items():
            if spec.get("required") and out.get(f) in (None, "") and not any(e.startswith(f) for e in errs):
                errs.append(f"{f} is required")
        if errs:
            errors.append({"row": i, "errors": errs})
        else:
            clean.append(out)
    return clean, errors


async def preview(db: AsyncSession, entity: str, content: bytes, filename: str, mapping: dict | None = None) -> dict:
    headers, rows = parse_file(content, filename)
    mapping = mapping or auto_map(entity, headers)
    clean, errors = await validate_rows(db, entity, rows, mapping)
    return {"entity": entity, "headers": headers, "mapping": mapping, "fields": {k: {"required": bool(v.get("required")), "type": v["type"],
            "options": v.get("options")} for k, v in ENTITY_FIELDS[entity].items()}, "rows_total": len(rows), "rows_valid": len(clean),
            "errors": errors[:100], "sample": rows[:5]}


async def commit(db: AsyncSession, entity: str, content: bytes, filename: str, mapping: dict | None, user: User) -> dict:
    """All-or-nothing import. Raises ValueError(errors) and leaves the database untouched on any error."""
    headers, rows = parse_file(content, filename)
    mapping = mapping or auto_map(entity, headers)
    clean, errors = await validate_rows(db, entity, rows, mapping)
    if errors:
        raise ImportValidationError(errors)
    refs = await _refs(db)
    created = updated = 0
    try:
        async with db.begin_nested():
            if entity == "accounts":
                for r in clean:
                    parent, owner = r.pop("parent_domain", None), r.pop("owner_email", None)
                    acc = refs.accounts.get(r["domain"])
                    if acc is None:
                        acc = Account(custom_metadata={}, owner_id=(owner or user).id, **{k: v for k, v in r.items() if v is not None})
                        db.add(acc)
                        refs.accounts[r["domain"]] = acc
                        created += 1
                    else:
                        for k, v in r.items():
                            if v is not None:
                                setattr(acc, k, v)
                        if owner:
                            acc.owner_id = owner.id
                        updated += 1
                    r["_parent"] = parent
                await db.flush()
                for r in clean:
                    if r.get("_parent"):
                        refs.accounts[r["domain"]].parent_id = refs.accounts[r["_parent"]].id
            elif entity == "contacts":
                for r in clean:
                    acc = refs.accounts[r.pop("account_domain")]
                    existing = (await db.execute(select(Contact).where(func.lower(Contact.email) == r["email"]))).scalars().first() if r.get("email") else None
                    if existing:
                        for k, v in r.items():
                            if v is not None:
                                setattr(existing, k, v)
                        updated += 1
                    else:
                        db.add(Contact(account_id=acc.id, last_name=r.pop("last_name", None) or "", **{k: v for k, v in r.items() if v is not None}))
                        created += 1
            elif entity == "deals":
                pipelines = {p.name.lower(): p for p in (await db.execute(select(Pipeline))).scalars().all()}
                default = next((p for p in pipelines.values() if p.is_default), next(iter(pipelines.values())))
                for r in clean:
                    p = pipelines.get((r.get("pipeline") or "").lower(), default)
                    stage = next((s for s in p.stages if r.get("stage") and s.name.lower() == r["stage"].lower()), p.stages[0])
                    acc = refs.accounts[r["account_domain"]]
                    owner = r.get("owner_email") or user
                    deal = Deal(title=r["title"], account_id=acc.id, pipeline_id=p.id, stage_id=stage.id, amount=r.get("amount") or 0,
                                currency=(r.get("currency") or "USD").upper()[:3], target_close_date=r.get("target_close_date"),
                                original_close_date=r.get("target_close_date"), owner_id=owner.id, source="import", risk_factors={}, ai_insights={})
                    db.add(deal)
                    await db.flush()
                    db.add(DealStageHistory(deal_id=deal.id, to_stage_id=stage.id, changed_by=user.id))
                    created += 1
            elif entity == "products":
                for r in clean:
                    price = r.pop("price_usd", None)
                    prod = (await db.execute(select(Product).where(Product.sku == r["sku"]))).scalars().first()
                    if prod is None:
                        prod = Product(**{k: v for k, v in r.items() if v is not None})
                        db.add(prod)
                        await db.flush()
                        created += 1
                    else:
                        for k, v in r.items():
                            if v is not None:
                                setattr(prod, k, v)
                        updated += 1
                    if price is not None:
                        entry = (await db.execute(select(PriceBookEntry).where(PriceBookEntry.product_id == prod.id, PriceBookEntry.currency == "USD"))).scalars().first()
                        tiers = [{"min_qty": 1, "unit_price": float(price)}]
                        if entry:
                            entry.tiers = tiers
                        else:
                            db.add(PriceBookEntry(product_id=prod.id, currency="USD", tiers=tiers))
            await db.flush()
    except Exception as exc:
        raise ImportValidationError([{"row": None, "errors": [f"Rolled back: {exc.__class__.__name__}: {str(exc)[:300]}"]}])
    return {"entity": entity, "created": created, "updated": updated, "rows": len(clean)}


class ImportValidationError(ValueError):
    def __init__(self, errors: list[dict]):
        super().__init__(f"{len(errors)} row(s) failed validation")
        self.errors = errors


EXPORT_COLUMNS = {
    "accounts": ["id", "name", "domain", "industry", "industry_code", "tier", "lifecycle_stage", "annual_revenue", "employee_count", "health_score",
                 "legal_name", "tax_id", "credit_limit", "credit_hold", "payment_terms", "erp_customer_id", "parent_id", "owner_id", "created_at"],
    "contacts": ["id", "account_id", "first_name", "last_name", "email", "phone", "mobile", "job_title", "department", "timezone", "buying_role",
                 "status", "consent_email", "opt_out_email", "do_not_sell", "relationship_strength", "created_at"],
    "deals": ["id", "title", "account_id", "pipeline_id", "stage_id", "amount", "currency", "target_close_date", "risk_score", "deal_type",
              "loss_reason", "owner_id", "created_at"],
    "products": ["id", "sku", "name", "family", "billing_type", "unit", "active"],
}
EXPORT_MODELS = {"accounts": Account, "contacts": Contact, "deals": Deal, "products": Product}


def serialize(rows: list, entity: str, fmt: str) -> tuple[bytes, str]:
    cols = EXPORT_COLUMNS[entity]
    records = [{c: getattr(r, c) for c in cols} for r in rows]
    if entity == "contacts":  # never export erased subjects' residual data
        records = [r for r in records if r["status"] != "erased"]
    if fmt == "json":
        return json.dumps(records, default=str, indent=2).encode(), "application/json"
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in records:
        w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    return buf.getvalue().encode(), "text/csv"

