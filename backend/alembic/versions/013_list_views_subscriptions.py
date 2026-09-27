"""Saved list views and scheduled report subscriptions.

list_views: a user's (or shared) way of looking at a list - columns, filters and sort over one report source.
report_subscriptions: a saved report delivered on a schedule (daily / weekly / monthly at an hour, UTC) to its
subscriber and optional extra recipients, run with each recipient's own permissions.

Revision ID: 013_list_views_subscriptions
Revises: 012_integration_keys
Create Date: 2026-09-27
"""
from alembic import op

revision = "013_list_views_subscriptions"
down_revision = "012_integration_keys"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
CREATE TABLE list_views (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(30) NOT NULL,
    name VARCHAR(120) NOT NULL,
    owner_id UUID REFERENCES users(id) ON DELETE CASCADE,
    visibility VARCHAR(10) NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'shared')),
    columns JSONB NOT NULL DEFAULT '[]'::jsonb,
    filters JSONB NOT NULL DEFAULT '[]'::jsonb,
    sort JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_list_views_source ON list_views (source, owner_id);

CREATE TABLE report_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id UUID NOT NULL REFERENCES saved_reports(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    frequency VARCHAR(10) NOT NULL CHECK (frequency IN ('daily', 'weekly', 'monthly')),
    weekday SMALLINT NOT NULL DEFAULT 0 CHECK (weekday BETWEEN 0 AND 6),
    day_of_month SMALLINT NOT NULL DEFAULT 1 CHECK (day_of_month BETWEEN 1 AND 28),
    hour SMALLINT NOT NULL DEFAULT 7 CHECK (hour BETWEEN 0 AND 23),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    last_sent_at TIMESTAMPTZ,
    last_status VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (report_id, user_id)
);
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS report_subscriptions;
DROP TABLE IF EXISTS list_views;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
