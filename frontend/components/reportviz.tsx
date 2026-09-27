"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { Table, Td } from "@/components/ui/extra";
import { cn, money, shortDate } from "@/lib/utils";

/* Report charts follow the data-viz method: one series in brand teal (--series-1); multi-series in the
   validated categorical order (--cat-1..7, rest folded into "Other"), assigned alphabetically so a value
   change never repaints a series; <=24px bars with 4px data-ends; 2px surface gaps between stacked
   segments; 2px lines; hairline grid; per-mark tooltips; a legend for 2+ series; the data table below. */

export type ChartType = "table" | "bar" | "column" | "line" | "stacked" | "matrix" | "number";
/** Called with one value per grouping when a bar, point, segment, cell or summary row is clicked. */
export type DrillFn = (values: unknown[], label: string) => void;
export interface RCol { key: string; label: string; type: string; role: "dimension" | "measure" | "column"; bucket?: string | null }
/** The same summary over the previous period (same length, or the previous calendar unit). */
export interface Comparison { label: string; start: string; end: string; rows: unknown[][]; truncated: boolean }
export interface RResult {
  source: string; source_label: string; columns: RCol[]; rows: unknown[][]; truncated: boolean; row_count: number; generated_at: string; comparison?: Comparison;
}

export interface Filter { field: string; op: string; value?: unknown }
export interface Definition {
  source: string; columns?: string[]; group_by?: { field: string; bucket?: string }[]; measures?: { agg: string; field?: string }[];
  filters?: Filter[]; sort?: { by: string; dir: "asc" | "desc" }; limit?: number; chart?: { type: ChartType }; compare?: "previous_period";
}
export interface SavedReport {
  id: string; name: string; description: string | null; source: string; definition: Definition; visibility: "private" | "shared";
  owner: string | null; can_edit: boolean; updated_at: string;
}

export const CHART_LABELS: Record<ChartType, string> = {
  table: "Table", bar: "Bar", column: "Column", line: "Line", stacked: "Stacked bar", matrix: "Matrix (pivot)", number: "Headline number",
};
const MAX_SERIES = 7;
const MAX_BARS = 15;

export function fmtValue(v: unknown, col: RCol, compact = false): string {
  if (v === null || v === undefined || v === "") return col.role === "dimension" ? "(blank)" : "—";
  if (col.type === "money") return money(Number(v), { compact });
  if (col.type === "number") return Number(v).toLocaleString("en-US", { maximumFractionDigits: 1, notation: compact && Math.abs(Number(v)) >= 10_000 ? "compact" : "standard" });
  if (col.type === "bool") return v ? "Yes" : "No";
  if (col.type === "date") {
    const s = String(v).slice(0, 10), d = new Date(`${s}T00:00:00`);
    switch (col.bucket) {
      case "year": return String(d.getFullYear());
      case "quarter": return `Q${Math.floor(d.getMonth() / 3) + 1} ${d.getFullYear()}`;
      case "month": return d.toLocaleDateString("en-US", { month: "short", year: "numeric" });
      case "week": return `Wk of ${shortDate(s)}`;
      default: return shortDate(s, true);
    }
  }
  return String(v);
}

/** The chart that can actually draw this result (e.g. two groupings always stack). */
export function effectiveChart(r: RResult, requested: ChartType | undefined): ChartType {
  const dims = r.columns.filter((c) => c.role === "dimension");
  const measures = r.columns.filter((c) => c.role === "measure");
  if (!measures.length || requested === "table") return "table";
  if (!dims.length) return "number";
  if (requested === "number") return "number";
  if (dims.length === 2) return requested === "matrix" ? "matrix" : "stacked";
  if (requested === "matrix") return "bar";
  if (requested === "line" || requested === "column") return dims[0].type === "date" ? requested : "bar";
  if (requested === "stacked") return "bar";
  return requested ?? (dims[0].type === "date" ? "column" : "bar");
}

