"""Self-service reporting: a whitelisted field catalogue per data source, compiled to SQL.

A report definition never carries SQL. It names a source, fields from that source's catalogue,
filters with typed operators, up to two groupings (dates bucketed by week/month/quarter/year)
and aggregate measures. Every source applies the viewer's row-level scope and requires read
permission on the underlying resource, so a shared report shows each viewer only their records.
"""
from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import Boolean, Date, Numeric, and_, case, cast, func, literal, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.rbac import Principal
from app.models import (
    Account, Activity, Campaign, CampaignMember, Contact, Deal, FxRate, Lead, Order, Pipeline, PipelineStage, Quote, SupportQueue, SupportTicket, Task, Territory, User,
)

MAX_ROWS = 2000
MAX_GROUPS = 2
BUCKETS = ("day", "week", "month", "quarter", "year")
AGGS = ("count", "sum", "avg", "min", "max")
OPS = {
    "text": ("eq", "neq", "in", "not_in", "contains", "is_empty", "not_empty"),
    "enum": ("eq", "neq", "in", "not_in", "is_empty", "not_empty"),
    "number": ("eq", "neq", "gt", "gte", "lt", "lte", "between", "is_empty", "not_empty"),
    "money": ("eq", "neq", "gt", "gte", "lt", "lte", "between", "is_empty", "not_empty"),
    "date": ("on", "before", "after", "between", "within", "is_empty", "not_empty"),
    "bool": ("is_true", "is_false", "is_empty", "not_empty"),
}
RELATIVE = ("today", "yesterday", "this_week", "last_week", "this_month", "last_month", "this_quarter", "last_quarter",
            "next_quarter", "this_year", "last_year", "last_7_days", "last_30_days", "last_90_days", "next_30_days", "next_90_days")


class ReportError(ValueError):
    pass


@dataclass
class F:
    label: str
    type: str  # text | enum | number | money | date | bool
    expr: Any
    options: list[str] | None = None  # enum values offered in the filter picker
    groupable: bool = True


@dataclass
class Source:
    key: str
    label: str
    resource: str
    description: str
    fields: dict[str, F]
    base: Callable[[], Any]           # -> Select with FROM/JOINs, no columns yet
    scope: Callable[[Principal, Any], Any]
    default_columns: list[str] = field(default_factory=list)
    id_col: Any = None  # primary key of the source's base table (workflows match single records by it)


# ---- sources -------------------------------------------------------------------------------------

def _deals() -> Source:
    owner, stage, pipe, acct, fx = aliased(User), aliased(PipelineStage), aliased(Pipeline), aliased(Account), aliased(FxRate)
    terr = aliased(Territory)
    usd = Deal.amount * func.coalesce(fx.rate_to_usd, 1)
    status = case((stage.is_closed_won, literal("Won")), (stage.is_closed_lost, literal("Lost")), else_=literal("Open"))
    open_weight = case((or_(stage.is_closed_won, stage.is_closed_lost), 0), else_=stage.default_probability)
    fields = {
        "title": F("Opportunity", "text", Deal.title, groupable=False),
        "account": F("Account", "text", acct.name),
        "industry": F("Industry", "text", acct.industry),
        "region": F("Region", "enum", acct.region, ["NA", "EMEA", "APAC", "LATAM"]),
        "tier": F("Account tier", "enum", acct.tier, ["SMB", "Mid-Market", "Enterprise"]),
        "territory": F("Territory", "text", terr.name),
        "pipeline": F("Pipeline", "text", pipe.name),
        "stage": F("Stage", "text", stage.name),
        "status": F("Status", "enum", status, ["Open", "Won", "Lost"]),
        "owner": F("Owner", "text", owner.full_name),
        "deal_type": F("Type", "enum", Deal.deal_type, ["new_business", "renewal", "upsell", "partner"]),
        "source": F("Source", "text", Deal.source),
        "currency": F("Currency", "enum", Deal.currency, ["USD", "EUR", "GBP", "CAD", "AUD", "INR"]),
        "amount": F("Amount (deal currency)", "number", Deal.amount, groupable=False),
        "amount_usd": F("Amount (USD)", "money", usd, groupable=False),
        "weighted_usd": F("Weighted (USD)", "money", usd * open_weight / 100, groupable=False),
        "probability": F("Stage probability %", "number", stage.default_probability),
        "forecast_category": F("Forecast category", "enum",
                               case((stage.is_closed_won, literal("closed")), (stage.is_closed_lost, literal("omitted")),
                                    else_=func.coalesce(Deal.forecast_category, stage.forecast_category)),
                               ["closed", "commit", "best_case", "pipeline", "omitted"]),
        "risk_score": F("Risk score", "number", Deal.risk_score),
        "close_date": F("Target close", "date", Deal.target_close_date),
        "created": F("Created", "date", Deal.created_at),
        "closed": F("Closed", "date", Deal.closed_at),
        "close_date_pushes": F("Close-date pushes", "number", Deal.close_date_pushes),
        "loss_reason": F("Loss reason", "text", Deal.loss_reason),
        "competitor": F("Competitor", "text", Deal.loss_competitor),
    }
    base = lambda: (select().select_from(Deal).join(acct, acct.id == Deal.account_id).join(stage, stage.id == Deal.stage_id)  # noqa: E731
                    .join(pipe, pipe.id == Deal.pipeline_id).outerjoin(owner, owner.id == Deal.owner_id)
                    .outerjoin(fx, fx.currency == Deal.currency).outerjoin(terr, terr.id == acct.territory_id))
    return Source("deals", "Opportunities", "deals", "Deals in every pipeline, with stage, owner, amounts in USD and dates.",
                  fields, base, lambda p, s: p.scope_deals(s), ["title", "account", "stage", "owner", "amount_usd", "close_date"], id_col=Deal.id)


