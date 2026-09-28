"""Schema rules the migrations create that the model classes don't declare inline (generated from the
PostgreSQL catalog by the portability tooling; edit alongside any migration that changes them).

Everything here is expressed portably so ``Base.metadata`` describes the complete schema for any database:
* check constraints in plain SQL (``IN (...)`` instead of ``= ANY(ARRAY[...])``, boolean logic spelled out);
* unique constraints and indexes under the database's own names;
* partial indexes carry their ``WHERE`` for databases that support them (PostgreSQL, SQLite, SQL Server);
  "IS NOT NULL" partial unique indexes become plain unique indexes elsewhere (NULLs never collide there);
* Postgres-only access methods (trigram, HNSW vector, GIN full-text) are raw DDL run only on PostgreSQL.

The rules are attached to ``Base.metadata`` by ``apply()`` (called from ``app.models``); ``tests/test_portability.py``
fails if the models plus these rules ever drift from the migrated PostgreSQL schema.
"""
from sqlalchemy import CheckConstraint, DDL, Index, MetaData, UniqueConstraint, and_, event, func, text

PARTIAL_INDEX_DIALECTS = ("postgresql", "sqlite", "mssql")  # support CREATE INDEX ... WHERE

CHECKS = [
    ('accounts', 'accounts_churn_risk_check', '(churn_risk >= 0) AND (churn_risk <= 100)'),
    ('accounts', 'accounts_credit_risk_band_check', "credit_risk_band IN ('low', 'medium', 'high')"),
    ('accounts', 'accounts_credit_risk_score_check', '(credit_risk_score >= 0) AND (credit_risk_score <= 100)'),
    ('accounts', 'accounts_lifecycle_stage_check', "lifecycle_stage IN ('prospect', 'customer', 'churned', 'partner')"),
    ('activities', 'activities_attendance_check', "attendance IN ('attended', 'no_show', 'cancelled', 'scheduled')"),
    ('activities', 'activities_direction_check', "direction IN ('inbound', 'outbound', 'internal')"),
    ('activities', 'activities_disposition_check', "disposition IN ('connected', 'left_voicemail', 'gatekeeper', 'no_answer', 'busy', 'wrong_number')"),
    ('agent_presence', 'agent_presence_capacity_check', '(capacity >= 1) AND (capacity <= 50)'),
    ('agent_presence', 'agent_presence_status_check', "status IN ('available', 'busy', 'away', 'offline')"),
    ('approval_groups', 'approval_groups_key_check', "key IN ('sales_manager', 'deal_desk', 'vp_sales', 'finance', 'legal')"),
    ('approval_policies', 'approval_policies_approver_role_check', "approver_role IN ('sales_manager', 'deal_desk', 'vp_sales', 'finance', 'legal')"),
    ('approval_policies', 'approval_policies_rule_type_check', "rule_type IN ('discount_pct', 'payment_terms', 'credit_hold', 'tcv', 'custom_terms', 'credit_risk')"),
    ('approval_requests', 'approval_requests_status_check', "status IN ('pending', 'approved', 'rejected', 'superseded')"),
    ('assignment_rules', 'assignment_rules_method_check', "method IN ('round_robin', 'account_owner', 'specific')"),
    ('bundle_components', 'bundle_components_check', 'bundle_id <> component_id'),
    ('bundle_components', 'bundle_components_quantity_check', 'quantity > 0'),
    ('campaign_members', 'campaign_members_check', '(lead_id IS NULL AND contact_id IS NOT NULL) OR (lead_id IS NOT NULL AND contact_id IS NULL)'),
    ('campaign_members', 'campaign_members_source_check', "source IN ('manual', 'filter', 'capture')"),
    ('campaign_members', 'campaign_members_status_check', "status IN ('targeted', 'sent', 'responded', 'registered', 'attended', 'unsubscribed', 'bounced')"),
    ('campaigns', 'campaigns_actual_cost_check', 'actual_cost >= 0'),
    ('campaigns', 'campaigns_budget_check', 'budget >= 0'),
    ('campaigns', 'campaigns_campaign_type_check', "campaign_type IN ('email', 'webinar', 'event', 'trade_show', 'paid_ads', 'content', 'partner', 'other')"),
    ('campaigns', 'campaigns_check', '(end_date IS NULL) OR (start_date IS NULL) OR (end_date >= start_date)'),
    ('campaigns', 'campaigns_expected_revenue_check', 'expected_revenue >= 0'),
    ('campaigns', 'campaigns_status_check', "status IN ('planned', 'active', 'completed', 'aborted')"),
    ('collateral', 'collateral_category_check', "category IN ('deck', 'whitepaper', 'battlecard', 'price_list', 'case_study')"),
    ('commission_plans', 'commission_plans_base_rate_check', '(base_rate >= 0) AND (base_rate <= 100)'),
    ('contacts', 'contacts_consent_email_check', "consent_email IN ('granted', 'denied', 'unknown')"),
    ('contacts', 'contacts_privacy_regime_check', "privacy_regime IN ('GDPR', 'CCPA', 'OTHER')"),
    ('contacts', 'contacts_status_check', "status IN ('active', 'departed', 'erased')"),
    ('contracts', 'contracts_status_check', "status IN ('active', 'expired', 'renewed', 'terminated')"),
    ('custom_field_definitions', 'custom_field_definitions_entity_check', "entity IN ('account', 'contact', 'deal', 'lead') OR entity LIKE 'object:%'"),
    ('custom_field_definitions', 'custom_field_definitions_field_type_check', "field_type IN ('text', 'number', 'date', 'select', 'boolean', 'url')"),
    ('dashboards', 'dashboards_visibility_check', "visibility IN ('private', 'shared')"),
    ('deal_alerts', 'deal_alerts_kind_check', "kind IN ('stagnant', 'close_date_pushed', 'sentiment_drift', 'missing_roles', 'overdue_close')"),
    ('deal_alerts', 'deal_alerts_severity_check', "severity IN ('low', 'medium', 'high')"),
    ('deal_partners', 'deal_partners_role_check', "role IN ('referral', 'co_sell', 'resell')"),
    ('deal_partners', 'deal_partners_split_pct_check', '(split_pct >= 0) AND (split_pct <= 100)'),
    ('deal_registrations', 'deal_registrations_status_check', "status IN ('submitted', 'approved', 'rejected', 'expired')"),
    ('deals', 'deals_deal_type_check', "deal_type IN ('new_business', 'renewal', 'upsell', 'partner')"),
    ('deals', 'deals_forecast_category_check', "forecast_category IN ('pipeline', 'best_case', 'commit', 'omitted')"),
    ('document_comments', 'document_comments_party_check', "party IN ('customer', 'company')"),
    ('documents', 'documents_esign_provider_check', "esign_provider IN ('builtin', 'docusign', 'adobe_sign')"),
    ('documents', 'documents_status_check', "status IN ('draft', 'in_negotiation', 'sent', 'partially_signed', 'completed', 'voided')"),
    ('document_templates', 'document_templates_doc_type_check', "doc_type IN ('nda', 'sow', 'order_form', 'proposal', 'msa', 'sla', 'dpa')"),
    ('document_versions', 'document_versions_source_check', "source IN ('internal', 'customer')"),
    ('email_events', 'email_events_kind_check', "kind IN ('open', 'click')"),
    ('engagement_events', 'engagement_events_event_type_check', "event_type IN ('form_submit', 'content_download', 'pricing_page_visit', 'webinar_registered', 'webinar_attended', 'email_open', 'email_click', 'trade_show_scan', 'meeting_booked', 'web_visit')"),
    ('erp_sync_runs', 'erp_sync_runs_direction_check', "direction IN ('inbound', 'outbound')"),
    ('forecast_adjustments', 'forecast_adjustments_best_case_amount_check', 'best_case_amount >= 0'),
    ('forecast_adjustments', 'forecast_adjustments_commit_amount_check', 'commit_amount >= 0'),
    ('forecast_submissions', 'forecast_submissions_best_case_amount_check', 'best_case_amount >= 0'),
    ('forecast_submissions', 'forecast_submissions_commit_amount_check', 'commit_amount >= 0'),
    ('forecast_submissions', 'forecast_submissions_scope_check', "scope IN ('self', 'team')"),
    ('inbound_emails', 'inbound_emails_status_check', "status IN ('case_created', 'appended', 'unmatched', 'ignored', 'converted', 'dismissed')"),
    ('intake_keys', 'intake_keys_kind_check', "kind IN ('web_form', 'webhook')"),
    ('invoices', 'invoices_status_check', "status IN ('open', 'paid', 'void')"),
    ('journey_enrollments', 'journey_enrollments_status_check', "status IN ('active', 'completed', 'exited')"),
    ('journeys', 'journeys_status_check', "status IN ('draft', 'active', 'paused', 'archived')"),
    ('kb_articles', 'kb_articles_status_check', "status IN ('draft', 'published')"),
    ('leads', 'leads_consent_email_check', "consent_email IN ('granted', 'denied', 'unknown')"),
    ('leads', 'leads_engagement_score_check', '(engagement_score >= 0) AND (engagement_score <= 100)'),
    ('leads', 'leads_fit_score_check', '(fit_score >= 0) AND (fit_score <= 100)'),
    ('leads', 'leads_privacy_regime_check', "privacy_regime IN ('GDPR', 'CCPA', 'OTHER')"),
    ('leads', 'leads_qualification_framework_check', "qualification_framework IN ('bant', 'meddpicc')"),
    ('leads', 'leads_score_check', '(score >= 0) AND (score <= 100)'),
    ('leads', 'leads_source_check', "source IN ('web_form', 'campaign', 'trade_show', 'partner', 'outbound', 'import', 'api', 'manual')"),
    ('leads', 'leads_status_check', "status IN ('new', 'working', 'mql', 'sql', 'converted', 'disqualified', 'recycled')"),
    ('list_views', 'list_views_visibility_check', "visibility IN ('private', 'shared')"),
    ('mailbox_connections', 'mailbox_connections_provider_check', "provider IN ('imap', 'microsoft_graph', 'google')"),
    ('onboarding_milestones', 'onboarding_milestones_status_check', "status IN ('pending', 'in_progress', 'done', 'blocked')"),
    ('onboarding_projects', 'onboarding_projects_status_check', "status IN ('not_started', 'in_progress', 'at_risk', 'completed')"),
    ('orders', 'orders_status_check', "status IN ('draft', 'submitted', 'sent_to_erp', 'acknowledged', 'failed', 'cancelled')"),
    ('partners', 'partners_partner_type_check', "partner_type IN ('distributor', 'agency', 'reseller', 'referral', 'technology')"),
    ('partners', 'partners_status_check', "status IN ('active', 'inactive')"),
    ('partners', 'partners_tier_check', "tier IN ('registered', 'silver', 'gold', 'platinum')"),
    ('pipelines', 'pipelines_kind_check', "kind IN ('direct', 'inbound', 'renewal', 'partner')"),
    ('pipeline_stages', 'pipeline_stages_forecast_category_check', "forecast_category IN ('pipeline', 'best_case', 'commit', 'closed', 'omitted')"),
    ('price_books', 'price_books_check', "(kind = 'customer' AND account_id IS NOT NULL) OR (kind <> 'customer' AND account_id IS NULL)"),
    ('price_books', 'price_books_check1', "(kind = 'regional' AND region IS NOT NULL) OR (kind <> 'regional' AND region IS NULL)"),
    ('price_books', 'price_books_kind_check', "kind IN ('list', 'customer', 'regional')"),
    ('product_rules', 'product_rules_check', 'product_id <> target_product_id'),
    ('product_rules', 'product_rules_rule_type_check', "rule_type IN ('requires', 'excludes')"),
    ('products', 'products_billing_type_check', "billing_type IN ('recurring', 'one_time')"),
    ('products', 'products_product_type_check', "product_type IN ('standard', 'bundle')"),
    ('promotions', 'promotions_discount_pct_check', '(discount_pct > 0) AND (discount_pct < 100)'),
    ('quotas', 'quotas_amount_check', 'amount >= 0'),
    ('quote_lines', 'quote_lines_discount_pct_check', '(discount_pct >= 0) AND (discount_pct <= 100)'),
    ('quote_lines', 'quote_lines_quantity_check', 'quantity > 0'),
    ('quotes', 'quotes_billing_frequency_check', "billing_frequency IN ('annual', 'quarterly', 'monthly')"),
    ('quotes', 'quotes_status_check', "status IN ('draft', 'pending_approval', 'approved', 'rejected', 'sent', 'accepted', 'expired')"),
    ('quotes', 'quotes_term_months_check', '(term_months >= 1) AND (term_months <= 120)'),
    ('report_subscriptions', 'report_subscriptions_day_of_month_check', '(day_of_month >= 1) AND (day_of_month <= 28)'),
    ('report_subscriptions', 'report_subscriptions_frequency_check', "frequency IN ('daily', 'weekly', 'monthly')"),
    ('report_subscriptions', 'report_subscriptions_hour_check', '(hour >= 0) AND (hour <= 23)'),
    ('report_subscriptions', 'report_subscriptions_weekday_check', '(weekday >= 0) AND (weekday <= 6)'),
    ('role_permissions', 'role_permissions_scope_check', "scope IN ('all', 'own')"),
    ('saved_reports', 'saved_reports_visibility_check', "visibility IN ('private', 'shared')"),
    ('signature_requests', 'signature_requests_signer_party_check', "signer_party IN ('customer', 'company')"),
    ('signature_requests', 'signature_requests_status_check', "status IN ('pending', 'signed', 'declined')"),
    ('support_queues', 'support_queues_routing_check', "routing IN ('least_loaded', 'presence')"),
    ('support_tickets', 'support_tickets_channel_check', "channel IN ('email', 'phone', 'web', 'portal', 'chat')"),
    ('support_tickets', 'support_tickets_csat_score_check', '(csat_score >= 1) AND (csat_score <= 5)'),
    ('support_tickets', 'support_tickets_severity_check', "severity IN ('low', 'medium', 'high', 'critical')"),
    ('support_tickets', 'support_tickets_status_check', "status IN ('open', 'pending', 'resolved', 'closed')"),
    ('tasks', 'tasks_priority_check', "priority IN ('low', 'normal', 'high', 'urgent')"),
    ('validation_rules', 'validation_rules_applies_on_check', "applies_on IN ('create', 'update', 'both')"),
    ('webhook_deliveries', 'webhook_deliveries_status_check', "status IN ('pending', 'success', 'failed', 'dead')"),
    ('workflow_runs', 'workflow_runs_status_check', "status IN ('done', 'failed', 'dry_run', 'waiting', 'cancelled')"),
]

