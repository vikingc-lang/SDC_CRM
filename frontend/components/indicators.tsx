import { AlertTriangle, CheckCircle2, Crown, Minus, OctagonAlert, ShieldAlert, Star, TrendingDown, TrendingUp, User, Users, Wallet } from "lucide-react";
import type { BuyingRole, Sentiment } from "@/lib/types";
import { cn } from "@/lib/utils";

export type StatusTone = "good" | "warning" | "critical";

export function healthTone(score: number): StatusTone {
  return score >= 70 ? "good" : score >= 40 ? "warning" : "critical";
}
export function riskTone(score: number): StatusTone {
  return score >= 60 ? "critical" : score >= 30 ? "warning" : "good";
}

const STATUS_VAR: Record<StatusTone, string> = {
  good: "var(--status-good)",
  warning: "var(--status-warning)",
  critical: "var(--status-critical)",
};

/** Meter: the fill carries severity; label + number keep it readable without color. */
export function HealthMeter({ score, showLabel = true, className }: { score: number; showLabel?: boolean; className?: string }) {
  const tone = healthTone(score);
  return (
    <div className={cn("flex items-center gap-2", className)} title={`Health ${score}/100`}>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted" role="meter" aria-valuenow={score} aria-valuemin={0} aria-valuemax={100} aria-label="Account health">
        <div className="h-full rounded-full" style={{ width: `${Math.max(4, score)}%`, background: STATUS_VAR[tone] }} />
      </div>
      {showLabel && <span className="tabular text-[12.5px] font-medium text-foreground">{score}</span>}
    </div>
  );
}

export function HealthRing({ score, size = 64 }: { score: number; size?: number }) {
  const tone = healthTone(score);
  const r = (size - 8) / 2;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative" style={{ width: size, height: size }} role="meter" aria-valuenow={score} aria-valuemin={0} aria-valuemax={100} aria-label="Account health">
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={6} className="stroke-muted" />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={6} strokeLinecap="round" stroke={STATUS_VAR[tone]} strokeDasharray={`${(score / 100) * c} ${c}`} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-lg font-semibold leading-none">{score}</span>
        <span className="mt-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">health</span>
      </div>
    </div>
  );
}

export function StatusDot({ tone, className }: { tone: StatusTone; className?: string }) {
  return <span className={cn("inline-block h-2 w-2 shrink-0 rounded-full", className)} style={{ background: STATUS_VAR[tone] }} />;
}

export function RiskBadge({ score, compact = false }: { score: number; compact?: boolean }) {
  const tone = riskTone(score);
  const Icon = tone === "critical" ? OctagonAlert : tone === "warning" ? AlertTriangle : CheckCircle2;
  const label = tone === "critical" ? "High risk" : tone === "warning" ? "Watch" : "On track";
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[11px] font-medium text-foreground"
      style={{ borderColor: `color-mix(in srgb, ${STATUS_VAR[tone]} 45%, transparent)`, background: `color-mix(in srgb, ${STATUS_VAR[tone]} 10%, transparent)` }}
      title={`Risk index ${score}/100`}
    >
      <Icon className="h-3 w-3" style={{ color: STATUS_VAR[tone] }} />
      {compact ? score : `${label} · ${score}`}
    </span>
  );
}

export function SentimentIcon({ sentiment, className }: { sentiment: Sentiment; className?: string }) {
  if (sentiment === "positive") return <TrendingUp className={cn("h-3.5 w-3.5", className)} style={{ color: STATUS_VAR.good }} aria-label="Positive" />;
  if (sentiment === "negative") return <TrendingDown className={cn("h-3.5 w-3.5", className)} style={{ color: STATUS_VAR.critical }} aria-label="Negative" />;
  return <Minus className={cn("h-3.5 w-3.5 text-subtle", className)} aria-label="Neutral" />;
}

const ROLE_META: Record<BuyingRole, { icon: typeof Star; cls: string }> = {
  Champion: { icon: Star, cls: "bg-primary-soft text-primary" },
  "Decision Maker": { icon: Crown, cls: "bg-ai-soft text-ai" },
  "Economic Buyer": { icon: Wallet, cls: "bg-sky-100 text-sky-800 dark:bg-sky-500/15 dark:text-sky-300" },
  Blocker: { icon: ShieldAlert, cls: "bg-rose-100 text-rose-800 dark:bg-rose-500/15 dark:text-rose-300" },
  Influencer: { icon: Users, cls: "bg-amber-100 text-amber-900 dark:bg-amber-500/15 dark:text-amber-300" },
  Evaluator: { icon: User, cls: "bg-muted text-muted-foreground" },
};

export function RoleBadge({ role, className }: { role: BuyingRole; className?: string }) {
  const { icon: Icon, cls } = ROLE_META[role] ?? ROLE_META.Evaluator;
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11.5px] font-medium", cls, className)}>
      <Icon className="h-3 w-3" />
      {role}
    </span>
  );
}

export const BUYING_ROLES: BuyingRole[] = ["Champion", "Decision Maker", "Economic Buyer", "Influencer", "Evaluator", "Blocker"];
