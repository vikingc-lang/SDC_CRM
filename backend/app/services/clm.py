"""Contract lifecycle (pillar 4) and renewals (pillar 7).

* Document assembly: NDA / SOW / Order Form templates (Jinja2, sandboxed) are
  rendered from CRM entity fields into a lightweight Markdown dialect, shown
  as HTML in the app and rendered to PDF.
* Built-in e-signature: each signer gets a single-use token link. Customers
  sign first, then the company countersigns. Every signature captures typed
  name, optional drawn signature, timestamp, IP and user agent. On completion a
  PDF with a signature certificate (document SHA-256, signer evidence) is
  generated and attached to the account timeline.
* A completed Order Form creates the contract; contracts spawn renewal
  opportunities 120 days before expiry in the Renewals pipeline.
"""
from __future__ import annotations

import hashlib
import html
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fpdf import FPDF
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    Account, Activity, Contact, Contract, Deal, DealStageHistory, Document, DocumentTemplate, Quote, SignatureRequest, Task, User,
)
from app.services import storage
from app.services.notify import emit, notify

COMPANY = {"name": "SDC Solutions", "product": "relate [R]", "signatory_title": "Authorized Signatory"}
RENEWAL_LEAD_DAYS = 120
_FONT_DIRS = ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu")

DEFAULT_TEMPLATES = {
    "nda": ("Mutual Non-Disclosure Agreement", """# Mutual Non-Disclosure Agreement

This Mutual Non-Disclosure Agreement (the "Agreement") is entered into as of **{{ today }}** between **{{ company.name }}** ("Company") and **{{ account.legal_name or account.name }}**{% if account.address %}, located at {{ account.address }}{% endif %} ("Counterparty").

## 1. Purpose
The parties wish to evaluate {{ deal.title or "a potential business relationship" }} (the "Purpose") and may disclose confidential information to each other for that Purpose only.

## 2. Confidential Information
"Confidential Information" means non-public business, technical, financial and customer information disclosed by either party, in any form, that is marked confidential or would reasonably be understood to be confidential.

## 3. Obligations
Each party will (a) use the other's Confidential Information only for the Purpose, (b) protect it with at least reasonable care, and (c) disclose it only to employees and advisors who need to know and are bound by similar obligations.

## 4. Term
This Agreement remains in effect for two (2) years from the date above. Obligations for trade secrets survive for as long as they remain trade secrets.

## 5. General
This Agreement is governed by the laws agreed between the parties in writing, contains the entire agreement on its subject, and may be signed electronically.
"""),
    "sow": ("Statement of Work", """# Statement of Work

**Customer:** {{ account.legal_name or account.name }}
**Engagement:** {{ deal.title }}
**Prepared:** {{ today }} by {{ owner.full_name or company.name }}

## 1. Background and objectives
{% if pain_points %}The Customer has identified the following priorities:
{% for p in pain_points %}- {{ p }}
{% endfor %}{% else %}Deliver and adopt {{ company.product }} to improve pipeline visibility and forecasting accuracy.
{% endif %}
## 2. Scope of services
{% if lines %}{% for l in lines %}- {{ l.name }}: {{ l.quantity }} x {{ l.unit }}
{% endfor %}{% else %}- Platform deployment in the Customer's private cloud
- Configuration of pipelines, stage gates and custom fields
- Data migration from the incumbent CRM
- Administrator and end-user enablement
{% endif %}
## 3. Milestones
- Kickoff and success plan
- Technical setup, SSO and integrations
- Data migration and validation
- User training
- Go-live and hypercare
- 90-day value review

## 4. Customer responsibilities
Provide timely access to stakeholders{% if champion %} (primary contact: {{ champion.name }}, {{ champion.title }}){% endif %}, systems and data required for the milestones above.

## 5. Acceptance
Each milestone is accepted when delivered as described, unless the Customer reports a material non-conformity within five (5) business days.
"""),
    "order_form": ("Order Form", """# Order Form

**Order Form number:** {{ quote.quote_number or "Draft" }}
**Customer:** {{ account.legal_name or account.name }}{% if account.tax_id %} (Tax ID {{ account.tax_id }}){% endif %}
**Billing address:** {{ account.address or "On file" }}
**Effective date:** {{ today }}
**Subscription term:** {{ quote.term_months or 12 }} months
**Payment terms:** {{ quote.payment_terms or account.payment_terms }}
**Currency:** {{ quote.currency or deal.currency }}

## Products and pricing
| Item | Qty | Unit price | Discount | Total |
|---|---|---|---|---|
{% for l in lines %}| {{ l.name }} ({{ l.unit }}) | {{ l.quantity }} | {{ l.net_unit_price }} | {{ l.discount_pct }}% | {{ l.line_total }} |
{% endfor %}
**Annual contract value (ACV):** {{ quote.acv }}
**Total contract value (TCV):** {{ quote.tcv }}

## Terms
This Order Form is governed by the Master Subscription Agreement between {{ company.name }} and the Customer. Fees are invoiced annually in advance{% if quote.payment_terms %} and payable {{ quote.payment_terms }}{% endif %}. This Order Form becomes binding when signed by both parties.
"""),
}

