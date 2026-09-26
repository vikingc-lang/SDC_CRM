"""Negotiation and execution (lead-to-order, step 6): redlines, comments, credit checks, external e-signature.

Redlining  every edit creates a numbered ``document_versions`` row (internal or customer); the
           document body always holds the latest version. Editing a document that was already
           sent voids the outstanding signatures, so parties always sign the exact text they
           reviewed (the SHA-256 changes with every version).
Comments   clause-level comments from both parties; customers comment or propose redlines from
           their signing link. Open comments must be resolved before sending for signature.
Credit     before an Order Form goes out for signature, accounts on credit hold or with a high
           credit-risk score need a Finance approval on the linked quote.
E-sign     ``builtin`` (default) or an external provider (DocuSign / Adobe Sign) via REST; the
           provider's completion webhook marks signers and completes the document through the same
           path as the built-in flow (signed PDF on the timeline, contract from the Order Form).
"""
from __future__ import annotations

import base64
import difflib
import hashlib
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Account, Activity, ApprovalRequest, Document, DocumentComment, DocumentVersion, Quote, SignatureRequest, User
from app.services import erp
from app.services.notify import notify


class CreditReviewRequired(ValueError):
    pass


# ---- versions & redlines --------------------------------------------------------------------------
async def new_version(db: AsyncSession, doc: Document, body: str, note: str | None, source: str, user: User | None = None,
                      author_name: str | None = None) -> DocumentVersion:
    if doc.status in ("completed", "voided"):
        raise ValueError(f"Document is {doc.status}; start a new document to change it")
    body = (body or "").strip()
    if len(body) < 20:
        raise ValueError("The revised document is empty")
    if hashlib.sha256(body.encode()).hexdigest() == doc.content_sha256:
        raise ValueError("No changes to save")
    await db.refresh(doc, ["signers"])
    voided = 0
    for s in list(doc.signers):  # outstanding signatures no longer match the text
        await db.delete(s)
        voided += 1
    doc.current_version += 1
    doc.body, doc.content_sha256 = body, hashlib.sha256(body.encode()).hexdigest()
    doc.status = "in_negotiation"
    doc.envelope_id = None
    version = DocumentVersion(document_id=doc.id, version=doc.current_version, body=body, content_sha256=doc.content_sha256, note=note,
                              source=source, created_by=user.id if user else None, author_name=author_name or (user.full_name if user else None))
    db.add(version)
    who = author_name or (user.full_name if user else "Customer")
    db.add(Activity(account_id=doc.account_id, deal_id=doc.deal_id, activity_type="document", source="system", sentiment="neutral",
                    subject=doc.title, summary=f"{doc.title}: version {doc.current_version} ({'customer redline' if source == 'customer' else 'internal revision'}) "
                                           f"by {who}{f': {note}' if note else ''}{f'. {voided} pending signature(s) voided' if voided else ''}"))
    await db.flush()
    return version


def diff(old: str, new: str) -> list[dict]:
    """Line-level redline: [{op: equal|insert|delete, text}]."""
    out: list[dict] = []
    a, b = old.splitlines(), new.splitlines()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            out += [{"op": "equal", "text": t} for t in a[i1:i2]]
        if tag in ("delete", "replace"):
            out += [{"op": "delete", "text": t} for t in a[i1:i2]]
        if tag in ("insert", "replace"):
            out += [{"op": "insert", "text": t} for t in b[j1:j2]]
    return out


async def versions(db: AsyncSession, doc: Document) -> list[DocumentVersion]:
    return (await db.execute(select(DocumentVersion).where(DocumentVersion.document_id == doc.id).order_by(DocumentVersion.version))).scalars().all()


async def add_comment(db: AsyncSession, doc: Document, body: str, party: str, author_name: str, author_email: str | None = None,
                      clause: str | None = None, user: User | None = None) -> DocumentComment:
    if doc.status in ("completed", "voided"):
        raise ValueError(f"Document is {doc.status}")
    if len((body or "").strip()) < 3:
        raise ValueError("Write a comment")
    c = DocumentComment(document_id=doc.id, version=doc.current_version, clause=(clause or "")[:200] or None, body=body.strip()[:4000],
                        party=party, author_name=author_name[:200], author_email=author_email, user_id=user.id if user else None)
    db.add(c)
    if party == "customer":
        if doc.status in ("sent", "partially_signed"):
            doc.status = "in_negotiation"
        owner = None
        if doc.deal_id:
            from app.models import Deal
            deal = await db.get(Deal, doc.deal_id)
            owner = deal.owner_id if deal else None
        notify(db, [owner, doc.created_by], "document", f"Customer comment on {doc.title}", f"{author_name}: {body[:200]}", f"/documents/{doc.id}")
        db.add(Activity(account_id=doc.account_id, deal_id=doc.deal_id, activity_type="document", source="system", sentiment="neutral",
                        subject=doc.title, summary=f"{author_name} requested changes to {doc.title}{f' ({clause})' if clause else ''}: {body[:300]}"))
    await db.flush()
    return c


async def open_comment_count(db: AsyncSession, doc: Document) -> int:
    return (await db.execute(select(func.count()).select_from(DocumentComment)
                             .where(DocumentComment.document_id == doc.id, DocumentComment.resolved.is_(False)))).scalar_one()


