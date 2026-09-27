"""Service and marketing depth: email-to-case, presence-based routing, email tracking and nurture journeys.

Revision ID: 016_service_marketing_depth
Revises: 015_custom_field_entities
Create Date: 2026-09-28
"""
from alembic import op

revision = "016_service_marketing_depth"
down_revision = "015_custom_field_entities"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
-- email-to-case
ALTER TABLE support_queues ADD COLUMN email_address VARCHAR(255) UNIQUE;
ALTER TABLE support_queues ADD COLUMN routing VARCHAR(12) NOT NULL DEFAULT 'least_loaded' CHECK (routing IN ('least_loaded', 'presence'));
ALTER TABLE support_tickets ADD COLUMN supplied_email VARCHAR(255);
ALTER TABLE support_tickets ADD COLUMN supplied_name VARCHAR(200);
ALTER TABLE case_comments ADD COLUMN message_id VARCHAR(500);
ALTER TABLE case_comments ADD COLUMN from_email VARCHAR(255);
ALTER TABLE case_comments ADD COLUMN emailed BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX ix_case_comments_message ON case_comments (message_id);

CREATE TABLE inbound_emails (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id VARCHAR(500) NOT NULL UNIQUE,
    from_email VARCHAR(255) NOT NULL,
    from_name VARCHAR(200),
    to_email VARCHAR(255),
    subject VARCHAR(500) NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    status VARCHAR(12) NOT NULL CHECK (status IN ('case_created', 'appended', 'unmatched', 'ignored', 'converted', 'dismissed')),
    detail VARCHAR(300),
    case_id UUID REFERENCES support_tickets(id) ON DELETE SET NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_inbound_emails_status ON inbound_emails (status, received_at DESC);
CREATE INDEX ix_inbound_emails_sender ON inbound_emails (lower(from_email), received_at DESC);

-- presence-based routing
CREATE TABLE agent_presence (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    status VARCHAR(10) NOT NULL DEFAULT 'offline' CHECK (status IN ('available', 'busy', 'away', 'offline')),
    capacity SMALLINT NOT NULL DEFAULT 5 CHECK (capacity BETWEEN 1 AND 50),
    last_assigned_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- nurture journeys and email tracking
CREATE TABLE journeys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(150) NOT NULL,
    description TEXT,
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    status VARCHAR(10) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'paused', 'archived')),
    steps JSONB NOT NULL DEFAULT '[]'::jsonb,
    sender_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    activated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE journey_enrollments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    journey_id UUID NOT NULL REFERENCES journeys(id) ON DELETE CASCADE,
    member_id UUID NOT NULL REFERENCES campaign_members(id) ON DELETE CASCADE,
    step INTEGER NOT NULL DEFAULT 0,
    next_at TIMESTAMPTZ,
    status VARCHAR(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'exited')),
    exit_reason VARCHAR(80),
    enrolled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (journey_id, member_id)
);
CREATE INDEX ix_journey_enrollments_due ON journey_enrollments (status, next_at);

CREATE TABLE email_sends (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token VARCHAR(40) NOT NULL UNIQUE,
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    journey_id UUID REFERENCES journeys(id) ON DELETE SET NULL,
    step_index INTEGER,
    member_id UUID REFERENCES campaign_members(id) ON DELETE SET NULL,
    enrollment_id UUID REFERENCES journey_enrollments(id) ON DELETE SET NULL,
    email VARCHAR(255) NOT NULL,
    subject VARCHAR(300) NOT NULL,
    message_id VARCHAR(500),
    delivered BOOLEAN NOT NULL DEFAULT FALSE,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    opened_at TIMESTAMPTZ,
    open_count INTEGER NOT NULL DEFAULT 0,
    clicked_at TIMESTAMPTZ,
    click_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_email_sends_campaign ON email_sends (campaign_id, sent_at);
CREATE INDEX ix_email_sends_journey ON email_sends (journey_id, step_index);
CREATE INDEX ix_email_sends_enrollment ON email_sends (enrollment_id, sent_at DESC);

CREATE TABLE email_events (
    id BIGSERIAL PRIMARY KEY,
    send_id UUID NOT NULL REFERENCES email_sends(id) ON DELETE CASCADE,
    kind VARCHAR(6) NOT NULL CHECK (kind IN ('open', 'click')),
    url TEXT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_email_events_send ON email_events (send_id, occurred_at);
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS email_events;
DROP TABLE IF EXISTS email_sends;
DROP TABLE IF EXISTS journey_enrollments;
DROP TABLE IF EXISTS journeys;
DROP TABLE IF EXISTS agent_presence;
DROP TABLE IF EXISTS inbound_emails;
DROP INDEX IF EXISTS ix_case_comments_message;
ALTER TABLE case_comments DROP COLUMN IF EXISTS emailed, DROP COLUMN IF EXISTS from_email, DROP COLUMN IF EXISTS message_id;
ALTER TABLE support_tickets DROP COLUMN IF EXISTS supplied_name, DROP COLUMN IF EXISTS supplied_email;
ALTER TABLE support_queues DROP COLUMN IF EXISTS routing, DROP COLUMN IF EXISTS email_address;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
