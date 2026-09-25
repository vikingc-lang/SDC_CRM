export type UUID = string;
export type BuyingRole = "Champion" | "Decision Maker" | "Economic Buyer" | "Blocker" | "Evaluator" | "Influencer";
export type Sentiment = "positive" | "neutral" | "negative";
export type ActivityType = "meeting" | "call" | "note" | "email" | "system";
export type LossReason = "competitor" | "budget_frozen" | "feature_gap" | "champion_departed" | "price" | "no_decision" | "timing" | "other";

export interface UserBrief { id: UUID; full_name: string }
export interface User extends UserBrief { email: string; role: string }

export interface AccountListItem {
  id: UUID; name: string; domain: string; industry: string | null; tier: string; health: number;
  owner: UserBrief | null; open_pipeline: number; open_deals: number; contacts: number; last_activity_at: string | null;
}

export interface Contact {
  id: UUID; account_id: UUID; first_name: string; last_name: string; name: string; email: string | null;
  phone: string | null; job_title: string | null; buying_role: BuyingRole; account_name?: string | null;
  mobile?: string | null; linkedin_url?: string | null; timezone?: string | null; department?: string | null; status?: "active" | "departed" | "erased";
  relationship_strength?: number | null; rsi_factors?: Record<string, number | string | null>;
  consent?: { email: "granted" | "denied" | "unknown"; basis: string | null; regime: string | null; updated_at: string | null; do_not_sell: boolean;
    opt_out: { email: boolean; phone: boolean; sms: boolean } };
  custom_fields?: Record<string, unknown>;
}

export interface RiskFactors { stale?: boolean; sentiment_drop?: boolean; no_champion?: boolean; days_since_activity?: number | null; days_in_stage?: number }

export interface Deal {
  id: UUID; title: string; amount: number; currency: string;
  account: { id: UUID; name: string; domain: string; health_score: number; credit_hold?: boolean };
  stage: string; stage_id: UUID; probability: number; risk_score: number; risk_factors: RiskFactors;
  weighted_value: number; target_close_date: string | null; days_in_stage: number; owner: UserBrief | null;
  primary_contact: { id: UUID; name: string; buying_role: BuyingRole } | null; loss_reason: LossReason | null;
  ai_insights: { competitors?: string[]; pain_points?: string[]; recap_email?: string; postmortem?: string;
    velocity?: { days_in_pipeline: number; benchmark_days: number; status: string }; last_trigger?: { stage: string; at: string } };
  created_at: string;
  amount_usd?: number; pipeline_id?: UUID; is_won?: boolean; is_lost?: boolean; deal_type?: string; source?: string; contract_id?: UUID | null;
  original_close_date?: string | null; close_date_pushes?: number; loss_debrief?: string | null; loss_competitor?: string | null; win_debrief?: string | null;
  custom_fields?: Record<string, unknown>;
}

export interface Activity {
  id: UUID; date: string; type: ActivityType; summary: string; sentiment: Sentiment;
  account: { id: UUID; name: string } | null; deal: { id: UUID; title: string } | null; user: UserBrief | null; similarity?: number | null;
  subject?: string | null; direction?: "inbound" | "outbound" | "internal" | null; duration_seconds?: number | null; disposition?: string | null;
  agenda?: string | null; attendance?: string | null; source?: string; contact?: { id: UUID; name: string } | null;
  attachments?: { id: UUID; filename: string; size_bytes: number; content_type: string }[];
  entity?: "activity" | "account"; matched_by?: ("keyword" | "vector")[]; score?: number; name?: string; health_score?: number;
}

export interface Task {
  id: UUID; title: string; due_date: string | null; completed: boolean; source: "manual" | "ai" | "system";
  description?: string | null; priority?: "low" | "normal" | "high" | "urgent"; owner?: UserBrief | null; assignee?: UserBrief | null;
  depends_on?: { id: UUID; title: string; completed: boolean } | null; blocked?: boolean; escalation_level?: number; milestone_id?: UUID | null;
  account: { id: UUID; name: string } | null; deal: { id: UUID; title: string } | null; created_at: string;
}

export interface Stage { id: UUID; name: string; stage_order: number; default_probability: number; is_closed_won: boolean; is_closed_lost: boolean }
export interface Pipeline { id: UUID; name: string; is_default: boolean; stages: Stage[] }

export interface KanbanColumn {
  id: UUID; name: string; stage_order: number; probability: number; is_closed_won: boolean; is_closed_lost: boolean;
  deals: Deal[]; metrics: { count: number; total: number; weighted: number };
}
export interface Kanban { pipeline: { id: UUID; name: string }; columns: KanbanColumn[] }

export interface GateCheck { criterion: string; met: boolean }
export interface StageHistory { from: string | null; to: string; forecast_delta: number; gate_overridden: boolean; by: UserBrief | null; at: string }

