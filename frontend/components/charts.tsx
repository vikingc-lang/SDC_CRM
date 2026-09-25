"use client";

import { useState } from "react";
import { cn, money } from "@/lib/utils";

/* Charts follow the data-viz reference: single blue series, <=24px bars with 4px data-end
   radius, hairline recessive grid, hover tooltips, text in text tokens (never series color). */

export function StatTile({ label, value, sub, icon, emphasis, className }: {
  label: string; value: string; sub?: React.ReactNode; icon?: React.ReactNode; emphasis?: boolean; className?: string;
}) {
  return (
    <div className={cn("rounded-lg border bg-surface p-4 shadow-card", className)}>
      <div className="flex items-center justify-between text-[12.5px] text-muted-foreground">
        <span>{label}</span>
        {icon}
      </div>
      <div className={cn("mt-2 font-semibold tracking-tight", emphasis ? "text-[30px] leading-9" : "text-2xl")}>{value}</div>
      {sub && <div className="mt-1 text-[12.5px] text-muted-foreground">{sub}</div>}
    </div>
  );
}

function Tip({ left, children }: { left: string; children: React.ReactNode }) {
  return (
    <div className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-md border bg-surface px-2.5 py-1.5 text-xs shadow-pop" style={{ left, marginTop: -8 }}>
      {children}
    </div>
  );
}

