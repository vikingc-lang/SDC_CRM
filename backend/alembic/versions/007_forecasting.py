"""Forecasting: forecast categories, rep submissions and manager adjustments.

Each stage maps to a default forecast category (Pipeline, Best Case, Commit; Closed and Omitted for
won and lost stages) and a rep can override the category on a deal. Reps submit Commit and Best Case
calls per quarter; managers can adjust a rep's call and submit a team call. Submissions keep a
snapshot of the calculated numbers at the time, so calls can be compared with what the CRM showed.

Revision ID: 007_forecasting
Revises: 006_workflows
Create Date: 2026-09-26
"""
from alembic import op

revision = "007_forecasting"
down_revision = "006_workflows"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
ALTER TABLE pipeline_stages ADD COLUMN forecast_category VARCHAR(12) NOT NULL DEFAULT 'pipeline'
    CHECK (forecast_category IN ('pipeline', 'best_case', 'commit', 'closed', 'omitted'));
UPDATE pipeline_stages SET forecast_category = CASE
    WHEN is_closed_won THEN 'closed'
    WHEN is_closed_lost THEN 'omitted'
    WHEN default_probability >= 70 THEN 'commit'
    WHEN default_probability >= 40 THEN 'best_case'
    ELSE 'pipeline' END;

ALTER TABLE deals ADD COLUMN forecast_category VARCHAR(12)
    CHECK (forecast_category IN ('pipeline', 'best_case', 'commit', 'omitted'));

CREATE TABLE forecast_submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period VARCHAR(7) NOT NULL,
    scope VARCHAR(4) NOT NULL CHECK (scope IN ('self', 'team')),
    commit_amount NUMERIC(15, 2) NOT NULL CHECK (commit_amount >= 0),
    best_case_amount NUMERIC(15, 2) NOT NULL CHECK (best_case_amount >= 0),
    calculated JSONB NOT NULL DEFAULT '{}'::jsonb,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_forecast_submissions_user_period ON forecast_submissions (user_id, period, scope, created_at DESC);

CREATE TABLE forecast_adjustments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manager_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rep_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period VARCHAR(7) NOT NULL,
    commit_amount NUMERIC(15, 2) NOT NULL CHECK (commit_amount >= 0),
    best_case_amount NUMERIC(15, 2) NOT NULL CHECK (best_case_amount >= 0),
    note TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (manager_id, rep_id, period)
);
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS forecast_adjustments;
DROP TABLE IF EXISTS forecast_submissions;
ALTER TABLE deals DROP COLUMN IF EXISTS forecast_category;
ALTER TABLE pipeline_stages DROP COLUMN IF EXISTS forecast_category;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
