export type UUID = string;
export type BuyingRole = "Champion" | "Decision Maker" | "Economic Buyer" | "Blocker" | "Evaluator" | "Influencer";
export type Sentiment = "positive" | "neutral" | "negative";
export type ActivityType = "meeting" | "call" | "note" | "email" | "system";
export type LossReason = "price" | "competitor" | "no_decision" | "timing" | "product_fit" | "other";

export interface UserBrief { id: UUID; full_name: string }
export interface User extends UserBrief { email: string; role: "super_admin" | "sales_manager" | "sales_rep" | "read_only" }

export interface AccountListItem {
  id: UUID; name: string; domain: string; industry: string | null; tier: string; health: number;
  owner: UserBrief | null; open_pipeline: number; open_deals: number; contacts: number; last_activity_at: string | null;
}

export interface Contact {
  id: UUID; account_id: UUID; first_name: string; last_name: string; name: string; email: string | null;
  phone: string | null; job_title: string | null; buying_role: BuyingRole; account_name?: string | null;
}

export interface RiskFactors { stale?: boolean; sentiment_drop?: boolean; no_champion?: boolean; days_since_activity?: number | null; days_in_stage?: number }

export interface Deal {
  id: UUID; title: string; amount: number; currency: string;
  account: { id: UUID; name: string; domain: string; health_score: number };
  stage: string; stage_id: UUID; probability: number; risk_score: number; risk_factors: RiskFactors;
  weighted_value: number; target_close_date: string | null; days_in_stage: number; owner: UserBrief | null;
  primary_contact: { id: UUID; name: string; buying_role: BuyingRole } | null; loss_reason: LossReason | null;
  ai_insights: { competitors?: string[]; pain_points?: string[]; recap_email?: string; postmortem?: string;
    velocity?: { days_in_pipeline: number; benchmark_days: number; status: string }; last_trigger?: { stage: string; at: string } };
  created_at: string;
}

export interface Activity {
  id: UUID; date: string; type: ActivityType; summary: string; sentiment: Sentiment;
  account: { id: UUID; name: string } | null; deal: { id: UUID; title: string } | null; user: UserBrief | null; similarity?: number | null;
}

export interface Task {
  id: UUID; title: string; due_date: string | null; completed: boolean; source: "manual" | "ai";
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
  stages: { id: UUID; name: string; probability: number; stage_order: number; is_closed_won: boolean; is_closed_lost: boolean }[];
  activities: Activity[]; tasks: Task[]; contacts: Contact[]; history: StageHistory[];
  next_best_actions: { action: string; why: string; impact: number }[];
}

export interface Account360 {
  account: { id: UUID; name: string; domain: string; industry: string | null; tier: string; health_score: number;
    health_breakdown: { recency: number; sentiment: number; velocity: number } | null; owner: UserBrief | null; created_at: string };
  contacts: Contact[]; deals: Deal[]; recent_activities: Activity[]; tasks: Task[];
  stage_history: (StageHistory & { deal_id: UUID })[];
  summary: { open_pipeline: number; weighted_pipeline: number; won_revenue: number; has_champion: boolean };
}

export interface DashboardSummary {
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
