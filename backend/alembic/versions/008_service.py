"""Customer service: cases (extending support tickets), queues, SLA clocks, conversations,
knowledge base, CSAT, and a Support Agent role.

Existing support tickets become cases in place: they gain a case number, contact, owner, queue,
channel, category, first-response and resolution due times, breach tracking and a CSAT survey.
Health and churn scoring keep reading the same table.

Revision ID: 008_service
Revises: 007_forecasting
Create Date: 2026-09-26
"""
from alembic import op

revision = "008_service"
down_revision = "007_forecasting"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role;
ALTER TABLE users ADD CONSTRAINT ck_users_role
    CHECK (role IN ('super_admin', 'sales_manager', 'account_executive', 'sdr', 'auditor', 'partner', 'support_agent'));

CREATE TABLE support_queues (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    member_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    auto_assign BOOLEAN NOT NULL DEFAULT TRUE,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE SEQUENCE case_number_seq START 1001;
ALTER TABLE support_tickets
    ADD COLUMN case_number VARCHAR(20) UNIQUE,
    ADD COLUMN description TEXT,
    ADD COLUMN contact_id UUID REFERENCES contacts(id) ON DELETE SET NULL,
    ADD COLUMN owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN queue_id UUID REFERENCES support_queues(id) ON DELETE SET NULL,
    ADD COLUMN channel VARCHAR(10) NOT NULL DEFAULT 'web' CHECK (channel IN ('email', 'phone', 'web', 'portal', 'chat')),
    ADD COLUMN category VARCHAR(60),
    ADD COLUMN first_response_due_at TIMESTAMPTZ,
    ADD COLUMN resolve_due_at TIMESTAMPTZ,
    ADD COLUMN first_responded_at TIMESTAMPTZ,
    ADD COLUMN sla_breached BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN breach_notified_at TIMESTAMPTZ,
    ADD COLUMN csat_token VARCHAR(64) UNIQUE,
    ADD COLUMN csat_score INT CHECK (csat_score BETWEEN 1 AND 5),
    ADD COLUMN csat_comment TEXT,
    ADD COLUMN csat_at TIMESTAMPTZ,
    ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
UPDATE support_tickets SET case_number = 'CS-' || LPAD(nextval('case_number_seq')::text, 5, '0') WHERE case_number IS NULL;
CREATE INDEX idx_tickets_owner ON support_tickets(owner_id, status);
CREATE INDEX idx_tickets_queue ON support_tickets(queue_id, status);

CREATE TABLE case_comments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id UUID NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
    author_id UUID REFERENCES users(id) ON DELETE SET NULL,
    body TEXT NOT NULL,
    internal BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_case_comments_case ON case_comments(case_id, created_at);

CREATE TABLE kb_articles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(200) NOT NULL,
    body TEXT NOT NULL,
    category VARCHAR(60),
    status VARCHAR(10) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published')),
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    author_id UUID REFERENCES users(id) ON DELETE SET NULL,
    views INT NOT NULL DEFAULT 0,
    helpful INT NOT NULL DEFAULT 0,
    not_helpful INT NOT NULL DEFAULT 0,
    search_tsv TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') || setweight(to_tsvector('english', coalesce(body, '')), 'B')) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_kb_search ON kb_articles USING GIN (search_tsv);
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS kb_articles;
DROP TABLE IF EXISTS case_comments;
ALTER TABLE support_tickets
    DROP COLUMN IF EXISTS case_number, DROP COLUMN IF EXISTS description, DROP COLUMN IF EXISTS contact_id,
    DROP COLUMN IF EXISTS owner_id, DROP COLUMN IF EXISTS queue_id, DROP COLUMN IF EXISTS channel, DROP COLUMN IF EXISTS category,
    DROP COLUMN IF EXISTS first_response_due_at, DROP COLUMN IF EXISTS resolve_due_at, DROP COLUMN IF EXISTS first_responded_at,
    DROP COLUMN IF EXISTS sla_breached, DROP COLUMN IF EXISTS breach_notified_at, DROP COLUMN IF EXISTS csat_token,
    DROP COLUMN IF EXISTS csat_score, DROP COLUMN IF EXISTS csat_comment, DROP COLUMN IF EXISTS csat_at, DROP COLUMN IF EXISTS updated_at;
DROP SEQUENCE IF EXISTS case_number_seq;
DROP TABLE IF EXISTS support_queues;
UPDATE users SET role = 'account_executive' WHERE role = 'support_agent';
ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role;
ALTER TABLE users ADD CONSTRAINT ck_users_role
    CHECK (role IN ('super_admin', 'sales_manager', 'account_executive', 'sdr', 'auditor', 'partner'));
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
