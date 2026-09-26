"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowLeft, ArrowUp, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { ReportViz, type RResult, type SavedReport } from "@/components/reportviz";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { cn, relativeDays } from "@/lib/utils";

type Size = "third" | "half" | "full";
interface TileOut { report_id: string; size: Size; report: SavedReport | null }
interface DashboardFull { id: string; name: string; description: string | null; visibility: "private" | "shared"; owner: string | null; can_edit: boolean; updated_at: string; tiles: TileOut[] }
const SPAN: Record<Size, string> = { third: "lg:col-span-2", half: "lg:col-span-3", full: "lg:col-span-6" };
const SIZE_LABEL: Record<Size, string> = { third: "Third", half: "Half", full: "Full width" };

export default function DashboardPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const d = useQuery({ queryKey: ["analytics", "dashboard", id], queryFn: () => get<DashboardFull>(`/analytics/dashboards/${id}`) });
  const cat = useQuery({ queryKey: ["analytics", "sources"], queryFn: () => get<{ can_share: boolean }>("/analytics/sources") });
  const [edit, setEdit] = useState<null | { name: string; description: string; visibility: "private" | "shared"; tiles: { report_id: string; size: Size }[] }>(null);
  const reports = useQuery({ queryKey: ["analytics", "reports"], queryFn: () => get<SavedReport[]>("/analytics/reports"), enabled: !!edit });
  const save = useMutation({
    mutationFn: async () => (await api.put(`/analytics/dashboards/${id}`, { ...edit, description: edit!.description || null })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["analytics"] }); setEdit(null); toast.success("Dashboard saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const del = useMutation({
    mutationFn: async () => api.delete(`/analytics/dashboards/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["analytics"] }); toast.success("Dashboard deleted"); router.push("/reports"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (d.isError) return <p className="text-sm text-muted-foreground">Dashboard not found. <Link href="/reports" className="text-primary hover:underline">Back to reports</Link></p>;
  if (!d.data) return <Skeleton className="h-[600px]" />;
  const dash = d.data;
  const names = new Map(dash.tiles.map((t) => [t.report_id, t.report?.name]));
  reports.data?.forEach((r) => names.set(r.id, r.name));
  const move = (k: number, dir: -1 | 1) => { const t = [...edit!.tiles]; [t[k], t[k + dir]] = [t[k + dir], t[k]]; setEdit({ ...edit!, tiles: t }); };

  return (
    <div className="mx-auto max-w-7xl">
      <Link href="/reports" className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />Dashboards</Link>
      {edit ? (
        <Card className="mb-5">
          <CardBody className="grid gap-3 sm:grid-cols-[1fr_1fr_200px]">
            <div><Label htmlFor="db-name">Name</Label><Input id="db-name" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></div>
            <div><Label htmlFor="db-desc">Description</Label><Input id="db-desc" value={edit.description} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></div>
            <div><Label htmlFor="db-vis">Who can see it</Label>
              <Select id="db-vis" value={edit.visibility} onChange={(e) => setEdit({ ...edit, visibility: e.target.value as "private" | "shared" })}>
                <option value="private">Only me</option>{cat.data?.can_share && <option value="shared">Everyone</option>}
              </Select></div>
            <div className="flex flex-wrap items-end justify-between gap-2 sm:col-span-3">
              <div className="flex min-w-0 flex-1 items-end gap-2">
                <div className="min-w-0 flex-1 sm:max-w-sm"><Label htmlFor="db-add">Add a report</Label>
                  <Select id="db-add" value="" onChange={(e) => e.target.value && setEdit({ ...edit, tiles: [...edit.tiles, { report_id: e.target.value, size: "half" }] })}>
                    <option value="">Choose a saved report…</option>
                    {reports.data?.filter((r) => edit.visibility === "private" || r.visibility === "shared").map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                  </Select></div>
                <Link href="/reports/builder" className="mb-2 whitespace-nowrap text-[13px] text-primary hover:underline">New report</Link>
              </div>
              <div className="flex gap-2">
                <Button variant="ghost" size="sm" loading={del.isPending} onClick={() => del.mutate()}><Trash2 className="h-3.5 w-3.5" />Delete dashboard</Button>
                <Button variant="outline" size="sm" onClick={() => setEdit(null)}>Cancel</Button>
                <Button size="sm" loading={save.isPending} onClick={() => save.mutate()}>Save</Button>
              </div>
            </div>
          </CardBody>
        </Card>
      ) : (
        <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">{dash.name}</h1>
            <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[13px] text-muted-foreground">
              {dash.description && <span>{dash.description}</span>}
              <Badge tone={dash.visibility === "shared" ? "primary" : "neutral"}>{dash.visibility === "shared" ? "Shared with team" : "Only you"}</Badge>
              <span>· {dash.owner ?? "Unknown"} · updated {relativeDays(dash.updated_at)}</span>
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => qc.invalidateQueries({ queryKey: ["analytics", "tile"] })}><RefreshCw className="h-3.5 w-3.5" />Refresh</Button>
            {dash.can_edit && <Button size="sm" onClick={() => setEdit({ name: dash.name, description: dash.description ?? "", visibility: dash.visibility, tiles: dash.tiles.map(({ report_id, size }) => ({ report_id, size })) })}><Pencil className="h-3.5 w-3.5" />Edit</Button>}
          </div>
        </div>
      )}

      {(edit ? edit.tiles : dash.tiles).length === 0 ? (
        <EmptyState icon={<Plus className="h-4 w-4" />} title="No reports on this dashboard yet" description={dash.can_edit ? "Choose Edit, then add saved reports." : undefined} />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-6">
          {edit ? edit.tiles.map((t, k) => (
            <Card key={`${t.report_id}-${k}`} className={cn("border-dashed", SPAN[t.size])}>
              <CardBody className="space-y-2">
                <div className="flex items-start justify-between gap-2">
                  <p className="font-medium">{names.get(t.report_id) ?? "Report"}</p>
                  <Button variant="ghost" size="icon" aria-label="Remove from dashboard" onClick={() => setEdit({ ...edit, tiles: edit.tiles.filter((_, j) => j !== k) })}><X className="h-3.5 w-3.5" /></Button>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <Select aria-label="Tile width" className="h-8 w-32 text-[13px]" value={t.size} onChange={(e) => { const ts = [...edit.tiles]; ts[k] = { ...t, size: e.target.value as Size }; setEdit({ ...edit, tiles: ts }); }}>
                    {(Object.keys(SIZE_LABEL) as Size[]).map((s) => <option key={s} value={s}>{SIZE_LABEL[s]}</option>)}
                  </Select>
                  <Button variant="ghost" size="icon" aria-label="Move earlier" disabled={k === 0} onClick={() => move(k, -1)}><ArrowUp className="h-3.5 w-3.5" /></Button>
                  <Button variant="ghost" size="icon" aria-label="Move later" disabled={k === edit.tiles.length - 1} onClick={() => move(k, 1)}><ArrowDown className="h-3.5 w-3.5" /></Button>
                </div>
              </CardBody>
            </Card>
          )) : dash.tiles.map((t, k) => <TileView key={`${t.report_id}-${k}`} tile={t} />)}
        </div>
      )}
    </div>
  );
}

function TileView({ tile }: { tile: TileOut }) {
  const r = tile.report;
  const run = useQuery({
    queryKey: ["analytics", "tile", tile.report_id], enabled: !!r, retry: false,
    queryFn: async () => (await api.post<RResult>(`/analytics/reports/${tile.report_id}/run`)).data,
  });
  return (
    <Card className={cn("min-w-0", SPAN[tile.size])}>
      <CardBody>
        {!r ? <p className="text-[13px] text-muted-foreground">This report was deleted or isn&apos;t available to you.</p> : (
          <>
            <div className="mb-3 flex items-start justify-between gap-2">
              <div className="min-w-0">
                <Link href={`/reports/builder?id=${r.id}`} className="font-medium hover:text-primary hover:underline">{r.name}</Link>
                {r.description && <p className="text-[12.5px] text-muted-foreground">{r.description}</p>}
              </div>
            </div>
            {run.isError ? <p className="text-[13px] text-destructive">{errorMessage(run.error)}</p>
              : !run.data ? <Skeleton className="h-32" /> : <ReportViz result={run.data} chart={r.definition.chart?.type} compact />}
          </>
        )}
      </CardBody>
    </Card>
  );
}
