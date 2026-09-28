"""Medium review findings: campaign email sends run in the background (with status), mailbox sync keeps a UID
cursor per folder, and workflow triggers are queued in the database so a restart never loses them.

Revision ID: 020_medium_findings
Revises: 019_high_findings
Create Date: 2026-09-28
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "020_medium_findings"
down_revision = "019_high_findings"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("send_status", sa.String(10)))
    op.add_column("campaigns", sa.Column("send_result", postgresql.JSONB))
    op.add_column("campaigns", sa.Column("send_requested_by", sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")))
    op.add_column("campaigns", sa.Column("send_requested_at", TZ))
    op.add_column("mailbox_connections", sa.Column("folder_state", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.create_table(
        "workflow_events",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("record_id", sa.Uuid, nullable=False),
        sa.Column("changed", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("depth", sa.Integer, nullable=False, server_default="0"),
        sa.Column("origin_rule_id", sa.Uuid),
        sa.Column("claimed_at", TZ),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_workflow_events_created", "workflow_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("workflow_events")
    op.drop_column("mailbox_connections", "folder_state")
    for c in ("send_requested_at", "send_requested_by", "send_result", "send_status"):
        op.drop_column("campaigns", c)
