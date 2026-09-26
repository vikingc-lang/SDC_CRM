"""Analytics: saved self-service reports and dashboards.

A report stores a declarative definition (source, columns, filters, groupings, measures, chart)
that the reporting engine compiles against a whitelisted field catalogue; it never stores SQL.
Shared reports and dashboards run with each viewer's own row-level scope.

Revision ID: 005_analytics
Revises: 004_identity
Create Date: 2026-09-26
"""
from alembic import op

revision = "005_analytics"
down_revision = "004_identity"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
CREATE TABLE saved_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(150) NOT NULL,
    description TEXT,
    owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
    source VARCHAR(30) NOT NULL,
    definition JSONB NOT NULL,
    visibility VARCHAR(10) NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'shared')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_saved_reports_owner ON saved_reports (owner_id);

CREATE TABLE dashboards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(150) NOT NULL,
    description TEXT,
    owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
    visibility VARCHAR(10) NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'shared')),
    tiles JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_dashboards_owner ON dashboards (owner_id);
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS dashboards;
DROP TABLE IF EXISTS saved_reports;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
