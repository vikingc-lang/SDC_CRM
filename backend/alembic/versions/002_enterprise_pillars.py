"""Enterprise capability pillars 1-10.

Adds: granular RBAC, immutable audit trail, custom fields, firmographics &
hierarchies, dedup/merge log, contact intelligence & privacy governance,
multi-pipeline gate rules & loss taxonomy, CPQ, documents & e-signature,
contracts, onboarding, support/usage signals, ERP customer master & invoices,
integration outbox, partner relationship management, notifications, risk
alerts, attachments and mailbox sync.

Revision ID: 002_enterprise_pillars
Revises: 001_initial_schema
Create Date: 2026-09-26
"""
from alembic import op

revision = "002_enterprise_pillars"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ===== Pillar 10: roles, RBAC ================================================
ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role;
UPDATE users SET role = 'account_executive' WHERE role = 'sales_rep';
UPDATE users SET role = 'auditor' WHERE role = 'read_only';
ALTER TABLE users
    ADD CONSTRAINT ck_users_role CHECK (role IN ('super_admin', 'sales_manager', 'account_executive', 'sdr', 'auditor', 'partner')),
    ALTER COLUMN role SET DEFAULT 'account_executive',
    ADD COLUMN manager_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN ical_token VARCHAR(64) UNIQUE,
    ADD COLUMN partner_id UUID;

CREATE TABLE role_permissions (
    role VARCHAR(50) NOT NULL,
    resource VARCHAR(50) NOT NULL,
    can_create BOOLEAN NOT NULL DEFAULT FALSE,
    can_read BOOLEAN NOT NULL DEFAULT FALSE,
    can_update BOOLEAN NOT NULL DEFAULT FALSE,
    can_delete BOOLEAN NOT NULL DEFAULT FALSE,
    can_export BOOLEAN NOT NULL DEFAULT FALSE,
    scope VARCHAR(10) NOT NULL DEFAULT 'own' CHECK (scope IN ('all', 'own')),
    PRIMARY KEY (role, resource)
);

-- ===== Pillar 10: immutable audit trail =====================================
CREATE TABLE audit_log (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID,
    entity VARCHAR(50) NOT NULL,
    record_id UUID,
    action VARCHAR(20) NOT NULL,
    field_name VARCHAR(100),
    old_value TEXT,
    new_value TEXT,
    encrypted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_audit_record ON audit_log(entity, record_id);
CREATE INDEX idx_audit_created ON audit_log(created_at DESC);

-- Append-only enforcement shared by audit_log, consent_events and erasure_log.
-- TRUNCATE is only possible for an explicit, session-scoped demo reset.
CREATE FUNCTION relate_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' AND current_setting('relate.allow_ledger_reset', true) = 'on' THEN
        RETURN NULL;
    END IF;
    RAISE EXCEPTION '% is append-only (% blocked)', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION relate_append_only();
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log FOR EACH STATEMENT EXECUTE FUNCTION relate_append_only();

-- ===== Pillar 1: firmographics, customer master, hierarchy, custom fields ===
ALTER TABLE accounts
    ADD COLUMN parent_id UUID REFERENCES accounts(id) ON DELETE SET NULL,
    ADD COLUMN industry_code VARCHAR(20),
    ADD COLUMN annual_revenue NUMERIC(16, 2),
    ADD COLUMN employee_count INT,
    ADD COLUMN locations JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN alt_domains JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN lifecycle_stage VARCHAR(20) NOT NULL DEFAULT 'prospect'
        CHECK (lifecycle_stage IN ('prospect', 'customer', 'churned', 'partner')),
    ADD COLUMN legal_name VARCHAR(255),
    ADD COLUMN tax_id VARCHAR(64),
    ADD COLUMN billing_address JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN credit_limit NUMERIC(16, 2),
    ADD COLUMN credit_hold BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN payment_terms VARCHAR(20) NOT NULL DEFAULT 'NET30',
    ADD COLUMN erp_customer_id VARCHAR(64) UNIQUE,
    ADD COLUMN erp_synced_at TIMESTAMPTZ,
    ADD COLUMN churn_risk INT NOT NULL DEFAULT 0 CHECK (churn_risk BETWEEN 0 AND 100),
    ADD COLUMN churn_factors JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN relationship_strength INT,
    ADD COLUMN embedding vector(1536);
CREATE INDEX idx_accounts_parent ON accounts(parent_id);
CREATE INDEX idx_accounts_name_trgm ON accounts USING gin (lower(name) gin_trgm_ops);

CREATE TABLE custom_field_definitions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity VARCHAR(20) NOT NULL CHECK (entity IN ('account', 'contact', 'deal')),
    key VARCHAR(64) NOT NULL,
    label VARCHAR(120) NOT NULL,
    field_type VARCHAR(20) NOT NULL CHECK (field_type IN ('text', 'number', 'date', 'select', 'boolean', 'url')),
    options JSONB NOT NULL DEFAULT '[]'::jsonb,
    required BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (entity, key)
);

