"use client";

import { AlarmClock, CheckCircle2, CircleAlert } from "lucide-react";
import { cn } from "@/lib/utils";

export type Priority = "critical" | "high" | "medium" | "low";
export interface Clock { state: "running" | "due_soon" | "breached" | "met" | "missed" | "none"; due_at?: string; done_at?: string; minutes_left?: number }
export interface CaseRow {
  id: string; case_number: string; subject: string; status: string; priority: Priority; channel: string; category: string | null;
  account: string; account_id: string; owner: string | null; owner_id: string | null; queue: string | null; queue_id: string | null;
  opened_at: string; updated_at: string; sla_breached: boolean; clocks: { first_response: Clock; resolution: Clock }; csat_score: number | null;
}
export interface ServiceMeta {
  queues: { id: string; name: string }[]; agents: { id: string; name: string; role: string }[]; priorities: Priority[];
  statuses: string[]; channels: string[]; sla: Record<Priority, { first_response_hours: number; resolve_hours: number }>; can_edit: boolean;
}

/* Priority and SLA state are status, so they use the reserved status colors, always with a label. */
const PRIORITY_COLOR: Record<Priority, string> = {
  critical: "var(--status-critical)", high: "var(--status-serious)", medium: "var(--status-warning)", low: "hsl(var(--subtle))",
};

export function PriorityPill({ p }: { p: Priority }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-[12.5px] capitalize">
      <span className="h-2 w-2 rounded-sm" style={{ background: PRIORITY_COLOR[p] }} aria-hidden />{p}
    </span>
  );
}

export function duration(mins: number) {
  const m = Math.abs(mins);
  if (m < 60) return `${Math.round(m)}m`;
  if (m < 60 * 48) return `${Math.round(m / 60)}h`;
  return `${Math.round(m / 1440)}d`;
}

/** The clock that matters now: first response until it's met, then resolution. */
export function SlaBadge({ clocks, status, compact }: { clocks: CaseRow["clocks"]; status: string; compact?: boolean }) {
  const c = clocks.first_response.state === "running" || clocks.first_response.state === "due_soon" || clocks.first_response.state === "breached"
    ? { ...clocks.first_response, label: "Response" } : { ...clocks.resolution, label: "Resolve" };
  if (status === "resolved" || status === "closed") {
    const ok = clocks.resolution.state !== "missed" && clocks.first_response.state !== "missed";
    return <span className={cn("inline-flex items-center gap-1 text-[12.5px]", ok ? "text-muted-foreground" : "text-destructive")}>
      {ok ? <CheckCircle2 className="h-3.5 w-3.5" style={{ color: "var(--status-good)" }} /> : <CircleAlert className="h-3.5 w-3.5" />}{ok ? "SLA met" : "SLA missed"}</span>;
  }
  if (c.state === "none") return <span className="text-[12.5px] text-muted-foreground">No SLA</span>;
  const breached = c.state === "breached", soon = c.state === "due_soon";
  const mins = c.minutes_left ?? 0;
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[12px] font-medium tabular")}
      style={{ color: breached ? "var(--status-critical)" : soon ? "var(--status-serious)" : undefined,
               borderColor: breached ? "color-mix(in srgb, var(--status-critical) 40%, transparent)" : soon ? "color-mix(in srgb, var(--status-serious) 40%, transparent)" : undefined }}>
      <AlarmClock className="h-3 w-3" aria-hidden />
      {compact ? "" : `${c.label} `}{breached ? `overdue ${duration(mins)}` : `due in ${duration(mins)}`}
    </span>
  );
}
