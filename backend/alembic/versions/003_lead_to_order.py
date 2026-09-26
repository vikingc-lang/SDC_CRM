"""Lead-to-order: leads, scoring, routing, deal desk, redlining, orders and ERP sales orders.

Adds: leads with engagement events, assignment rules, intake keys (web forms /
webhooks) and app settings (ICP, scoring, qualification); account geography and
credit risk; Legal Counsel / Procurement buying roles; price books (list,
customer, regional), promotions, bundles and product rules; a sequential
approval chain (sales manager -> deal desk -> VP sales -> finance -> legal) with
approver groups; proposal / MSA / SLA / DPA documents with versions, redlines and
comments plus external e-signature envelopes; primary quotes, order readiness
fields on deals, and orders with billing schedules pushed to the ERP.

Revision ID: 003_lead_to_order
Revises: 002_enterprise_pillars
Create Date: 2026-09-26
"""
from alembic import op

revision = "003_lead_to_order"
down_revision = "002_enterprise_pillars"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
-- ===== Accounts: geography & credit risk ======================================
ALTER TABLE accounts
    ADD COLUMN country VARCHAR(64),
    ADD COLUMN region VARCHAR(40),
    ADD COLUMN credit_risk_score INT CHECK (credit_risk_score BETWEEN 0 AND 100),
    ADD COLUMN credit_risk_band VARCHAR(10) CHECK (credit_risk_band IN ('low', 'medium', 'high')),
    ADD COLUMN credit_risk_factors JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE contacts DROP CONSTRAINT IF EXISTS ck_contacts_buying_role;
ALTER TABLE contacts ADD CONSTRAINT ck_contacts_buying_role CHECK (buying_role IN
    ('Champion', 'Decision Maker', 'Economic Buyer', 'Blocker', 'Evaluator', 'Influencer', 'Legal Counsel', 'Procurement'));

-- ===== Leads ==================================================================
CREATE TABLE leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    email VARCHAR(255),
    phone VARCHAR(50),
    job_title VARCHAR(150),
    company_name VARCHAR(255),
    domain VARCHAR(255),
    industry VARCHAR(100),
    employee_count INT,
    annual_revenue NUMERIC(16, 2),
    country VARCHAR(64),
    region VARCHAR(40),
    source VARCHAR(20) NOT NULL DEFAULT 'manual'
        CHECK (source IN ('web_form', 'campaign', 'trade_show', 'partner', 'outbound', 'import', 'api', 'manual')),
    campaign VARCHAR(120),
    status VARCHAR(20) NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'working', 'mql', 'sql', 'converted', 'disqualified', 'recycled')),
    owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
    fit_score INT NOT NULL DEFAULT 0 CHECK (fit_score BETWEEN 0 AND 100),
    engagement_score INT NOT NULL DEFAULT 0 CHECK (engagement_score BETWEEN 0 AND 100),
    score INT NOT NULL DEFAULT 0 CHECK (score BETWEEN 0 AND 100),
    score_breakdown JSONB NOT NULL DEFAULT '{}'::jsonb,
    qualification_framework VARCHAR(10) NOT NULL DEFAULT 'bant' CHECK (qualification_framework IN ('bant', 'meddpicc')),
    qualification JSONB NOT NULL DEFAULT '{}'::jsonb,
    consent_email VARCHAR(20) NOT NULL DEFAULT 'unknown' CHECK (consent_email IN ('granted', 'denied', 'unknown')),
    privacy_regime VARCHAR(10) CHECK (privacy_regime IN ('GDPR', 'CCPA', 'OTHER')),
    consent_source VARCHAR(200),
    consent_at TIMESTAMPTZ,
    duplicate_matches JSONB NOT NULL DEFAULT '[]'::jsonb,
    enrichment JSONB NOT NULL DEFAULT '{}'::jsonb,
    enriched_at TIMESTAMPTZ,
    assignment_rule_id UUID,
    assigned_at TIMESTAMPTZ,
    mql_at TIMESTAMPTZ,
    converted_account_id UUID REFERENCES accounts(id) ON DELETE SET NULL,
    converted_contact_id UUID REFERENCES contacts(id) ON DELETE SET NULL,
    converted_deal_id UUID REFERENCES deals(id) ON DELETE SET NULL,
    converted_at TIMESTAMPTZ,
    converted_by UUID REFERENCES users(id) ON DELETE SET NULL,
    disqualified_reason VARCHAR(40),
    disqualify_note TEXT,
    external_id VARCHAR(200) UNIQUE,
    custom_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_leads_email ON leads (lower(email));
