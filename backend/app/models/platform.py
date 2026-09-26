"""Platform core: RBAC, audit trail, custom fields, privacy ledgers, notifications, files, mail."""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, LargeBinary, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

__all__ = [
    "RolePermission", "AuditLog", "CustomFieldDefinition", "MergeLog", "DedupDismissal", "SubjectKey",
    "ConsentEvent", "ErasureLog", "Notification", "Attachment", "MailboxConnection", "DealAlert", "FxRate",
    "IntegrationEvent", "ErpSyncRun", "SsoLoginState", "SavedReport", "Dashboard", "WorkflowRule", "WorkflowRun", "ForecastSubmission", "ForecastAdjustment",
]


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role: Mapped[str] = mapped_column(String(50), primary_key=True)
    resource: Mapped[str] = mapped_column(String(50), primary_key=True)
    can_create: Mapped[bool] = mapped_column(Boolean, default=False)
    can_read: Mapped[bool] = mapped_column(Boolean, default=False)
    can_update: Mapped[bool] = mapped_column(Boolean, default=False)
    can_delete: Mapped[bool] = mapped_column(Boolean, default=False)
    can_export: Mapped[bool] = mapped_column(Boolean, default=False)
    scope: Mapped[str] = mapped_column(String(10), default="own")


class AuditLog(Base):
    """Append-only (enforced by a database trigger)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    entity: Mapped[str] = mapped_column(String(50))
    record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(20))
    field_name: Mapped[str | None] = mapped_column(String(100))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _ts()


class CustomFieldDefinition(Base):
    __tablename__ = "custom_field_definitions"

    id: Mapped[uuid.UUID] = _pk()
    entity: Mapped[str] = mapped_column(String(20))
    key: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(120))
    field_type: Mapped[str] = mapped_column(String(20))
    options: Mapped[list] = mapped_column(JSONB, default=list)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _ts()


class MergeLog(Base):
    __tablename__ = "merge_log"

    id: Mapped[uuid.UUID] = _pk()
    entity: Mapped[str] = mapped_column(String(20))
    survivor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    merged_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    field_resolution: Mapped[dict] = mapped_column(JSONB, default=dict)
    score: Mapped[float | None] = mapped_column(Numeric(5, 4))
    automatic: Mapped[bool] = mapped_column(Boolean, default=False)
    merged_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _ts()


class DedupDismissal(Base):
    __tablename__ = "dedup_dismissals"

    entity: Mapped[str] = mapped_column(String(20), primary_key=True)
    id_a: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id_b: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    dismissed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _ts()


class SubjectKey(Base):
    """Per-contact data key. Destroying it crypto-shreds that person's PII in the audit trail."""

    __tablename__ = "subject_keys"

    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    key: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = _ts()


class ConsentEvent(Base):
    __tablename__ = "consent_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    event_type: Mapped[str] = mapped_column(String(30))
    channel: Mapped[str | None] = mapped_column(String(20))
    regulation: Mapped[str | None] = mapped_column(String(10))
    source: Mapped[str | None] = mapped_column(String(60))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _ts()


class ErasureLog(Base):
    __tablename__ = "erasure_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    subject_hash: Mapped[str] = mapped_column(String(64))
    fields_erased: Mapped[list] = mapped_column(JSONB)
    regulation: Mapped[str | None] = mapped_column(String(10))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    key_destroyed: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts()


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(300))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = _pk()
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    activity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("activities.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(255))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _ts()


class MailboxConnection(Base):
    __tablename__ = "mailbox_connections"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(20), default="imap")
    email_address: Mapped[str] = mapped_column(String(255))
    imap_host: Mapped[str | None] = mapped_column(String(255))
    imap_port: Mapped[int | None] = mapped_column(Integer, default=993)
    smtp_host: Mapped[str | None] = mapped_column(String(255))
    smtp_port: Mapped[int | None] = mapped_column(Integer, default=587)
    username: Mapped[str | None] = mapped_column(String(255))
    secret_encrypted: Mapped[str | None] = mapped_column(Text)
    last_uid: Mapped[int] = mapped_column(BigInteger, default=0)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="active")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class DealAlert(Base):
    __tablename__ = "deal_alerts"

    id: Mapped[uuid.UUID] = _pk()
    deal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deals.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _ts()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    deal = relationship("Deal", lazy="joined")


class FxRate(Base):
    __tablename__ = "fx_rates"

    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    rate_to_usd: Mapped[float] = mapped_column(Numeric(14, 6))
    updated_at: Mapped[datetime] = _ts()


class IntegrationEvent(Base):
    """Outbox feeding neighbouring SDC modules (promo, Yield, deduct, nexora)."""

    __tablename__ = "integration_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict] = mapped_column(JSONB)
    targets: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = _ts()
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ErpSyncRun(Base):
    __tablename__ = "erp_sync_runs"

    id: Mapped[uuid.UUID] = _pk()
    connector: Mapped[str] = mapped_column(String(30))
    direction: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="running")
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _ts()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SsoLoginState(Base):
    """One pending OpenID Connect sign-in: CSRF state, replay nonce and PKCE verifier (kept server-side)."""
    __tablename__ = "sso_login_states"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(64))
    code_verifier: Mapped[str] = mapped_column(String(128))
    return_to: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = _ts()


class SavedReport(Base):
    """A self-service report definition (see services/reporting.py); never raw SQL."""
    __tablename__ = "saved_reports"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(30))
    definition: Mapped[dict] = mapped_column(JSONB)
    visibility: Mapped[str] = mapped_column(String(10), default="private")
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Dashboard(Base):
    """A grid of saved reports; tiles = [{"report_id": str, "size": "third" | "half" | "full"}]."""
    __tablename__ = "dashboards"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    visibility: Mapped[str] = mapped_column(String(10), default="private")
    tiles: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WorkflowRule(Base):
    """No-code automation: trigger + conditions (reporting filter syntax) + actions. See services/workflows.py."""
    __tablename__ = "workflow_rules"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(30))
    trigger: Mapped[dict] = mapped_column(JSONB)
    conditions: Mapped[list] = mapped_column(JSONB, default=list)
    actions: Mapped[list] = mapped_column(JSONB, default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[uuid.UUID] = _pk()
    rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_rules.id", ondelete="CASCADE"))
    record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    trigger: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(10))
    detail: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = _ts()


class ForecastSubmission(Base):
    """A rep's (scope 'self') or manager's (scope 'team') forecast call for a quarter, e.g. period '2026-Q4'."""
    __tablename__ = "forecast_submissions"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    period: Mapped[str] = mapped_column(String(7))
    scope: Mapped[str] = mapped_column(String(4))
    commit_amount: Mapped[float] = mapped_column(Numeric(15, 2))
    best_case_amount: Mapped[float] = mapped_column(Numeric(15, 2))
    calculated: Mapped[dict] = mapped_column(JSONB, default=dict)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class ForecastAdjustment(Base):
    """A manager's override of one rep's call for a quarter."""
    __tablename__ = "forecast_adjustments"

    id: Mapped[uuid.UUID] = _pk()
    manager_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    rep_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    period: Mapped[str] = mapped_column(String(7))
    commit_amount: Mapped[float] = mapped_column(Numeric(15, 2))
    best_case_amount: Mapped[float] = mapped_column(Numeric(15, 2))
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
