"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

export function Tabs<T extends string>({ tabs, value, onChange, className }: {
  tabs: { value: T; label: React.ReactNode; count?: number }[]; value: T; onChange: (v: T) => void; className?: string;
}) {
  return (
    <div role="tablist" className={cn("-mx-1 mb-5 flex gap-1 overflow-x-auto border-b px-1 scrollbar-thin", className)}>
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          aria-selected={value === t.value}
          onClick={() => onChange(t.value)}
          className={cn(
            "-mb-px flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2 text-[13.5px] transition-colors",
            value === t.value ? "border-primary font-medium text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
          )}
        >
          {t.label}
          {t.count !== undefined && <span className="rounded-full bg-muted px-1.5 text-[11px] text-muted-foreground">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Field({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("min-w-0", className)}>
      <dt className="text-[12px] text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 truncate text-[13.5px]">{children ?? <span className="text-subtle">—</span>}</dd>
    </div>
  );
}

export function Table({ head, children, className, minWidth = 640 }: { head: React.ReactNode[]; children: React.ReactNode; className?: string; minWidth?: number }) {
  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="w-full text-left text-sm" style={{ minWidth }}>
        <thead className="border-b bg-surface-2/60 text-[12px] text-muted-foreground">
          <tr>{head.map((h, i) => <th key={i} className="whitespace-nowrap px-4 py-2.5 font-medium">{h}</th>)}</tr>
        </thead>
        <tbody className="divide-y">{children}</tbody>
      </table>
    </div>
  );
}

export function Td({ children, className }: { children?: React.ReactNode; className?: string }) {
  return <td className={cn("px-4 py-2.5 align-top", className)}>{children}</td>;
}

export function StatusPill({ status }: { status: string }) {
  const tone: Record<string, string> = {
    approved: "good", accepted: "good", completed: "good", active: "good", done: "good", signed: "good", earned: "good", succeeded: "good", resolved: "good",
    pending_approval: "warning", pending: "warning", sent: "info", partially_signed: "info", submitted: "warning", in_progress: "info", open: "warning",
    rejected: "critical", voided: "critical", declined: "critical", failed: "critical", expired: "muted", draft: "muted", renewed: "info",
    at_risk: "critical", blocked: "critical", new: "info", working: "info", mql: "warning", sql: "good", converted: "good", disqualified: "muted",
    recycled: "muted", sent_to_erp: "info", acknowledged: "good", cancelled: "muted", in_negotiation: "warning", overdue: "critical", departed: "muted", erased: "muted", not_started: "muted", lost: "critical", pipeline: "info", superseded: "muted",
  };
  const t = tone[status] ?? "muted";
  const color = t === "good" ? "var(--status-good)" : t === "warning" ? "var(--status-warning)" : t === "critical" ? "var(--status-critical)" : t === "info" ? "var(--series-1)" : "hsl(var(--subtle))";
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11.5px] font-medium capitalize"
      style={{ borderColor: `color-mix(in srgb, ${color} 40%, transparent)`, background: `color-mix(in srgb, ${color} 10%, transparent)` }}>
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
      {status === "mql" || status === "sql" ? status.toUpperCase() : status.replace(/_/g, " ")}
    </span>
  );
}

export function fmtMoney(value: number | null | undefined, currency = "USD", compact = false) {
  const v = value ?? 0;
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: compact || Math.abs(v) >= 1000 ? 0 : 2,
      notation: compact && Math.abs(v) >= 10_000 ? "compact" : "standard" }).format(v);
  } catch {
    return `${currency} ${v.toLocaleString()}`;
  }
}

export function bytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