export interface DealDetail extends Deal {
  stages: { id: UUID; name: string; probability: number; stage_order: number; is_closed_won: boolean; is_closed_lost: boolean; gate_rules?: GateRule[] }[];
  activities: Activity[]; tasks: Task[]; contacts: Contact[]; history: StageHistory[];
  next_best_actions: NBA[];
  pipeline: { id: UUID; name: string; kind: string };
  quotes: Quote[]; documents: DocumentSummary[]; alerts: Alert[]; loss_taxonomy: Record<string, string>;
  partners: { id: UUID; partner: { id: UUID; name: string; tier: string }; role: string; split_pct: number; commission_rate: number | null }[];
}

export interface Account360 {
  account: {
    id: UUID; name: string; domain: string; alt_domains: string[]; industry: string | null; industry_code: string | null; tier: string;
    lifecycle_stage: string; annual_revenue: number | null; employee_count: number | null;
    locations: { type: string; city: string; region?: string | null; country: string }[];
    health_score: number; health_breakdown: { recency: number; sentiment: number; velocity: number; support?: number; milestones?: number;
      sentiment_drift?: number; open_tickets?: number; overdue_items?: number } | null;
    relationship_strength: number | null; churn_risk: number; churn_factors: Record<string, unknown>; owner: UserBrief | null; created_at: string;
    customer_master: { legal_name: string | null; tax_id: string | null; billing_address: Record<string, string>; payment_terms: string;
      credit_limit: number | null; credit_hold: boolean; erp_customer_id: string | null; erp_synced_at: string | null };
    custom_fields: Record<string, unknown>; parent: { id: UUID; name: string } | null; subsidiaries: { id: UUID; name: string; health_score: number }[];
  };
  custom_field_definitions: CustomFieldDef[];
  contacts: Contact[]; deals: Deal[]; recent_activities: Activity[]; tasks: Task[];
  stage_history: (StageHistory & { deal_id: UUID })[];
  contracts: Contract[]; onboarding: OnboardingProject[];
  support_tickets: { id: UUID; subject: string; severity: string; status: string; opened_at: string }[];
  usage: { date: string; active_users: number; licensed_users: number; feature_adoption: number }[];
  finance: ArSummary; alerts: Alert[];
  files: { id: UUID; filename: string; size_bytes: number; content_type: string; created_at: string }[];
  summary: { open_pipeline: number; weighted_pipeline: number; won_revenue: number; active_contract_value: number; has_champion: boolean };
}

export interface DashboardSummary {
  by_pipeline?: { pipeline: string; count: number; total: number; weighted: number }[];
  total_pipeline: number; weighted_pipeline: number; open_deals: number; at_risk_deals: number; won_this_quarter: number;
  win_rate: number | null; avg_deal_size: number; open_tasks: number; overdue_tasks: number;
  by_stage: { stage: string; count: number; total: number; weighted: number }[];
  by_close_month: { month: string; weighted: number }[];
}

export interface Briefing {
  headline: string; generated_at: string;
  priorities: { kind: "task" | "risk" | "closing" | "health"; id: string; title: string; detail: string; severity: "high" | "medium"; href: string }[];
}

export interface ExtractedContact { first_name: string; last_name: string; job_title: string | null; email: string | null; buying_role: BuyingRole }
export interface QuickLogResponse {
  account_name: string | null; domain: string | null; contacts: ExtractedContact[];
  deal: { title: string | null; amount: number | null; target_close_date: string | null; suggested_stage: string | null } | null;
  activity_type: Exclude<ActivityType, "system">; summary: string; action_items: { task: string; due_date: string | null }[];
  sentiment: Sentiment; matched_account_id: UUID | null; matched_deal_id: UUID | null; engine: string;
  signals: { competitors?: string[]; pain_points?: string[]; risks?: string[] };
}

// ---- enterprise pillars -------------------------------------------------------------
export type Role = "super_admin" | "sales_manager" | "account_executive" | "sdr" | "auditor" | "partner";
export type Action = "create" | "read" | "update" | "delete" | "export";
export type Perm = Record<Action, boolean> & { scope: "all" | "own" };
export interface Me {
  id: UUID; email: string; full_name: string; role: Role; manager_id: UUID | null;
  partner: { id: UUID; name: string; tier: string } | null;
  permissions: Record<string, Perm>;
}

export interface GateRule { type: string; label?: string; [k: string]: unknown }
export interface PipelineFull { id: UUID; name: string; kind: "direct" | "inbound" | "renewal" | "partner"; description: string | null; is_default: boolean;
  stages: (Stage & { gate_rules: GateRule[] })[] }

