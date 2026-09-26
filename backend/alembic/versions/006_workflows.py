"""Workflows: no-code automation rules (trigger -> conditions -> actions) and their run log.

Conditions reuse the reporting field catalogue and filter syntax, so rules never carry SQL.
Record triggers fire after a commit that creates or changes a record; scheduled rules are
evaluated hourly and act at most once per record (or again after a cool-down).

Revision ID: 006_workflows
Revises: 005_analytics
Create Date: 2026-09-26
"""
from alembic import op

revision = "006_workflows"
down_revision = "005_analytics"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
CREATE TABLE workflow_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(150) NOT NULL,
    description TEXT,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    source VARCHAR(30) NOT NULL,
    trigger JSONB NOT NULL,
    conditions JSONB NOT NULL DEFAULT '[]'::jsonb,
    actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    last_run_at TIMESTAMPTZ,
    run_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE workflow_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id UUID NOT NULL REFERENCES workflow_rules(id) ON DELETE CASCADE,
    record_id UUID,
    trigger VARCHAR(20) NOT NULL,
    status VARCHAR(10) NOT NULL CHECK (status IN ('done', 'failed', 'dry_run')),
    detail JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_workflow_runs_rule_record ON workflow_runs (rule_id, record_id, created_at DESC);
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS workflow_runs;
DROP TABLE IF EXISTS workflow_rules;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