def _accounts() -> Source:
    owner, terr = aliased(User), aliased(Territory)
    fields = {
        "name": F("Account", "text", Account.name, groupable=False),
        "industry": F("Industry", "text", Account.industry),
        "tier": F("Tier", "enum", Account.tier, ["SMB", "Mid-Market", "Enterprise"]),
        "region": F("Region", "enum", Account.region, ["NA", "EMEA", "APAC", "LATAM"]),
        "country": F("Country", "text", Account.country),
        "territory": F("Territory", "text", terr.name),
        "lifecycle": F("Lifecycle stage", "enum", Account.lifecycle_stage, ["prospect", "customer", "churned", "partner"]),
        "owner": F("Owner", "text", owner.full_name),
        "health": F("Health score", "number", Account.health_score),
        "churn_risk": F("Churn risk", "number", Account.churn_risk),
        "annual_revenue": F("Annual revenue", "money", Account.annual_revenue, groupable=False),
        "employees": F("Employees", "number", Account.employee_count, groupable=False),
        "credit_hold": F("Credit hold", "bool", Account.credit_hold),
        "created": F("Created", "date", Account.created_at),
    }
    base = lambda: (select().select_from(Account).outerjoin(owner, owner.id == Account.owner_id)  # noqa: E731
                    .outerjoin(terr, terr.id == Account.territory_id))
    return Source("accounts", "Accounts", "accounts", "Customer and prospect accounts with health, churn risk and firmographics.",
                  fields, base, lambda p, s: p.scope_accounts(s), ["name", "industry", "tier", "owner", "health"], id_col=Account.id)


def _contacts() -> Source:
    acct = aliased(Account)
    fields = {
        "name": F("Contact", "text", Contact.first_name + " " + Contact.last_name, groupable=False),
        "account": F("Account", "text", acct.name),
        "job_title": F("Job title", "text", Contact.job_title),
        "department": F("Department", "text", Contact.department),
        "buying_role": F("Buying role", "enum", Contact.buying_role,
                         ["Champion", "Decision Maker", "Economic Buyer", "Blocker", "Evaluator", "Influencer", "Legal Counsel", "Procurement"]),
        "status": F("Status", "enum", Contact.status, ["active", "departed", "erased"]),
        "consent_email": F("Email consent", "enum", Contact.consent_email, ["granted", "denied", "unknown"]),
        "relationship": F("Relationship strength", "number", Contact.relationship_strength),
        "created": F("Created", "date", Contact.created_at),
    }
    base = lambda: select().select_from(Contact).join(acct, acct.id == Contact.account_id)  # noqa: E731
    return Source("contacts", "Contacts", "contacts", "People at your accounts with buying roles and consent.",
                  fields, base, lambda p, s: p.scope_accounts(s, "contacts", Contact.account_id), ["name", "account", "job_title", "buying_role"], id_col=Contact.id)


def _leads() -> Source:
    owner = aliased(User)
    fields = {
        "name": F("Lead", "text", func.concat_ws(" ", Lead.first_name, Lead.last_name), groupable=False),
        "company": F("Company", "text", Lead.company_name),
        "source": F("Source", "text", Lead.source),
        "campaign": F("Campaign", "text", Lead.campaign),
        "status": F("Status", "enum", Lead.status, ["new", "working", "mql", "sql", "converted", "disqualified", "recycled"]),
        "owner": F("Owner", "text", owner.full_name),
        "industry": F("Industry", "text", Lead.industry),
        "region": F("Region", "enum", Lead.region, ["NA", "EMEA", "APAC", "LATAM"]),
        "country": F("Country", "text", Lead.country),
        "score": F("Score", "number", Lead.score),
        "fit_score": F("Fit score", "number", Lead.fit_score),
        "engagement_score": F("Engagement score", "number", Lead.engagement_score),
        "disqualified_reason": F("Disqualify reason", "text", Lead.disqualified_reason),
        "created": F("Created", "date", Lead.created_at),
        "mql_at": F("Became MQL", "date", Lead.mql_at),
        "converted_at": F("Converted", "date", Lead.converted_at),
    }
    base = lambda: select().select_from(Lead).outerjoin(owner, owner.id == Lead.owner_id)  # noqa: E731

    def scope(p: Principal, s):
        return s.where(or_(Lead.owner_id == p.id, Lead.owner_id.is_(None))) if p.is_own_scope("leads") else s
    return Source("leads", "Leads", "leads", "Inbound and imported leads with scores, status and conversion dates.",
                  fields, base, scope, ["name", "company", "source", "status", "score", "owner"], id_col=Lead.id)


def _activities() -> Source:
    user, acct = aliased(User), aliased(Account)
    fields = {
        "type": F("Type", "enum", Activity.activity_type, ["meeting", "call", "note", "email", "system", "file", "document"]),
        "subject": F("Summary", "text", Activity.summary, groupable=False),
        "account": F("Account", "text", acct.name),
        "user": F("Logged by", "text", user.full_name),
        "sentiment": F("Sentiment", "enum", Activity.sentiment, ["positive", "neutral", "negative"]),
        "direction": F("Direction", "enum", Activity.direction, ["inbound", "outbound", "internal"]),
        "disposition": F("Call outcome", "text", Activity.disposition),
        "source": F("Source", "text", Activity.source),
        "duration_min": F("Duration (min)", "number", func.round(cast(Activity.duration_seconds / 60.0, Numeric), 1), groupable=False),
        "occurred": F("Occurred", "date", Activity.occurred_at),
    }
    base = lambda: (select().select_from(Activity).join(acct, acct.id == Activity.account_id)  # noqa: E731
                    .outerjoin(user, user.id == Activity.user_id))
    return Source("activities", "Activities", "activities", "Emails, calls, meetings and notes on the timeline.",
                  fields, base, lambda p, s: p.scope_accounts(s, "activities", Activity.account_id), ["occurred", "type", "account", "user", "subject"], id_col=Activity.id)


