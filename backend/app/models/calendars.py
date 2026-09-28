"""Two-way calendar sync with Google Calendar and Microsoft 365 (see services/calendar_sync.py)."""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import UTCDateTime, UUID

__all__ = ["CalendarConnection", "CalendarLink", "CALENDAR_PROVIDERS"]

CALENDAR_PROVIDERS = ("google", "microsoft")


class CalendarConnection(Base):
    """A user's connected calendar. OAuth tokens are encrypted at rest; ``sync_token`` is the provider's
    incremental-sync cursor (Google syncToken, Microsoft Graph deltaLink)."""
    __tablename__ = "calendar_connections"
    __table_args__ = (CheckConstraint("provider IN ('google', 'microsoft')", name="ck_calendar_connections_provider"),
                      UniqueConstraint("user_id", "provider", name="uq_calendar_connections_user_provider"))

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(12))
    account_email: Mapped[str | None] = mapped_column(String(255))
    calendar_id: Mapped[str] = mapped_column(String(255), default="primary", server_default="primary")
    token_encrypted: Mapped[str] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sync_token: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="active", server_default="active")
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)


class CalendarLink(Base):
    """Which remote event mirrors which CRM meeting. ``local_hash`` is the meeting's synced fields at the last
    sync, so a later change on either side is detected without timestamps; a link whose meeting was deleted
    (``activity_id`` NULL) deletes the remote event on the next push."""
    __tablename__ = "calendar_links"
    __table_args__ = (UniqueConstraint("connection_id", "remote_id", name="uq_calendar_links_remote"),
                      Index("ix_calendar_links_activity", "activity_id"))

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calendar_connections.id", ondelete="CASCADE"))
    remote_id: Mapped[str] = mapped_column(String(255))
    activity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("activities.id", ondelete="SET NULL"))
    remote_etag: Mapped[str | None] = mapped_column(String(255))
    local_hash: Mapped[str | None] = mapped_column(String(64))
    synced_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