CREATE INDEX idx_leads_domain ON leads (domain);
CREATE INDEX idx_leads_status_owner ON leads (status, owner_id);

CREATE TABLE engagement_events (
    id BIGSERIAL PRIMARY KEY,
    lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
    contact_id UUID REFERENCES contacts(id) ON DELETE SET NULL,
    event_type VARCHAR(30) NOT NULL CHECK (event_type IN ('form_submit', 'content_download', 'pricing_page_visit', 'webinar_registered',
        'webinar_attended', 'email_open', 'email_click', 'trade_show_scan', 'meeting_booked', 'web_visit')),
    detail VARCHAR(500),
    points INT NOT NULL DEFAULT 0,
    source VARCHAR(40),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_engagement_lead ON engagement_events (lead_id, occurred_at DESC);
CREATE INDEX idx_engagement_contact ON engagement_events (contact_id, occurred_at DESC);

CREATE TABLE assignment_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(120) NOT NULL,
    priority INT NOT NULL DEFAULT 100,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    criteria JSONB NOT NULL DEFAULT '{}'::jsonb,
    method VARCHAR(20) NOT NULL DEFAULT 'round_robin' CHECK (method IN ('round_robin', 'account_owner', 'specific')),
    assignee_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    rr_index INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE intake_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(120) NOT NULL,
    kind VARCHAR(10) NOT NULL DEFAULT 'webhook' CHECK (kind IN ('web_form', 'webhook')),
    key_hash VARCHAR(64) NOT NULL UNIQUE,
    key_prefix VARCHAR(12) NOT NULL,
    source VARCHAR(20) NOT NULL DEFAULT 'api',
    campaign VARCHAR(120),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    last_used_at TIMESTAMPTZ,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE app_settings (
    key VARCHAR(60) PRIMARY KEY,
    value JSONB NOT NULL,
    updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ===== Deal desk: price books, promotions, bundles, rules =====================
CREATE TABLE price_books (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(120) NOT NULL,
    kind VARCHAR(10) NOT NULL CHECK (kind IN ('list', 'customer', 'regional')),
    account_id UUID REFERENCES accounts(id) ON DELETE CASCADE,
    region VARCHAR(40),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from DATE,
    valid_to DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((kind = 'customer') = (account_id IS NOT NULL)),
    CHECK ((kind = 'regional') = (region IS NOT NULL))
);
ALTER TABLE price_book_entries ADD COLUMN price_book_id UUID REFERENCES price_books(id) ON DELETE CASCADE;
ALTER TABLE price_book_entries DROP CONSTRAINT IF EXISTS price_book_entries_product_id_currency_key;
CREATE UNIQUE INDEX uq_price_list_entry ON price_book_entries (product_id, currency) WHERE price_book_id IS NULL;
CREATE UNIQUE INDEX uq_price_book_entry ON price_book_entries (product_id, currency, price_book_id) WHERE price_book_id IS NOT NULL;

CREATE TABLE promotions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(40) NOT NULL UNIQUE,
    name VARCHAR(120) NOT NULL,
    discount_pct NUMERIC(5, 2) NOT NULL CHECK (discount_pct > 0 AND discount_pct < 100),
    product_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    min_quantity NUMERIC(12, 2) NOT NULL DEFAULT 0,
    valid_from DATE,
    valid_to DATE,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE products ADD COLUMN product_type VARCHAR(10) NOT NULL DEFAULT 'standard' CHECK (product_type IN ('standard', 'bundle'));
CREATE TABLE bundle_components (
    bundle_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    component_id UUID NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    quantity NUMERIC(12, 2) NOT NULL DEFAULT 1 CHECK (quantity > 0),
    PRIMARY KEY (bundle_id, component_id),
    CHECK (bundle_id <> component_id)
);
CREATE TABLE product_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    rule_type VARCHAR(10) NOT NULL CHECK (rule_type IN ('requires', 'excludes')),
    target_product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    message VARCHAR(300),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (product_id <> target_product_id)
);

ALTER TABLE quotes
    ADD COLUMN price_book_id UUID REFERENCES price_books(id) ON DELETE SET NULL,
    ADD COLUMN promo_code VARCHAR(40),
    ADD COLUMN promo_discount_total NUMERIC(16, 2) NOT NULL DEFAULT 0,
    ADD COLUMN is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN locked_at TIMESTAMPTZ,
    ADD COLUMN custom_terms TEXT,
    ADD COLUMN billing_frequency VARCHAR(10) NOT NULL DEFAULT 'annual' CHECK (billing_frequency IN ('annual', 'quarterly', 'monthly'));
CREATE UNIQUE INDEX uq_quotes_primary_per_deal ON quotes (deal_id) WHERE is_primary;
ALTER TABLE quote_lines
    ADD COLUMN parent_line_id UUID REFERENCES quote_lines(id) ON DELETE CASCADE,
    ADD COLUMN is_included BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN promo_discount_pct NUMERIC(5, 2) NOT NULL DEFAULT 0,
    ADD COLUMN price_source VARCHAR(160) NOT NULL DEFAULT 'list';

-- ===== Approval chain ============================================================
ALTER TABLE approval_policies DROP CONSTRAINT IF EXISTS approval_policies_rule_type_check;
ALTER TABLE approval_policies ADD CONSTRAINT approval_policies_rule_type_check
    CHECK (rule_type IN ('discount_pct', 'payment_terms', 'credit_hold', 'tcv', 'custom_terms', 'credit_risk'));
ALTER TABLE approval_policies DROP CONSTRAINT IF EXISTS approval_policies_approver_role_check;
ALTER TABLE approval_policies ADD CONSTRAINT approval_policies_approver_role_check
    CHECK (approver_role IN ('sales_manager', 'deal_desk', 'vp_sales', 'finance', 'legal'));
ALTER TABLE approval_requests ADD COLUMN level INT NOT NULL DEFAULT 1;
CREATE TABLE approval_groups (
    key VARCHAR(30) PRIMARY KEY CHECK (key IN ('sales_manager', 'deal_desk', 'vp_sales', 'finance', 'legal')),
    name VARCHAR(80) NOT NULL,
    member_ids JSONB NOT NULL DEFAULT '[]'::jsonb
);

-- ===== Contracting: legal documents, redlines, external e-signature ==========
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_status_check;
ALTER TABLE documents ADD CONSTRAINT documents_status_check
    CHECK (status IN ('draft', 'in_negotiation', 'sent', 'partially_signed', 'completed', 'voided'));
ALTER TABLE document_templates DROP CONSTRAINT IF EXISTS document_templates_doc_type_check;
ALTER TABLE document_templates ADD CONSTRAINT document_templates_doc_type_check
    CHECK (doc_type IN ('nda', 'sow', 'order_form', 'proposal', 'msa', 'sla', 'dpa'));
ALTER TABLE documents
    ADD COLUMN current_version INT NOT NULL DEFAULT 1,
    ADD COLUMN esign_provider VARCHAR(20) NOT NULL DEFAULT 'builtin' CHECK (esign_provider IN ('builtin', 'docusign', 'adobe_sign')),
    ADD COLUMN envelope_id VARCHAR(120);
CREATE TABLE document_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version INT NOT NULL,
    body TEXT NOT NULL,
    content_sha256 VARCHAR(64) NOT NULL,
    note TEXT,
    source VARCHAR(10) NOT NULL DEFAULT 'internal' CHECK (source IN ('internal', 'customer')),
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    author_name VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (document_id, version)
);
CREATE TABLE document_comments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version INT NOT NULL,
    clause VARCHAR(200),
    body TEXT NOT NULL,
    party VARCHAR(10) NOT NULL CHECK (party IN ('customer', 'company')),
    author_name VARCHAR(200) NOT NULL,
    author_email VARCHAR(255),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    resolved BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_document_comments_doc ON document_comments (document_id, created_at);

