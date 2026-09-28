"""Portable data model: timestamps the models require become NOT NULL, and document numbers come from a
counter table instead of a Postgres sequence / count(*)+1 (which could hand two concurrent creators the same number).

Revision ID: 017_portable_data_model
Revises: 016_service_marketing_depth
Create Date: 2026-09-28
"""
import re

import sqlalchemy as sa
from alembic import op

revision = "017_portable_data_model"
down_revision = "016_service_marketing_depth"
branch_labels = None
depends_on = None

NOT_NULL = """accounts.created_at accounts.updated_at activities.created_at activities.occurred_at approval_requests.created_at
attachments.created_at collateral.created_at collateral_downloads.created_at contacts.created_at contracts.created_at
custom_field_definitions.created_at deal_alerts.created_at deal_registrations.created_at deal_stage_history.changed_at
deals.created_at deals.stage_entered_at deals.updated_at dedup_dismissals.created_at document_templates.created_at
documents.created_at erp_sync_runs.started_at fx_rates.updated_at mailbox_connections.created_at merge_log.created_at
notifications.created_at onboarding_projects.created_at partners.created_at pipelines.created_at products.created_at
quotes.created_at quotes.updated_at signature_requests.created_at subject_keys.created_at tasks.created_at
users.created_at users.updated_at""".split()

# counter key -> (table, column, number pattern with the year and running number)
YEARLY = {
    "quote": ("quotes", "quote_number", re.compile(r"^Q-(\d{4})-(\d+)$")),
    "order": ("orders", "order_number", re.compile(r"^ORD-(\d{4})-(\d+)$")),
    "contract": ("contracts", "contract_number", re.compile(r"^CT-(\d{4})-(\d+)$")),
}


def upgrade() -> None:
    conn = op.get_bind()
    for tc in NOT_NULL:
        table, column = tc.split(".")
        conn.execute(sa.text(f"UPDATE {table} SET {column} = now() WHERE {column} IS NULL"))
        op.alter_column(table, column, nullable=False)

    counters = op.create_table(
        "number_sequences",
        sa.Column("name", sa.String(60), primary_key=True),
        sa.Column("value", sa.BigInteger, nullable=False, server_default="0"),
    )
    rows = []
    last_case = conn.execute(sa.text("SELECT CASE WHEN is_called THEN last_value ELSE last_value - 1 END FROM case_number_seq")).scalar()
    top_case = conn.execute(sa.text("SELECT max(CAST(substr(case_number, 4) AS INTEGER)) FROM support_tickets WHERE case_number LIKE 'CS-%'")).scalar()
    rows.append({"name": "case", "value": max(int(last_case or 1000), int(top_case or 0))})
    for key, (table, column, pattern) in YEARLY.items():
        top: dict[str, int] = {}
        for (number,) in conn.execute(sa.text(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL")):
            m = pattern.match(number)
            if m:
                top[m.group(1)] = max(top.get(m.group(1), 0), int(m.group(2)))
        rows += [{"name": f"{key}:{year}", "value": n} for year, n in sorted(top.items())]
    op.bulk_insert(counters, rows)
    op.execute("DROP SEQUENCE IF EXISTS case_number_seq")


def downgrade() -> None:
    conn = op.get_bind()
    last_case = conn.execute(sa.text("SELECT value FROM number_sequences WHERE name = 'case'")).scalar() or 1000
    op.execute(f"CREATE SEQUENCE case_number_seq START {int(last_case) + 1}")
    op.drop_table("number_sequences")
    for tc in NOT_NULL:
        table, column = tc.split(".")
        op.alter_column(table, column, nullable=True)