UNIQUES = [
    ('campaign_members', 'campaign_members_campaign_id_contact_id_key', ('campaign_id', 'contact_id')),
    ('campaign_members', 'campaign_members_campaign_id_lead_id_key', ('campaign_id', 'lead_id')),
    ('custom_field_definitions', 'custom_field_definitions_entity_key_key', ('entity', 'key')),
    ('deal_partners', 'deal_partners_deal_id_partner_id_key', ('deal_id', 'partner_id')),
    ('document_versions', 'document_versions_document_id_version_key', ('document_id', 'version')),
    ('forecast_adjustments', 'forecast_adjustments_manager_id_rep_id_period_key', ('manager_id', 'rep_id', 'period')),
    ('journey_enrollments', 'journey_enrollments_journey_id_member_id_key', ('journey_id', 'member_id')),
    ('order_lines', 'order_lines_order_id_line_no_key', ('order_id', 'line_no')),
    ('product_usage', 'product_usage_account_id_metric_date_key', ('account_id', 'metric_date')),
    ('quotas', 'quotas_user_id_period_key', ('user_id', 'period')),
    ('report_subscriptions', 'report_subscriptions_report_id_user_id_key', ('report_id', 'user_id')),
    ('validation_rules', 'validation_rules_entity_name_key', ('entity', 'name')),
    ('webhook_deliveries', 'webhook_deliveries_subscription_id_event_id_key', ('subscription_id', 'event_id')),
]

