import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const full = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

export function money(value: number | null | undefined, opts: { compact?: boolean } = {}) {
  const v = value ?? 0;
  if (opts.compact && Math.abs(v) >= 10_000) return `$${compact.format(v)}`;
  return full.format(v);
}

export function initials(name?: string | null) {
  if (!name) return "?";
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join("");
}

export function relativeDays(iso?: string | null) {
  if (!iso) return "never";
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

export function shortDate(iso?: string | null, withYear = false) {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", ...(withYear ? { year: "numeric" } : {}) });
}

export function dueLabel(iso?: string | null) {
  if (!iso) return { label: "No date", tone: "muted" as const };
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const d = new Date(`${iso}T00:00:00`);
  const diff = Math.round((d.getTime() - today.getTime()) / 86_400_000);
  if (diff < 0) return { label: `${-diff}d overdue`, tone: "critical" as const };
  if (diff === 0) return { label: "Today", tone: "warning" as const };
  if (diff === 1) return { label: "Tomorrow", tone: "normal" as const };
  return { label: shortDate(iso), tone: "normal" as const };
}