_env = SandboxedEnvironment(autoescape=False, trim_blocks=False, lstrip_blocks=False)


def _fmt_money(v, cur="USD") -> str:
    return f"{cur} {float(v or 0):,.2f}"


async def build_context(db: AsyncSession, account: Account, deal: Deal | None, quote: Quote | None) -> dict:
    owner = await db.get(User, deal.owner_id) if deal and deal.owner_id else None
    contacts = (await db.execute(select(Contact).where(Contact.account_id == account.id, Contact.status == "active"))).scalars().all()
    champion = next((c for c in contacts if c.buying_role in ("Champion", "Decision Maker")), contacts[0] if contacts else None)
    addr = account.billing_address or {}
    cur = quote.currency if quote else (deal.currency if deal else "USD")
    return {
        "today": date.today().strftime("%B %d, %Y"),
        "company": COMPANY,
        "account": {"name": account.name, "legal_name": account.legal_name, "domain": account.domain, "tax_id": account.tax_id,
                    "payment_terms": account.payment_terms,
                    "address": ", ".join(str(addr[k]) for k in ("line1", "city", "region", "postal_code", "country") if addr.get(k))},
        "deal": {"title": deal.title if deal else None, "currency": deal.currency if deal else "USD",
                 "amount": _fmt_money(deal.amount, cur) if deal else None},
        "owner": {"full_name": owner.full_name if owner else None},
        "champion": {"name": champion.full_name, "title": champion.job_title or champion.buying_role} if champion else None,
        "pain_points": (deal.ai_insights or {}).get("pain_points", [])[:5] if deal else [],
        "quote": {"quote_number": quote.quote_number, "term_months": quote.term_months, "payment_terms": quote.payment_terms,
                  "currency": quote.currency, "acv": _fmt_money(quote.acv, cur), "tcv": _fmt_money(quote.tcv, cur)} if quote else {},
        "lines": [{"name": l.product.name, "unit": l.product.unit, "quantity": f"{float(l.quantity):g}",
                   "net_unit_price": _fmt_money(l.net_unit_price, cur), "discount_pct": f"{float(l.discount_pct):g}",
                   "line_total": _fmt_money(l.line_total, cur)} for l in (quote.lines if quote else [])],
    }


def render_template(body: str, context: dict) -> str:
    return _env.from_string(body).render(**context).strip() + "\n"


# ---- Markdown-lite -> HTML (for the app and the PDF) --------------------------
def to_html(md: str) -> str:
    out, in_list, table = [], False, []

    def inline(t: str) -> str:
        t = html.escape(t)
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)

    def flush_table():
        nonlocal table
        if table:
            rows = [r for r in table if not re.fullmatch(r"\|?[\s\-|:]+\|?", r)]
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
            head, *body = cells
            out.append('<table width="100%" border="1"><thead><tr>' + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table>")
            table = []

    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("|"):
            table.append(line)
            continue
        flush_table()
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(line[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.strip():
            out.append(f"<p>{inline(line.strip())}</p>")
    flush_table()
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _font(pdf: FPDF) -> str:
    for d in _FONT_DIRS:
        regular, bold = Path(d) / "DejaVuSans.ttf", Path(d) / "DejaVuSans-Bold.ttf"
        if regular.exists():
            pdf.add_font("DejaVu", "", str(regular))
            pdf.add_font("DejaVu", "B", str(bold if bold.exists() else regular))
            return "DejaVu"
    return "Helvetica"


def _latin(text: str) -> str:
    return text.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "•": "-", "…": "..."})).encode("latin-1", "replace").decode("latin-1")