# (table, name, [(kind 'col'|'lower', column, descending)], where, unique)
INDEXES = [
    ('accounts', 'idx_accounts_domain', [('col', 'domain', False)], None, False),
    ('accounts', 'idx_accounts_parent', [('col', 'parent_id', False)], None, False),
    ('accounts', 'ix_accounts_territory', [('col', 'territory_id', False)], None, False),
    ('accounts', 'ux_accounts_external_id', [('col', 'external_id', False)], 'external_id IS NOT NULL', True),
    ('activities', 'idx_activities_account', [('col', 'account_id', False)], None, False),
    ('activities', 'idx_activities_contact', [('col', 'contact_id', False)], None, False),
    ('activities', 'idx_activities_deal', [('col', 'deal_id', False)], None, False),
    ('activities', 'idx_activities_occurred', [('col', 'occurred_at', True)], None, False),
    ('approval_requests', 'idx_approvals_status', [('col', 'status', False), ('col', 'required_role', False)], None, False),
    ('attachments', 'idx_attachments_account', [('col', 'account_id', False)], None, False),
    ('audit_log', 'idx_audit_created', [('col', 'created_at', True)], None, False),
    ('audit_log', 'idx_audit_record', [('col', 'entity', False), ('col', 'record_id', False)], None, False),
    ('campaign_members', 'ix_campaign_members_campaign', [('col', 'campaign_id', False), ('col', 'status', False)], None, False),
    ('campaign_members', 'ix_campaign_members_contact', [('col', 'contact_id', False)], None, False),
    ('campaign_members', 'ix_campaign_members_lead', [('col', 'lead_id', False)], None, False),
    ('case_comments', 'idx_case_comments_case', [('col', 'case_id', False), ('col', 'created_at', False)], None, False),
    ('case_comments', 'ix_case_comments_message', [('col', 'message_id', False)], None, False),
    ('consent_events', 'idx_consent_contact', [('col', 'contact_id', False)], None, False),
    ('contacts', 'idx_contacts_account', [('col', 'account_id', False)], None, False),
    ('contacts', 'ux_contacts_external_id', [('col', 'external_id', False)], 'external_id IS NOT NULL', True),
    ('contracts', 'idx_contracts_account', [('col', 'account_id', False)], None, False),
    ('contracts', 'idx_contracts_end', [('col', 'end_date', False)], None, False),
    ('custom_records', 'ix_custom_records_account', [('col', 'account_id', False)], None, False),
    ('custom_records', 'ix_custom_records_object', [('col', 'object_id', False), ('col', 'created_at', True)], None, False),
    ('dashboards', 'ix_dashboards_owner', [('col', 'owner_id', False)], None, False),
    ('deal_alerts', 'uq_open_alert', [('col', 'deal_id', False), ('col', 'kind', False)], 'resolved_at IS NULL', True),
    ('deal_stage_history', 'idx_stage_history_deal', [('col', 'deal_id', False)], None, False),
    ('deals', 'idx_deals_account', [('col', 'account_id', False)], None, False),
    ('deals', 'idx_deals_stage', [('col', 'stage_id', False)], None, False),
    ('deals', 'ux_deals_external_id', [('col', 'external_id', False)], 'external_id IS NOT NULL', True),
    ('document_comments', 'idx_document_comments_doc', [('col', 'document_id', False), ('col', 'created_at', False)], None, False),
    ('email_events', 'ix_email_events_send', [('col', 'send_id', False), ('col', 'occurred_at', False)], None, False),
    ('email_sends', 'ix_email_sends_campaign', [('col', 'campaign_id', False), ('col', 'sent_at', False)], None, False),
    ('email_sends', 'ix_email_sends_enrollment', [('col', 'enrollment_id', False), ('col', 'sent_at', True)], None, False),
    ('email_sends', 'ix_email_sends_journey', [('col', 'journey_id', False), ('col', 'step_index', False)], None, False),
    ('engagement_events', 'idx_engagement_contact', [('col', 'contact_id', False), ('col', 'occurred_at', True)], None, False),
    ('engagement_events', 'idx_engagement_lead', [('col', 'lead_id', False), ('col', 'occurred_at', True)], None, False),
    ('forecast_submissions', 'ix_forecast_submissions_user_period', [('col', 'user_id', False), ('col', 'period', False), ('col', 'scope', False), ('col', 'created_at', True)], None, False),
    ('inbound_emails', 'ix_inbound_emails_sender', [('lower', 'from_email', False), ('col', 'received_at', True)], None, False),
    ('inbound_emails', 'ix_inbound_emails_status', [('col', 'status', False), ('col', 'received_at', True)], None, False),
    ('invoices', 'idx_invoices_account', [('col', 'account_id', False), ('col', 'status', False)], None, False),
    ('journey_enrollments', 'ix_journey_enrollments_due', [('col', 'status', False), ('col', 'next_at', False)], None, False),
    ('leads', 'idx_leads_domain', [('col', 'domain', False)], None, False),
    ('leads', 'idx_leads_email', [('lower', 'email', False)], None, False),
    ('leads', 'idx_leads_status_owner', [('col', 'status', False), ('col', 'owner_id', False)], None, False),
    ('list_views', 'ix_list_views_source', [('col', 'source', False), ('col', 'owner_id', False)], None, False),
    ('notifications', 'idx_notifications_user', [('col', 'user_id', False), ('col', 'read_at', False)], None, False),
    ('orders', 'uq_orders_quote', [('col', 'quote_id', False)], "status <> 'cancelled'", True),
    ('price_book_entries', 'uq_price_book_entry', [('col', 'product_id', False), ('col', 'currency', False), ('col', 'price_book_id', False)], 'price_book_id IS NOT NULL', True),
    ('price_book_entries', 'uq_price_list_entry', [('col', 'product_id', False), ('col', 'currency', False)], 'price_book_id IS NULL', True),
    ('quotes', 'idx_quotes_deal', [('col', 'deal_id', False)], None, False),
    ('quotes', 'uq_quotes_primary_per_deal', [('col', 'deal_id', False)], 'is_primary', True),
    ('saved_reports', 'ix_saved_reports_owner', [('col', 'owner_id', False)], None, False),
    ('support_tickets', 'idx_tickets_account', [('col', 'account_id', False), ('col', 'status', False)], None, False),
    ('support_tickets', 'idx_tickets_owner', [('col', 'owner_id', False), ('col', 'status', False)], None, False),
    ('support_tickets', 'idx_tickets_queue', [('col', 'queue_id', False), ('col', 'status', False)], None, False),
    ('tasks', 'idx_tasks_assignee', [('col', 'assignee_id', False)], None, False),
    ('tasks', 'idx_tasks_due', [('col', 'due_date', False)], None, False),
    ('users', 'ux_users_sso_subject', [('col', 'sso_subject', False)], 'sso_subject IS NOT NULL', True),
    ('webhook_deliveries', 'ix_webhook_deliveries_due', [('col', 'status', False), ('col', 'next_attempt_at', False)], None, False),
    ('webhook_deliveries', 'ix_webhook_deliveries_sub', [('col', 'subscription_id', False), ('col', 'id', True)], None, False),
    ('workflow_runs', 'ix_workflow_runs_rule_record', [('col', 'rule_id', False), ('col', 'record_id', False), ('col', 'created_at', True)], None, False),
]

