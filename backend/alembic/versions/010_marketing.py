"""Marketing: campaigns, campaign members, attribution and a Marketing role.

A campaign has a unique code that ties it to captured leads (a lead whose campaign / utm_campaign
equals the code joins it as a responder). Members are leads or contacts with a response status. Each
member gets an unsubscribe token for campaign email. Pipeline is attributed from member leads that
became deals (sourced) and from member contacts' accounts that opened deals after joining (influenced).

Revision ID: 010_marketing
Revises: 009_performance
Create Date: 2026-09-27
"""
from alembic import op

revision = "010_marketing"
down_revision = "009_performance"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role;
ALTER TABLE users ADD CONSTRAINT ck_users_role
    CHECK (role IN ('super_admin', 'sales_manager', 'account_executive', 'sdr', 'auditor', 'partner', 'support_agent', 'marketing'));

CREATE TABLE campaigns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(150) NOT NULL,
    code VARCHAR(80) NOT NULL UNIQUE,
    campaign_type VARCHAR(20) NOT NULL DEFAULT 'email'
        CHECK (campaign_type IN ('email', 'webinar', 'event', 'trade_show', 'paid_ads', 'content', 'partner', 'other')),
    status VARCHAR(12) NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'active', 'completed', 'aborted')),
    description TEXT,
    owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
    start_date DATE,
    end_date DATE,
    budget NUMERIC(15, 2) NOT NULL DEFAULT 0 CHECK (budget >= 0),
    actual_cost NUMERIC(15, 2) NOT NULL DEFAULT 0 CHECK (actual_cost >= 0),
    expected_revenue NUMERIC(15, 2) NOT NULL DEFAULT 0 CHECK (expected_revenue >= 0),
    email_subject VARCHAR(200),
    email_body TEXT,
    last_sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);

CREATE TABLE campaign_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
    contact_id UUID REFERENCES contacts(id) ON DELETE CASCADE,
    status VARCHAR(12) NOT NULL DEFAULT 'targeted'
        CHECK (status IN ('targeted', 'sent', 'responded', 'registered', 'attended', 'unsubscribed', 'bounced')),
    source VARCHAR(10) NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'filter', 'capture')),
    token VARCHAR(48) NOT NULL UNIQUE,
    sent_at TIMESTAMPTZ,
    responded_at TIMESTAMPTZ,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((lead_id IS NULL) <> (contact_id IS NULL)),
    UNIQUE (campaign_id, lead_id),
    UNIQUE (campaign_id, contact_id)
);
CREATE INDEX ix_campaign_members_campaign ON campaign_members (campaign_id, status);
CREATE INDEX ix_campaign_members_lead ON campaign_members (lead_id);
CREATE INDEX ix_campaign_members_contact ON campaign_members (contact_id);
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS campaign_members;
DROP TABLE IF EXISTS campaigns;
UPDATE users SET role = 'sales_manager' WHERE role = 'marketing';
ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role;
ALTER TABLE users ADD CONSTRAINT ck_users_role
    CHECK (role IN ('super_admin', 'sales_manager', 'account_executive', 'sdr', 'auditor', 'partner', 'support_agent'));
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