def _tasks() -> Source:
    owner, assignee, acct = aliased(User), aliased(User), aliased(Account)
    overdue = and_(Task.completed.is_(False), Task.due_date < func.current_date())
    fields = {
        "title": F("Task", "text", Task.title, groupable=False),
        "priority": F("Priority", "enum", Task.priority, ["low", "normal", "high", "urgent"]),
        "completed": F("Completed", "bool", Task.completed),
        "overdue": F("Overdue", "bool", overdue),
        "owner": F("Owner", "text", owner.full_name),
        "assignee": F("Assignee", "text", assignee.full_name),
        "account": F("Account", "text", acct.name),
        "escalation_level": F("Escalation level", "number", Task.escalation_level),
        "due": F("Due", "date", Task.due_date),
        "created": F("Created", "date", Task.created_at),
        "completed_at": F("Completed on", "date", Task.completed_at),
    }
    base = lambda: (select().select_from(Task).outerjoin(owner, owner.id == Task.owner_id)  # noqa: E731
                    .outerjoin(assignee, assignee.id == Task.assignee_id).outerjoin(acct, acct.id == Task.account_id))

    def scope(p: Principal, s):
        if not p.is_own_scope("tasks"):
            return s
        return s.where(or_(Task.owner_id == p.id, Task.assignee_id == p.id, Task.account_id.in_(p.owned_account_ids())))
    return Source("tasks", "Tasks", "tasks", "Follow-ups and to-dos with owners, due dates and SLA escalation.",
                  fields, base, scope, ["title", "priority", "owner", "due", "completed"], id_col=Task.id)


def _quotes() -> Source:
    acct, fx = aliased(Account), aliased(FxRate)
    deal = aliased(Deal)
    fields = {
        "quote_number": F("Quote #", "text", Quote.quote_number, groupable=False),
        "name": F("Quote", "text", Quote.name, groupable=False),
        "deal": F("Opportunity", "text", deal.title),
        "account": F("Account", "text", acct.name),
        "status": F("Status", "enum", Quote.status, ["draft", "pending_approval", "approved", "rejected", "sent", "accepted", "expired"]),
        "is_primary": F("Primary quote", "bool", Quote.is_primary),
        "currency": F("Currency", "enum", Quote.currency, ["USD", "EUR", "GBP", "CAD", "AUD", "INR"]),
        "acv_usd": F("ACV (USD)", "money", Quote.acv * func.coalesce(fx.rate_to_usd, 1), groupable=False),
        "tcv_usd": F("TCV (USD)", "money", Quote.tcv * func.coalesce(fx.rate_to_usd, 1), groupable=False),
        "discount_usd": F("Discount (USD)", "money", Quote.discount_total * func.coalesce(fx.rate_to_usd, 1), groupable=False),
        "max_discount_pct": F("Max line discount %", "number", Quote.max_discount_pct),
        "term_months": F("Term (months)", "number", Quote.term_months),
        "created": F("Created", "date", Quote.created_at),
        "approved": F("Approved", "date", Quote.approved_at),
    }
    base = lambda: (select().select_from(Quote).join(deal, deal.id == Quote.deal_id).join(acct, acct.id == deal.account_id)  # noqa: E731
                    .outerjoin(fx, fx.currency == Quote.currency))

    def scope(p: Principal, s):
        if not p.is_own_scope("quotes"):
            return s
        return s.where(or_(deal.owner_id == p.id, deal.account_id.in_(p.owned_account_ids())))
    return Source("quotes", "Quotes", "quotes", "CPQ quotes with ACV, TCV, discounts and approval status.",
                  fields, base, scope, ["quote_number", "account", "status", "acv_usd", "created"], id_col=Quote.id)


def _orders() -> Source:
    acct, fx = aliased(Account), aliased(FxRate)
    fields = {
        "order_number": F("Order #", "text", Order.order_number, groupable=False),
        "account": F("Account", "text", acct.name),
        "status": F("Status", "enum", Order.status, ["draft", "submitted", "sent_to_erp", "acknowledged", "failed", "cancelled"]),
        "erp_status": F("ERP status", "text", Order.erp_status),
        "billing_frequency": F("Billing", "enum", Order.billing_frequency, ["annual", "quarterly", "monthly"]),
        "currency": F("Currency", "enum", Order.currency, ["USD", "EUR", "GBP", "CAD", "AUD", "INR"]),
        "total_usd": F("Total (USD)", "money", Order.total * func.coalesce(fx.rate_to_usd, 1), groupable=False),
        "term_months": F("Term (months)", "number", Order.term_months),
        "created": F("Created", "date", Order.created_at),
        "submitted": F("Submitted", "date", Order.submitted_at),
    }
    base = lambda: (select().select_from(Order).join(acct, acct.id == Order.account_id)  # noqa: E731
                    .outerjoin(fx, fx.currency == Order.currency))
    return Source("orders", "Orders", "orders", "Sales orders raised at Closed-Won and their ERP hand-off.",
                  fields, base, lambda p, s: p.scope_accounts(s, "orders", Order.account_id), ["order_number", "account", "status", "total_usd", "created"], id_col=Order.id)