# Postgres-only indexes (access methods other databases don't have)
POSTGRES_INDEXES = [
    ('accounts', 'idx_accounts_name_trgm', 'CREATE INDEX idx_accounts_name_trgm ON accounts USING gin (lower((name)::text) gin_trgm_ops)'),
    ('activities', 'idx_activities_embedding', 'CREATE INDEX idx_activities_embedding ON activities USING hnsw (embedding vector_cosine_ops)'),
    ('activities', 'idx_activities_tsv', 'CREATE INDEX idx_activities_tsv ON activities USING gin (search_tsv)'),
    ('kb_articles', 'idx_kb_search', 'CREATE INDEX idx_kb_search ON kb_articles USING gin (search_tsv)'),
]


def _where(expr: str) -> dict:
    """Per-dialect WHERE for a partial index (booleans are 1/0 outside PostgreSQL)."""
    other = expr if expr != "is_primary" else "is_primary = 1"
    return {"postgresql_where": text(expr), "sqlite_where": text(other), "mssql_where": text(other)}


def _degrades_safely(expr, parts) -> bool:
    """A unique index "WHERE <its only column> IS NOT NULL" means the same as a plain unique index on databases
    that ignore NULLs in unique indexes (MySQL, MariaDB, Oracle)."""
    return expr is not None and len(parts) == 1 and expr == f"{parts[0][1]} IS NOT NULL"


