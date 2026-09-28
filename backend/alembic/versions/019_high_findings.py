"""High review findings: customer signing links expire (and can be re-sent).

Revision ID: 019_high_findings
Revises: 018_p0_depth
Create Date: 2026-09-28
"""
import sqlalchemy as sa
from alembic import op

revision = "019_high_findings"
down_revision = "018_p0_depth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("signature_requests", sa.Column("expires_at", sa.DateTime(timezone=True)))
    # links already out get the standard window from today rather than expiring at once
    op.execute("UPDATE signature_requests SET expires_at = now() + interval '14 days' WHERE status = 'pending' AND signer_party = 'customer'")


def downgrade() -> None:
    op.drop_column("signature_requests", "expires_at")
