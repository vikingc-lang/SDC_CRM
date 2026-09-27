"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Copy, Download, Plus, Save, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { FilterRow, IconX, NO_VALUE } from "@/components/filters";
import { type DrillRequest, DrillDialog } from "@/components/drill";
import { SubscribeButton } from "@/components/subscribe";
import { CHART_LABELS, type ChartType, type Definition, effectiveChart, ReportViz, ResultTable, type RResult, type SavedReport } from "@/components/reportviz";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { api, downloadFile, errorMessage, get } from "@/lib/api";
import { cn } from "@/lib/utils";

interface SrcField { key: string; label: string; type: string; options: string[] | null; groupable: boolean; ops: string[] }
interface Src { key: string; label: string; description: string; default_columns: string[]; fields: SrcField[] }
interface Catalogue { sources: Src[]; periods: string[]; buckets: string[]; aggregates: string[]; can_share: boolean; can_export: boolean }

const AGG_LABELS: Record<string, string> = { count: "Count of records", sum: "Total", avg: "Average", min: "Lowest", max: "Highest" };
const BUCKET_LABELS: Record<string, string> = { day: "Day", week: "Week", month: "Month", quarter: "Quarter", year: "Year" };

/** Drop filters the user hasn't finished filling in, so the preview and save never fail on them. */
function cleanDef(def: Definition): Definition {
  return { ...def, filters: (def.filters ?? []).filter((x) => NO_VALUE.includes(x.op) || (Array.isArray(x.value) ? x.value.every((v) => v !== "") : x.value !== "" && x.value !== undefined)) };
}

function defaultDef(src: Src): Definition {
  const money = src.fields.find((f) => f.type === "money");
  const group = src.fields.find((f) => f.groupable && (f.type === "enum" || f.type === "text"));
  return { source: src.key, group_by: group ? [{ field: group.key }] : [], measures: [{ agg: "count" }, ...(money ? [{ agg: "sum", field: money.key }] : [])],
    filters: [], chart: { type: "bar" } };
}