def _campaigns() -> Source:
    owner = aliased(User)
    members = select(func.count(CampaignMember.id)).where(CampaignMember.campaign_id == Campaign.id).scalar_subquery()
    responses = (select(func.count(CampaignMember.id)).where(CampaignMember.campaign_id == Campaign.id,
                                                             CampaignMember.status.in_(("responded", "registered", "attended"))).scalar_subquery())
    fields = {
        "name": F("Campaign", "text", Campaign.name, groupable=False),
        "type": F("Type", "enum", Campaign.campaign_type, ["email", "webinar", "event", "trade_show", "paid_ads", "content", "partner", "other"]),
        "status": F("Status", "enum", Campaign.status, ["planned", "active", "completed", "aborted"]),
        "owner": F("Owner", "text", owner.full_name),
        "budget": F("Budget", "money", Campaign.budget, groupable=False),
        "actual_cost": F("Actual cost", "money", Campaign.actual_cost, groupable=False),
        "expected_revenue": F("Expected revenue", "money", Campaign.expected_revenue, groupable=False),
        "members": F("Members", "number", members, groupable=False),
        "responses": F("Responses", "number", responses, groupable=False),
        "start": F("Start", "date", Campaign.start_date),
        "end": F("End", "date", Campaign.end_date),
    }
    base = lambda: select().select_from(Campaign).outerjoin(owner, owner.id == Campaign.owner_id)  # noqa: E731
    return Source("campaigns", "Campaigns", "campaigns", "Marketing campaigns with budget, cost, members and responses.",
                  fields, base, lambda p, s: s, ["name", "type", "status", "members", "responses", "actual_cost"], id_col=Campaign.id)


def _cases() -> Source:
    acct, owner, queue = aliased(Account), aliased(User), aliased(SupportQueue)
    # Postgres only has round(numeric, int): cast the epoch-seconds arithmetic before rounding to 0.1 h
    first_resp_h = func.round(cast(func.extract("epoch", SupportTicket.first_responded_at - SupportTicket.opened_at) / 3600.0, Numeric), 1)
    resolve_h = func.round(cast(func.extract("epoch", SupportTicket.resolved_at - SupportTicket.opened_at) / 3600.0, Numeric), 1)
    fields = {
        "case_number": F("Case #", "text", SupportTicket.case_number, groupable=False),
        "subject": F("Subject", "text", SupportTicket.subject, groupable=False),
        "account": F("Account", "text", acct.name),
        "owner": F("Owner", "text", owner.full_name),
        "queue": F("Queue", "text", queue.name),
        "status": F("Status", "enum", SupportTicket.status, ["open", "pending", "resolved", "closed"]),
        "priority": F("Priority", "enum", SupportTicket.severity, ["critical", "high", "medium", "low"]),
        "channel": F("Channel", "enum", SupportTicket.channel, ["email", "phone", "web", "portal", "chat"]),
        "category": F("Category", "text", SupportTicket.category),
        "sla_breached": F("SLA breached", "bool", SupportTicket.sla_breached),
        "csat": F("CSAT (1-5)", "number", SupportTicket.csat_score),
        "first_response_hours": F("Hours to first response", "number", first_resp_h, groupable=False),
        "resolution_hours": F("Hours to resolve", "number", resolve_h, groupable=False),
        "opened": F("Opened", "date", SupportTicket.opened_at),
        "resolved": F("Resolved", "date", SupportTicket.resolved_at),
    }
    base = lambda: (select().select_from(SupportTicket).join(acct, acct.id == SupportTicket.account_id)  # noqa: E731
                    .outerjoin(owner, owner.id == SupportTicket.owner_id).outerjoin(queue, queue.id == SupportTicket.queue_id))

    def scope(p: Principal, s):
        if not p.is_own_scope("cases"):
            return s
        return s.where(or_(SupportTicket.owner_id == p.id, SupportTicket.account_id.in_(p.owned_account_ids())))
    return Source("cases", "Cases", "cases", "Customer service cases with priority, SLA, response times and CSAT.",
                  fields, base, scope, ["case_number", "subject", "account", "priority", "status", "owner"], id_col=SupportTicket.id)


SOURCES: dict[str, Source] = {s.key: s for s in (_deals(), _accounts(), _contacts(), _leads(), _activities(), _tasks(), _quotes(), _orders(), _cases(), _campaigns())}


# ---- custom fields ---------------------------------------------------------------------------------
# Tenant-defined fields live in each record's JSONB column. They are merged into the static catalogue as
# "cf_<key>" fields, typed from their definition, so they can be filtered, grouped, summed, charted and used
# in workflow conditions like any built-in field. Refreshed every CF_TTL seconds and whenever an admin
# changes a definition (invalidate_custom_fields).
CF_TTL = 30.0  # safety net; changes are normally picked up through the definition signature below
CF_ENTITY = {"accounts": ("account", lambda: Account.custom_metadata), "contacts": ("contact", lambda: Contact.custom_fields),
             "deals": ("deal", lambda: Deal.custom_fields), "leads": ("lead", lambda: Lead.custom_fields)}
_live: dict[str, Source] = {}
_live_at = 0.0
_live_sig: tuple | None = None


def src_of(key: str) -> Source | None:
    """The source with its custom fields (static catalogue if custom fields haven't been loaded yet)."""
    return _live.get(key) or SOURCES.get(key)


def invalidate_custom_fields() -> None:
    global _live_at
    _live_at = 0.0


