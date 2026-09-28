"""P0 reach: a notification center with preferences, email digests and Web Push; the buying committee and org
chart; a behavioural event store with dynamic segments; pre-built connectors; and the tenant registry.

Revision ID: 021_p0_reach
Revises: 020_medium_findings
Create Date: 2026-09-28
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "021_p0_reach"
down_revision = "020_medium_findings"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)
EMPTY = sa.text("'{}'::jsonb")
COMMITTEE_ROLES = ("Economic Buyer", "Decision Maker", "Champion", "Technical Buyer", "Influencer", "Evaluator", "User",
                   "Legal Counsel", "Procurement", "Blocker")


def _in(col: str, values: tuple) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    # notification center
    op.add_column("users", sa.Column("notification_prefs", postgresql.JSONB, nullable=False, server_default=EMPTY))
    op.add_column("notifications", sa.Column("priority", sa.String(6), nullable=False, server_default="normal"))
    op.add_column("notifications", sa.Column("archived_at", TZ))
    op.add_column("notifications", sa.Column("snoozed_until", TZ))
    op.add_column("notifications", sa.Column("delivery", postgresql.JSONB, nullable=False, server_default=EMPTY))
    op.add_column("notifications", sa.Column("delivered_at", TZ))
    # notifications from before this release count as delivered (no email or push storm on upgrade)
    op.execute("UPDATE notifications SET delivered_at = created_at")
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("endpoint", sa.Text, nullable=False, unique=True),
        sa.Column("p256dh", sa.String(200), nullable=False),
        sa.Column("auth", sa.String(64), nullable=False),
        sa.Column("user_agent", sa.String(200)),
        sa.Column("failures", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_used_at", TZ),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_push_subscriptions_user", "push_subscriptions", ["user_id"])

    # org chart and buying committee
    op.add_column("contacts", sa.Column("reports_to_id", sa.Uuid, sa.ForeignKey("contacts.id", ondelete="SET NULL")))
    op.add_column("contacts", sa.Column("influence", sa.String(10)))
    op.add_column("contacts", sa.Column("stance", sa.String(12)))
    op.create_check_constraint("ck_contacts_influence", "contacts", _in("influence", ("high", "medium", "low")))
    op.create_check_constraint("ck_contacts_stance", "contacts", _in("stance", ("champion", "supporter", "neutral", "skeptic", "blocker")))
    op.create_check_constraint("ck_contacts_reports_to_self", "contacts", "reports_to_id <> id")
    op.create_index("ix_contacts_reports_to", "contacts", ["reports_to_id"])
    op.create_table(
        "deal_contacts",
        sa.Column("deal_id", sa.Uuid, sa.ForeignKey("deals.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("contact_id", sa.Uuid, sa.ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("influence", sa.String(10), nullable=False, server_default="medium"),
        sa.Column("stance", sa.String(12), nullable=False, server_default="neutral"),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("notes", sa.String(500)),
        sa.Column("added_at", TZ, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(_in("role", COMMITTEE_ROLES), name="ck_deal_contacts_role"),
        sa.CheckConstraint(_in("influence", ("high", "medium", "low")), name="ck_deal_contacts_influence"),
        sa.CheckConstraint(_in("stance", ("champion", "supporter", "neutral", "skeptic", "blocker")), name="ck_deal_contacts_stance"),
    )
    op.create_index("ix_deal_contacts_contact", "deal_contacts", ["contact_id"])

    # behavioural event store and segments
    op.create_table(
        "behavior_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("event", sa.String(60), nullable=False),
        sa.Column("anonymous_id", sa.String(64)),
        sa.Column("contact_id", sa.Uuid, sa.ForeignKey("contacts.id", ondelete="CASCADE")),
        sa.Column("lead_id", sa.Uuid, sa.ForeignKey("leads.id", ondelete="CASCADE")),
        sa.Column("account_id", sa.Uuid, sa.ForeignKey("accounts.id", ondelete="CASCADE")),
        sa.Column("url", sa.String(1000)),
        sa.Column("properties", postgresql.JSONB, nullable=False, server_default=EMPTY),
        sa.Column("source", sa.String(20), nullable=False, server_default="web"),
        sa.Column("occurred_at", TZ, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_behavior_events_contact", "behavior_events", ["contact_id", "occurred_at"])
    op.create_index("ix_behavior_events_lead", "behavior_events", ["lead_id", "occurred_at"])
    op.create_index("ix_behavior_events_anon", "behavior_events", ["anonymous_id"])
    op.create_index("ix_behavior_events_event", "behavior_events", ["event", "occurred_at"])
    op.create_table(
        "segments",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("object", sa.String(10), nullable=False),
        sa.Column("rules", postgresql.JSONB, nullable=False),
        sa.Column("member_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("refreshed_at", TZ),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("object IN ('contact', 'lead')", name="ck_segments_object"),
    )
    op.create_table(
        "segment_members",
        sa.Column("segment_id", sa.Uuid, sa.ForeignKey("segments.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("record_id", sa.Uuid, primary_key=True),
        sa.Column("entered_at", TZ, nullable=False, server_default=sa.text("now()")),
    )

    # pre-built connectors
    op.create_table(
        "connectors",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("config", postgresql.JSONB, nullable=False),
        sa.Column("secret", sa.Text),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("state", postgresql.JSONB, nullable=False, server_default=EMPTY),
        sa.Column("last_run_at", TZ),
        sa.Column("last_error", sa.String(500)),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.text("now()")),
    )

    # tenant registry (control plane; used in the primary database)
    op.create_table(
        "tenants",
        sa.Column("slug", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("db_name", sa.String(63), nullable=False, unique=True),
        sa.Column("hosts", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("max_users", sa.Integer),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="ck_tenants_status"),
    )


def downgrade() -> None:
    for t in ("tenants", "connectors", "segment_members", "segments", "behavior_events", "deal_contacts", "push_subscriptions"):
        op.drop_table(t)
    op.drop_index("ix_contacts_reports_to", "contacts")
    for c in ("ck_contacts_reports_to_self", "ck_contacts_stance", "ck_contacts_influence"):
        op.drop_constraint(c, "contacts")
    for c in ("stance", "influence", "reports_to_id"):
        op.drop_column("contacts", c)
    for c in ("delivered_at", "delivery", "snoozed_until", "archived_at", "priority"):
        op.drop_column("notifications", c)
    op.drop_column("users", "notification_prefs")
