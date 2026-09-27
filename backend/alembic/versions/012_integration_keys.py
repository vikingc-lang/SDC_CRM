"""Integration keys: external_id on accounts, contacts and deals (leads already have one).

Lets outside systems (ERP, marketing automation, data warehouses) upsert records by their own ids through
/api/v1/upsert without creating duplicates.

Revision ID: 012_integration_keys
Revises: 011_developer_platform
Create Date: 2026-09-27
"""
from alembic import op

revision = "012_integration_keys"
down_revision = "011_developer_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("accounts", "contacts", "deals"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN external_id VARCHAR(200)")
        op.execute(f"CREATE UNIQUE INDEX ux_{table}_external_id ON {table} (external_id) WHERE external_id IS NOT NULL")


def downgrade() -> None:
    for table in ("accounts", "contacts", "deals"):
        op.execute(f"DROP INDEX IF EXISTS ux_{table}_external_id")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS external_id")