function Builder() {
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const reportId = params.get("id");
  const cat = useQuery({ queryKey: ["analytics", "sources"], queryFn: () => get<Catalogue>("/analytics/sources") });
  const saved = useQuery({ queryKey: ["analytics", "report", reportId], queryFn: () => get<SavedReport>(`/analytics/reports/${reportId}`), enabled: !!reportId });
  const [def, setDef] = useState<Definition | null>(null);
  const [debounced, setDebounced] = useState<Definition | null>(null);
  const [saveOpen, setSaveOpen] = useState<null | "save" | "copy">(null);
  const [drill, setDrill] = useState<DrillRequest | null>(null);

  useEffect(() => {
    if (def) return;
    if (reportId && saved.data) setDef(saved.data.definition);
    else if (!reportId && cat.data?.sources.length) setDef(defaultDef(cat.data.sources[0]));
  }, [def, reportId, saved.data, cat.data]);
  useEffect(() => { const t = setTimeout(() => setDebounced(def), 350); return () => clearTimeout(t); }, [def]);

  const run = useQuery({
    queryKey: ["analytics", "run", debounced], enabled: !!debounced, placeholderData: keepPreviousData, retry: false,
    queryFn: async () => (await api.post<RResult>("/analytics/run", { definition: cleanDef(debounced!) })).data,
  });
  const src = cat.data?.sources.find((s) => s.key === def?.source);
  const summary = !!def && !def.columns;

  const del = useMutation({
    mutationFn: async () => api.delete(`/analytics/reports/${reportId}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["analytics"] }); toast.success("Report deleted"); router.push("/reports?tab=reports"); },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (cat.isError) return <p className="text-sm text-destructive">{errorMessage(cat.error)}</p>;
  if (!cat.data || !def || !src) return <Skeleton className="h-[600px]" />;
  const set = (patch: Partial<Definition>) => setDef({ ...def, ...patch });
  const field = (k: string) => src.fields.find((f) => f.key === k);
  const numeric = src.fields.filter((f) => f.type === "money" || f.type === "number");
  const groupable = src.fields.filter((f) => f.groupable);
  const chart = run.data ? effectiveChart(run.data, def.chart?.type) : def.chart?.type;
  const sortables = summary
    ? [...(def.group_by ?? []).map((g) => ({ key: g.field, label: field(g.field)?.label ?? g.field })),
       ...(def.measures ?? []).map((m) => ({ key: m.agg === "count" ? "count" : `${m.agg}_${m.field}`, label: m.agg === "count" ? "Count" : `${AGG_LABELS[m.agg]} ${field(m.field!)?.label}` }))]
    : (def.columns ?? []).map((c) => ({ key: c, label: field(c)?.label ?? c }));

  return (
    <div className="mx-auto max-w-7xl">
      <Link href="/reports?tab=reports" className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />Reports</Link>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{saved.data?.name ?? "New report"}</h1>
          <p className="mt-0.5 text-[13px] text-muted-foreground">
            {saved.data ? <>{saved.data.owner ?? "Unknown"} · <Badge tone={saved.data.visibility === "shared" ? "primary" : "neutral"}>{saved.data.visibility === "shared" ? "Shared with team" : "Only you"}</Badge></> : "Pick a data source, then group, summarise and filter. The preview updates as you go."}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {saved.data && cat.data.can_export && (
            <Button variant="outline" size="sm" onClick={() => downloadFile(`/analytics/reports/${reportId}/export`, `${saved.data!.name}.csv`).catch((e) => toast.error(errorMessage(e)))}><Download className="h-3.5 w-3.5" />Export CSV</Button>
          )}
          {saved.data?.can_edit && <Button variant="ghost" size="sm" loading={del.isPending} onClick={() => del.mutate()}><Trash2 className="h-3.5 w-3.5" />Delete</Button>}
          {saved.data && <SubscribeButton reportId={saved.data.id} shared={saved.data.visibility === "shared"} />}
          {saved.data && <Button variant="outline" size="sm" onClick={() => setSaveOpen("copy")}><Copy className="h-3.5 w-3.5" />Save as copy</Button>}
          {(!saved.data || saved.data.can_edit) && <Button size="sm" onClick={() => setSaveOpen("save")}><Save className="h-3.5 w-3.5" />Save</Button>}
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-[320px_1fr]">
        <Card className="h-fit">
          <CardBody className="space-y-5">
            <div>
              <Label htmlFor="rb-source">Data source</Label>
              <Select id="rb-source" value={def.source} onChange={(e) => setDef(defaultDef(cat.data!.sources.find((s) => s.key === e.target.value)!))}>
                {cat.data.sources.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
              </Select>
              <p className="mt-1 text-[12px] text-subtle">{src.description}</p>
            </div>

            <div role="radiogroup" aria-label="Report type" className="grid grid-cols-2 rounded-md bg-muted p-0.5 text-[13px]">
              {[["summary", "Summary"], ["list", "List of records"]].map(([k, l]) => (
                <button key={k} type="button" role="radio" aria-checked={(k === "summary") === summary}
                  className={cn("rounded px-2 py-1.5", (k === "summary") === summary ? "bg-surface font-medium shadow-sm" : "text-muted-foreground")}
                  onClick={() => set(k === "summary" ? { columns: undefined, group_by: defaultDef(src).group_by, measures: defaultDef(src).measures, sort: undefined, chart: { type: "bar" } }
                    : { columns: src.default_columns, group_by: undefined, measures: undefined, sort: undefined, chart: { type: "table" }, compare: undefined })}>{l}</button>
              ))}
            </div>

            {summary ? (
              <>
                <Section title="Group by" hint="Up to two. Dates group by week, month, quarter or year.">
                  {(def.group_by ?? []).map((g, k) => (
                    <div key={k} className="flex gap-1.5">
                      <Select aria-label={`Grouping ${k + 1}`} className="min-w-0 flex-1" value={g.field} onChange={(e) => { const gb = [...def.group_by!]; gb[k] = { field: e.target.value }; set({ group_by: gb, sort: undefined }); }}>
                        {groupable.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                      </Select>
                      {field(g.field)?.type === "date" && (
                        <Select aria-label="Date grouping" className="w-28" value={g.bucket ?? "month"} onChange={(e) => { const gb = [...def.group_by!]; gb[k] = { ...g, bucket: e.target.value }; set({ group_by: gb }); }}>
                          {cat.data!.buckets.map((b) => <option key={b} value={b}>{BUCKET_LABELS[b]}</option>)}
                        </Select>
                      )}
                      <IconX label="Remove grouping" onClick={() => set({ group_by: def.group_by!.filter((_, j) => j !== k), sort: undefined })} />
                    </div>
                  ))}
                  {(def.group_by?.length ?? 0) < 2 && <AddButton onClick={() => set({ group_by: [...(def.group_by ?? []), { field: groupable.find((f) => !def.group_by?.some((g) => g.field === f.key))?.key ?? groupable[0].key }] })}>Add grouping</AddButton>}
                </Section>

                <Section title="Summarise" hint="What each group shows.">
                  {(def.measures ?? []).map((m, k) => (
                    <div key={k} className="flex gap-1.5">
                      <Select aria-label={`Summary ${k + 1}`} className={m.agg === "count" ? "min-w-0 flex-1" : "w-28"} value={m.agg}
                        onChange={(e) => { const ms = [...def.measures!]; ms[k] = e.target.value === "count" ? { agg: "count" } : { agg: e.target.value, field: m.field ?? numeric[0]?.key }; set({ measures: ms, sort: undefined }); }}>
                        {cat.data!.aggregates.filter((a) => a === "count" || numeric.length).map((a) => <option key={a} value={a}>{AGG_LABELS[a]}</option>)}
                      </Select>
                      {m.agg !== "count" && (
                        <Select aria-label="Of field" className="min-w-0 flex-1" value={m.field} onChange={(e) => { const ms = [...def.measures!]; ms[k] = { ...m, field: e.target.value }; set({ measures: ms, sort: undefined }); }}>
                          {numeric.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                        </Select>
                      )}
                      {def.measures!.length > 1 && <IconX label="Remove summary" onClick={() => set({ measures: def.measures!.filter((_, j) => j !== k), sort: undefined })} />}
                    </div>
                  ))}
                  {(def.measures?.length ?? 0) < 4 && <AddButton onClick={() => set({ measures: [...(def.measures ?? []), numeric[0] ? { agg: "sum", field: numeric[0].key } : { agg: "count" }] })}>Add summary</AddButton>}
                  <p className="text-[12px] text-subtle">Charts plot the first summary; the table shows them all.</p>
                </Section>
              </>
            ) : (
              <Section title="Columns">
                <div className="flex flex-wrap gap-1.5">
                  {(def.columns ?? []).map((c) => (
                    <span key={c} className="inline-flex items-center gap-1 rounded-full border py-0.5 pl-2.5 pr-1 text-[12.5px]">
                      {field(c)?.label}
                      <button type="button" aria-label={`Remove ${field(c)?.label}`} className="rounded-full p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                        onClick={() => set({ columns: def.columns!.filter((x) => x !== c) })}><X className="h-3 w-3" /></button>
                    </span>
                  ))}
                </div>
                <Select aria-label="Add column" value="" onChange={(e) => e.target.value && set({ columns: [...(def.columns ?? []), e.target.value] })}>
                  <option value="">Add a column…</option>
                  {src.fields.filter((f) => !def.columns?.includes(f.key)).map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                </Select>
              </Section>
            )}

            <Section title="Filters">
              {(def.filters ?? []).map((f, k) => <FilterRow key={k} f={f} src={src} periods={cat.data!.periods}
                onChange={(nf) => { const fs = [...def.filters!]; fs[k] = nf; set({ filters: fs }); }} onRemove={() => set({ filters: def.filters!.filter((_, j) => j !== k) })} />)}
              <AddButton onClick={() => { const f = src.fields[0]; set({ filters: [...(def.filters ?? []), { field: f.key, op: f.ops[0], value: "" }] }); }}>Add filter</AddButton>
              {summary && (
                <label className="flex items-start gap-2 pt-1 text-[13px]">
                  <input type="checkbox" className="mt-0.5 h-4 w-4" checked={def.compare === "previous_period"}
                    disabled={!def.compare && (!(def.filters ?? []).some((f) => f.op === "within") || (def.group_by ?? []).some((g) => field(g.field)?.type === "date"))}
                    onChange={(e) => set({ compare: e.target.checked ? "previous_period" : undefined })} />
                  <span>Compare with the previous period
                    <span className="block text-[12px] text-subtle">Needs an “in period” date filter (e.g. Created in this quarter); not with date groupings.</span></span>
                </label>
              )}
            </Section>

            <Section title="Sort">
              <div className="flex gap-1.5">
                <Select aria-label="Sort by" className="min-w-0 flex-1" value={def.sort?.by ?? ""} onChange={(e) => set({ sort: e.target.value ? { by: e.target.value, dir: def.sort?.dir ?? "desc" } : undefined })}>
                  <option value="">Default</option>
                  {sortables.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                </Select>
                <Select aria-label="Sort direction" className="w-32" value={def.sort?.dir ?? "desc"} disabled={!def.sort} onChange={(e) => set({ sort: { by: def.sort!.by, dir: e.target.value as "asc" | "desc" } })}>
                  <option value="desc">High → low</option><option value="asc">Low → high</option>
                </Select>
              </div>
            </Section>

            {summary && (
              <Section title="Show as">
                <div className="flex flex-wrap gap-1.5">
                  {(["bar", "column", "line", "stacked", "matrix", "number", "table"] as ChartType[]).map((t) => (
                    <button key={t} type="button" onClick={() => set({ chart: { type: t } })} aria-pressed={def.chart?.type === t}
                      className={cn("rounded-full border px-2.5 py-1 text-[12.5px]", def.chart?.type === t ? "border-primary bg-primary-soft text-foreground" : "text-muted-foreground hover:text-foreground")}>{CHART_LABELS[t]}</button>
                  ))}
                </div>
                {run.data && chart !== def.chart?.type && <p className="text-[12px] text-subtle">Showing a {CHART_LABELS[chart as ChartType].toLowerCase()} chart: {hint(def.chart?.type, def.group_by?.length ?? 0)}</p>}
              </Section>
            )}
          </CardBody>
        </Card>

        <div className="min-w-0 space-y-5">
          <Card>
            <CardHeader title={run.data ? `${run.data.row_count.toLocaleString()} ${summary ? "groups" : "records"}` : "Preview"}
              description={run.isFetching ? "Updating…" : src.label} />
            <CardBody>
              {run.isError ? <p className="text-sm text-destructive">{errorMessage(run.error)}</p>
                : !run.data ? <Skeleton className="h-48" /> : <ReportViz result={run.data} chart={def.chart?.type} onDrill={summary ? (values, label) => setDrill({ definition: def, values, label }) : undefined} />}
              {run.data && summary && run.data.rows.length > 0 && <p className="mt-3 text-[12px] text-subtle">Click any bar, point, cell or row to see the records behind it.</p>}
            </CardBody>
          </Card>
          {run.data && chart !== "table" && run.data.rows.length > 0 && (
            <Card><CardHeader title="Data" /><ResultTable result={run.data} onDrill={summary ? (values, label) => setDrill({ definition: def, values, label }) : undefined} /></Card>
          )}
        </div>
      </div>
      <DrillDialog request={drill} onClose={() => setDrill(null)} />
      <SaveDialog mode={saveOpen} onClose={() => setSaveOpen(null)} def={def} saved={saved.data} canShare={cat.data.can_share}
        onSaved={(id) => { qc.invalidateQueries({ queryKey: ["analytics"] }); if (id !== reportId) router.replace(`/reports/builder?id=${id}`); }} />
    </div>
  );
}

function hint(requested: ChartType | undefined, groups: number) {
  if (groups === 0) return "add a grouping to chart the data.";
  if (groups === 2) return "two groupings are shown stacked or as a matrix.";
  if (requested === "matrix") return "a matrix needs two groupings.";
  if (requested === "line" || requested === "column") return "line and column charts need a date grouping.";
  return "a stacked bar needs two groupings.";
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <div><p className="text-[12px] font-medium uppercase tracking-wide text-muted-foreground">{title}</p>{hint && <p className="text-[12px] text-subtle">{hint}</p>}</div>
      {children}
    </div>
  );
}

function AddButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return <button type="button" onClick={onClick} className="inline-flex items-center gap-1 text-[13px] text-primary hover:underline"><Plus className="h-3.5 w-3.5" />{children}</button>;
}

function SaveDialog({ mode, onClose, def, saved, canShare, onSaved }: {
  mode: null | "save" | "copy"; onClose: () => void; def: Definition; saved?: SavedReport; canShare: boolean; onSaved: (id: string) => void;
}) {
  const [f, setF] = useState({ name: "", description: "", visibility: "private" as "private" | "shared" });
  useEffect(() => {
    if (!mode) return;
    setF(mode === "save" && saved ? { name: saved.name, description: saved.description ?? "", visibility: saved.visibility }
      : { name: saved ? `${saved.name} (copy)` : "", description: saved?.description ?? "", visibility: "private" });
  }, [mode, saved]);
  const clean = useMemo(() => cleanDef(def), [def]);
  const m = useMutation({
    mutationFn: async () => {
      const body = { ...f, description: f.description || null, definition: clean };
      return mode === "save" && saved ? (await api.put<SavedReport>(`/analytics/reports/${saved.id}`, body)).data : (await api.post<SavedReport>("/analytics/reports", body)).data;
    },
    onSuccess: (r) => { toast.success(`Saved "${r.name}"`); onClose(); onSaved(r.id); },
  });
  return (
    <Dialog open={!!mode} onOpenChange={(o) => !o && onClose()}>
      <DialogContent title="Save report" className="max-w-md">
        <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <h2 className="text-[15px] font-semibold">{mode === "copy" ? "Save a copy" : "Save report"}</h2>
          <div><Label htmlFor="rs-name">Name</Label><Input id="rs-name" required maxLength={150} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
          <div><Label htmlFor="rs-desc">Description</Label><Textarea id="rs-desc" rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></div>
          <div><Label htmlFor="rs-vis">Who can see it</Label>
            <Select id="rs-vis" value={f.visibility} onChange={(e) => setF({ ...f, visibility: e.target.value as "private" | "shared" })}>
              <option value="private">Only me</option>
              {canShare && <option value="shared">Everyone (each person sees only records they have access to)</option>}
            </Select></div>
          {m.isError && <p className="text-sm text-destructive">{errorMessage(m.error)}</p>}
          <div className="flex justify-end"><Button type="submit" size="sm" loading={m.isPending}>Save</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function ReportBuilderPage() {
  return <Suspense fallback={<Skeleton className="h-[600px]" />}><Builder /></Suspense>;
}
