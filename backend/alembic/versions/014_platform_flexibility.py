"""Platform flexibility: custom objects, field-level security, validation rules, account sharing rules.

custom_field_definitions: entity widened for custom objects ("object:<key>"); ``access`` holds per-role
field security ({role: "read" | "hidden"}; absent = editable); ``updated_at`` lets the live report
catalogue notice access changes.

Revision ID: 014_platform_flexibility
Revises: 013_list_views_subscriptions
Create Date: 2026-09-27
"""
from alembic import op

revision = "014_platform_flexibility"
down_revision = "013_list_views_subscriptions"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
ALTER TABLE custom_field_definitions ALTER COLUMN entity TYPE VARCHAR(80);
ALTER TABLE custom_field_definitions ADD COLUMN access JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE custom_field_definitions ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TABLE custom_objects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key VARCHAR(40) NOT NULL UNIQUE,
    label VARCHAR(80) NOT NULL,
    plural_label VARCHAR(80) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE custom_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    object_id UUID NOT NULL REFERENCES custom_objects(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    account_id UUID REFERENCES accounts(id) ON DELETE SET NULL,
    owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_custom_records_object ON custom_records (object_id, created_at DESC);
CREATE INDEX ix_custom_records_account ON custom_records (account_id);

CREATE TABLE validation_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity VARCHAR(60) NOT NULL,
    name VARCHAR(150) NOT NULL,
    description TEXT,
    conditions JSONB NOT NULL DEFAULT '[]'::jsonb,
    message VARCHAR(300) NOT NULL,
    applies_on VARCHAR(10) NOT NULL DEFAULT 'both' CHECK (applies_on IN ('create', 'update', 'both')),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (entity, name)
);

CREATE TABLE sharing_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(150) NOT NULL UNIQUE,
    description TEXT,
    criteria JSONB NOT NULL DEFAULT '[]'::jsonb,
    roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS sharing_rules;
DROP TABLE IF EXISTS validation_rules;
DROP TABLE IF EXISTS custom_records;
DROP TABLE IF EXISTS custom_objects;
DELETE FROM custom_field_definitions WHERE length(entity) > 20;
ALTER TABLE custom_field_definitions DROP COLUMN IF EXISTS updated_at;
ALTER TABLE custom_field_definitions DROP COLUMN IF EXISTS access;
ALTER TABLE custom_field_definitions ALTER COLUMN entity TYPE VARCHAR(20);
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