-- ===== Closed-won readiness & orders ============================================
ALTER TABLE deals
    ADD COLUMN po_number VARCHAR(64),
    ADD COLUMN bill_to JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN ship_to JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN tax_exempt BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN tax_exempt_cert_id UUID REFERENCES attachments(id) ON DELETE SET NULL,
    ADD COLUMN requested_delivery_date DATE,
    ADD COLUMN incoterms VARCHAR(10),
    ADD COLUMN lead_id UUID REFERENCES leads(id) ON DELETE SET NULL;

CREATE TABLE orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(30) NOT NULL UNIQUE,
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    deal_id UUID REFERENCES deals(id) ON DELETE SET NULL,
    quote_id UUID REFERENCES quotes(id) ON DELETE SET NULL,
    contract_id UUID REFERENCES contracts(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'submitted', 'sent_to_erp', 'acknowledged', 'failed', 'cancelled')),
    currency VARCHAR(3) NOT NULL,
    po_number VARCHAR(64),
    payment_terms VARCHAR(20) NOT NULL,
    billing_frequency VARCHAR(10) NOT NULL DEFAULT 'annual',
    term_months INT NOT NULL DEFAULT 12,
    start_date DATE,
    requested_delivery_date DATE,
    incoterms VARCHAR(10),
    bill_to JSONB NOT NULL DEFAULT '{}'::jsonb,
    ship_to JSONB NOT NULL DEFAULT '{}'::jsonb,
    tax_exempt BOOLEAN NOT NULL DEFAULT FALSE,
    total NUMERIC(16, 2) NOT NULL DEFAULT 0,
    erp_order_id VARCHAR(64),
    erp_status VARCHAR(40),
    erp_message TEXT,
    erp_attempts INT NOT NULL DEFAULT 0,
    submitted_at TIMESTAMPTZ,
    erp_sent_at TIMESTAMPTZ,
    erp_acknowledged_at TIMESTAMPTZ,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX uq_orders_quote ON orders (quote_id) WHERE status <> 'cancelled';
