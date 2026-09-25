"""Initial relate [R] schema (Functional Solution Specification section 4).

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-09-25
"""
from alembic import op

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE_SQL = """
-- Step 1: Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- Step 2: User Identity & Tenant Accounts
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(150) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'sales_rep'
        CONSTRAINT ck_users_role CHECK (role IN ('super_admin', 'sales_manager', 'sales_rep', 'read_only')),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE accounts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    domain VARCHAR(255) UNIQUE NOT NULL,
    industry VARCHAR(100),
    tier VARCHAR(50) NOT NULL DEFAULT 'Mid-Market'
        CONSTRAINT ck_accounts_tier CHECK (tier IN ('SMB', 'Mid-Market', 'Enterprise')),
    health_score INT NOT NULL DEFAULT 100 CONSTRAINT ck_accounts_health CHECK (health_score BETWEEN 0 AND 100),
    owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
    custom_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_accounts_domain ON accounts(domain);

-- Step 3: Contacts & Buying Committee Roles
-- (email is UNIQUE but nullable so AI-extracted contacts without an address can be captured)
CREATE TABLE contacts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    email VARCHAR(255) UNIQUE,
    phone VARCHAR(50),
    job_title VARCHAR(150),
    buying_role VARCHAR(50) NOT NULL DEFAULT 'Evaluator'
        CONSTRAINT ck_contacts_buying_role CHECK (buying_role IN ('Champion', 'Decision Maker', 'Economic Buyer', 'Blocker', 'Evaluator', 'Influencer')),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_contacts_account ON contacts(account_id);

-- Step 4: Pipelines & Stage Gates
CREATE TABLE pipelines (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(100) NOT NULL,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE pipeline_stages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pipeline_id UUID NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    stage_order INT NOT NULL,
    default_probability INT NOT NULL CONSTRAINT ck_stage_probability CHECK (default_probability BETWEEN 0 AND 100),
    is_closed_won BOOLEAN NOT NULL DEFAULT FALSE,
    is_closed_lost BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE(pipeline_id, stage_order)
);

-- Step 5: Deals & Opportunity Tracking
CREATE TABLE deals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title VARCHAR(255) NOT NULL,
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    primary_contact_id UUID REFERENCES contacts(id) ON DELETE SET NULL,
    pipeline_id UUID NOT NULL REFERENCES pipelines(id) ON DELETE RESTRICT,
    stage_id UUID NOT NULL REFERENCES pipeline_stages(id) ON DELETE RESTRICT,
    owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
    amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    target_close_date DATE,
    risk_score INT NOT NULL DEFAULT 0 CONSTRAINT ck_deals_risk CHECK (risk_score BETWEEN 0 AND 100),
    risk_factors JSONB NOT NULL DEFAULT '{}'::jsonb,
    ai_insights JSONB NOT NULL DEFAULT '{}'::jsonb,
    loss_reason VARCHAR(50) CONSTRAINT ck_deals_loss_reason
        CHECK (loss_reason IS NULL OR loss_reason IN ('price', 'competitor', 'no_decision', 'timing', 'product_fit', 'other')),
    stage_entered_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_deals_account ON deals(account_id);
CREATE INDEX idx_deals_stage ON deals(stage_id);

-- Step 6: Activities & semantic memory (pgvector)
CREATE TABLE activities (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    deal_id UUID REFERENCES deals(id) ON DELETE SET NULL,
    contact_id UUID REFERENCES contacts(id) ON DELETE SET NULL,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    activity_type VARCHAR(20) NOT NULL DEFAULT 'note'
        CONSTRAINT ck_activities_type CHECK (activity_type IN ('meeting', 'call', 'note', 'email', 'system')),
    summary TEXT NOT NULL,
    raw_text TEXT,
    sentiment VARCHAR(10) NOT NULL DEFAULT 'neutral'
        CONSTRAINT ck_activities_sentiment CHECK (sentiment IN ('positive', 'neutral', 'negative')),
    occurred_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    embedding vector(1536),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_activities_account ON activities(account_id);
CREATE INDEX idx_activities_deal ON activities(deal_id);
CREATE INDEX idx_activities_occurred ON activities(occurred_at DESC);
CREATE INDEX idx_activities_embedding ON activities USING hnsw (embedding vector_cosine_ops);

-- Step 7: Action items
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title VARCHAR(500) NOT NULL,
    due_date DATE,
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at TIMESTAMPTZ,
    account_id UUID REFERENCES accounts(id) ON DELETE CASCADE,
    deal_id UUID REFERENCES deals(id) ON DELETE SET NULL,
    activity_id UUID REFERENCES activities(id) ON DELETE SET NULL,
    owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
    source VARCHAR(20) NOT NULL DEFAULT 'manual',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_tasks_due ON tasks(due_date);

-- Step 8: Stage-gate audit trail
CREATE TABLE deal_stage_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    deal_id UUID NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    from_stage_id UUID REFERENCES pipeline_stages(id) ON DELETE SET NULL,
    to_stage_id UUID NOT NULL REFERENCES pipeline_stages(id) ON DELETE CASCADE,
    changed_by UUID REFERENCES users(id) ON DELETE SET NULL,
    forecast_delta NUMERIC(14, 2) NOT NULL DEFAULT 0,
    gate_overridden BOOLEAN NOT NULL DEFAULT FALSE,
    changed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_stage_history_deal ON deal_stage_history(deal_id);
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS deal_stage_history;
DROP TABLE IF EXISTS tasks;
DROP TABLE IF EXISTS activities;
DROP TABLE IF EXISTS deals;
DROP TABLE IF EXISTS pipeline_stages;
DROP TABLE IF EXISTS pipelines;
DROP TABLE IF EXISTS contacts;
DROP TABLE IF EXISTS accounts;
DROP TABLE IF EXISTS users;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
