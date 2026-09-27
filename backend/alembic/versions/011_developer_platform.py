"""Developer platform: API keys, webhook subscriptions and a delivery log.

API keys act as a named user (so RBAC and row-level scope apply) and can be read-only or expire; only
a SHA-256 hash is stored. Webhook subscriptions receive outbox events matching their patterns, signed
with HMAC-SHA256; each (subscription, event) pair is one delivery retried with back-off. A
subscription starts at the newest event, so it isn't flooded with history.

Revision ID: 011_developer_platform
Revises: 010_marketing
Create Date: 2026-09-27
"""
from alembic import op

revision = "011_developer_platform"
down_revision = "010_marketing"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
CREATE TABLE api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    prefix VARCHAR(16) NOT NULL,
    key_hash VARCHAR(64) NOT NULL UNIQUE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    read_only BOOLEAN NOT NULL DEFAULT FALSE,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    expires_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE webhook_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    url VARCHAR(500) NOT NULL,
    event_types JSONB NOT NULL DEFAULT '["*"]'::jsonb,
    secret_enc TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    cursor_event_id BIGINT NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    disabled_reason VARCHAR(200),
    last_success_at TIMESTAMPTZ,
    last_failure_at TIMESTAMPTZ,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE webhook_deliveries (
    id BIGSERIAL PRIMARY KEY,
    subscription_id UUID NOT NULL REFERENCES webhook_subscriptions(id) ON DELETE CASCADE,
    event_id BIGINT REFERENCES integration_events(id) ON DELETE CASCADE,
    event_type VARCHAR(60) NOT NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'success', 'failed', 'dead')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    response_code INTEGER,
    error VARCHAR(500),
    duration_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_at TIMESTAMPTZ,
    UNIQUE (subscription_id, event_id)
);
CREATE INDEX ix_webhook_deliveries_due ON webhook_deliveries (status, next_attempt_at);
CREATE INDEX ix_webhook_deliveries_sub ON webhook_deliveries (subscription_id, id DESC);
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS webhook_deliveries;
DROP TABLE IF EXISTS webhook_subscriptions;
DROP TABLE IF EXISTS api_keys;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