def render_pdf(document: Document, signers: list[SignatureRequest]) -> bytes:
    pdf = FPDF(format="A4")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(True, 18)
    family = _font(pdf)
    unicode_ok = family == "DejaVu"
    clean = (lambda s: s) if unicode_ok else _latin
    pdf.add_page()
    pdf.set_font(family, size=10)
    pdf.write_html(clean(to_html(document.body)), font_family=family)

    # Signature certificate
    pdf.add_page()
    pdf.set_font(family, "B", 14)
    pdf.cell(0, 10, clean("Signature certificate"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(family, size=9)
    meta = [
        ("Document", document.title), ("Document ID", str(document.id)),
        ("Content SHA-256", document.content_sha256), ("Completed", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")),
        ("Signature system", f"{COMPANY['product']} built-in e-signature"),
    ]
    for k, v in meta:
        pdf.multi_cell(0, 5.5, clean(f"{k}: {v}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    for s in signers:
        pdf.set_font(family, "B", 11)
        pdf.cell(0, 7, clean(f"{s.signer_name} ({'Customer' if s.signer_party == 'customer' else COMPANY['name']})"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(family, size=9)
        lines = [f"Email: {s.signer_email}", f"Signed as: {s.signature_text or '-'}",
                 f"Signed at: {s.signed_at.strftime('%Y-%m-%d %H:%M:%S UTC') if s.signed_at else '-'}",
                 f"IP address: {s.signed_ip or '-'}", f"User agent: {(s.user_agent or '-')[:110]}"]
        for line in lines:
            pdf.multi_cell(0, 5, clean(line), new_x="LMARGIN", new_y="NEXT")
        if s.signature_image and s.signature_image.startswith("data:image/png;base64,"):
            import base64
            import io

            try:
                pdf.image(io.BytesIO(base64.b64decode(s.signature_image.split(",", 1)[1])), w=55)
            except Exception:  # malformed drawing should never block the certificate
                pass
        pdf.ln(3)
    return bytes(pdf.output())


# ---- lifecycle --------------------------------------------------------------------
async def ensure_templates(db: AsyncSession) -> None:
    for doc_type, (name, body) in DEFAULT_TEMPLATES.items():
        exists = (await db.execute(select(DocumentTemplate.id).where(DocumentTemplate.doc_type == doc_type, DocumentTemplate.active.is_(True)))).first()
        if not exists:
            db.add(DocumentTemplate(doc_type=doc_type, name=name, body=body))
    await db.flush()


async def generate(db: AsyncSession, doc_type: str, account: Account, deal: Deal | None, quote: Quote | None, user_id) -> Document:
    await ensure_templates(db)
    tpl = (await db.execute(select(DocumentTemplate).where(DocumentTemplate.doc_type == doc_type, DocumentTemplate.active.is_(True))
                            .order_by(DocumentTemplate.version.desc()))).scalars().first()
    if doc_type == "order_form" and quote is None:
        raise ValueError("An Order Form needs an approved quote")
    if quote is not None and quote.status not in ("approved", "sent", "accepted"):
        raise ValueError("The quote must be approved before generating an Order Form")
    body = render_template(tpl.body, await build_context(db, account, deal, quote))
    doc = Document(template_id=tpl.id, doc_type=doc_type, title=f"{tpl.name}: {account.name}", account_id=account.id,
                   deal_id=deal.id if deal else None, quote_id=quote.id if quote else None, body=body,
                   content_sha256=hashlib.sha256(body.encode()).hexdigest(), status="draft", created_by=user_id)
    db.add(doc)
    await db.flush()
    await db.refresh(doc, ["signers", "account"])
    return doc


async def send_for_signature(db: AsyncSession, doc: Document, signers: list[dict]) -> Document:
    if doc.status not in ("draft",):
        raise ValueError(f"Document is already {doc.status}")
    if not any(s["party"] == "customer" for s in signers) or not any(s["party"] == "company" for s in signers):
        raise ValueError("Add at least one customer signer and one company countersigner")
    for s in sorted(signers, key=lambda s: 0 if s["party"] == "customer" else 1):
        db.add(SignatureRequest(document_id=doc.id, signer_name=s["name"], signer_email=s["email"], signer_party=s["party"],
                                sign_order=1 if s["party"] == "customer" else 2, token=secrets.token_urlsafe(32)))
    doc.status = "sent"
    if doc.quote_id:
        quote = await db.get(Quote, doc.quote_id)
        if quote and quote.status == "approved":
            quote.status = "sent"
    db.add(Activity(account_id=doc.account_id, deal_id=doc.deal_id, activity_type="document", source="system", sentiment="neutral",
                    subject=doc.title, summary=f"{doc.title} sent for e-signature to {', '.join(s['name'] for s in signers)}"))
    await db.flush()
    await db.refresh(doc, ["signers"])
    return doc


def sign_url(token: str) -> str:
    return f"{settings.public_web_url.rstrip('/')}/sign/{token}"


def is_turn(req: SignatureRequest, doc: Document) -> bool:
    return all(s.status == "signed" for s in doc.signers if s.sign_order < req.sign_order)


async def sign(db: AsyncSession, req: SignatureRequest, signature_text: str, signature_image: str | None, ip: str | None,
               user_agent: str | None, decline: bool = False) -> Document:
    doc = await db.get(Document, req.document_id)
    await db.refresh(doc, ["signers"])
    if doc.status in ("completed", "voided"):
        raise ValueError(f"Document is {doc.status}")
    if req.status != "pending":
        raise ValueError("This signature request was already used")
    if not is_turn(req, doc):
        raise ValueError("Waiting for earlier signers")
    now = datetime.now(timezone.utc)
    if decline:
        req.status, doc.status = "declined", "voided"
        db.add(Activity(account_id=doc.account_id, deal_id=doc.deal_id, activity_type="document", source="system", sentiment="negative",
                        subject=doc.title, summary=f"{req.signer_name} declined to sign {doc.title}"))
        await db.flush()
        return doc
    if not signature_text or len(signature_text.strip()) < 2:
        raise ValueError("Type your full name to sign")
    req.status, req.signature_text, req.signed_at = "signed", signature_text.strip()[:200], now
    req.signature_image = signature_image if signature_image and signature_image.startswith("data:image/png;base64,") and len(signature_image) < 400_000 else None
    req.signed_ip, req.user_agent = (ip or "")[:64], (user_agent or "")[:300]
    remaining = [s for s in doc.signers if s.status != "signed"]
    if remaining:
        doc.status = "partially_signed"
        nxt = min(remaining, key=lambda s: s.sign_order)
        users = (await db.execute(select(User.id).where(func.lower(User.email) == nxt.signer_email.lower()))).scalars().all()
        notify(db, users, "signature", f"Countersignature needed: {doc.title}", f"{req.signer_name} signed. Your signature is next.", f"/documents/{doc.id}")
    else:
        await _complete(db, doc)
    await db.flush()
    return doc


async def _complete(db: AsyncSession, doc: Document) -> None:
    now = datetime.now(timezone.utc)
    pdf = render_pdf(doc, list(doc.signers))
    att = storage.save(pdf, f"{doc.title.replace(':', ' -')} (signed).pdf", "application/pdf", account_id=doc.account_id)
    db.add(att)
    await db.flush()
    doc.pdf_attachment_id, doc.status, doc.completed_at = att.id, "completed", now
    act = Activity(account_id=doc.account_id, deal_id=doc.deal_id, activity_type="document", source="system", sentiment="positive",
                   subject=doc.title, summary=f"{doc.title} fully executed (countersigned). Signed PDF attached.")
    db.add(act)
    await db.flush()
    att.activity_id = act.id
    if doc.doc_type == "order_form" and doc.quote_id:
        quote = await db.get(Quote, doc.quote_id)
        quote.status = "accepted"
        await create_contract_from_quote(db, quote, doc)


async def next_contract_number(db: AsyncSession) -> str:
    year = date.today().year
    count = (await db.execute(select(func.count()).select_from(Contract).where(Contract.contract_number.like(f"CT-{year}-%")))).scalar_one()
    return f"CT-{year}-{count + 1:04d}"


async def create_contract_from_quote(db: AsyncSession, quote: Quote, doc: Document | None = None, start: date | None = None) -> Contract:
    deal = await db.get(Deal, quote.deal_id)
    start = start or date.today()
    end = _add_months(start, quote.term_months) - timedelta(days=1)
    contract = Contract(
        account_id=deal.account_id, deal_id=deal.id, quote_id=quote.id, document_id=doc.id if doc else None,
        contract_number=await next_contract_number(db), name=f"{deal.account.name}: {quote.name}", start_date=start, end_date=end,
        currency=quote.currency, acv=quote.acv, tcv=quote.tcv, payment_terms=quote.payment_terms, auto_renew=False, status="active",
        terms={"term_months": quote.term_months, "lines": [{"sku": l.product.sku, "name": l.product.name, "quantity": float(l.quantity),
               "net_unit_price": float(l.net_unit_price), "discount_pct": float(l.discount_pct), "billing_type": l.billing_type} for l in quote.lines]},
    )
    db.add(contract)
    deal.account.lifecycle_stage = "customer"
    await db.flush()
    if deal.contract_id:  # this deal renewed an earlier contract
        prior = await db.get(Contract, deal.contract_id)
        if prior and prior.id != contract.id:
            prior.status = "renewed"
    emit(db, "contract.created", "contract", contract.id, {
        "contract_number": contract.contract_number, "account_id": str(contract.account_id), "account": deal.account.name,
        "start_date": contract.start_date.isoformat(), "end_date": contract.end_date.isoformat(), "currency": contract.currency,
        "acv": float(contract.acv), "tcv": float(contract.tcv), "payment_terms": contract.payment_terms, "terms": contract.terms,
    })
    return contract


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    last = (date(year + (month // 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(d.day, last))


async def run_renewals(db: AsyncSession, today: date | None = None) -> dict:
    """Expire lapsed contracts and open renewal opportunities 120 days before expiry."""
    from app.services.pipeline_service import default_pipeline

    today = today or date.today()
    stats = {"expired": 0, "renewals_created": 0}
    for c in (await db.execute(select(Contract).where(Contract.status == "active", Contract.end_date < today))).scalars().unique().all():
        c.status = "expired"
        stats["expired"] += 1
    due = (
        await db.execute(select(Contract).where(Contract.status == "active", Contract.renewal_deal_id.is_(None),
                                                Contract.end_date <= today + timedelta(days=RENEWAL_LEAD_DAYS)))
    ).scalars().unique().all()
    if due:
        pipeline = await default_pipeline(db, kind="renewal")
        first = pipeline.stages[0]
        for c in due:
            account = await db.get(Account, c.account_id)
            deal = Deal(title=f"{account.name} renewal ({c.end_date.year})", account_id=c.account_id, pipeline_id=pipeline.id,
                        stage_id=first.id, owner_id=account.owner_id, amount=c.acv, currency=c.currency, target_close_date=c.end_date,
                        original_close_date=c.end_date, deal_type="renewal", source="renewal", contract_id=c.id, risk_factors={},
                        ai_insights={"renewal_of": c.contract_number, "prior_terms": c.terms, "prior_acv": float(c.acv)})
            db.add(deal)
            await db.flush()
            c.renewal_deal_id = deal.id
            db.add(DealStageHistory(deal_id=deal.id, from_stage_id=None, to_stage_id=first.id))
            db.add(Task(title=f"Prepare renewal for {c.contract_number} (expires {c.end_date:%b %d, %Y})", due_date=min(c.end_date, today + timedelta(days=14)),
                        account_id=c.account_id, deal_id=deal.id, owner_id=account.owner_id, assignee_id=account.owner_id, source="system", priority="high"))
            notify(db, [account.owner_id], "renewal", f"Renewal opened: {account.name}", f"{c.contract_number} expires {c.end_date:%b %d, %Y}", f"/deals/{deal.id}")
            emit(db, "contract.renewal_opened", "contract", c.id, {"contract_number": c.contract_number, "deal_id": str(deal.id),
                                                                     "account_id": str(c.account_id), "acv": float(c.acv), "end_date": c.end_date.isoformat()})
            stats["renewals_created"] += 1
    await db.flush()
    return stats


def document_out(d: Document, include_body: bool = True) -> dict:
    return {
        "id": d.id, "doc_type": d.doc_type, "title": d.title, "status": d.status, "account": {"id": d.account.id, "name": d.account.name},
        "deal_id": d.deal_id, "quote_id": d.quote_id, "content_sha256": d.content_sha256, "pdf_attachment_id": d.pdf_attachment_id,
        "created_at": d.created_at, "completed_at": d.completed_at,
        "body_html": to_html(d.body) if include_body else None, "body": d.body if include_body else None,
        "signers": [{"id": s.id, "name": s.signer_name, "email": s.signer_email, "party": s.signer_party, "order": s.sign_order,
                     "status": s.status, "signed_at": s.signed_at, "signed_ip": s.signed_ip, "signature_text": s.signature_text,
                     "sign_url": sign_url(s.token) if s.status == "pending" else None} for s in d.signers],
    }


def contract_out(c: Contract) -> dict:
    today = date.today()
    return {"id": c.id, "contract_number": c.contract_number, "name": c.name, "account": {"id": c.account.id, "name": c.account.name},
            "deal_id": c.deal_id, "quote_id": c.quote_id, "document_id": c.document_id, "start_date": c.start_date, "end_date": c.end_date,
            "days_to_expiry": (c.end_date - today).days, "currency": c.currency, "acv": float(c.acv), "tcv": float(c.tcv),
            "payment_terms": c.payment_terms, "auto_renew": c.auto_renew, "status": c.status, "terms": c.terms,
            "renewal_deal_id": c.renewal_deal_id}
