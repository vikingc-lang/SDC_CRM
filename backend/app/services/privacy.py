"""Privacy & compliance governance (pillar 2): consent, opt-outs, erasure.

Erasure is cryptographic: the contact's PII columns are anonymised in place,
free-text mentions in activity notes are redacted, and the per-person data key
is destroyed so every historical PII value in the append-only audit trail
becomes unreadable. An ``erasure_log`` entry keeps a SHA-256 fingerprint as
evidence without retaining the data.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import current_user_id, decrypt_value, log_action
from app.models import Activity, AuditLog, ConsentEvent, Contact, ErasureLog, SubjectKey

CHANNELS = ("email", "phone", "sms")


def can_contact(contact: Contact, channel: str) -> tuple[bool, str | None]:
    if contact.status == "erased":
        return False, "Contact was erased under a privacy request"
    if channel == "email":
        if contact.opt_out_email:
            return False, "Contact opted out of email"
        if contact.privacy_regime == "GDPR" and contact.consent_email != "granted" and contact.consent_basis != "legitimate_interest":
            return False, "GDPR: no recorded consent or legitimate-interest basis for email"
    if channel == "phone" and contact.opt_out_phone:
        return False, "Contact opted out of phone"
    if channel == "sms" and contact.opt_out_sms:
        return False, "Contact opted out of SMS"
    return True, None


async def record_consent(db: AsyncSession, contact: Contact, *, consent_email: str | None = None, basis: str | None = None,
                         regime: str | None = None, opt_outs: dict | None = None, do_not_sell: bool | None = None,
                         source: str = "crm_user") -> None:
    now = datetime.now(timezone.utc)
    uid = current_user_id.get()
    if regime is not None:
        contact.privacy_regime = regime or None
    if consent_email is not None and consent_email != contact.consent_email:
        contact.consent_email = consent_email
        db.add(ConsentEvent(contact_id=contact.id, event_type=f"consent_{consent_email}", channel="email",
                            regulation=contact.privacy_regime, source=source, details={"basis": basis}, user_id=uid))
    if basis is not None:
        contact.consent_basis = basis or None
    for ch, value in (opt_outs or {}).items():
        if ch in CHANNELS and value is not None and getattr(contact, f"opt_out_{ch}") != value:
            setattr(contact, f"opt_out_{ch}", value)
            db.add(ConsentEvent(contact_id=contact.id, event_type="opt_out" if value else "opt_in", channel=ch,
                                regulation=contact.privacy_regime, source=source, user_id=uid))
    if do_not_sell is not None and do_not_sell != contact.do_not_sell:
        contact.do_not_sell = do_not_sell
        db.add(ConsentEvent(contact_id=contact.id, event_type="do_not_sell" if do_not_sell else "allow_sale",
                            regulation="CCPA", source=source, user_id=uid))
    contact.consent_updated_at = now


async def erase_contact(db: AsyncSession, contact: Contact, regulation: str | None = None) -> ErasureLog:
    if contact.status == "erased":
        raise ValueError("Contact already erased")
    pii = {f: getattr(contact, f) for f in Contact.PII_FIELDS}
    subject_hash = hashlib.sha256(json.dumps({"id": str(contact.id), **{k: v for k, v in pii.items()}}, sort_keys=True, default=str).encode()).hexdigest()
    names = [v for v in (pii["email"], pii["phone"], pii["mobile"], f"{pii['first_name']} {pii['last_name']}".strip()) if v and len(v) > 3]

    # Redact free-text mentions in notes linked to this person or their account.
    if names:
        pattern = re.compile("|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)), re.IGNORECASE)
        acts = (await db.execute(select(Activity).where(Activity.account_id == contact.account_id))).scalars().unique().all()
        for a in acts:
            for attr in ("summary", "raw_text", "subject"):
                val = getattr(a, attr)
                if val and pattern.search(val):
                    setattr(a, attr, pattern.sub("[erased]", val))
                    a.embedding = None  # re-embedded without the personal data by the next index run

    contact.first_name, contact.last_name = "Erased", "Contact"
    for f in ("email", "phone", "mobile", "linkedin_url", "timezone"):
        setattr(contact, f, None)
    contact.status = "erased"
    contact.consent_email, contact.opt_out_email, contact.opt_out_phone, contact.opt_out_sms, contact.do_not_sell = "denied", True, True, True, True
    contact.custom_fields = {}
    await db.flush()  # audit rows for the anonymisation are encrypted with the key we destroy next

    await db.execute(delete(SubjectKey).where(SubjectKey.contact_id == contact.id))
    entry = ErasureLog(contact_id=contact.id, subject_hash=subject_hash, fields_erased=[k for k, v in pii.items() if v],
                       regulation=regulation or contact.privacy_regime, requested_by=current_user_id.get(), key_destroyed=True)
    db.add(entry)
    db.add(ConsentEvent(contact_id=contact.id, event_type="erased", regulation=regulation or contact.privacy_regime,
                        source="erasure_request", details={"subject_hash": subject_hash}, user_id=current_user_id.get()))
    log_action(db, "erase", "contacts", contact.id, f"PII erased; key destroyed; evidence sha256={subject_hash}")
    await db.flush()
    return entry


async def readable_audit(db: AsyncSession, rows: list[AuditLog]) -> list[dict]:
    """Decrypt PII audit values where the subject key still exists."""
    keys: dict = {}
    out = []
    for r in rows:
        old, new = r.old_value, r.new_value
        if r.encrypted and r.record_id:
            if r.record_id not in keys:
                k = await db.get(SubjectKey, r.record_id)
                keys[r.record_id] = k.key if k else None
            key = keys[r.record_id]
            if key is None:
                old = "[crypto-shredded]" if old is not None else None
                new = "[crypto-shredded]" if new is not None else None
            else:
                old = decrypt_value(key, old) if old is not None else None
                new = decrypt_value(key, new) if new is not None else None
        out.append({"id": r.id, "user_id": r.user_id, "entity": r.entity, "record_id": r.record_id, "action": r.action,
                    "field_name": r.field_name, "old_value": old, "new_value": new, "encrypted": r.encrypted, "created_at": r.created_at})
    return out
