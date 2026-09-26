"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BarChart3, LayoutDashboard, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { CHART_LABELS, type SavedReport } from "@/components/reportviz";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Table, Td } from "@/components/ui/extra";
import { Input, Label, Select } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { relativeDays } from "@/lib/utils";

interface DashboardItem { id: string; name: string; description: string | null; visibility: "private" | "shared"; owner: string | null; tile_count: number; updated_at: string }
const SOURCE_LABELS: Record<string, string> = { deals: "Opportunities", accounts: "Accounts", contacts: "Contacts", leads: "Leads", activities: "Activities", tasks: "Tasks", quotes: "Quotes", orders: "Orders", cases: "Cases" };

function Visibility({ v }: { v: "private" | "shared" }) {
  return <Badge tone={v === "shared" ? "primary" : "neutral"}>{v === "shared" ? "Shared" : "Only me"}</Badge>;
}

export function DashboardsList() {
  const router = useRouter();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["analytics", "dashboards"], queryFn: () => get<DashboardItem[]>("/analytics/dashboards") });
  const cat = useQuery({ queryKey: ["analytics", "sources"], queryFn: () => get<{ can_share: boolean }>("/analytics/sources") });
  const [f, setF] = useState<{ name: string; visibility: "private" | "shared" } | null>(null);
  const create = useMutation({
    mutationFn: async () => (await api.post<{ id: string }>("/analytics/dashboards", { ...f, tiles: [] })).data,
    onSuccess: (d) => { qc.invalidateQueries({ queryKey: ["analytics"] }); router.push(`/reports/dashboards/${d.id}`); },
  });
  return (
    <>
      <div className="mb-4 flex justify-end"><Button size="sm" onClick={() => setF({ name: "", visibility: "private" })}><Plus className="h-3.5 w-3.5" />New dashboard</Button></div>
      {!list.data ? <Skeleton className="h-40" /> : !list.data.length ? (
        <Card><EmptyState icon={<LayoutDashboard className="h-4 w-4" />} title="No dashboards yet" description="Create one, then add saved reports to it." /></Card>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {list.data.map((d) => (
            <Link key={d.id} href={`/reports/dashboards/${d.id}`} className="group rounded-lg border bg-surface p-4 shadow-card transition-colors hover:border-primary/40">
              <div className="flex items-start justify-between gap-2">
                <p className="font-medium group-hover:text-primary">{d.name}</p>
                <Visibility v={d.visibility} />
              </div>
              {d.description && <p className="mt-1 line-clamp-2 text-[13px] text-muted-foreground">{d.description}</p>}
              <p className="mt-3 text-[12px] text-subtle">{d.tile_count} report{d.tile_count === 1 ? "" : "s"} · {d.owner ?? "Unknown"} · updated {relativeDays(d.updated_at)}</p>
            </Link>
          ))}
        </div>
      )}
      <Dialog open={!!f} onOpenChange={(o) => !o && setF(null)}>
        <DialogContent title="New dashboard" className="max-w-md">
          {f && (
            <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
              <h2 className="text-[15px] font-semibold">New dashboard</h2>
              <div><Label htmlFor="nd-name">Name</Label><Input id="nd-name" required maxLength={150} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
              <div><Label htmlFor="nd-vis">Who can see it</Label>
                <Select id="nd-vis" value={f.visibility} onChange={(e) => setF({ ...f, visibility: e.target.value as "private" | "shared" })}>
                  <option value="private">Only me</option>{cat.data?.can_share && <option value="shared">Everyone</option>}
                </Select></div>
              {create.isError && <p className="text-sm text-destructive">{errorMessage(create.error)}</p>}
              <div className="flex justify-end"><Button type="submit" size="sm" loading={create.isPending}>Create</Button></div>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

export function SavedReportsList() {
  const list = useQuery({ queryKey: ["analytics", "reports"], queryFn: () => get<SavedReport[]>("/analytics/reports") });
  return (
    <Card>
      <CardHeader title="Saved reports" description="Shared reports show each person only the records they have access to."
        action={<Link href="/reports/builder"><Button size="sm"><Plus className="h-3.5 w-3.5" />New report</Button></Link>} />
      {!list.data ? <Skeleton className="m-5 h-40" /> : !list.data.length ? (
        <EmptyState icon={<BarChart3 className="h-4 w-4" />} title="No saved reports yet" description="Build a report and save it to reuse it or put it on a dashboard." />
      ) : (
        <Table head={["Report", "Source", "Shown as", "Visibility", "Owner", "Updated"]} minWidth={760}>
          {list.data.map((r) => (
            <tr key={r.id}>
              <Td><Link href={`/reports/builder?id=${r.id}`} className="font-medium hover:text-primary hover:underline">{r.name}</Link>
                {r.description && <span className="block text-[12px] text-muted-foreground">{r.description}</span>}</Td>
              <Td className="text-[13px]">{SOURCE_LABELS[r.source] ?? r.source}</Td>
              <Td className="text-[13px]">{r.definition.columns ? "List" : CHART_LABELS[r.definition.chart?.type ?? "bar"]}</Td>
              <Td><Visibility v={r.visibility} /></Td>
              <Td className="text-[13px]">{r.owner ?? "—"}</Td>
              <Td className="text-[12.5px] text-muted-foreground">{relativeDays(r.updated_at)}</Td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}