CREATE TABLE merge_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity VARCHAR(20) NOT NULL,
    survivor_id UUID NOT NULL,
    merged_id UUID NOT NULL,
    snapshot JSONB NOT NULL,
    field_resolution JSONB NOT NULL DEFAULT '{}'::jsonb,
    score NUMERIC(5, 4),
    automatic BOOLEAN NOT NULL DEFAULT FALSE,
    merged_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE dedup_dismissals (
    entity VARCHAR(20) NOT NULL,
    id_a UUID NOT NULL,
    id_b UUID NOT NULL,
    dismissed_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity, id_a, id_b)
);

-- ===== Pillar 2: contact intelligence & privacy governance ==================
ALTER TABLE contacts
    ADD COLUMN mobile VARCHAR(50),
    ADD COLUMN linkedin_url VARCHAR(255),
    ADD COLUMN timezone VARCHAR(64),
    ADD COLUMN department VARCHAR(100),
    ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'departed', 'erased')),
    ADD COLUMN departed_at TIMESTAMPTZ,
    ADD COLUMN consent_email VARCHAR(20) NOT NULL DEFAULT 'unknown' CHECK (consent_email IN ('granted', 'denied', 'unknown')),
    ADD COLUMN consent_basis VARCHAR(40),
    ADD COLUMN privacy_regime VARCHAR(10) CHECK (privacy_regime IN ('GDPR', 'CCPA', 'OTHER')),
    ADD COLUMN consent_updated_at TIMESTAMPTZ,
    ADD COLUMN do_not_sell BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN opt_out_email BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN opt_out_phone BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN opt_out_sms BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN relationship_strength INT,
    ADD COLUMN rsi_factors JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN custom_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE contacts DROP CONSTRAINT IF EXISTS ck_contacts_buying_role;
ALTER TABLE contacts ADD CONSTRAINT ck_contacts_buying_role
    CHECK (buying_role IN ('Champion', 'Decision Maker', 'Economic Buyer', 'Blocker', 'Evaluator', 'Influencer'));

