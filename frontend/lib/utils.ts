import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { currentLocale, currentTimeZone } from "@/lib/i18n";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const formats = new Map<string, Intl.NumberFormat>();
function nf(options: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = `${currentLocale()}|${JSON.stringify(options)}`;
  let f = formats.get(key);
  if (!f) { f = new Intl.NumberFormat(currentLocale(), options); formats.set(key, f); }
  return f;
}

/** US-dollar amounts (pipeline and forecast values are converted to USD) in the user's number format. */
export function money(value: number | null | undefined, opts: { compact?: boolean } = {}) {
  const v = value ?? 0;
  if (opts.compact && Math.abs(v) >= 10_000) return nf({ style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 }).format(v);
  return nf({ style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(v);
}

/** A plain number in the user's format (thousands separators, decimal mark). */
export function fmtNumber(value: number | null | undefined, digits = 0) {
  return nf({ maximumFractionDigits: digits, minimumFractionDigits: digits && Math.abs(value ?? 0) < 1 ? Math.min(digits, 2) : 0 }).format(value ?? 0);
}

/** Date and time in the user's format and time zone. */
export function fmtDateTime(iso?: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(currentLocale(), { dateStyle: "medium", timeStyle: "short", timeZone: currentTimeZone() });
}

function english() {
  return currentLocale().startsWith("en");
}

function relative(value: number, unit: Intl.RelativeTimeFormatUnit) {
  return new Intl.RelativeTimeFormat(currentLocale(), { numeric: "auto" }).format(value, unit);
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
  if (!iso) return english() ? "never" : "—";
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (!english()) {
    if (days < 30) return relative(-Math.max(0, days), "day");
    return days < 365 ? relative(-Math.floor(days / 30), "month") : relative(-Math.floor(days / 365), "year");
  }
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

export function shortDate(iso?: string | null, withYear = false) {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  return d.toLocaleDateString(currentLocale(), { month: "short", day: "numeric", ...(withYear ? { year: "numeric" } : {}) });
}

export function dueLabel(iso?: string | null) {
  if (!iso) return { label: "No date", tone: "muted" as const };
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const d = new Date(`${iso}T00:00:00`);
  const diff = Math.round((d.getTime() - today.getTime()) / 86_400_000);
  if (!english() && diff <= 1) {
    const label = relative(diff, "day");
    return { label: label.charAt(0).toUpperCase() + label.slice(1), tone: diff < 0 ? "critical" as const : diff === 0 ? "warning" as const : "normal" as const };
  }
  if (diff < 0) return { label: `${-diff}d overdue`, tone: "critical" as const };
  if (diff === 0) return { label: "Today", tone: "warning" as const };
  if (diff === 1) return { label: "Tomorrow", tone: "normal" as const };
  return { label: shortDate(iso), tone: "normal" as const };
}