CREATE TABLE order_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    line_no INT NOT NULL,
    parent_line_no INT,
    product_id UUID REFERENCES products(id) ON DELETE SET NULL,
    sku VARCHAR(64) NOT NULL,
    name VARCHAR(255) NOT NULL,
    quantity NUMERIC(12, 2) NOT NULL,
    unit_list_price NUMERIC(14, 4) NOT NULL,
    discount_pct NUMERIC(5, 2) NOT NULL DEFAULT 0,
    net_unit_price NUMERIC(14, 4) NOT NULL,
    line_total NUMERIC(16, 2) NOT NULL,
    billing_type VARCHAR(20) NOT NULL,
    billing_schedule JSONB NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE (order_id, line_no)
);
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS order_lines;
DROP TABLE IF EXISTS orders;
ALTER TABLE deals DROP COLUMN IF EXISTS po_number, DROP COLUMN IF EXISTS bill_to, DROP COLUMN IF EXISTS ship_to,
    DROP COLUMN IF EXISTS tax_exempt, DROP COLUMN IF EXISTS tax_exempt_cert_id, DROP COLUMN IF EXISTS requested_delivery_date,
    DROP COLUMN IF EXISTS incoterms, DROP COLUMN IF EXISTS lead_id;

DROP TABLE IF EXISTS document_comments;
DROP TABLE IF EXISTS document_versions;
ALTER TABLE documents DROP COLUMN IF EXISTS current_version, DROP COLUMN IF EXISTS esign_provider, DROP COLUMN IF EXISTS envelope_id;
DELETE FROM documents WHERE doc_type IN ('proposal', 'msa', 'sla', 'dpa');
DELETE FROM document_templates WHERE doc_type IN ('proposal', 'msa', 'sla', 'dpa');
ALTER TABLE document_templates DROP CONSTRAINT IF EXISTS document_templates_doc_type_check;
ALTER TABLE document_templates ADD CONSTRAINT document_templates_doc_type_check CHECK (doc_type IN ('nda', 'sow', 'order_form'));
UPDATE documents SET status = 'draft' WHERE status = 'in_negotiation';
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_status_check;
ALTER TABLE documents ADD CONSTRAINT documents_status_check CHECK (status IN ('draft', 'sent', 'partially_signed', 'completed', 'voided'));