export function ReportViz({ result, chart, compact = false, onDrill }: { result: RResult; chart?: ChartType; compact?: boolean; onDrill?: DrillFn }) {
  const type = effectiveChart(result, chart);
  const dims = result.columns.filter((c) => c.role === "dimension").length;
  const drill = dims ? onDrill : undefined;  // only summaries have groups to drill into
  if (!result.rows.length) return <p className="py-8 text-center text-sm text-muted-foreground">No records match this report.</p>;
  if (type === "number") return <Headline result={result} />;
  if (type === "table") return <ResultTable result={result} maxRows={compact ? 8 : undefined} onDrill={drill} />;
  if (type === "matrix") return <Matrix result={result} onDrill={drill} />;
  if (type === "stacked") return <StackedBars result={result} onDrill={drill} />;
  if (type === "line") return <LineChart result={result} onDrill={drill} />;
  if (type === "column") return <Columns result={result} onDrill={drill} />;
  return <Bars result={result} onDrill={drill} />;
}

/** Two groupings as a cross-tab of the first measure, with row and column totals. */
function Matrix({ result, onDrill }: { result: RResult; onDrill?: DrillFn }) {
  const { i, col } = measure(result);
  const [d0, d1] = result.columns;
  const key = (v: unknown) => JSON.stringify(v ?? null);
  const rowsRaw = new Map<string, unknown>(), colsRaw = new Map<string, unknown>(), cell = new Map<string, number>();
  const rowTot = new Map<string, number>(), colTot = new Map<string, number>();
  for (const r of result.rows) {
    const a = key(r[0]), b = key(r[1]), v = Number(r[i] ?? 0);
    rowsRaw.set(a, r[0]); colsRaw.set(b, r[1]);
    cell.set(`${a}|${b}`, (cell.get(`${a}|${b}`) ?? 0) + v);
    rowTot.set(a, (rowTot.get(a) ?? 0) + v); colTot.set(b, (colTot.get(b) ?? 0) + v);
  }
  const byTotal = (m: Map<string, number>) => [...m.keys()].sort((x, y) => (m.get(y) ?? 0) - (m.get(x) ?? 0));
  const inDateOrder = (keys: string[], dim: RCol, raw: Map<string, unknown>) =>
    dim.type === "date" ? [...keys].sort((x, y) => String(raw.get(x) ?? "").localeCompare(String(raw.get(y) ?? ""))) : keys;
  const rows = inDateOrder(byTotal(rowTot).slice(0, 40), d0, rowsRaw);
  const cols = inDateOrder(byTotal(colTot).slice(0, 12), d1, colsRaw);
  const grand = [...rowTot.values()].reduce((a, b) => a + b, 0);
  const max = Math.max(1, ...[...cell.values()]);
  const num = "px-2.5 py-1.5 text-right tabular";
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full border-collapse text-[12.5px]">
        <thead>
          <tr className="border-b">
            <th className="px-2.5 py-1.5 text-left font-medium text-muted-foreground">{d0.label} / {d1.label}</th>
            {cols.map((c) => <th key={c} className="px-2.5 py-1.5 text-right font-medium text-muted-foreground">{fmtValue(colsRaw.get(c), d1)}</th>)}
            <th className="px-2.5 py-1.5 text-right font-semibold">Total</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r} className="border-b last:border-b-0">
              <td className="px-2.5 py-1.5 text-muted-foreground">{fmtValue(rowsRaw.get(r), d0)}</td>
              {cols.map((c) => {
                const v = cell.get(`${r}|${c}`);
                const clickable = v !== undefined && !!onDrill;
                return (
                  <td key={c} className={cn(num, clickable && "cursor-pointer hover:underline")}
                    style={v ? { background: `color-mix(in srgb, var(--series-1) ${Math.round((v / max) * 22)}%, transparent)` } : undefined}
                    onClick={clickable ? () => onDrill!([rowsRaw.get(r), colsRaw.get(c)], `${fmtValue(rowsRaw.get(r), d0)} · ${fmtValue(colsRaw.get(c), d1)}`) : undefined}
                    title={clickable ? "Show these records" : undefined}>
                    {v === undefined ? <span className="text-subtle">·</span> : fmtValue(v, col)}
                  </td>
                );
              })}
              <td className={cn(num, "font-medium")}>{fmtValue(rowTot.get(r), col)}</td>
            </tr>
          ))}
          <tr className="border-t-2">
            <td className="px-2.5 py-1.5 font-semibold">Total</td>
            {cols.map((c) => <td key={c} className={cn(num, "font-medium")}>{fmtValue(colTot.get(c), col)}</td>)}
            <td className={cn(num, "font-semibold")}>{fmtValue(grand, col)}</td>
          </tr>
        </tbody>
      </table>
      {(rowTot.size > rows.length || colTot.size > cols.length) && (
        <p className="mt-2 text-[12px] text-subtle">Showing the largest {rows.length} rows and {cols.length} columns; totals include everything.</p>
      )}
    </div>
  );
}