CREATE TABLE subject_keys (
    contact_id UUID PRIMARY KEY,
    key BYTEA NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE consent_events (
    id BIGSERIAL PRIMARY KEY,
    contact_id UUID,
    event_type VARCHAR(30) NOT NULL,
    channel VARCHAR(20),
    regulation VARCHAR(10),
    source VARCHAR(60),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    user_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_consent_contact ON consent_events(contact_id);
CREATE TRIGGER consent_events_append_only BEFORE UPDATE OR DELETE ON consent_events FOR EACH ROW EXECUTE FUNCTION relate_append_only();
CREATE TRIGGER consent_events_no_truncate BEFORE TRUNCATE ON consent_events FOR EACH STATEMENT EXECUTE FUNCTION relate_append_only();
CREATE TABLE erasure_log (
    id BIGSERIAL PRIMARY KEY,
    contact_id UUID NOT NULL,
    subject_hash VARCHAR(64) NOT NULL,
    fields_erased JSONB NOT NULL,
    regulation VARCHAR(10),
    requested_by UUID,
    key_destroyed BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER erasure_log_append_only BEFORE UPDATE OR DELETE ON erasure_log FOR EACH ROW EXECUTE FUNCTION relate_append_only();
CREATE TRIGGER erasure_log_no_truncate BEFORE TRUNCATE ON erasure_log FOR EACH STATEMENT EXECUTE FUNCTION relate_append_only();

-- ===== Pillar 3: multi-pipeline, gate rules, loss taxonomy, FX ===============
ALTER TABLE pipelines
    ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'direct' CHECK (kind IN ('direct', 'inbound', 'renewal', 'partner')),
    ADD COLUMN description TEXT;
ALTER TABLE pipeline_stages ADD COLUMN gate_rules JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE deals DROP CONSTRAINT IF EXISTS ck_deals_loss_reason;
UPDATE deals SET loss_reason = 'feature_gap' WHERE loss_reason = 'product_fit';
ALTER TABLE deals
    ADD CONSTRAINT ck_deals_loss_reason CHECK (loss_reason IS NULL OR loss_reason IN
        ('competitor', 'budget_frozen', 'feature_gap', 'champion_departed', 'price', 'no_decision', 'timing', 'other')),
    ADD COLUMN loss_debrief TEXT,
    ADD COLUMN loss_competitor VARCHAR(100),
    ADD COLUMN win_debrief TEXT,
    ADD COLUMN deal_type VARCHAR(20) NOT NULL DEFAULT 'new_business'
        CHECK (deal_type IN ('new_business', 'renewal', 'upsell', 'partner')),
    ADD COLUMN source VARCHAR(20) NOT NULL DEFAULT 'direct',
    ADD COLUMN contract_id UUID,
    ADD COLUMN original_close_date DATE,
    ADD COLUMN close_date_pushes INT NOT NULL DEFAULT 0,
    ADD COLUMN custom_fields JSONB NOT NULL DEFAULT '{}'::jsonb;
UPDATE deals SET original_close_date = target_close_date;

CREATE TABLE fx_rates (
    currency VARCHAR(3) PRIMARY KEY,
    rate_to_usd NUMERIC(14, 6) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ===== Pillar 5: activity ledger, attachments, tasks/SLA, mailbox ============
ALTER TABLE activities DROP CONSTRAINT IF EXISTS ck_activities_type;
ALTER TABLE activities
    ADD CONSTRAINT ck_activities_type CHECK (activity_type IN ('meeting', 'call', 'note', 'email', 'system', 'file', 'document')),
    ADD COLUMN direction VARCHAR(10) CHECK (direction IN ('inbound', 'outbound', 'internal')),
    ADD COLUMN subject VARCHAR(500),
    ADD COLUMN duration_seconds INT,
    ADD COLUMN disposition VARCHAR(20) CHECK (disposition IN ('connected', 'left_voicemail', 'gatekeeper', 'no_answer', 'busy', 'wrong_number')),
    ADD COLUMN agenda TEXT,
    ADD COLUMN attendance VARCHAR(12) CHECK (attendance IN ('attended', 'no_show', 'cancelled', 'scheduled')),
    ADD COLUMN external_id VARCHAR(500) UNIQUE,
    ADD COLUMN thread_id VARCHAR(500),
    ADD COLUMN source VARCHAR(20) NOT NULL DEFAULT 'manual',
    ADD COLUMN search_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(subject, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(raw_text, ''))
    ) STORED;
CREATE INDEX idx_activities_tsv ON activities USING gin (search_tsv);
CREATE INDEX idx_activities_contact ON activities(contact_id);

CREATE TABLE attachments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id UUID REFERENCES accounts(id) ON DELETE CASCADE,
    activity_id UUID REFERENCES activities(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(120) NOT NULL,
    size_bytes BIGINT NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    storage_key VARCHAR(255) NOT NULL,
    uploaded_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_attachments_account ON attachments(account_id);

ALTER TABLE tasks
    ADD COLUMN assignee_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN description TEXT,
    ADD COLUMN priority VARCHAR(10) NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    ADD COLUMN depends_on_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
    ADD COLUMN escalation_level INT NOT NULL DEFAULT 0,
    ADD COLUMN escalated_at TIMESTAMPTZ,
    ADD COLUMN milestone_id UUID;
CREATE INDEX idx_tasks_assignee ON tasks(assignee_id);

CREATE TABLE notifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind VARCHAR(30) NOT NULL,
    title VARCHAR(300) NOT NULL,
    body TEXT,
    link VARCHAR(300),
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_notifications_user ON notifications(user_id, read_at);

CREATE TABLE mailbox_connections (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(20) NOT NULL DEFAULT 'imap' CHECK (provider IN ('imap', 'microsoft_graph', 'google')),
    email_address VARCHAR(255) NOT NULL,
    imap_host VARCHAR(255),
    imap_port INT DEFAULT 993,
    smtp_host VARCHAR(255),
    smtp_port INT DEFAULT 587,
    username VARCHAR(255),
    secret_encrypted TEXT,
    last_uid BIGINT NOT NULL DEFAULT 0,
    last_synced_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    last_error TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ===== Pillar 6: risk & slippage copilot ======================================
CREATE TABLE deal_alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    deal_id UUID NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    kind VARCHAR(30) NOT NULL CHECK (kind IN ('stagnant', 'close_date_pushed', 'sentiment_drift', 'missing_roles', 'overdue_close')),
    severity VARCHAR(10) NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    message TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX uq_open_alert ON deal_alerts(deal_id, kind) WHERE resolved_at IS NULL;

-- ===== Pillar 4: CPQ, approvals, documents, e-signature, contracts ==========
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sku VARCHAR(64) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    family VARCHAR(100),
    billing_type VARCHAR(20) NOT NULL DEFAULT 'recurring' CHECK (billing_type IN ('recurring', 'one_time')),
    unit VARCHAR(40) NOT NULL DEFAULT 'user / month',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE price_book_entries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    currency VARCHAR(3) NOT NULL,
    tiers JSONB NOT NULL,
    UNIQUE (product_id, currency)
);
CREATE TABLE approval_policies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(120) NOT NULL,
    rule_type VARCHAR(30) NOT NULL CHECK (rule_type IN ('discount_pct', 'payment_terms', 'credit_hold', 'tcv')),
    threshold NUMERIC(16, 2),
    approver_role VARCHAR(30) NOT NULL CHECK (approver_role IN ('sales_manager', 'finance')),
    active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE quotes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    deal_id UUID NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    quote_number VARCHAR(30) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    term_months INT NOT NULL DEFAULT 12 CHECK (term_months BETWEEN 1 AND 120),
    payment_terms VARCHAR(20) NOT NULL DEFAULT 'NET30',
    status VARCHAR(20) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'pending_approval', 'approved', 'rejected', 'sent', 'accepted', 'expired')),
    valid_until DATE,
    list_total NUMERIC(16, 2) NOT NULL DEFAULT 0,
    discount_total NUMERIC(16, 2) NOT NULL DEFAULT 0,
    max_discount_pct NUMERIC(5, 2) NOT NULL DEFAULT 0,
    one_time_total NUMERIC(16, 2) NOT NULL DEFAULT 0,
    acv NUMERIC(16, 2) NOT NULL DEFAULT 0,
    tcv NUMERIC(16, 2) NOT NULL DEFAULT 0,
    notes TEXT,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_quotes_deal ON quotes(deal_id);
CREATE TABLE quote_lines (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    quote_id UUID NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    position INT NOT NULL DEFAULT 0,
    description VARCHAR(500),
    quantity NUMERIC(12, 2) NOT NULL CHECK (quantity > 0),
    list_unit_price NUMERIC(14, 4) NOT NULL,
    discount_pct NUMERIC(5, 2) NOT NULL DEFAULT 0 CHECK (discount_pct BETWEEN 0 AND 100),
    net_unit_price NUMERIC(14, 4) NOT NULL,
    billing_type VARCHAR(20) NOT NULL,
    line_total NUMERIC(16, 2) NOT NULL
);
CREATE TABLE approval_requests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    quote_id UUID NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
    required_role VARCHAR(30) NOT NULL,
    reason TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'superseded')),
    decided_by UUID REFERENCES users(id) ON DELETE SET NULL,
    decided_at TIMESTAMPTZ,
    comment TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_approvals_status ON approval_requests(status, required_role);

