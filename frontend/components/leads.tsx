"use client";

import { Copy } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export const SOURCES = ["web_form", "campaign", "trade_show", "partner", "outbound", "import", "api", "manual"] as const;
export const EVENT_TYPES = ["form_submit", "content_download", "pricing_page_visit", "webinar_registered", "webinar_attended", "email_open",
  "email_click", "trade_show_scan", "meeting_booked", "web_visit"] as const;
export const DISQUALIFY_REASONS = ["no_budget", "no_need", "not_icp", "competitor", "student_or_personal", "duplicate", "unresponsive", "other"] as const;

export function ScoreBar({ score, threshold = 60, className }: { score: number; threshold?: number; className?: string }) {
  const tone = score >= threshold ? "var(--status-good)" : score >= threshold * 0.6 ? "var(--status-warning)" : "hsl(var(--subtle))";
  return (
    <div className={cn("flex items-center gap-2", className)} title={`Score ${score} / 100 (MQL at ${threshold})`}>
      <div className="relative h-1.5 w-16 overflow-hidden rounded-full bg-muted">
        <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${score}%`, background: tone }} />
        <div className="absolute inset-y-0 w-px bg-foreground/40" style={{ left: `${threshold}%` }} />
      </div>
      <span className="tabular text-[12.5px] font-medium">{score}</span>
    </div>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  return (
    <Button type="button" variant="outline" size="sm" onClick={() => { navigator.clipboard?.writeText(text); toast.success("Copied"); }}>
      <Copy className="h-3.5 w-3.5" />{label}
    </Button>
  );
}
