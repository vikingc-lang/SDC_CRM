"""Developer platform: API keys, webhook subscriptions and deliveries (see services/developer.py)."""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import JSONB, UTCDateTime, UUID

__all__ = ["ApiKey", "WebhookSubscription", "WebhookDelivery", "Connector"]


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    read_only: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(500))
    event_types: Mapped[list] = mapped_column(JSONB, default=lambda: ["*"])
    secret_enc: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    cursor_event_id: Mapped[int] = mapped_column(BigInteger, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    disabled_reason: Mapped[str | None] = mapped_column(String(200))
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_failure_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    subscription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"))
    event_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("integration_events.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(10), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    response_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(500))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Connector(Base):
    """A configured pre-built integration (Slack, Microsoft Teams, Mailchimp, BambooHR; services/connectors.py).
    ``config`` holds the non-secret settings; ``secret`` the Fernet-encrypted credentials."""
    __tablename__ = "connectors"

    id: Mapped[uuid.UUID] = _pk()
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    secret: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    state: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")  # cursors and counters
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