CREATE TABLE document_templates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    doc_type VARCHAR(20) NOT NULL CHECK (doc_type IN ('nda', 'sow', 'order_form')),
    name VARCHAR(120) NOT NULL,
    body TEXT NOT NULL,
    version INT NOT NULL DEFAULT 1,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    template_id UUID REFERENCES document_templates(id) ON DELETE SET NULL,
    doc_type VARCHAR(20) NOT NULL,
    title VARCHAR(255) NOT NULL,
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    deal_id UUID REFERENCES deals(id) ON DELETE SET NULL,
    quote_id UUID REFERENCES quotes(id) ON DELETE SET NULL,
    body TEXT NOT NULL,
    content_sha256 VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'sent', 'partially_signed', 'completed', 'voided')),
    pdf_attachment_id UUID REFERENCES attachments(id) ON DELETE SET NULL,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ
);
CREATE TABLE signature_requests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    signer_name VARCHAR(200) NOT NULL,
    signer_email VARCHAR(255) NOT NULL,
    signer_party VARCHAR(20) NOT NULL CHECK (signer_party IN ('customer', 'company')),
    sign_order INT NOT NULL DEFAULT 1,
    token VARCHAR(64) UNIQUE NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'signed', 'declined')),
    signature_text VARCHAR(200),
    signature_image TEXT,
    signed_at TIMESTAMPTZ,
    signed_ip VARCHAR(64),
    user_agent VARCHAR(300),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE contracts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    deal_id UUID REFERENCES deals(id) ON DELETE SET NULL,
    quote_id UUID REFERENCES quotes(id) ON DELETE SET NULL,
    document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
    contract_number VARCHAR(30) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    acv NUMERIC(16, 2) NOT NULL DEFAULT 0,
    tcv NUMERIC(16, 2) NOT NULL DEFAULT 0,
    payment_terms VARCHAR(20) NOT NULL DEFAULT 'NET30',
    auto_renew BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'expired', 'renewed', 'terminated')),
    terms JSONB NOT NULL DEFAULT '{}'::jsonb,
    renewal_deal_id UUID REFERENCES deals(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_contracts_account ON contracts(account_id);
CREATE INDEX idx_contracts_end ON contracts(end_date);
ALTER TABLE deals ADD CONSTRAINT fk_deals_contract FOREIGN KEY (contract_id) REFERENCES contracts(id) ON DELETE SET NULL;

-- ===== Pillar 7: onboarding, support & adoption signals =====================
CREATE TABLE onboarding_projects (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    deal_id UUID UNIQUE REFERENCES deals(id) ON DELETE SET NULL,
    contract_id UUID REFERENCES contracts(id) ON DELETE SET NULL,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'not_started' CHECK (status IN ('not_started', 'in_progress', 'at_risk', 'completed')),
    owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
    kickoff_date DATE,
    target_go_live DATE,
    scope JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE onboarding_milestones (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES onboarding_projects(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    due_date DATE,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'done', 'blocked')),
    position INT NOT NULL DEFAULT 0,
    completed_at TIMESTAMPTZ
);
ALTER TABLE tasks ADD CONSTRAINT fk_tasks_milestone FOREIGN KEY (milestone_id) REFERENCES onboarding_milestones(id) ON DELETE SET NULL;

CREATE TABLE support_tickets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    external_id VARCHAR(64),
    subject VARCHAR(300) NOT NULL,
    severity VARCHAR(10) NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    status VARCHAR(10) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'pending', 'resolved', 'closed')),
    opened_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMPTZ
);
CREATE INDEX idx_tickets_account ON support_tickets(account_id, status);
CREATE TABLE product_usage (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    metric_date DATE NOT NULL,
    active_users INT NOT NULL,
    licensed_users INT NOT NULL,
    feature_adoption NUMERIC(5, 2) NOT NULL DEFAULT 0,
    UNIQUE (account_id, metric_date)
);

