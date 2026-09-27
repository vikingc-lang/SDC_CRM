"""Custom fields on leads and on custom objects: widen the entity check from migration 002.

Revision ID: 015_custom_field_entities
Revises: 014_platform_flexibility
Create Date: 2026-09-27
"""
from alembic import op

revision = "015_custom_field_entities"
down_revision = "014_platform_flexibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE custom_field_definitions DROP CONSTRAINT IF EXISTS custom_field_definitions_entity_check;
    ALTER TABLE custom_field_definitions ADD CONSTRAINT custom_field_definitions_entity_check
        CHECK (entity IN ('account', 'contact', 'deal', 'lead') OR entity ~ '^object:[a-z][a-z0-9_]{1,30}$');
    """)


def downgrade() -> None:
    op.execute("""
    DELETE FROM custom_field_definitions WHERE entity NOT IN ('account', 'contact', 'deal');
    ALTER TABLE custom_field_definitions DROP CONSTRAINT IF EXISTS custom_field_definitions_entity_check;
    ALTER TABLE custom_field_definitions ADD CONSTRAINT custom_field_definitions_entity_check CHECK (entity IN ('account', 'contact', 'deal'));
    """)
