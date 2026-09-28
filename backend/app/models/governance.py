"""AI governance: metered model calls (cost, tokens, trust signals) and AI agent actions awaiting or past review
(see services/ai_governance.py and services/agents.py)."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import JSONB, UTCDateTime, UUID

__all__ = ["AiUsage", "AiAction", "AI_ACTION_STATUSES"]

AI_ACTION_STATUSES = ("pending", "applied", "rejected", "failed", "expired")


class AiUsage(Base):
    """One call to the language model (or one refused before it was made): who, which feature, tokens, cost,
    and the trust layer's findings. Prompt and response excerpts are kept (masked) only when logging is on."""
    __tablename__ = "ai_usage"
    __table_args__ = (
        Index("ix_ai_usage_created", "created_at"),
        Index("ix_ai_usage_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    feature: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(12))  # ok | fallback | error | blocked | refused
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    pii_masked: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    injection_flags: Mapped[list] = mapped_column(JSONB, default=list)
    prompt_excerpt: Mapped[str | None] = mapped_column(Text)
    response_excerpt: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)


class AiAction(Base):
    """A change an AI agent wants to make. Applied at once when the agent's policy is "auto" (kept for the record),
    otherwise waits for the record owner, their manager or an admin to approve or reject it."""
    __tablename__ = "ai_actions"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'applied', 'rejected', 'failed', 'expired')", name="ck_ai_actions_status"),
        CheckConstraint("mode IN ('auto', 'approve')", name="ck_ai_actions_mode"),
        Index("ix_ai_actions_status_owner", "status", "owner_id"),
        Index("ix_ai_actions_entity", "entity_type", "entity_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent: Mapped[str] = mapped_column(String(40))
    action_type: Mapped[str] = mapped_column(String(30))
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(300))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="pending", server_default="pending")
    mode: Mapped[str] = mapped_column(String(8))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    result: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