export interface QuoteLine { id: UUID; product_id: UUID; sku: string; name: string; description: string | null; billing_type: "recurring" | "one_time"; unit: string;
  quantity: number; list_unit_price: number; discount_pct: number; net_unit_price: number; line_total: number }
export interface ApprovalReq { id: UUID; required_role: "sales_manager" | "finance"; reason: string; status: string; comment: string | null;
  decided_by: UserBrief | null; decided_at: string | null; created_at: string }
export interface Quote { id: UUID; deal_id: UUID; quote_number: string; name: string; currency: string; term_months: number; payment_terms: string;
  status: "draft" | "pending_approval" | "approved" | "rejected" | "sent" | "accepted" | "expired"; valid_until: string | null;
  list_total: number; discount_total: number; max_discount_pct: number; one_time_total: number; acv: number; tcv: number; notes: string | null;
  approved_at: string | null; created_at: string; deal: { id: UUID; title: string; account: { id: UUID; name: string; credit_hold: boolean } } | null;
  lines: QuoteLine[]; approvals: ApprovalReq[]; required_approvals?: { required_role: string; reason: string }[]; documents?: DocumentSummary[] }
export interface Product { id: UUID; sku: string; name: string; description: string | null; family: string | null; billing_type: "recurring" | "one_time";
  unit: string; active: boolean; prices: { currency: string; tiers: { min_qty: number; unit_price: number }[] }[] }

export interface Signer { id: UUID; name: string; email: string; party: "customer" | "company"; order: number; status: string; signed_at: string | null;
  signed_ip: string | null; signature_text: string | null; sign_url: string | null }
export interface DocumentSummary { id: UUID; doc_type: "nda" | "sow" | "order_form"; title: string; status: string; account: { id: UUID; name: string };
  deal_id: UUID | null; quote_id: UUID | null; content_sha256: string; pdf_attachment_id: UUID | null; created_at: string; completed_at: string | null;
  signers: Signer[]; body_html?: string | null; body?: string | null }
export interface Contract { id: UUID; contract_number: string; name: string; account: { id: UUID; name: string }; deal_id: UUID | null; start_date: string;
  end_date: string; days_to_expiry: number; currency: string; acv: number; tcv: number; payment_terms: string; auto_renew: boolean; status: string;
  terms: Record<string, unknown>; renewal_deal_id: UUID | null }
export interface Milestone { id: UUID; title: string; due_date: string | null; status: "pending" | "in_progress" | "done" | "blocked"; completed_at: string | null; overdue: boolean }
export interface OnboardingProject { id: UUID; name: string; status: string; account: { id: UUID; name: string; health_score: number }; deal_id: UUID | null;
  owner: UserBrief | null; kickoff_date: string | null; target_go_live: string | null; progress: number; overdue: number; milestones: Milestone[];
  scope: { pre_sales_summary?: string[]; buyer_priorities?: string[]; competitive_context?: string[]; products?: { sku: string; name: string; quantity: number }[];
    stakeholders?: { name: string; title: string | null; role: string; email: string | null }[]; commercials?: Record<string, unknown> } }
export interface ArSummary { open_balance: number; overdue_balance: number; buckets: Record<"current" | "1_30" | "31_60" | "61_90" | "90_plus", number>;
  credit_limit: number | null; credit_available: number | null; credit_hold: boolean; erp_customer_id: string | null; erp_synced_at: string | null;
  invoices?: { id: UUID; invoice_number: string; issue_date: string; due_date: string; currency: string; amount: number; balance: number; status: string; days_overdue: number }[] }
export interface Alert { id: UUID; kind: string; severity: "low" | "medium" | "high"; message: string; created_at?: string;
  deal?: { id: UUID; title: string; account: string; amount: number; currency: string } }
export interface NBA { kind: string; action: string; why: string; impact: number; message: string | null }
export interface Partner { id: UUID; name: string; partner_type: string; tier: string; domains: string[]; territories: string[]; commission_rate: number;
  referral_fee_rate: number; status: string }
export interface Registration { id: UUID; partner: { id: UUID; name: string; tier: string }; company_name: string; domain: string; contact_name: string | null;
  contact_email: string | null; estimated_amount: number; currency: string; territory: string | null; product_interest: string | null; notes: string | null;
  status: string; exclusivity_expires_at: string | null; conflicts: { type: string; severity: string; account?: string; partner?: string; open_deals?: number; expires?: string }[];
  decision_note: string | null; deal_id: UUID | null; created_at: string }
export interface Notification { id: UUID; kind: string; title: string; body: string | null; link: string | null; read: boolean; created_at: string }
export interface CustomFieldDef { id?: UUID; entity?: string; key: string; label: string; field_type: "text" | "number" | "date" | "select" | "boolean" | "url"; options: string[]; required?: boolean }