DROP TABLE IF EXISTS approval_groups;
ALTER TABLE approval_requests DROP COLUMN IF EXISTS level;
DELETE FROM approval_policies WHERE approver_role NOT IN ('sales_manager', 'finance') OR rule_type IN ('custom_terms', 'credit_risk');
ALTER TABLE approval_policies DROP CONSTRAINT IF EXISTS approval_policies_approver_role_check;
ALTER TABLE approval_policies ADD CONSTRAINT approval_policies_approver_role_check CHECK (approver_role IN ('sales_manager', 'finance'));
ALTER TABLE approval_policies DROP CONSTRAINT IF EXISTS approval_policies_rule_type_check;
ALTER TABLE approval_policies ADD CONSTRAINT approval_policies_rule_type_check CHECK (rule_type IN ('discount_pct', 'payment_terms', 'credit_hold', 'tcv'));

ALTER TABLE quote_lines DROP COLUMN IF EXISTS parent_line_id, DROP COLUMN IF EXISTS is_included,
    DROP COLUMN IF EXISTS promo_discount_pct, DROP COLUMN IF EXISTS price_source;
DROP INDEX IF EXISTS uq_quotes_primary_per_deal;
ALTER TABLE quotes DROP COLUMN IF EXISTS price_book_id, DROP COLUMN IF EXISTS promo_code, DROP COLUMN IF EXISTS promo_discount_total,
    DROP COLUMN IF EXISTS is_primary, DROP COLUMN IF EXISTS locked_at, DROP COLUMN IF EXISTS custom_terms, DROP COLUMN IF EXISTS billing_frequency;
DROP TABLE IF EXISTS product_rules;
DROP TABLE IF EXISTS bundle_components;
ALTER TABLE products DROP COLUMN IF EXISTS product_type;
DROP TABLE IF EXISTS promotions;
DELETE FROM price_book_entries WHERE price_book_id IS NOT NULL;
DROP INDEX IF EXISTS uq_price_book_entry;
DROP INDEX IF EXISTS uq_price_list_entry;
ALTER TABLE price_book_entries DROP COLUMN IF EXISTS price_book_id;
ALTER TABLE price_book_entries ADD CONSTRAINT price_book_entries_product_id_currency_key UNIQUE (product_id, currency);
DROP TABLE IF EXISTS price_books;

DROP TABLE IF EXISTS app_settings;
DROP TABLE IF EXISTS intake_keys;
DROP TABLE IF EXISTS assignment_rules;
DROP TABLE IF EXISTS engagement_events;
DROP TABLE IF EXISTS leads;

UPDATE contacts SET buying_role = 'Evaluator' WHERE buying_role IN ('Legal Counsel', 'Procurement');
ALTER TABLE contacts DROP CONSTRAINT IF EXISTS ck_contacts_buying_role;
ALTER TABLE contacts ADD CONSTRAINT ck_contacts_buying_role
    CHECK (buying_role IN ('Champion', 'Decision Maker', 'Economic Buyer', 'Blocker', 'Evaluator', 'Influencer'));
ALTER TABLE accounts DROP COLUMN IF EXISTS country, DROP COLUMN IF EXISTS region, DROP COLUMN IF EXISTS credit_risk_score,
    DROP COLUMN IF EXISTS credit_risk_band, DROP COLUMN IF EXISTS credit_risk_factors;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
