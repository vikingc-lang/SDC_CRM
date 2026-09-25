"""Content-addressed file storage on a private volume (attachments, generated PDFs, collateral)."""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from app.core.config import settings
from app.models import Attachment


def _root() -> Path:
    root = Path(settings.storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def save(data: bytes, filename: str, content_type: str, *, account_id=None, activity_id=None, uploaded_by=None) -> Attachment:
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ValueError(f"File exceeds {settings.max_upload_mb} MB")
    digest = hashlib.sha256(data).hexdigest()
    key = f"{digest[:2]}/{digest}"
    path = _root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    safe_name = os.path.basename(filename or "file")[:255] or "file"
    return Attachment(account_id=account_id, activity_id=activity_id, filename=safe_name, content_type=content_type or "application/octet-stream",
                      size_bytes=len(data), sha256=digest, storage_key=key, uploaded_by=uploaded_by)


def read(att: Attachment) -> bytes:
    path = (_root() / att.storage_key).resolve()
    if _root() not in path.parents:
        raise FileNotFoundError("Invalid storage key")
    return path.read_bytes()