function measure(r: RResult) {
  const i = r.columns.findIndex((c) => c.role === "measure");
  return { i, col: r.columns[i] };
}

function Tip({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return <div className="pointer-events-none absolute z-10 whitespace-nowrap rounded-md border bg-surface px-2.5 py-1.5 text-xs shadow-pop" style={style}>{children}</div>;
}

/** Change against the previous period, e.g. "▲ 12% vs last quarter (40)". Neutral colour: whether up is good depends on the measure. */
export function Delta({ now, before, col, label }: { now: number; before: number; col: RCol; label: string }) {
  const diff = now - before;
  const pct = before ? Math.round((100 * diff) / Math.abs(before)) : null;
  const arrow = diff > 0 ? "▲" : diff < 0 ? "▼" : "=";
  return (
    <span className="tabular text-[12.5px] text-muted-foreground" title={`Previous: ${fmtValue(before, col)}`}>
      <span className="font-medium text-foreground">{arrow} {pct === null ? (diff ? "new" : "no change") : `${Math.abs(pct)}%`}</span> vs {label} ({fmtValue(before, col, true)})
    </span>
  );
}

function Headline({ result }: { result: RResult }) {
  const { i, col } = measure(result);
  const total = result.rows.reduce((s, r) => s + Number(r[i] ?? 0), 0);
  const others = result.columns.filter((c, j) => c.role === "measure" && j !== i);
  const cmp = result.comparison;
  return (
    <div className="py-2">
      <div className="text-[34px] font-semibold leading-10 tracking-tight tabular">{fmtValue(total, col, true)}</div>
      <div className="mt-1 text-[12.5px] text-muted-foreground">{col.label}</div>
      {cmp && <div className="mt-1"><Delta now={total} before={cmp.rows.reduce((s, r) => s + Number(r[i] ?? 0), 0)} col={col} label={cmp.label} /></div>}
      {others.map((c) => {
        const j = result.columns.indexOf(c);
        return <div key={c.key} className="mt-0.5 text-[12.5px] text-muted-foreground tabular">{c.label}: {fmtValue(result.rows.reduce((s, r) => s + Number(r[j] ?? 0), 0), c, true)}</div>;
      })}
    </div>
  );
}

function Bars({ result, onDrill }: { result: RResult; onDrill?: DrillFn }) {
  const [hover, setHover] = useState<number | null>(null);
  const { i, col } = measure(result);
  const dim = result.columns[0];
  const rows = result.rows.slice(0, MAX_BARS);
  const max = Math.max(1, ...rows.map((r) => Math.max(0, Number(r[i] ?? 0))));
  const extra = result.columns.filter((c, j) => c.role === "measure" && j !== i);
  return (
    <div>
      <div className="space-y-2">
        {rows.map((r, k) => {
          const v = Math.max(0, Number(r[i] ?? 0));
          return (
            <div key={k} className={cn("relative grid grid-cols-[minmax(0,9rem)_1fr_4.5rem] items-center gap-3", onDrill && "cursor-pointer")}
              onMouseEnter={() => setHover(k)} onMouseLeave={() => setHover(null)} onClick={onDrill ? () => onDrill([r[0]], fmtValue(r[0], dim)) : undefined}>
              <div className="truncate text-[13px] text-muted-foreground" title={fmtValue(r[0], dim)}>{fmtValue(r[0], dim)}</div>
              <div className="relative h-5">
                <div className={cn("absolute inset-y-0.5 left-0 rounded-r bg-series-1 transition-opacity", hover !== null && hover !== k && "opacity-60")} style={{ width: `${Math.max(0.5, (v / max) * 100)}%` }} />
                {hover === k && (
                  <Tip style={{ left: `${Math.min(75, Math.max(10, (v / max) * 100))}%`, bottom: "100%", marginBottom: 6 }}>
                    <div className="font-medium">{fmtValue(r[0], dim)}</div>
                    <div className="tabular">{col.label}: {fmtValue(r[i], col)}</div>
                    {extra.map((c) => <div key={c.key} className="tabular text-muted-foreground">{c.label}: {fmtValue(r[result.columns.indexOf(c)], c)}</div>)}
                  </Tip>
                )}
              </div>
              <div className="tabular text-right text-[13px] font-medium">{fmtValue(r[i], col, true)}</div>
            </div>
          );
        })}
      </div>
      {result.rows.length > MAX_BARS && <p className="mt-2 text-[12px] text-subtle">Top {MAX_BARS} of {result.rows.length}. See the table for the rest.</p>}
    </div>
  );
}

function niceScale(max: number) {
  const raw = Math.max(max, 1) / 3;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const n = raw / pow;
  const step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * pow;
  const top = Math.ceil(Math.max(max, 1) / step) * step;
  return { top, ticks: Array.from({ length: Math.round(top / step) + 1 }, (_, k) => k * step) };
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);  // 0 until measured: never draw at a guessed width
  useLayoutEffect(() => {  // measure before paint so the first frame is already laid out
    if (!ref.current) return;
    setW(Math.max(200, Math.floor(ref.current.getBoundingClientRect().width)));
    const ro = new ResizeObserver(([e]) => setW(Math.max(200, Math.floor(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function YAxis({ ticks, top, H, col }: { ticks: number[]; top: number; H: number; col: RCol }) {
  return (
    <div className="relative w-12 shrink-0" style={{ height: H }}>
      {ticks.map((t) => <span key={t} className="tabular absolute right-0 -translate-y-1/2 text-[11px] text-subtle" style={{ top: H - (t / top) * H }}>{fmtValue(t, col, true)}</span>)}
    </div>
  );
}

function every(n: number, room: number) {
  return Math.max(1, Math.ceil(n / Math.max(1, room)));
}

function Columns({ result, onDrill }: { result: RResult; onDrill?: DrillFn }) {
  const [hover, setHover] = useState<number | null>(null);
  const [ref, w] = useWidth<HTMLDivElement>();
  const { i, col } = measure(result);
  const dim = result.columns[0];
  const rows = result.rows.filter((r) => r[0] !== null);  // undated records stay in the table, not on a time axis
  const undated = result.rows.length - rows.length;
  const { top, ticks } = niceScale(Math.max(0, ...rows.map((r) => Number(r[i] ?? 0))));
  const H = 170;
  const labelEvery = every(rows.length, Math.floor(w / 64));
  if (!rows.length) return <p className="py-8 text-center text-sm text-muted-foreground">None of these records has a date to plot.</p>;
  return (
    <div className="flex gap-2">
      <YAxis ticks={ticks} top={top} H={H} col={col} />
      <div ref={ref} className="relative min-w-0 flex-1">
        <div className="absolute inset-x-0 top-0" style={{ height: H }}>
          {ticks.map((t) => <div key={t} className="absolute inset-x-0 border-t border-border" style={{ top: H - (t / top) * H }} />)}
        </div>
        <div className="relative flex items-end gap-0.5" style={{ height: H }}>
          {rows.map((r, k) => {
            const v = Math.max(0, Number(r[i] ?? 0));
            return (
              <div key={k} className={cn("relative flex h-full min-w-0 flex-1 items-end justify-center", onDrill && "cursor-pointer")}
                onMouseEnter={() => setHover(k)} onMouseLeave={() => setHover(null)} onClick={onDrill ? () => onDrill([r[0]], fmtValue(r[0], dim)) : undefined}>
                <div className={cn("w-full max-w-[24px] rounded-t bg-series-1 transition-opacity", hover !== null && hover !== k && "opacity-60")} style={{ height: Math.max(2, (v / top) * H) }} />
                {hover === k && (
                  <Tip style={{ bottom: (v / top) * H + 8, left: "50%", transform: "translateX(-50%)" }}>
                    <div className="font-medium">{fmtValue(r[0], dim)}</div>
                    <div className="tabular">{col.label}: {fmtValue(r[i], col)}</div>
                  </Tip>
                )}
              </div>
            );
          })}
        </div>
        <div className="mt-1.5 flex gap-0.5">
          {rows.map((r, k) => <span key={k} className="min-w-0 flex-1 truncate text-center text-[11px] text-subtle">{k % labelEvery === 0 ? fmtValue(r[0], dim) : ""}</span>)}
        </div>
        <Undated n={undated} />
      </div>
    </div>
  );
}

function LineChart({ result, onDrill }: { result: RResult; onDrill?: DrillFn }) {
  const [hover, setHover] = useState<number | null>(null);
  const [ref, w] = useWidth<HTMLDivElement>();
  const { i, col } = measure(result);
  const dim = result.columns[0];
  const rows = result.rows.filter((r) => r[0] !== null);
  const undated = result.rows.length - rows.length;
  const { top, ticks } = niceScale(Math.max(0, ...rows.map((r) => Number(r[i] ?? 0))));
  const H = 170, pad = 6;
  const x = (k: number) => (rows.length === 1 ? w / 2 : pad + (k * (w - 2 * pad)) / (rows.length - 1));
  const y = (v: number) => H - (Math.max(0, v) / top) * H;
  const pts = rows.map((r, k) => [x(k), y(Number(r[i] ?? 0))] as const);
  const path = pts.map(([px, py], k) => `${k ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join("");
  const area = pts.length ? `${path}L${pts[pts.length - 1][0].toFixed(1)},${H}L${pts[0][0].toFixed(1)},${H}Z` : "";
  const labelEvery = every(rows.length, Math.floor(w / 72));
  if (!rows.length) return <p className="py-8 text-center text-sm text-muted-foreground">None of these records has a date to plot.</p>;
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const bx = e.currentTarget.getBoundingClientRect().left;
    const px = e.clientX - bx;
    let best = 0;
    pts.forEach(([p], k) => { if (Math.abs(p - px) < Math.abs(pts[best][0] - px)) best = k; });
    setHover(best);
  };
  const last = pts.length - 1;
  return (
    <div className="flex gap-2">
      <YAxis ticks={ticks} top={top} H={H} col={col} />
      <div ref={ref} className="relative min-w-0 flex-1">
        {w > 0 && <>
        <svg width={w} height={H} className={cn("block overflow-visible", onDrill && "cursor-pointer")} onMouseMove={onMove} onMouseLeave={() => setHover(null)}
          onClick={onDrill && hover !== null ? () => onDrill([rows[hover][0]], fmtValue(rows[hover][0], dim)) : undefined} role="img" aria-label={`${col.label} by ${dim.label}`}>
          {ticks.map((t) => <line key={t} x1={0} x2={w} y1={y(t)} y2={y(t)} stroke="hsl(var(--border))" strokeWidth={1} />)}
          <path d={area} fill="var(--series-1)" fillOpacity={0.1} />
          <path d={path} fill="none" stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {hover !== null && <line x1={pts[hover][0]} x2={pts[hover][0]} y1={0} y2={H} stroke="hsl(var(--subtle))" strokeWidth={1} />}
          {[hover ?? last].map((k) => <circle key={k} cx={pts[k][0]} cy={pts[k][1]} r={4.5} fill="var(--series-1)" stroke="hsl(var(--surface))" strokeWidth={2} />)}
        </svg>
        {hover !== null && (
          <Tip style={{ left: Math.min(w - 90, Math.max(0, pts[hover][0] - 60)), top: -8, transform: "translateY(-100%)" }}>
            <div className="font-medium">{fmtValue(rows[hover][0], dim)}</div>
            <div className="tabular">{col.label}: {fmtValue(rows[hover][i], col)}</div>
          </Tip>
        )}
        <div className="relative mt-1.5 h-4">
          {rows.map((r, k) => k % labelEvery === 0 && (
            <span key={k} className="absolute -translate-x-1/2 whitespace-nowrap text-[11px] text-subtle" style={{ left: Math.min(w - 24, Math.max(24, x(k))) }}>{fmtValue(r[0], dim)}</span>
          ))}
        </div>
        <Undated n={undated} />
        </>}
      </div>
    </div>
  );
}

function Undated({ n }: { n: number }) {
  return n ? <p className="mt-2 text-[12px] text-subtle">{n} group{n === 1 ? "" : "s"} with no date not plotted; see the table.</p> : null;
}

function StackedBars({ result, onDrill }: { result: RResult; onDrill?: DrillFn }) {
  const [hover, setHover] = useState<string | null>(null);
  const { i, col } = measure(result);
  const [d0, d1] = result.columns;
  // series = second grouping; keep the 7 largest, fold the rest into "Other"
  const totals = new Map<string, number>();
  for (const r of result.rows) totals.set(fmtValue(r[1], d1), (totals.get(fmtValue(r[1], d1)) ?? 0) + Math.max(0, Number(r[i] ?? 0)));
  const ranked = [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
  const kept = ranked.length > MAX_SERIES ? ranked.slice(0, MAX_SERIES - 1) : ranked;
  const series = [...kept].sort((a, b) => a.localeCompare(b));  // color by name, not by rank
  const hasOther = ranked.length > kept.length;
  const color = (s: string) => (s === "Other" ? "var(--cat-other)" : `var(--cat-${series.indexOf(s) + 1})`);
  const cats = new Map<string, Map<string, number>>();
  const rawCat = new Map<string, unknown>(), rawSeries = new Map<string, unknown>();
  for (const r of result.rows) {
    rawCat.set(fmtValue(r[0], d0), r[0]); rawSeries.set(fmtValue(r[1], d1), r[1]);
    const c = fmtValue(r[0], d0), sRaw = fmtValue(r[1], d1), s = kept.includes(sRaw) ? sRaw : "Other";
    const m = cats.get(c) ?? new Map<string, number>();
    m.set(s, (m.get(s) ?? 0) + Math.max(0, Number(r[i] ?? 0)));
    cats.set(c, m);
  }
  const catRows = [...cats.entries()].map(([c, m]) => ({ c, m, total: [...m.values()].reduce((a, b) => a + b, 0) }))
    .sort((a, b) => b.total - a.total).slice(0, MAX_BARS);
  const max = Math.max(1, ...catRows.map((r) => r.total));
  const order = hasOther ? [...series, "Other"] : series;
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {order.map((s) => <span key={s} className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: color(s) }} />{s}</span>)}
      </div>
      <div className="space-y-2">
        {catRows.map(({ c, m, total }) => (
          <div key={c} className="grid grid-cols-[minmax(0,9rem)_1fr_4.5rem] items-center gap-3">
            <div className="truncate text-[13px] text-muted-foreground" title={c}>{c}</div>
            <div className="relative flex h-4 gap-[2px]" style={{ width: `${Math.max(0.5, (total / max) * 100)}%` }}>
              {order.filter((s) => (m.get(s) ?? 0) > 0).map((s, k, arr) => {
                const v = m.get(s)!, id = `${c}|${s}`;
                return (
                  <div key={s} className={cn("relative h-full transition-opacity", k === arr.length - 1 && "rounded-r", hover && hover !== id && "opacity-60",
                    onDrill && s !== "Other" && "cursor-pointer")}
                    style={{ flexGrow: v, flexBasis: 0, background: color(s), minWidth: 2 }} onMouseEnter={() => setHover(id)} onMouseLeave={() => setHover(null)}
                    onClick={onDrill && s !== "Other" ? () => onDrill([rawCat.get(c), rawSeries.get(s)], `${c} · ${s}`) : undefined}>
                    {hover === id && (
                      <Tip style={{ bottom: "100%", left: "50%", transform: "translateX(-50%)", marginBottom: 6 }}>
                        <div className="font-medium">{c} · {s}</div>
                        <div className="tabular">{col.label}: {fmtValue(v, col)}</div>
                      </Tip>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="tabular text-right text-[13px] font-medium">{fmtValue(total, col, true)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function ResultTable({ result, maxRows, onDrill }: { result: RResult; maxRows?: number; onDrill?: DrillFn }) {
  const rows = maxRows ? result.rows.slice(0, maxRows) : result.rows;
  const numeric = (c: RCol) => c.type === "money" || c.type === "number";
  const dimIdx = result.columns.map((c, j) => (c.role === "dimension" ? j : -1)).filter((j) => j >= 0);
  const drill = dimIdx.length ? onDrill : undefined;
  // with a comparison: the first summary's previous-period value and change, matched on the grouping values
  const cmp = result.comparison && dimIdx.length ? result.comparison : undefined;
  const mi = result.columns.findIndex((c) => c.role === "measure");
  const keyOf = (r: unknown[]) => JSON.stringify(dimIdx.map((j) => r[j]));
  const prev = new Map((cmp?.rows ?? []).map((r) => [keyOf(r), Number(r[mi] ?? 0)]));
  const head = [...result.columns.map((c) => <span key={c.key} className={cn(numeric(c) && "block text-right")}>{c.label}</span>),
    ...(cmp && mi >= 0 ? [<span key="_prev" className="block text-right">{result.columns[mi].label}, {cmp.label}</span>, <span key="_chg" className="block text-right">Change</span>] : [])];
  return (
    <div>
      <Table head={head} minWidth={Math.max(480, head.length * 130)}>
        {rows.map((r, k) => (
          <tr key={k} className={cn(drill && "cursor-pointer hover:bg-muted/50")} title={drill ? "Show these records" : undefined}
            onClick={drill ? () => drill(dimIdx.map((j) => r[j]), dimIdx.map((j) => fmtValue(r[j], result.columns[j])).join(" · ")) : undefined}>
            {result.columns.map((c, j) => <Td key={c.key} className={cn("text-[13px]", numeric(c) && "tabular text-right")}>{fmtValue(r[j], c)}</Td>)}
            {cmp && mi >= 0 && (() => {
              const before = prev.get(keyOf(r)) ?? 0, now = Number(r[mi] ?? 0), diff = now - before;
              return <>
                <Td className="tabular text-right text-[13px] text-muted-foreground">{fmtValue(before, result.columns[mi])}</Td>
                <Td className="tabular text-right text-[13px]">{diff > 0 ? "▲ " : diff < 0 ? "▼ " : ""}{before ? `${Math.abs(Math.round((100 * diff) / Math.abs(before)))}%` : diff ? "new" : "—"}</Td>
              </>;
            })()}
          </tr>
        ))}
      </Table>
      {(maxRows && result.rows.length > maxRows) || result.truncated ? (
        <p className="px-4 py-2 text-[12px] text-subtle">
          {maxRows && result.rows.length > maxRows ? `Showing ${maxRows} of ${result.rows.length} rows.` : ""} {result.truncated ? "Results are capped; add filters to narrow them." : ""}
        </p>
      ) : null}
    </div>
  );
}