def _custom_expr(col, defn):
    """Typed read of a JSONB custom value. Values that don't parse (stored before the field was defined, or by a
    sync) read as empty instead of failing the whole query."""
    raw = col[defn.key].astext
    if defn.field_type == "number":
        return case((raw.op("~")(r"^\s*-?[0-9]+(\.[0-9]+)?\s*$"), cast(raw, Numeric)), else_=None)
    if defn.field_type == "date":
        return case((raw.op("~")(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}"), cast(func.substr(raw, 1, 10), Date)), else_=None)
    if defn.field_type == "boolean":
        return case((func.lower(raw) == "true", literal(True)), (func.lower(raw) == "false", literal(False)), else_=None)
    return raw


async def refresh_custom_fields(db: AsyncSession, force: bool = False) -> None:
    """Rebuild the live catalogue when the definitions change. A one-row signature query (count + latest
    creation) runs per call, so every API replica sees a new or deleted field on its very next request."""
    global _live, _live_at, _live_sig
    from app.models import CustomFieldDefinition

    sig = tuple((await db.execute(select(func.count(CustomFieldDefinition.id), func.max(CustomFieldDefinition.created_at)))).one())
    if not force and sig == _live_sig and time.monotonic() - _live_at < CF_TTL:
        return
    defs = (await db.execute(select(CustomFieldDefinition).order_by(CustomFieldDefinition.label))).scalars().all()
    live = {}
    for key, src in SOURCES.items():
        ent = CF_ENTITY.get(key)
        extra = {}
        if ent:
            for d in (x for x in defs if x.entity == ent[0]):
                typ = {"number": "number", "date": "date", "boolean": "bool", "select": "enum"}.get(d.field_type, "text")
                extra[f"cf_{d.key}"] = F(d.label, typ, _custom_expr(ent[1](), d), list(d.options or []) if typ == "enum" else None,
                                         groupable=typ in ("text", "enum", "date", "bool"))
        live[key] = replace(src, fields={**src.fields, **extra}) if extra else src
    _live, _live_at, _live_sig = live, time.monotonic(), sig


def catalogue(p: Principal) -> list[dict]:
    out = []
    for key in SOURCES:
        s = src_of(key)
        if not p.can(s.resource, "read"):
            continue
        out.append({"key": s.key, "label": s.label, "description": s.description, "default_columns": s.default_columns,
                    "fields": [{"key": k, "label": f.label, "type": f.type, "options": f.options, "groupable": f.groupable,
                                "ops": list(OPS[f.type])} for k, f in s.fields.items()]})
    return out


# ---- compile & run ------------------------------------------------------------------------------

def _period(name: str, today: date) -> tuple[date, date]:
    """Inclusive start, exclusive end."""
    q_start = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)

    def add_months(d: date, n: int) -> date:
        m = d.month - 1 + n
        return date(d.year + m // 12, m % 12 + 1, 1)
    week = today - timedelta(days=today.weekday())
    month = today.replace(day=1)
    table = {
        "today": (today, today + timedelta(days=1)),
        "yesterday": (today - timedelta(days=1), today),
        "this_week": (week, week + timedelta(days=7)),
        "last_week": (week - timedelta(days=7), week),
        "this_month": (month, add_months(month, 1)),
        "last_month": (add_months(month, -1), month),
        "this_quarter": (q_start, add_months(q_start, 3)),
        "last_quarter": (add_months(q_start, -3), q_start),
        "next_quarter": (add_months(q_start, 3), add_months(q_start, 6)),
        "this_year": (date(today.year, 1, 1), date(today.year + 1, 1, 1)),
        "last_year": (date(today.year - 1, 1, 1), date(today.year, 1, 1)),
        "last_7_days": (today - timedelta(days=6), today + timedelta(days=1)),
        "last_30_days": (today - timedelta(days=29), today + timedelta(days=1)),
        "last_90_days": (today - timedelta(days=89), today + timedelta(days=1)),
        "next_30_days": (today, today + timedelta(days=30)),
        "next_90_days": (today, today + timedelta(days=90)),
    }
    if name not in table:
        raise ReportError(f"Unknown period '{name}'")
    return table[name]


_UNIT = {"today": "day", "yesterday": "day", "this_week": "week", "last_week": "week", "this_month": "month", "last_month": "month",
         "this_quarter": "quarter", "last_quarter": "quarter", "next_quarter": "quarter", "this_year": "year", "last_year": "year"}
_PREV_LABEL = {"today": "yesterday", "yesterday": "the day before", "this_week": "last week", "this_month": "last month",
               "this_quarter": "last quarter", "next_quarter": "this quarter", "this_year": "last year"}


def previous_range(name: str, today: date) -> tuple[date, date, str]:
    """The period immediately before a relative period, as (inclusive start, exclusive end, label). Calendar periods
    step back one calendar unit (this month -> last month, whatever its length); rolling windows step back by
    their own length (last 30 days -> the 30 days before that)."""
    s, e = _period(name, today)
    unit = _UNIT.get(name)
    if unit is None:
        n = (e - s).days
        return s - timedelta(days=n), s, f"previous {n} days"
    if unit in ("day", "week"):
        step = timedelta(days=1 if unit == "day" else 7)
        return s - step, s, _PREV_LABEL.get(name, f"previous {unit}")
    m = s.month - 1 - {"month": 1, "quarter": 3, "year": 12}[unit]
    return date(s.year + m // 12, m % 12 + 1, 1), s, _PREV_LABEL.get(name, f"previous {unit}")


def _compare_filter(filters: list[dict]) -> int | None:
    """Index of the relative-date filter a comparison shifts: the last one (a dashboard's period comes last)."""
    idx = [i for i, f in enumerate(filters or []) if f.get("op") == "within"]
    return idx[-1] if idx else None


def previous_period_definition(defn: dict, today: date | None = None) -> tuple[dict, dict]:
    filters = list(defn.get("filters") or [])
    i = _compare_filter(filters)
    if i is None:
        raise ReportError("Comparing to the previous period needs a relative date filter, e.g. Created within this quarter")
    start, end, label = previous_range(str(filters[i].get("value")), today or date.today())
    filters[i] = {**filters[i], "op": "between", "value": [start.isoformat(), (end - timedelta(days=1)).isoformat()]}
    prev = {k: v for k, v in defn.items() if k != "compare"}
    return {**prev, "filters": filters}, {"label": label, "start": start.isoformat(), "end": (end - timedelta(days=1)).isoformat()}


def _as_date(v) -> date:
    try:
        return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])
    except ValueError as e:
        raise ReportError(f"'{v}' is not a date (use YYYY-MM-DD)") from e


def _as_num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError) as e:
        raise ReportError(f"'{v}' is not a number") from e