/** Bullet-style bars: track = total open pipeline, fill = risk-adjusted weighted value. */
export function ForecastByStage({ data }: { data: { stage: string; count: number; total: number; weighted: number }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...data.map((d) => d.total));
  return (
    <div>
      <div className="mb-3 flex items-center gap-4 text-xs text-muted-foreground" aria-hidden>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-series-1" />Weighted</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-series-track" />Total pipeline</span>
      </div>
      <div className="space-y-3">
        {data.map((d, i) => (
          <div key={d.stage} className="relative grid grid-cols-[112px_1fr_72px] items-center gap-3" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <div className="truncate text-[13px] text-muted-foreground">{d.stage}</div>
            <div className="relative h-5 cursor-default">
              <div className="absolute inset-y-0 left-0 rounded-r bg-series-track" style={{ width: `${(d.total / max) * 100}%` }} />
              <div className="absolute inset-y-[3px] left-0 rounded-r bg-series-1" style={{ width: `${(d.weighted / max) * 100}%` }} />
              {hover === i && (
                <Tip left={`${Math.min(80, Math.max(20, (d.total / max) * 100))}%`}>
                  <div className="font-medium">{d.stage}</div>
                  <div className="tabular text-muted-foreground">{d.count} deal{d.count !== 1 && "s"} · {money(d.total)} total</div>
                  <div className="tabular">Weighted {money(d.weighted)}</div>
                </Tip>
              )}
            </div>
            <div className="tabular text-right text-[13px] font-medium">{money(d.weighted, { compact: true })}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function ForecastByMonth({ data }: { data: { month: string; weighted: number }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const rows = data.slice(0, 8);
  const max = Math.max(1, ...rows.map((d) => d.weighted));
  const step = niceStep(max);
  const top = Math.ceil(max / step) * step;
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
  const H = 150;
  const label = (m: string) => new Date(`${m}-01T00:00:00`).toLocaleDateString("en-US", { month: "short" });
  if (!rows.length) return <p className="py-10 text-center text-sm text-muted-foreground">No close dates set on open deals.</p>;
  return (
    <div className="relative flex gap-2">
      <div className="relative w-10 shrink-0" style={{ height: H }}>
        {ticks.map((t) => (
          <span key={t} className="tabular absolute right-0 -translate-y-1/2 text-[11px] text-subtle" style={{ top: H - (t / top) * H }}>{money(t, { compact: true })}</span>
        ))}
      </div>
      <div className="relative flex-1">
        <div className="absolute inset-x-0 top-0" style={{ height: H }}>
          {ticks.map((t) => (
            <div key={t} className="absolute inset-x-0 border-t border-border" style={{ top: H - (t / top) * H }} />
          ))}
        </div>
        <div className="relative flex items-end justify-around" style={{ height: H }}>
          {rows.map((d, i) => (
            <div key={d.month} className="relative flex h-full w-full items-end justify-center" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <div className={cn("w-full max-w-[24px] rounded-t bg-series-1 transition-opacity", hover !== null && hover !== i && "opacity-60")} style={{ height: Math.max(2, (d.weighted / top) * H) }} />
              {hover === i && (
                <div className="pointer-events-none absolute z-10 -translate-y-full whitespace-nowrap rounded-md border bg-surface px-2.5 py-1.5 text-xs shadow-pop" style={{ bottom: (d.weighted / top) * H + 8 }}>
                  <div className="font-medium">{new Date(`${d.month}-01T00:00:00`).toLocaleDateString("en-US", { month: "long", year: "numeric" })}</div>
                  <div className="tabular">Weighted {money(d.weighted)}</div>
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="mt-1.5 flex justify-around">
          {rows.map((d) => (
            <span key={d.month} className="w-full text-center text-[11px] text-subtle">{label(d.month)}</span>
          ))}
        </div>
      </div>
    </div>
  );
}

function niceStep(max: number) {
  const raw = max / 3;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const n = raw / pow;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * pow;
}

/** Three-part health breakdown (recency / sentiment / velocity) as labelled meters. */
export function HealthBreakdown({ breakdown }: { breakdown: { recency: number; sentiment: number; velocity: number; support?: number; milestones?: number } }) {
  const parts = [
    { key: "Recency", weight: "30%", value: breakdown.recency },
    { key: "Sentiment", weight: "25%", value: breakdown.sentiment },
    { key: "Velocity", weight: "15%", value: breakdown.velocity },
    ...(breakdown.support !== undefined ? [{ key: "Support load", weight: "15%", value: breakdown.support }] : []),
    ...(breakdown.milestones !== undefined ? [{ key: "Milestones", weight: "15%", value: breakdown.milestones }] : []),
  ];
  return (
    <div className="space-y-2.5">
      {parts.map((p) => (
        <div key={p.key} className="grid grid-cols-[124px_1fr_32px] items-center gap-3 text-[12.5px]">
          <span className="text-muted-foreground">{p.key} <span className="text-subtle">· {p.weight}</span></span>
          <div className="h-1.5 overflow-hidden rounded-full bg-series-track">
            <div className="h-full rounded-full bg-series-1" style={{ width: `${Math.max(2, p.value)}%` }} />
          </div>
          <span className="tabular text-right font-medium">{p.value}</span>
        </div>
      ))}
    </div>
  );
}

const AGING_LABEL: Record<string, string> = { current: "Current", "1_30": "1–30", "31_60": "31–60", "61_90": "61–90", "90_plus": "90+" };

/** A/R aging: one sequential ramp step per bucket (older = darker), labelled so color never carries meaning alone. */
export function AgingBars({ buckets, currency = "USD" }: { buckets: Record<string, number>; currency?: string }) {
  const [hover, setHover] = useState<string | null>(null);
  const keys = ["current", "1_30", "31_60", "61_90", "90_plus"];
  const max = Math.max(1, ...keys.map((k) => buckets[k] ?? 0));
  const ramp = ["#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"];
  return (
    <div className="flex items-end gap-2" style={{ height: 120 }}>
      {keys.map((k, i) => {
        const v = buckets[k] ?? 0;
        return (
          <div key={k} className="relative flex h-full flex-1 flex-col items-center justify-end gap-1" onMouseEnter={() => setHover(k)} onMouseLeave={() => setHover(null)}>
            {hover === k && <div className="pointer-events-none absolute -top-2 z-10 -translate-y-full whitespace-nowrap rounded-md border bg-surface px-2 py-1 text-xs shadow-pop">{AGING_LABEL[k]} days: {money(v)}</div>}
            <span className="tabular text-[11px] text-muted-foreground">{v ? money(v, { compact: true }) : "—"}</span>
            <div className="w-full max-w-[28px] rounded-t" style={{ height: `${Math.max(2, (v / max) * 80)}px`, background: ramp[i] }} />
            <span className="text-[11px] text-subtle">{AGING_LABEL[k]}</span>
          </div>
        );
      })}
      <span className="sr-only">{currency}</span>
    </div>
  );
}

/** Seat utilisation over time (active / licensed). */
export function UsageTrend({ points }: { points: { date: string; active_users: number; licensed_users: number }[] }) {
  if (points.length < 2) return <p className="text-[13px] text-muted-foreground">Not enough adoption data yet.</p>;
  const W = 320, H = 90;
  const max = Math.max(...points.map((p) => p.licensed_users), 1);
  const x = (i: number) => (i / (points.length - 1)) * W;
  const y = (v: number) => H - (v / max) * H;
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.active_users).toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H + 4}`} className="h-24 w-full" role="img" aria-label="Active users trend">
        <line x1={0} x2={W} y1={y(last.licensed_users)} y2={y(last.licensed_users)} className="stroke-border" strokeDasharray="0" strokeWidth={1} />
        <path d={`${line} L${W},${H} L0,${H} Z`} fill="var(--series-1)" opacity={0.1} />
        <path d={line} fill="none" stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={x(points.length - 1)} cy={y(last.active_users)} r={4} fill="var(--series-1)" stroke="hsl(var(--surface))" strokeWidth={2} />
      </svg>
      <p className="mt-1 text-[12px] text-muted-foreground">
        {last.active_users} of {last.licensed_users} seats active ({Math.round((100 * last.active_users) / last.licensed_users)}%) · licensed seats shown as the top line
      </p>
    </div>
  );
}
