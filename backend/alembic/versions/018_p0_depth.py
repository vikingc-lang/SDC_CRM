"""P0 depth: AI metering and trust log, AI agent actions with approval, resumable multi-step workflows,
opportunity products / deal teams / splits, dated exchange rates, tax rates and totals, two-way calendar sync,
and a per-user locale.

Revision ID: 018_p0_depth
Revises: 017_portable_data_model
Create Date: 2026-09-28
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "018_p0_depth"
down_revision = "017_portable_data_model"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)
JSONB = postgresql.JSONB
NOW = sa.text("now()")

# name, country, region, tax code, rate: starting points for the built-in engine (admins edit them)
TAX_RATES = [
    ("VAT", "GB", None, None, 20), ("VAT", "IE", None, None, 23), ("VAT", "DE", None, None, 19), ("VAT", "FR", None, None, 20),
    ("VAT", "NL", None, None, 21), ("VAT", "ES", None, None, 21), ("VAT", "IT", None, None, 22), ("GST", "AU", None, None, 10),
    ("GST", "NZ", None, None, 15), ("GST", "SG", None, None, 9), ("Consumption tax", "JP", None, None, 10), ("GST", "CA", None, None, 5),
    ("HST", "CA", "ON", None, 13), ("VAT", "AE", None, None, 5), ("GST", "IN", None, None, 18),
    ("Sales tax", "US", "NY", None, 4), ("Sales tax", "US", "TX", None, 6.25), ("Sales tax", "US", "WA", None, 6.5),
]


def upgrade() -> None:
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("feature", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.String(100)),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("pii_masked", sa.Integer, nullable=False, server_default="0"),
        sa.Column("injection_flags", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("prompt_excerpt", sa.Text),
        sa.Column("response_excerpt", sa.Text),
        sa.Column("detail", sa.String(300)),
        sa.Column("created_at", TZ, nullable=False, server_default=NOW),
    )
    op.create_index("ix_ai_usage_created", "ai_usage", ["created_at"])
    op.create_index("ix_ai_usage_user_created", "ai_usage", ["user_id", "created_at"])

    op.create_table(
        "ai_actions",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("agent", sa.String(40), nullable=False),
        sa.Column("action_type", sa.String(30), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.Uuid, nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("rationale", sa.Text),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("owner_id", sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("decided_by", sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("decided_at", TZ),
        sa.Column("result", sa.String(300)),
        sa.Column("created_at", TZ, nullable=False, server_default=NOW),
        sa.CheckConstraint("status IN ('pending', 'applied', 'rejected', 'failed', 'expired')", name="ck_ai_actions_status"),
        sa.CheckConstraint("mode IN ('auto', 'approve')", name="ck_ai_actions_mode"),
    )
    op.create_index("ix_ai_actions_status_owner", "ai_actions", ["status", "owner_id"])
    op.create_index("ix_ai_actions_entity", "ai_actions", ["entity_type", "entity_id"])

    op.add_column("workflow_runs", sa.Column("resume_at", TZ))
    op.add_column("workflow_runs", sa.Column("pending_actions", JSONB))
    op.create_index("ix_workflow_runs_waiting", "workflow_runs", ["status", "resume_at"])
    op.drop_constraint("workflow_runs_status_check", "workflow_runs", type_="check")
    op.create_check_constraint("workflow_runs_status_check", "workflow_runs", "status IN ('done', 'failed', 'dry_run', 'waiting', 'cancelled')")

    op.add_column("deals", sa.Column("amount_source", sa.String(6), nullable=False, server_default="manual"))
    op.create_table(
        "deal_line_items",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("deal_id", sa.Uuid, sa.ForeignKey("deals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Uuid, sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("description", sa.String(500)),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 4), nullable=False),
        sa.Column("discount_pct", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("term_months", sa.Integer, nullable=False, server_default="12"),
        sa.Column("total", sa.Numeric(16, 2), nullable=False),
        sa.Column("billing_type", sa.String(20), nullable=False),
        sa.Column("created_at", TZ, nullable=False, server_default=NOW),
        sa.CheckConstraint("quantity > 0", name="ck_deal_line_items_qty"),
        sa.CheckConstraint("discount_pct >= 0 AND discount_pct <= 100", name="ck_deal_line_items_discount"),
    )
    op.create_index("ix_deal_line_items_deal", "deal_line_items", ["deal_id"])
    op.create_table(
        "deal_team_members",
        sa.Column("deal_id", sa.Uuid, sa.ForeignKey("deals.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("access", sa.String(5), nullable=False, server_default="read"),
        sa.Column("added_at", TZ, nullable=False, server_default=NOW),
        sa.CheckConstraint("access IN ('read', 'edit')", name="ck_deal_team_access"),
    )
    op.create_index("ix_deal_team_user", "deal_team_members", ["user_id"])
    op.create_table(
        "deal_splits",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("deal_id", sa.Uuid, sa.ForeignKey("deals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("split_type", sa.String(8), nullable=False),
        sa.Column("percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("created_at", TZ, nullable=False, server_default=NOW),
        sa.CheckConstraint("split_type IN ('revenue', 'overlay')", name="ck_deal_splits_type"),
        sa.CheckConstraint("percent > 0 AND percent <= 100", name="ck_deal_splits_percent"),
        sa.UniqueConstraint("deal_id", "user_id", "split_type", name="uq_deal_splits"),
    )
    op.create_index("ix_deal_splits_user", "deal_splits", ["user_id"])

    op.create_table(
        "fx_rate_history",
        sa.Column("currency", sa.String(3), primary_key=True),
        sa.Column("effective_date", sa.Date, primary_key=True),
        sa.Column("rate_to_usd", sa.Numeric(14, 6), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("created_at", TZ, nullable=False, server_default=NOW),
    )
    # today's rates apply to all earlier dates until real history is added
    op.execute("INSERT INTO fx_rate_history (currency, effective_date, rate_to_usd, source) "
               "SELECT currency, DATE '2000-01-01', rate_to_usd, 'initial' FROM fx_rates")

    tax = op.create_table(
        "tax_rates",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("region", sa.String(10)),
        sa.Column("tax_code", sa.String(20)),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("rate", sa.Numeric(6, 3), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.CheckConstraint("rate >= 0 AND rate <= 100", name="ck_tax_rates_rate"),
    )
    op.create_index("ix_tax_rates_country", "tax_rates", ["country"])
    import uuid

    op.bulk_insert(tax, [{"id": uuid.uuid4(), "name": n, "country": c, "region": r, "tax_code": t, "rate": rate} for n, c, r, t, rate in TAX_RATES])
    op.add_column("products", sa.Column("tax_code", sa.String(20)))
    for table in ("quotes", "orders"):
        op.add_column(table, sa.Column("tax_total", sa.Numeric(16, 2), nullable=False, server_default="0"))
        op.add_column(table, sa.Column("tax_detail", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))

    op.create_table(
        "calendar_connections",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(12), nullable=False),
        sa.Column("account_email", sa.String(255)),
        sa.Column("calendar_id", sa.String(255), nullable=False, server_default="primary"),
        sa.Column("token_encrypted", sa.Text, nullable=False),
        sa.Column("token_expires_at", TZ),
        sa.Column("sync_token", sa.Text),
        sa.Column("status", sa.String(12), nullable=False, server_default="active"),
        sa.Column("last_synced_at", TZ),
        sa.Column("last_error", sa.String(500)),
        sa.Column("created_at", TZ, nullable=False, server_default=NOW),
        sa.CheckConstraint("provider IN ('google', 'microsoft')", name="ck_calendar_connections_provider"),
        sa.UniqueConstraint("user_id", "provider", name="uq_calendar_connections_user_provider"),
    )
    op.create_table(
        "calendar_links",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("connection_id", sa.Uuid, sa.ForeignKey("calendar_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("remote_id", sa.String(255), nullable=False),
        sa.Column("activity_id", sa.Uuid, sa.ForeignKey("activities.id", ondelete="SET NULL")),
        sa.Column("remote_etag", sa.String(255)),
        sa.Column("local_hash", sa.String(64)),
        sa.Column("synced_at", TZ, nullable=False, server_default=NOW),
        sa.UniqueConstraint("connection_id", "remote_id", name="uq_calendar_links_remote"),
    )
    op.create_index("ix_calendar_links_activity", "calendar_links", ["activity_id"])

    op.add_column("users", sa.Column("locale", sa.String(10)))
    op.add_column("users", sa.Column("timezone", sa.String(64)))


def downgrade() -> None:
    op.drop_column("users", "timezone")
    op.drop_column("users", "locale")
    op.drop_table("calendar_links")
    op.drop_table("calendar_connections")
    for table in ("quotes", "orders"):
        op.drop_column(table, "tax_detail")
        op.drop_column(table, "tax_total")
    op.drop_column("products", "tax_code")
    op.drop_table("tax_rates")
    op.drop_table("fx_rate_history")
    op.drop_table("deal_splits")
    op.drop_table("deal_team_members")
    op.drop_table("deal_line_items")
    op.drop_column("deals", "amount_source")
    op.execute("UPDATE workflow_runs SET status = 'failed' WHERE status IN ('waiting', 'cancelled')")
    op.drop_constraint("workflow_runs_status_check", "workflow_runs", type_="check")
    op.create_check_constraint("workflow_runs_status_check", "workflow_runs", "status IN ('done', 'failed', 'dry_run')")
    op.drop_index("ix_workflow_runs_waiting", "workflow_runs")
    op.drop_column("workflow_runs", "pending_actions")
    op.drop_column("workflow_runs", "resume_at")
    op.drop_table("ai_actions")
    op.drop_table("ai_usage")