def _condition(f: F, op: str, value):
    if op not in OPS[f.type]:
        raise ReportError(f"'{op}' doesn't apply to {f.label}")
    e = f.expr
    if op == "is_empty":
        return or_(e.is_(None), e == "") if f.type == "text" else e.is_(None)
    if op == "not_empty":
        return and_(e.isnot(None), e != "") if f.type == "text" else e.isnot(None)
    if op == "is_true":
        return e.is_(True)
    if op == "is_false":
        return or_(e.is_(False), e.is_(None))
    if f.type == "date":
        d = cast(e, Date)
        if op == "within":
            start, end = _period(str(value), date.today())
            return and_(d >= start, d < end)
        if op == "between":
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ReportError("'between' needs a start and end date")
            return and_(d >= _as_date(value[0]), d <= _as_date(value[1]))
        v = _as_date(value)
        return {"on": d == v, "before": d < v, "after": d > v}[op]
    if f.type in ("number", "money"):
        if op == "between":
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ReportError("'between' needs a low and high value")
            return and_(e >= _as_num(value[0]), e <= _as_num(value[1]))
        v = _as_num(value)
        return {"eq": e == v, "neq": e != v, "gt": e > v, "gte": e >= v, "lt": e < v, "lte": e <= v}[op]
    # text / enum
    if op in ("in", "not_in"):
        vals = [str(x) for x in (value if isinstance(value, (list, tuple)) else str(value).split(","))]
        cond = e.in_(vals)
        return cond if op == "in" else or_(not_(cond), e.is_(None))
    if op == "contains":
        return e.ilike(f"%{str(value).replace('%', '').replace('_', ' ')}%")
    return e == str(value) if op == "eq" else or_(e != str(value), e.is_(None))


def _bucketed(f: F, bucket: str | None):
    if f.type != "date":
        return f.expr
    b = bucket or "month"
    if b not in BUCKETS:
        raise ReportError(f"Unknown date grouping '{b}'")
    return cast(func.date_trunc(b, f.expr), Date)


def validate(defn: dict) -> dict:
    src = src_of(defn.get("source") or "")
    if src is None:
        raise ReportError("Choose a data source")
    groups = defn.get("group_by") or []
    if len(groups) > MAX_GROUPS:
        raise ReportError(f"Group by at most {MAX_GROUPS} fields")
    for g in groups:
        f = src.fields.get(g.get("field"))
        if f is None or not f.groupable:
            raise ReportError(f"Can't group by '{g.get('field')}'")
    for m in defn.get("measures") or []:
        if m.get("agg") not in AGGS:
            raise ReportError(f"Unknown summary '{m.get('agg')}'")
        if m["agg"] != "count":
            f = src.fields.get(m.get("field"))
            if f is None or f.type not in ("number", "money"):
                raise ReportError(f"{m['agg'].title()} needs a number field")
    for c in defn.get("columns") or []:
        if c not in src.fields:
            raise ReportError(f"Unknown column '{c}'")
    for flt in defn.get("filters") or []:
        if flt.get("field") not in src.fields:
            raise ReportError(f"Unknown filter field '{flt.get('field')}'")
    if defn.get("compare"):
        if defn["compare"] != "previous_period":
            raise ReportError("Compare must be 'previous_period'")
        if not (groups or defn.get("measures")):
            raise ReportError("Only summaries can be compared with the previous period")
        if any(src.fields[g["field"]].type == "date" for g in groups):
            raise ReportError("Can't compare with the previous period while grouping by a date")
        if _compare_filter(defn.get("filters") or []) is None:
            raise ReportError("Comparing to the previous period needs a relative date filter, e.g. Created within this quarter")
    return defn


def _measure_key(m: dict) -> str:
    return "count" if m["agg"] == "count" else f"{m['agg']}_{m['field']}"


def _measure_label(src: Source, m: dict) -> str:
    if m["agg"] == "count":
        return f"Number of {src.label.lower()}"
    names = {"sum": "Total", "avg": "Average", "min": "Lowest", "max": "Highest"}
    return f"{names[m['agg']]} {src.fields[m['field']].label}"


