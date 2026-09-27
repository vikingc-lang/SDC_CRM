"""Sales performance: territories, quotas and commission plans.

Territories carry matching criteria (regions, countries, industries, tiers, employee range) and are
checked in priority order; realignment stamps each account with its first matching territory.
Quotas are per seller per quarter (period '2026-Q4'), in USD. Commission plans pay a base rate on
bookings up to 100% of quota and accelerated rates on the slices above, as tiers of quota attainment.

Revision ID: 009_performance
Revises: 008_service
Create Date: 2026-09-27
"""
from alembic import op

revision = "009_performance"
down_revision = "008_service"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
CREATE TABLE territories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    parent_id UUID REFERENCES territories(id) ON DELETE SET NULL,
    manager_id UUID REFERENCES users(id) ON DELETE SET NULL,
    member_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    criteria JSONB NOT NULL DEFAULT '{}'::jsonb,
    priority INTEGER NOT NULL DEFAULT 100,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE accounts ADD COLUMN territory_id UUID REFERENCES territories(id) ON DELETE SET NULL;
CREATE INDEX ix_accounts_territory ON accounts (territory_id);

CREATE TABLE quotas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period VARCHAR(7) NOT NULL,
    amount NUMERIC(15, 2) NOT NULL CHECK (amount >= 0),
    set_by UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, period)
);

CREATE TABLE commission_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    base_rate NUMERIC(6, 3) NOT NULL CHECK (base_rate >= 0 AND base_rate <= 100),
    tiers JSONB NOT NULL DEFAULT '[]'::jsonb,
    roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    member_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS commission_plans;
DROP TABLE IF EXISTS quotas;
ALTER TABLE accounts DROP COLUMN IF EXISTS territory_id;
DROP TABLE IF EXISTS territories;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