def _only(*dialects):
    return lambda ddl, target, bind, dialect=None, **kw: (dialect or bind.dialect).name in dialects


def _not(*dialects):
    return lambda ddl, target, bind, dialect=None, **kw: (dialect or bind.dialect).name not in dialects


def apply(metadata: MetaData) -> None:
    tables = metadata.tables
    for table, name, sql in CHECKS:
        tables[table].append_constraint(CheckConstraint(text(sql), name=name))
    for table, name, cols in UNIQUES:
        tables[table].append_constraint(UniqueConstraint(*cols, name=name))
    for table, name, parts, where, unique in INDEXES:
        t = tables[table]
        exprs = []
        for kind, col, desc in parts:
            e = func.lower(t.c[col]) if kind == "lower" else t.c[col]
            exprs.append(e.desc() if desc else e)
        idx = Index(name, *exprs, unique=unique, **(_where(where) if where else {}))
        if where and not _degrades_safely(where, parts):
            idx.ddl_if(callable_=_only(*PARTIAL_INDEX_DIALECTS))  # the application enforces the rule as well
        elif any(kind == "lower" for kind, _, _ in parts):
            idx.ddl_if(callable_=_not("mssql"))  # SQL Server can't index an expression without a computed column
        # (an Index built from a table's columns attaches itself to that table)
    for table, name, ddl in POSTGRES_INDEXES:
        event.listen(tables[table], "after_create", DDL(ddl).execute_if(dialect="postgresql"))
    _nullable_uniques_for_sql_server(metadata)


def _nullable_uniques_for_sql_server(metadata: MetaData) -> None:
    """SQL Server treats NULLs as equal in unique constraints, so a nullable unique column (external ids, tokens)
    becomes a filtered unique index there instead."""
    for t in metadata.sorted_tables:
        for c in list(t.constraints):
            if isinstance(c, UniqueConstraint) and any(col.nullable for col in c.columns):
                cols = list(c.columns)
                c.ddl_if(callable_=_not("mssql"))
                idx = Index(f"uqn_{t.name}_{'_'.join(col.name for col in cols)}"[:120], *cols, unique=True,
                            mssql_where=and_(*[col.isnot(None) for col in cols]))
                idx.ddl_if(dialect="mssql")
                idx.info["mssql_only"] = True