def _json(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


async def run(db: AsyncSession, p: Principal, defn: dict) -> dict:
    await refresh_custom_fields(db)
    validate(defn)
    src = src_of(defn["source"])
    if not p.can(src.resource, "read"):
        raise ReportError(f"Your role can't read {src.label.lower()}")
    stmt = src.scope(p, src.base())
    for flt in defn.get("filters") or []:
        stmt = stmt.where(_condition(src.fields[flt["field"]], flt.get("op", "eq"), flt.get("value")))
    limit = min(int(defn.get("limit") or 500), MAX_ROWS)
    groups = defn.get("group_by") or []
    measures = defn.get("measures") or ([{"agg": "count"}] if groups else [])
    sort = defn.get("sort") or {}
    desc = sort.get("dir", "desc") == "desc"

    if groups or measures:
        dims = [(g["field"], _bucketed(src.fields[g["field"]], g.get("bucket")).label(g["field"])) for g in groups]
        meas = []
        for m in measures:
            expr = func.count() if m["agg"] == "count" else getattr(func, m["agg"])(src.fields[m["field"]].expr)
            meas.append((_measure_key(m), expr.label(_measure_key(m))))
        stmt = stmt.add_columns(*[d for _, d in dims], *[e for _, e in meas])
        if dims:
            stmt = stmt.group_by(*[d for _, d in dims])
        keys = {k: e for k, e in dims + meas}
        by = sort.get("by")
        if by in keys:
            order = keys[by].desc().nullslast() if desc else keys[by].asc().nullsfirst()
            stmt = stmt.order_by(order)
        elif dims and any(src.fields[g["field"]].type == "date" for g in groups):
            stmt = stmt.order_by(*[d.asc() for _, d in dims])
        elif meas:
            stmt = stmt.order_by(meas[0][1].desc().nullslast())
        cols = ([{"key": g["field"], "label": src.fields[g["field"]].label + (f" ({g.get('bucket') or 'month'})" if src.fields[g["field"]].type == "date" else ""),
                  "type": "date" if src.fields[g["field"]].type == "date" else src.fields[g["field"]].type, "role": "dimension",
                  "bucket": (g.get("bucket") or "month") if src.fields[g["field"]].type == "date" else None} for g in groups]
                + [{"key": _measure_key(m), "label": _measure_label(src, m),
                    "type": "number" if m["agg"] == "count" else src.fields[m["field"]].type, "role": "measure"} for m in measures])
    else:
        columns = defn.get("columns") or src.default_columns
        stmt = stmt.add_columns(*[src.fields[c].expr.label(c) for c in columns])
        if defn.get("with_ids") and src.id_col is not None:
            stmt = stmt.add_columns(src.id_col.label("_id"))
        by = sort.get("by")
        if by in src.fields:
            e = src.fields[by].expr
            stmt = stmt.order_by(e.desc().nullslast() if desc else e.asc().nullsfirst())
        cols = [{"key": c, "label": src.fields[c].label, "type": src.fields[c].type, "role": "column"} for c in columns]

    rows = (await db.execute(stmt.limit(limit + 1))).all()
    truncated = len(rows) > limit
    data = [[_json(v) for v in r] for r in rows[:limit]]
    out = {"source": src.key, "source_label": src.label, "columns": cols, "rows": data, "truncated": truncated,
           "row_count": len(data), "generated_at": datetime.now(timezone.utc).isoformat()}
    if defn.get("with_ids") and not (groups or measures) and src.id_col is not None:
        out["ids"] = [r.pop() for r in data]  # the trailing _id column
        out["link"] = LINKS.get(src.key)
    if defn.get("compare") and (groups or measures):
        prev_defn, window = previous_period_definition(defn)
        prev = await run(db, p, prev_defn)
        out["comparison"] = {**window, "rows": prev["rows"], "truncated": prev["truncated"]}
    return out


# ---- dashboard filters and drill-down ------------------------------------------------------------
# The date field a dashboard's period applies to, and where a record opens, per source.
DASH_DATE = {"deals": "close_date", "accounts": "created", "contacts": "created", "leads": "created", "activities": "occurred",
             "tasks": "due", "quotes": "created", "orders": "created", "cases": "opened", "campaigns": "start"}
LINKS = {"deals": "/deals/{id}", "accounts": "/accounts/{id}", "contacts": "/contacts/{id}", "leads": "/leads/{id}", "quotes": "/quotes/{id}",
         "orders": "/orders/{id}", "cases": "/cases/{id}", "campaigns": "/campaigns/{id}", "tasks": "/tasks"}


def with_dashboard_filters(defn: dict, period: str | None, owner: str | None) -> tuple[dict, list[str]]:
    """Add a dashboard's period / owner filters to one tile's definition where the source supports them.
    Returns the new definition and the filters that could not apply (so the tile can say so)."""
    src = src_of(defn.get("source") or "")
    if src is None:
        raise ReportError("Choose a data source")
    extra, skipped = [], []
    if period:
        if period not in RELATIVE:
            raise ReportError("Unknown period")
        date_field = DASH_DATE.get(src.key)
        if date_field and date_field in src.fields:
            extra.append({"field": date_field, "op": "within", "value": period})
        else:
            skipped.append("period")
    if owner:
        if "owner" in src.fields:
            extra.append({"field": "owner", "op": "eq", "value": owner})
        else:
            skipped.append("owner")
    return ({**defn, "filters": [*(defn.get("filters") or []), *extra]} if extra else defn), skipped


def _bucket_range(value: str, bucket: str) -> tuple[date, date]:
    start = date.fromisoformat(str(value)[:10])
    if bucket == "day":
        return start, start
    if bucket == "week":
        return start, start + timedelta(days=6)
    months = {"month": 1, "quarter": 3, "year": 12}[bucket]
    m = start.month - 1 + months
    return start, date(start.year + m // 12, m % 12 + 1, 1) - timedelta(days=1)


def drill_definition(defn: dict, values: list) -> dict:
    """The record list behind one cell of a summary: the report's filters plus one condition per grouping."""
    validate(defn)
    src = src_of(defn["source"])
    groups = defn.get("group_by") or []
    if len(values) > len(groups):
        raise ReportError("More values than groupings")
    filters = list(defn.get("filters") or [])
    for g, v in zip(groups, values):
        f = src.fields[g["field"]]
        if v is None or v == "":
            filters.append({"field": g["field"], "op": "is_empty"})
        elif f.type == "date":
            lo, hi = _bucket_range(v, g.get("bucket") or "month")
            filters.append({"field": g["field"], "op": "between", "value": [lo.isoformat(), hi.isoformat()]})
        elif f.type == "bool":
            filters.append({"field": g["field"], "op": "is_true" if v in (True, "true", "True", 1) else "is_false"})
        else:
            filters.append({"field": g["field"], "op": "eq", "value": v})
    group_cols = [g["field"] for g in groups if g["field"] not in src.default_columns]
    return {"source": src.key, "filters": filters, "columns": [*src.default_columns, *group_cols][:10], "limit": 500, "with_ids": True}


# ---- system-side helpers (workflows): no user scope, single records -----------------------------

def validate_filters(source: str, filters: list[dict]) -> None:
    src = src_of(source)
    if src is None:
        raise ReportError("Choose a record type")
    for flt in filters or []:
        f = src.fields.get(flt.get("field"))
        if f is None:
            raise ReportError(f"Unknown condition field '{flt.get('field')}'")
        _condition(f, flt.get("op", "eq"), flt.get("value"))  # raises on a bad operator or value


async def match_ids(db: AsyncSession, source: str, filters: list[dict], ids: list | None = None, limit: int = 500,
                    exclude=None) -> list:
    """Ids of records that satisfy every filter (optionally only among ``ids``, and never those in the ``exclude``
    subquery), in a stable order so successive batches make progress. Not scoped to a user."""
    await refresh_custom_fields(db)
    src = src_of(source)
    stmt = src.base().add_columns(src.id_col)
    for flt in filters or []:
        stmt = stmt.where(_condition(src.fields[flt["field"]], flt.get("op", "eq"), flt.get("value")))
    if ids is not None:
        if not ids:
            return []
        stmt = stmt.where(src.id_col.in_(ids))
    if exclude is not None:
        stmt = stmt.where(src.id_col.not_in(exclude))
    return [r[0] for r in (await db.execute(stmt.order_by(src.id_col).limit(limit)))]


def _plain(v, f: F) -> str:
    if v is None or v == "":
        return ""
    if f.type == "money":
        return f"${float(v):,.0f}"
    if f.type == "number":
        return f"{float(v):,.1f}".rstrip("0").rstrip(".")
    if f.type == "bool":
        return "Yes" if v else "No"
    if isinstance(v, (date, datetime)):
        return v.date().isoformat() if isinstance(v, datetime) else v.isoformat()
    return str(v)


async def record_values(db: AsyncSession, source: str, record_id) -> dict[str, str]:
    """Every catalogue field of one record, formatted as text (for message templates)."""
    await refresh_custom_fields(db)
    src = src_of(source)
    keys = list(src.fields)
    stmt = src.base().add_columns(*[src.fields[k].expr.label(k) for k in keys]).where(src.id_col == record_id)
    row = (await db.execute(stmt)).first()
    return {k: _plain(v, src.fields[k]) for k, v in zip(keys, row)} if row else {}


def to_csv(result: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c["label"] for c in result["columns"]])
    for r in result["rows"]:
        w.writerow(["" if v is None else v for v in r])
    return buf.getvalue()


# ---- starter content ----------------------------------------------------------------------------

STARTER_REPORTS = [
    ("Won this quarter", "Closed-Won revenue in the current quarter.",
     {"source": "deals", "filters": [{"field": "status", "op": "eq", "value": "Won"}, {"field": "closed", "op": "within", "value": "this_quarter"}],
      "measures": [{"agg": "sum", "field": "amount_usd"}], "chart": {"type": "number"}}),
    ("Open pipeline", "Total value of open opportunities.",
     {"source": "deals", "filters": [{"field": "status", "op": "eq", "value": "Open"}],
      "measures": [{"agg": "sum", "field": "amount_usd"}], "chart": {"type": "number"}}),
    ("Weighted pipeline", "Open value weighted by stage probability.",
     {"source": "deals", "filters": [{"field": "status", "op": "eq", "value": "Open"}],
      "measures": [{"agg": "sum", "field": "weighted_usd"}], "chart": {"type": "number"}}),
    ("Open pipeline by stage", "Where open value sits in the funnel.",
     {"source": "deals", "filters": [{"field": "status", "op": "eq", "value": "Open"}], "group_by": [{"field": "stage"}],
      "measures": [{"agg": "sum", "field": "amount_usd"}, {"agg": "count"}], "chart": {"type": "bar"}}),
    ("Weighted pipeline by close month", "Risk-free view of what should land when.",
     {"source": "deals", "filters": [{"field": "status", "op": "eq", "value": "Open"}], "group_by": [{"field": "close_date", "bucket": "month"}],
      "measures": [{"agg": "sum", "field": "weighted_usd"}], "chart": {"type": "column"}}),
    ("Pipeline by owner and stage", "Each rep's open pipeline, split by stage.",
     {"source": "deals", "filters": [{"field": "status", "op": "eq", "value": "Open"}], "group_by": [{"field": "owner"}, {"field": "stage"}],
      "measures": [{"agg": "sum", "field": "amount_usd"}], "chart": {"type": "stacked"}}),
    ("Leads by source (90 days)", "Where new leads came from in the last 90 days.",
     {"source": "leads", "filters": [{"field": "created", "op": "within", "value": "last_90_days"}], "group_by": [{"field": "source"}],
      "measures": [{"agg": "count"}], "chart": {"type": "bar"}}),
    ("Activity by week", "Logged emails, calls, meetings and notes per week.",
     {"source": "activities", "filters": [{"field": "occurred", "op": "within", "value": "last_90_days"}],
      "group_by": [{"field": "occurred", "bucket": "week"}], "measures": [{"agg": "count"}], "chart": {"type": "line"}}),
    ("At-risk open deals", "Open opportunities with a risk score of 60 or more.",
     {"source": "deals", "filters": [{"field": "status", "op": "eq", "value": "Open"}, {"field": "risk_score", "op": "gte", "value": 60}],
      "columns": ["title", "account", "owner", "stage", "amount_usd", "risk_score", "close_date"],
      "sort": {"by": "risk_score", "dir": "desc"}, "chart": {"type": "table"}}),
]
STARTER_LAYOUT = [("Won this quarter", "third"), ("Open pipeline", "third"), ("Weighted pipeline", "third"),
                  ("Open pipeline by stage", "half"), ("Weighted pipeline by close month", "half"),
                  ("Pipeline by owner and stage", "full"), ("Leads by source (90 days)", "half"), ("Activity by week", "half"),
                  ("At-risk open deals", "full")]


async def ensure_starter_content(db: AsyncSession, owner_id) -> bool:
    """Create the shared 'Sales overview' dashboard and its reports once (idempotent)."""
    from app.models import Dashboard, SavedReport

    if (await db.execute(select(Dashboard.id).where(Dashboard.name == "Sales overview"))).first():
        return False
    by_name = {}
    for name, desc, defn in STARTER_REPORTS:
        validate(defn)
        r = SavedReport(name=name, description=desc, owner_id=owner_id, source=defn["source"], definition=defn, visibility="shared")
        db.add(r)
        by_name[name] = r
    await db.flush()
    db.add(Dashboard(name="Sales overview", description="Pipeline, bookings, lead flow and activity at a glance.", owner_id=owner_id,
                     visibility="shared", tiles=[{"report_id": str(by_name[n].id), "size": s} for n, s in STARTER_LAYOUT]))
    return True
