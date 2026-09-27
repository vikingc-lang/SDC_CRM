"""Idempotent upsert for integrations: create-or-update records by the caller's own id (external_id).

Matching, in order: external_id; then the natural key (account domain, contact email, lead email); otherwise
a new record is created and stamped with the external_id. Related records are referenced by *their* external
id or natural key (account_external_id / account_domain, owner_email), so a sync never needs Cirra's UUIDs.

Updates go through the same rules as the UI: row-level scope, custom-field validation, territory placement for
new accounts, lead capture (dedup, scoring, routing) for leads, and the integration outbox events. A deal's
stage can be set when it is created; later stage moves must use PATCH /deals/{id}/stage so stage gates apply.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import Principal
from app.models import Account, Contact, Deal, DealStageHistory, Lead, Pipeline, PipelineStage, User
from app.services import custom_fields, enrichment, performance, scoring
from app.services.notify import emit

ENTITIES = ("accounts", "contacts", "deals", "leads")
TIERS = ("SMB", "Mid-Market", "Enterprise")
ROLES = ("Champion", "Decision Maker", "Economic Buyer", "Blocker", "Evaluator", "Influencer", "Legal Counsel", "Procurement")
MAX_BATCH = 500


class SyncError(ValueError):
    pass


def _str(v, n: int) -> str | None:
    v = (str(v).strip() if v is not None else "")
    return v[:n] or None


def _num(v, what: str):
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation as e:
        raise SyncError(f"{what} must be a number") from e


async def _user(db: AsyncSession, email: str | None) -> uuid.UUID | None:
    if not email:
        return None
    uid = (await db.execute(select(User.id).where(func.lower(User.email) == email.strip().lower(), User.is_active.is_(True)))).scalar()
    if uid is None:
        raise SyncError(f"No active user {email}")
    return uid


async def _account_ref(db: AsyncSession, data: dict) -> Account:
    acc = None
    if data.get("account_external_id"):
        acc = (await db.execute(select(Account).where(Account.external_id == str(data["account_external_id"])))).scalars().first()
    elif data.get("account_domain"):
        acc = (await db.execute(select(Account).where(func.lower(Account.domain) == str(data["account_domain"]).strip().lower()))).scalars().first()
    else:
        raise SyncError("Give account_external_id or account_domain")
    if acc is None:
        raise SyncError("The referenced account doesn't exist yet (upsert the account first)")
    return acc


async def _visible(db: AsyncSession, p: Principal, account_id, resource: str) -> None:
    if p.is_own_scope(resource):
        ok = (await db.execute(p.owned_account_ids().where(Account.id == account_id))).first()
        if not ok:
            raise SyncError("Record not found")  # outside the caller's scope: same answer as missing


async def _custom(db: AsyncSession, entity: str, values, existing: dict | None) -> dict | None:
    if values is None:
        return None
    if not isinstance(values, dict):
        raise SyncError("custom_fields must be an object")
    try:
        return await custom_fields.validate(db, entity, values, existing or {})
    except custom_fields.CustomFieldError as e:
        raise SyncError(str(e)) from e


# ---- per entity ------------------------------------------------------------------------------------------

async def _upsert_account(db: AsyncSession, p: Principal, ext: str, d: dict) -> tuple[Account, bool]:
    acc = (await db.execute(select(Account).where(Account.external_id == ext))).scalars().first()
    domain = (_str(d.get("domain"), 255) or "").lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").split("/")[0] or None
    if acc is None and domain:
        acc = (await db.execute(select(Account).where(func.lower(Account.domain) == domain))).scalars().first()
        if acc is not None and acc.external_id and acc.external_id != ext:
            raise SyncError(f"The account with domain {domain} is already linked to external id {acc.external_id}")
    created = acc is None
    if created:
        if not p.can("accounts", "create"):
            raise SyncError("Your role can't create accounts")
        if not (_str(d.get("name"), 255) and domain):
            raise SyncError("A new account needs a name and a domain")
        acc = Account(name=_str(d["name"], 255), domain=domain, owner_id=await _user(db, d.get("owner_email")) or p.id, custom_metadata={})
        db.add(acc)
    else:
        if not p.can("accounts", "update"):
            raise SyncError("Your role can't update accounts")
        await _visible(db, p, acc.id, "accounts")
        if d.get("name"):
            acc.name = _str(d["name"], 255)
        if d.get("owner_email"):
            acc.owner_id = await _user(db, d["owner_email"])
    acc.external_id = ext
    if "industry" in d:
        acc.industry = _str(d["industry"], 100)
    if d.get("tier"):
        if d["tier"] not in TIERS:
            raise SyncError(f"tier must be one of {', '.join(TIERS)}")
        acc.tier = d["tier"]
    if "country" in d:
        acc.country = _str(d["country"], 64)
        acc.region = enrichment.region_for(acc.country)
    for k in ("annual_revenue", "employee_count"):
        if k in d:
            v = _num(d[k], k)
            setattr(acc, k, int(v) if (k == "employee_count" and v is not None) else v)
    if d.get("parent_external_id"):
        parent = (await db.execute(select(Account).where(Account.external_id == str(d["parent_external_id"])))).scalars().first()
        if parent is None or parent.id == acc.id:
            raise SyncError("parent_external_id doesn't match another account")
        acc.parent_id = parent.id
    cf = await _custom(db, "account", d.get("custom_fields"), acc.custom_metadata)
    if cf is not None:
        acc.custom_metadata = cf  # validate() merges onto the existing values; null clears
    if created:
        await performance.assign(db, acc)
        await db.flush()
        emit(db, "account.created", "account", acc.id, {"account_id": str(acc.id), "name": acc.name, "domain": acc.domain, "source": "upsert"})
    return acc, created


async def _upsert_contact(db: AsyncSession, p: Principal, ext: str, d: dict) -> tuple[Contact, bool]:
    c = (await db.execute(select(Contact).where(Contact.external_id == ext))).scalars().first()
    email = (_str(d.get("email"), 255) or "").lower() or None
    if c is None and email:
        c = (await db.execute(select(Contact).where(func.lower(Contact.email) == email))).scalars().first()
        if c is not None and c.external_id and c.external_id != ext:
            raise SyncError(f"The contact {email} is already linked to external id {c.external_id}")
    created = c is None
    if created:
        if not p.can("contacts", "create"):
            raise SyncError("Your role can't create contacts")
        acc = await _account_ref(db, d)
        await _visible(db, p, acc.id, "contacts")
        if not _str(d.get("first_name"), 100):
            raise SyncError("A new contact needs a first_name")
        c = Contact(account_id=acc.id, first_name=_str(d["first_name"], 100), last_name=_str(d.get("last_name"), 100) or "")
        db.add(c)
    else:
        if not p.can("contacts", "update"):
            raise SyncError("Your role can't update contacts")
        if c.status == "erased":
            raise SyncError("This contact was erased under a privacy request and can't be recreated by sync")
        await _visible(db, p, c.account_id, "contacts")
        if d.get("account_external_id") or d.get("account_domain"):
            acc = await _account_ref(db, d)
            await _visible(db, p, acc.id, "contacts")
            c.account_id = acc.id
    c.external_id = ext
    for k, n in (("first_name", 100), ("last_name", 100), ("phone", 50), ("mobile", 50), ("job_title", 150), ("department", 100), ("timezone", 64)):
        if k in d and (k not in ("first_name",) or d[k]):
            setattr(c, k, _str(d[k], n) or ("" if k == "last_name" else None))
    if email:
        c.email = email
    if d.get("buying_role"):
        if d["buying_role"] not in ROLES:
            raise SyncError(f"buying_role must be one of {', '.join(ROLES)}")
        c.buying_role = d["buying_role"]
    cf = await _custom(db, "contact", d.get("custom_fields"), c.custom_fields)
    if cf is not None:
        c.custom_fields = cf
    await db.flush()
    if created:
        emit(db, "contact.created", "contact", c.id, {"contact_id": str(c.id), "account_id": str(c.account_id), "email": c.email, "source": "upsert"})
    await scoring.rescore_account(db, c.account_id)
    return c, created


async def _upsert_deal(db: AsyncSession, p: Principal, ext: str, d: dict) -> tuple[Deal, bool]:
    deal = (await db.execute(select(Deal).where(Deal.external_id == ext))).scalars().first()
    created = deal is None
    if created:
        if not p.can("deals", "create"):
            raise SyncError("Your role can't create deals")
        acc = await _account_ref(db, d)
        await _visible(db, p, acc.id, "deals")
        if not _str(d.get("title"), 255):
            raise SyncError("A new deal needs a title")
        pipe = None
        if d.get("pipeline"):
            pipe = (await db.execute(select(Pipeline).where(func.lower(Pipeline.name) == str(d["pipeline"]).lower()))).scalars().first()
            if pipe is None:
                raise SyncError(f"Unknown pipeline '{d['pipeline']}'")
        else:
            pipe = (await db.execute(select(Pipeline).where(Pipeline.is_default.is_(True)))).scalars().first()
        stages = sorted((await db.execute(select(PipelineStage).where(PipelineStage.pipeline_id == pipe.id))).scalars().all(), key=lambda s: s.stage_order)
        stage = stages[0]
        if d.get("stage"):
            stage = next((s for s in stages if s.name.lower() == str(d["stage"]).lower()), None)
            if stage is None or stage.is_closed_won or stage.is_closed_lost:
                raise SyncError("stage must be an open stage of the pipeline (close deals through the stage endpoint)")
        deal = Deal(title=_str(d["title"], 255), account_id=acc.id, pipeline_id=pipe.id, stage_id=stage.id, owner_id=await _user(db, d.get("owner_email")) or p.id,
                    amount=0, currency="USD", source="api", deal_type="new_business", risk_factors={}, ai_insights={})
        db.add(deal)
    else:
        if not p.can("deals", "update"):
            raise SyncError("Your role can't update deals")
        await _visible(db, p, deal.account_id, "deals")
        if d.get("stage"):
            current = await db.get(PipelineStage, deal.stage_id)
            if current.name.lower() != str(d["stage"]).lower():
                raise SyncError("Stage changes go through PATCH /deals/{id}/stage so stage gates apply")
        if d.get("title"):
            deal.title = _str(d["title"], 255)
        if d.get("owner_email"):
            deal.owner_id = await _user(db, d["owner_email"])
    deal.external_id = ext
    if "amount" in d:
        deal.amount = _num(d["amount"], "amount") or 0
    if d.get("currency"):
        deal.currency = str(d["currency"]).upper()[:3]
    if d.get("target_close_date"):
        try:
            close = date.fromisoformat(str(d["target_close_date"])[:10])
        except ValueError as e:
            raise SyncError("target_close_date must be YYYY-MM-DD") from e
        if deal.target_close_date and close > deal.target_close_date:
            deal.close_date_pushes = (deal.close_date_pushes or 0) + 1
        deal.target_close_date = close
        deal.original_close_date = deal.original_close_date or close
    cf = await _custom(db, "deal", d.get("custom_fields"), deal.custom_fields)
    if cf is not None:
        deal.custom_fields = cf
    await db.flush()
    if created:
        db.add(DealStageHistory(deal_id=deal.id, from_stage_id=None, to_stage_id=deal.stage_id, changed_by=p.id))
        emit(db, "deal.created", "deal", deal.id, {"deal_id": str(deal.id), "account_id": str(deal.account_id), "title": deal.title,
                                                   "amount": float(deal.amount or 0), "currency": deal.currency, "source": "upsert"})
    return deal, created


async def _upsert_lead(db: AsyncSession, p: Principal, ext: str, d: dict) -> tuple[Lead, bool]:
    """Existing lead (by external_id, else by open lead with the same email): the fields sent overwrite the stored
    ones and the lead is rescored. New lead: full capture (dedup, enrichment, consent, scoring, routing)."""
    from app.services import leads as lead_svc

    fields = lead_svc.normalize({k: v for k, v in d.items() if k not in ("custom_fields", "external_id")})
    lead = (await db.execute(select(Lead).where(Lead.external_id == ext))).scalars().first()
    if lead is None and fields.get("email"):
        lead = (await db.execute(select(Lead).where(func.lower(Lead.email) == fields["email"], Lead.status.in_(lead_svc.OPEN_STATUSES))
                                 .order_by(Lead.created_at))).scalars().first()
        if lead is not None and lead.external_id and lead.external_id != ext:
            raise SyncError(f"A lead with this email is already linked to external id {lead.external_id}")
    if lead is not None:
        if not p.can("leads", "update"):
            raise SyncError("Your role can't update leads")
        if p.is_own_scope("leads") and lead.owner_id not in (None, p.id):
            raise SyncError("Record not found")  # another rep's lead: same answer as missing
        if lead.status == "converted":
            raise SyncError("This lead was converted; update the account, contact or deal instead")
        sent = {k for k in d}  # only overwrite what the caller actually sent
        aliases = {f: names for f, names in lead_svc.ALIASES.items()}
        for f, v in fields.items():
            if v is not None and (f in sent or any(n in sent for n in aliases.get(f, ()))):
                setattr(lead, f, v)
        if "country" in fields and fields.get("country"):
            lead.region = enrichment.region_for(lead.country)
        lead.external_id = ext
        created = False
        await lead_svc.rescore(db, lead)
    else:
        if not p.can("leads", "create"):
            raise SyncError("Your role can't create leads")
        try:
            lead, _ = await lead_svc.capture(db, {**{k: v for k, v in d.items() if k != "custom_fields"}, "external_id": ext},
                                             source=d.get("source") if d.get("source") in lead_svc.SOURCES else "api", campaign=d.get("campaign"))
        except lead_svc.LeadError as e:
            raise SyncError(str(e)) from e
        created = True
    cf = await _custom(db, "lead", d.get("custom_fields"), lead.custom_fields)
    if cf is not None:
        lead.custom_fields = cf
    await db.flush()
    return lead, created


HANDLERS = {"accounts": _upsert_account, "contacts": _upsert_contact, "deals": _upsert_deal, "leads": _upsert_lead}


async def upsert(db: AsyncSession, p: Principal, entity: str, external_id: str, data: dict) -> dict:
    ext = _str(external_id, 200)
    if not ext:
        raise SyncError("external_id is required")
    rec, created = await HANDLERS[entity](db, p, ext, data or {})
    return {"external_id": ext, "id": rec.id, "status": "created" if created else "updated"}


async def upsert_many(db: AsyncSession, p: Principal, entity: str, records: list[dict]) -> dict:
    """Each row in its own savepoint: a bad row is reported and skipped; the others still commit."""
    if len(records) > MAX_BATCH:
        raise SyncError(f"At most {MAX_BATCH} records per call")
    results = []
    for row in records:
        ext = (row or {}).get("external_id")
        try:
            async with db.begin_nested():
                results.append(await upsert(db, p, entity, ext, {k: v for k, v in row.items() if k != "external_id"}))
        except SyncError as e:
            results.append({"external_id": ext, "status": "error", "error": str(e)})
        except IntegrityError as e:  # e.g. an email or domain already used by another record
            results.append({"external_id": ext, "status": "error", "error": f"Conflicts with an existing record: {str(e.orig).splitlines()[0][:160]}"})
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("created", "updated", "error")}
    return {"results": results, **counts}
