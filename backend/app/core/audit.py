"""Immutable, field-level audit trail (pillar 10) with crypto-shreddable PII (pillar 2).

A ``before_flush`` hook records every insert, update and delete across all
business entities: ``user_id, entity, record_id, field_name, old_value,
new_value, created_at``. Rows are append-only (a database trigger rejects
UPDATE/DELETE).

Personal data of contacts is written encrypted with a per-person AES-256-GCM
key (``subject_keys``). Erasing the person destroys the key, which makes every
historical PII value in the trail unrecoverable without rewriting the log.
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

current_user_id: ContextVar[uuid.UUID | None] = ContextVar("current_user_id", default=None)
audit_enabled: ContextVar[bool] = ContextVar("audit_enabled", default=True)

_SKIP_FIELDS = {"embedding", "search_tsv", "updated_at", "created_at", "password_hash", "secret_encrypted", "key"}
_UNAUDITED = {
    "AuditLog", "ConsentEvent", "ErasureLog", "Notification", "SubjectKey", "IntegrationEvent", "ErpSyncRun",
    "CollateralDownload",
}
_MAX = 4000


def _fmt(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        text = json.dumps(value, default=str, sort_keys=True)
    elif isinstance(value, (datetime, date)):
        text = value.isoformat()
    elif isinstance(value, (Decimal, uuid.UUID)):
        text = str(value)
    elif isinstance(value, (bytes, bytearray)):
        text = f"<{len(value)} bytes>"
    else:
        text = str(value)
    return text[:_MAX]


def encrypt_value(key: bytes, plaintext: str) -> str:
    nonce = os.urandom(12)
    return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, plaintext.encode(), None)).decode()


def decrypt_value(key: bytes, token: str) -> str:
    raw = base64.b64decode(token)
    return AESGCM(key).decrypt(raw[:12], raw[12:], None).decode()


def _subject_key(session: Session, contact_id: uuid.UUID) -> bytes:
    from app.models import SubjectKey

    with session.no_autoflush:
        for obj in session.new:
            if isinstance(obj, SubjectKey) and obj.contact_id == contact_id:
                return obj.key
        existing = session.get(SubjectKey, contact_id)
    if existing is not None:
        return existing.key
    key = AESGCM.generate_key(bit_length=256)
    session.add(SubjectKey(contact_id=contact_id, key=key))
    return key


def _columns(obj):
    mapper = inspect(obj).mapper
    return [c.key for c in mapper.column_attrs if c.key not in _SKIP_FIELDS]


def _record_id(obj):
    rid = getattr(obj, "id", None)
    return rid if isinstance(rid, uuid.UUID) else None


def _entity_name(obj) -> str:
    return getattr(obj, "__tablename__", type(obj).__name__.lower())


@event.listens_for(Session, "before_flush")
def _audit_before_flush(session: Session, flush_context, instances) -> None:
    if not audit_enabled.get():
        return
    from app.models import AuditLog, Contact

    user_id = current_user_id.get()
    rows: list[AuditLog] = []

    def add(obj, action, field=None, old=None, new=None):
        encrypted = False
        if isinstance(obj, Contact) and (field is None or field in Contact.PII_FIELDS) and (old is not None or new is not None):
            key = _subject_key(session, obj.id)
            old = encrypt_value(key, old) if old is not None else None
            new = encrypt_value(key, new) if new is not None else None
            encrypted = True
        rows.append(AuditLog(user_id=user_id, entity=_entity_name(obj), record_id=_record_id(obj), action=action,
                             field_name=field, old_value=old, new_value=new, encrypted=encrypted))

    for obj in list(session.new):
        if type(obj).__name__ in _UNAUDITED:
            continue
        id_col = inspect(obj).mapper.columns.get("id")
        if id_col is not None and getattr(obj, "id", None) is None and getattr(id_col.type, "as_uuid", False):
            obj.id = uuid.uuid4()  # assign now so the audit row can reference the record
        snapshot = {k: _fmt(getattr(obj, k, None)) for k in _columns(obj) if getattr(obj, k, None) is not None}
        add(obj, "create", None, None, json.dumps(snapshot, sort_keys=True)[:_MAX])

    for obj in list(session.dirty):
        if type(obj).__name__ in _UNAUDITED or not session.is_modified(obj, include_collections=False):
            continue
        state = inspect(obj)
        for key in _columns(obj):
            hist = state.attrs[key].history
            if not hist.has_changes():
                continue
            old = _fmt(hist.deleted[0]) if hist.deleted else None
            new = _fmt(hist.added[0]) if hist.added else None
            if old != new:
                add(obj, "update", key, old, new)

    for obj in list(session.deleted):
        if type(obj).__name__ in _UNAUDITED:
            continue
        snapshot = {k: _fmt(getattr(obj, k, None)) for k in _columns(obj) if getattr(obj, k, None) is not None}
        add(obj, "delete", None, json.dumps(snapshot, sort_keys=True)[:_MAX], None)

    session.add_all(rows)


def log_action(session, action: str, entity: str, record_id=None, detail: str | None = None) -> None:
    """Explicit audit entries for non-CRUD events (export, merge, erase, login...)."""
    from app.models import AuditLog

    session.add(AuditLog(user_id=current_user_id.get(), entity=entity, record_id=record_id, action=action, new_value=detail))