def comment_out(c: DocumentComment) -> dict:
    return {"id": c.id, "version": c.version, "clause": c.clause, "body": c.body, "party": c.party, "author_name": c.author_name,
            "resolved": c.resolved, "created_at": c.created_at}


def version_out(v: DocumentVersion) -> dict:
    return {"id": v.id, "version": v.version, "note": v.note, "source": v.source, "author_name": v.author_name,
            "content_sha256": v.content_sha256, "created_at": v.created_at}


# ---- credit & risk -----------------------------------------------------------------------------------
async def credit_check(db: AsyncSession, doc: Document) -> dict:
    """Order Forms for held or high-risk accounts need a Finance approval on their quote before signature."""
    if doc.doc_type != "order_form":
        return {"required": False}
    account = await db.get(Account, doc.account_id)
    risk = await erp.assess_credit_risk(db, account)
    if not (account.credit_hold or risk["band"] == "high"):
        return {"required": False, **risk}
    finance_ok = doc.quote_id and (await db.execute(select(ApprovalRequest.id).where(
        ApprovalRequest.quote_id == doc.quote_id, ApprovalRequest.required_role == "finance", ApprovalRequest.status == "approved"))).first()
    if not finance_ok:
        reason = "credit hold" if account.credit_hold else f"high credit risk ({risk['score']})"
        raise CreditReviewRequired(f"Credit review required: {account.name} is on {reason}. The quote needs Finance approval before signature.")
    return {"required": True, "cleared": True, **risk}


# ---- external e-signature providers ---------------------------------------------------------------
async def _create_envelope(provider: str, doc: Document, pdf: bytes) -> str:
    """POST the execution packet to the provider; returns the envelope / agreement id."""
    if not settings.esign_api_url or not settings.esign_api_token:
        raise RuntimeError(f"{provider} is not configured (ESIGN_API_URL / ESIGN_API_TOKEN)")
    signers = [{"name": s.signer_name, "email": s.signer_email, "routingOrder": s.sign_order} for s in doc.signers]
    if provider == "docusign":
        url = f"{settings.esign_api_url.rstrip('/')}/envelopes"
        payload = {"emailSubject": f"Please sign: {doc.title}", "status": "sent",
                   "documents": [{"documentBase64": base64.b64encode(pdf).decode(), "name": f"{doc.title}.pdf", "fileExtension": "pdf", "documentId": "1"}],
                   "recipients": {"signers": [{**s, "recipientId": str(i + 1)} for i, s in enumerate(signers)]},
                   "customFields": {"textCustomFields": [{"name": "cirra_document_id", "value": str(doc.id)}]}}
    else:  # adobe_sign
        url = f"{settings.esign_api_url.rstrip('/')}/agreements"
        payload = {"name": doc.title, "signatureType": "ESIGN", "state": "IN_PROCESS", "externalId": {"id": str(doc.id)},
                   "fileInfos": [{"document": {"name": f"{doc.title}.pdf", "mimeType": "application/pdf",
                                               "content": base64.b64encode(pdf).decode()}}],
                   "participantSetsInfo": [{"order": s["routingOrder"], "role": "SIGNER", "memberInfos": [{"email": s["email"]}]} for s in signers]}
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {settings.esign_api_token}"}) as c:
        r = await c.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    return str(data.get("envelopeId") or data.get("id"))


async def send_external(db: AsyncSession, doc: Document, provider: str) -> None:
    from app.services.clm import render_pdf

    try:
        envelope = await _create_envelope(provider, doc, render_pdf(doc, []))
    except Exception as exc:
        raise ValueError(f"Could not send through {provider.replace('_', ' ').title()}: {exc}") from exc
    doc.esign_provider, doc.envelope_id = provider, envelope


async def handle_provider_event(db: AsyncSession, provider: str, event: dict) -> Document:
    """Normalised provider callback: {envelope_id, status: signer_completed|completed|declined, signer_email?, signed_at?, ip?}."""
    from app.services.clm import _complete

    doc = (await db.execute(select(Document).where(Document.envelope_id == str(event.get("envelope_id")), Document.esign_provider == provider))).scalars().first()
    if doc is None:
        raise LookupError("Unknown envelope")
    await db.refresh(doc, ["signers", "account"])
    if doc.status in ("completed", "voided"):
        return doc
    now = datetime.now(timezone.utc)
    status = event.get("status")
    if status == "declined":
        doc.status = "voided"
        for s in doc.signers:
            if s.status == "pending" and (not event.get("signer_email") or s.signer_email.lower() == event["signer_email"].lower()):
                s.status = "declined"
        db.add(Activity(account_id=doc.account_id, deal_id=doc.deal_id, activity_type="document", source="system", sentiment="negative",
                        subject=doc.title, summary=f"{doc.title} declined in {provider}"))
        return doc
    targets = [s for s in doc.signers if s.status == "pending" and
               (status == "completed" or (event.get("signer_email") or "").lower() == s.signer_email.lower())]
    for s in targets:
        s.status, s.signed_at = "signed", now
        s.signature_text = s.signer_name
        s.signed_ip, s.user_agent = (event.get("ip") or "")[:64] or None, f"{provider} envelope {doc.envelope_id}"
    if all(s.status == "signed" for s in doc.signers):
        await _complete(db, doc)
    else:
        doc.status = "partially_signed"
    await db.flush()
    return doc