-- ===== Pillar 8: invoices, ERP sync, ecosystem outbox =======================
CREATE TABLE invoices (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    erp_invoice_id VARCHAR(64) UNIQUE,
    invoice_number VARCHAR(64) NOT NULL,
    issue_date DATE NOT NULL,
    due_date DATE NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    amount NUMERIC(16, 2) NOT NULL,
    balance NUMERIC(16, 2) NOT NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'paid', 'void')),
    synced_at TIMESTAMPTZ
);
CREATE INDEX idx_invoices_account ON invoices(account_id, status);
CREATE TABLE erp_sync_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    connector VARCHAR(30) NOT NULL,
    direction VARCHAR(10) NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    status VARCHAR(20) NOT NULL DEFAULT 'running',
    stats JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT,
    started_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ
);
CREATE TABLE integration_events (
    id BIGSERIAL PRIMARY KEY,
    event_type VARCHAR(60) NOT NULL,
    entity_type VARCHAR(30) NOT NULL,
    entity_id UUID,
    payload JSONB NOT NULL,
    targets JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    delivered_at TIMESTAMPTZ
);

-- ===== Pillar 9: partner relationship management ============================
CREATE TABLE partners (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    partner_type VARCHAR(20) NOT NULL CHECK (partner_type IN ('distributor', 'agency', 'reseller', 'referral', 'technology')),
    tier VARCHAR(20) NOT NULL DEFAULT 'registered' CHECK (tier IN ('registered', 'silver', 'gold', 'platinum')),
    domains JSONB NOT NULL DEFAULT '[]'::jsonb,
    territories JSONB NOT NULL DEFAULT '[]'::jsonb,
    commission_rate NUMERIC(5, 2) NOT NULL DEFAULT 10,
    referral_fee_rate NUMERIC(5, 2) NOT NULL DEFAULT 5,
    status VARCHAR(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE users ADD CONSTRAINT fk_users_partner FOREIGN KEY (partner_id) REFERENCES partners(id) ON DELETE SET NULL;
CREATE TABLE deal_registrations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    partner_id UUID NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
    submitted_by UUID REFERENCES users(id) ON DELETE SET NULL,
    company_name VARCHAR(255) NOT NULL,
    domain VARCHAR(255) NOT NULL,
    contact_name VARCHAR(200),
    contact_email VARCHAR(255),
    estimated_amount NUMERIC(16, 2) NOT NULL DEFAULT 0,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    territory VARCHAR(60),
    product_interest VARCHAR(255),
    notes TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'approved', 'rejected', 'expired')),
    exclusivity_expires_at DATE,
    conflicts JSONB NOT NULL DEFAULT '[]'::jsonb,
    decided_by UUID REFERENCES users(id) ON DELETE SET NULL,
    decided_at TIMESTAMPTZ,
    decision_note TEXT,
    deal_id UUID REFERENCES deals(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE deal_partners (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    deal_id UUID NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    partner_id UUID NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL CHECK (role IN ('referral', 'co_sell', 'resell')),
    split_pct NUMERIC(5, 2) NOT NULL DEFAULT 100 CHECK (split_pct BETWEEN 0 AND 100),
    commission_rate NUMERIC(5, 2),
    registration_id UUID REFERENCES deal_registrations(id) ON DELETE SET NULL,
    UNIQUE (deal_id, partner_id)
);
CREATE TABLE collateral (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title VARCHAR(255) NOT NULL,
    description TEXT,
    category VARCHAR(20) NOT NULL CHECK (category IN ('deck', 'whitepaper', 'battlecard', 'price_list', 'case_study')),
    attachment_id UUID NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    min_tier VARCHAR(20) NOT NULL DEFAULT 'registered',
    allowed_domains JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_published BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE collateral_downloads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    collateral_id UUID NOT NULL REFERENCES collateral(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    partner_id UUID REFERENCES partners(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS collateral_downloads, collateral, deal_partners, deal_registrations CASCADE;
ALTER TABLE users DROP CONSTRAINT IF EXISTS fk_users_partner;
DROP TABLE IF EXISTS partners, integration_events, erp_sync_runs, invoices, product_usage, support_tickets CASCADE;
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS fk_tasks_milestone;
DROP TABLE IF EXISTS onboarding_milestones, onboarding_projects CASCADE;
ALTER TABLE deals DROP CONSTRAINT IF EXISTS fk_deals_contract;
DROP TABLE IF EXISTS contracts, signature_requests, documents, document_templates, approval_requests, quote_lines, quotes,
    approval_policies, price_book_entries, products, deal_alerts, mailbox_connections, notifications, attachments, fx_rates,
    erasure_log, consent_events, subject_keys, dedup_dismissals, merge_log, custom_field_definitions, audit_log,
    role_permissions CASCADE;
DROP FUNCTION IF EXISTS relate_append_only() CASCADE;
ALTER TABLE tasks DROP COLUMN IF EXISTS assignee_id, DROP COLUMN IF EXISTS description, DROP COLUMN IF EXISTS priority,
    DROP COLUMN IF EXISTS depends_on_id, DROP COLUMN IF EXISTS escalation_level, DROP COLUMN IF EXISTS escalated_at,
    DROP COLUMN IF EXISTS milestone_id;
ALTER TABLE activities DROP COLUMN IF EXISTS search_tsv, DROP COLUMN IF EXISTS direction, DROP COLUMN IF EXISTS subject,
    DROP COLUMN IF EXISTS duration_seconds, DROP COLUMN IF EXISTS disposition, DROP COLUMN IF EXISTS agenda,
    DROP COLUMN IF EXISTS attendance, DROP COLUMN IF EXISTS external_id, DROP COLUMN IF EXISTS thread_id, DROP COLUMN IF EXISTS source;
ALTER TABLE deals DROP COLUMN IF EXISTS loss_debrief, DROP COLUMN IF EXISTS loss_competitor, DROP COLUMN IF EXISTS win_debrief,
    DROP COLUMN IF EXISTS deal_type, DROP COLUMN IF EXISTS source, DROP COLUMN IF EXISTS contract_id,
    DROP COLUMN IF EXISTS original_close_date, DROP COLUMN IF EXISTS close_date_pushes, DROP COLUMN IF EXISTS custom_fields;
ALTER TABLE deals DROP CONSTRAINT IF EXISTS ck_deals_loss_reason;
UPDATE deals SET loss_reason = 'product_fit' WHERE loss_reason = 'feature_gap';
UPDATE deals SET loss_reason = 'other' WHERE loss_reason IN ('budget_frozen', 'champion_departed');
ALTER TABLE deals ADD CONSTRAINT ck_deals_loss_reason
    CHECK (loss_reason IS NULL OR loss_reason IN ('price', 'competitor', 'no_decision', 'timing', 'product_fit', 'other'));
ALTER TABLE pipeline_stages DROP COLUMN IF EXISTS gate_rules;
ALTER TABLE pipelines DROP COLUMN IF EXISTS kind, DROP COLUMN IF EXISTS description;
ALTER TABLE activities DROP CONSTRAINT IF EXISTS ck_activities_type;
DELETE FROM activities WHERE activity_type IN ('file', 'document');
ALTER TABLE activities ADD CONSTRAINT ck_activities_type CHECK (activity_type IN ('meeting', 'call', 'note', 'email', 'system'));
ALTER TABLE contacts DROP COLUMN IF EXISTS mobile, DROP COLUMN IF EXISTS linkedin_url, DROP COLUMN IF EXISTS timezone,
    DROP COLUMN IF EXISTS department, DROP COLUMN IF EXISTS status, DROP COLUMN IF EXISTS departed_at,
    DROP COLUMN IF EXISTS consent_email, DROP COLUMN IF EXISTS consent_basis, DROP COLUMN IF EXISTS privacy_regime,
    DROP COLUMN IF EXISTS consent_updated_at, DROP COLUMN IF EXISTS do_not_sell, DROP COLUMN IF EXISTS opt_out_email,
    DROP COLUMN IF EXISTS opt_out_phone, DROP COLUMN IF EXISTS opt_out_sms, DROP COLUMN IF EXISTS relationship_strength,
    DROP COLUMN IF EXISTS rsi_factors, DROP COLUMN IF EXISTS custom_fields, DROP COLUMN IF EXISTS updated_at;
DROP INDEX IF EXISTS idx_accounts_name_trgm, idx_accounts_parent, idx_activities_contact, idx_activities_tsv, idx_tasks_assignee;
ALTER TABLE accounts DROP COLUMN IF EXISTS parent_id, DROP COLUMN IF EXISTS industry_code, DROP COLUMN IF EXISTS annual_revenue,
    DROP COLUMN IF EXISTS employee_count, DROP COLUMN IF EXISTS locations, DROP COLUMN IF EXISTS alt_domains,
    DROP COLUMN IF EXISTS lifecycle_stage, DROP COLUMN IF EXISTS legal_name, DROP COLUMN IF EXISTS tax_id,
    DROP COLUMN IF EXISTS billing_address, DROP COLUMN IF EXISTS credit_limit, DROP COLUMN IF EXISTS credit_hold,
    DROP COLUMN IF EXISTS payment_terms, DROP COLUMN IF EXISTS erp_customer_id, DROP COLUMN IF EXISTS erp_synced_at,
    DROP COLUMN IF EXISTS churn_risk, DROP COLUMN IF EXISTS churn_factors, DROP COLUMN IF EXISTS relationship_strength,
    DROP COLUMN IF EXISTS embedding;
ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role;
UPDATE users SET role = 'sales_rep' WHERE role IN ('account_executive', 'sdr', 'partner');
UPDATE users SET role = 'read_only' WHERE role = 'auditor';
ALTER TABLE users DROP COLUMN IF EXISTS manager_id, DROP COLUMN IF EXISTS is_active, DROP COLUMN IF EXISTS ical_token,
    DROP COLUMN IF EXISTS partner_id, ALTER COLUMN role SET DEFAULT 'sales_rep',
    ADD CONSTRAINT ck_users_role CHECK (role IN ('super_admin', 'sales_manager', 'sales_rep', 'read_only'));
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
