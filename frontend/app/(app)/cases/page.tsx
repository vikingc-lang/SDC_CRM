"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LifeBuoy, Plus, Search } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { PageHeader } from "@/components/AppShell";
import { StatTile } from "@/components/charts";
import { type CaseRow, PriorityPill, type ServiceMeta, SlaBadge } from "@/components/service";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { StatusPill, Table, Tabs, Td } from "@/components/ui/extra";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import { BulkBar, RowBox, SelectAllBox, useSelection } from "@/components/bulk";
import { ListViewPicker, useListView, ViewGrid } from "@/components/listviews";
import type { AccountListItem } from "@/lib/types";
import { relativeDays } from "@/lib/utils";

type View = "mine" | "unassigned" | "open" | "breached" | "resolved" | "all";
interface Stats { mine: number; unassigned: number; open: number; breached: number; csat_30d: number | null; csat_responses_30d: number }

export default function CasesPage() {
  const { can } = useMe();
  const [view, setView] = useState<View>("open");
  const [queue, setQueue] = useState("");
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const meta = useQuery({ queryKey: ["cases", "meta"], queryFn: () => get<ServiceMeta>("/cases/meta") });
  const stats = useQuery({ queryKey: ["cases", "stats"], queryFn: () => get<Stats>("/cases/stats") });
  const list = useQuery({ queryKey: ["cases", "list", view, queue, search], placeholderData: (p) => p,
    queryFn: () => get<CaseRow[]>("/cases", { view, queue_id: queue || undefined, search: search || undefined }) });
  const s = stats.data;
  const sel = useSelection((list.data ?? []).map((c) => c.id));
  const bulk = can("cases", "update");
  const lv = useListView("cases");
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader title="Service" description="Customer cases with SLA clocks, queues and a shared knowledge base."
        actions={can("cases", "create") && <Button size="sm" onClick={() => setCreating(true)}><Plus className="h-3.5 w-3.5" />New case</Button>} />
      <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="My open cases" value={s ? String(s.mine) : "…"} />
        <StatTile label="Unassigned" value={s ? String(s.unassigned) : "…"} sub={s ? `of ${s.open} open` : undefined} />
        <StatTile label="SLA breached" value={s ? String(s.breached) : "…"} sub="open cases past a target" />
        <StatTile label="CSAT, last 30 days" value={s?.csat_30d != null ? `${s.csat_30d.toFixed(1)} / 5` : "—"} sub={s ? `${s.csat_responses_30d} response${s.csat_responses_30d === 1 ? "" : "s"}` : undefined} />
      </div>
      <ListViewPicker lv={lv} className="mb-3 flex-wrap" />
      {lv.view ? <ViewGrid lv={lv} /> : (<>
      <Tabs value={view} onChange={setView} tabs={[
        { value: "open", label: "All open" }, { value: "mine", label: "Mine" }, { value: "unassigned", label: "Unassigned" },
        { value: "breached", label: "Breached" }, { value: "resolved", label: "Resolved" }, { value: "all", label: "Everything" }]} />
      <div className="mb-4 flex flex-wrap gap-2">
        <div className="relative min-w-0 flex-1 sm:max-w-xs">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input aria-label="Search cases" className="pl-8" placeholder="Case number or subject" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <Select aria-label="Queue" className="w-auto" value={queue} onChange={(e) => setQueue(e.target.value)}>
          <option value="">All queues</option>{meta.data?.queues.map((q) => <option key={q.id} value={q.id}>{q.name}</option>)}
        </Select>
      </div>
      <BulkBar entity="cases" sel={sel} />
      <Card>
        {!list.data ? <Skeleton className="m-5 h-48" /> : !list.data.length ? (
          <EmptyState icon={<LifeBuoy className="h-4 w-4" />} title="No cases here" description={view === "mine" ? "Nothing assigned to you right now." : undefined} />
        ) : (
          <Table head={[...(bulk ? [<SelectAllBox key="all" sel={sel} label="Select all cases" />] : []), "Case", "Account", "Priority", "Status", "SLA", "Owner", "Updated"]} minWidth={900}>
            {list.data.map((c) => (
              <tr key={c.id} className={sel.has(c.id) ? "bg-primary-soft/40" : undefined}>
                {bulk && <Td className="w-8"><RowBox sel={sel} id={c.id} label={c.case_number} /></Td>}
                <Td><Link href={`/cases/${c.id}`} className="font-medium hover:text-primary hover:underline">{c.subject}</Link>
                  <span className="block text-[12px] text-muted-foreground tabular">{c.case_number}{c.queue ? ` · ${c.queue}` : ""}</span></Td>
                <Td className="text-[13px]">{c.account}</Td>
                <Td><PriorityPill p={c.priority} /></Td>
                <Td><StatusPill status={c.status} /></Td>
                <Td><SlaBadge clocks={c.clocks} status={c.status} /></Td>
                <Td className="text-[13px]">{c.owner ?? <span className="text-muted-foreground">Unassigned</span>}</Td>
                <Td className="whitespace-nowrap text-[12.5px] text-muted-foreground">{relativeDays(c.updated_at)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      </>)}
      {meta.data && <NewCaseDialog open={creating} onClose={() => setCreating(false)} meta={meta.data} />}
    </div>
  );
}

function NewCaseDialog({ open, onClose, meta }: { open: boolean; onClose: () => void; meta: ServiceMeta }) {
  const router = useRouter();
  const qc = useQueryClient();
  const [accSearch, setAccSearch] = useState("");
  const [f, setF] = useState({ account_id: "", contact_id: "", subject: "", description: "", severity: "medium", channel: "email", category: "", queue_id: "" });
  const accounts = useQuery({ queryKey: ["accounts", "pick", accSearch], enabled: open, placeholderData: (p) => p,
    queryFn: () => get<AccountListItem[]>("/accounts", { search: accSearch || undefined, limit: 25 }) });
  const contacts = useQuery({ queryKey: ["contacts", "of", f.account_id], enabled: !!f.account_id,
    queryFn: () => get<{ id: string; first_name: string; last_name: string; email: string | null }[]>("/contacts", { account_id: f.account_id }) });
  const create = useMutation({
    mutationFn: async () => (await api.post<{ id: string }>("/cases", { ...f, contact_id: f.contact_id || null, queue_id: f.queue_id || null,
      category: f.category || null, description: f.description || null })).data,
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["cases"] }); onClose(); router.push(`/cases/${r.id}`); },
  });
  const sla = meta.sla[f.severity as keyof ServiceMeta["sla"]];
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent title="New case" className="max-w-lg">
        <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <h2 className="text-[15px] font-semibold">New case</h2>
          <div><Label htmlFor="nc-acc-search">Account</Label>
            <div className="grid grid-cols-2 gap-2">
              <Input id="nc-acc-search" placeholder="Search accounts" value={accSearch} onChange={(e) => setAccSearch(e.target.value)} />
              <Select id="nc-account" aria-label="Account" required value={f.account_id} onChange={(e) => setF({ ...f, account_id: e.target.value, contact_id: "" })}>
                <option value="">Choose…</option>{accounts.data?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
            </div></div>
          <div><Label htmlFor="nc-contact">Contact</Label>
            <Select id="nc-contact" value={f.contact_id} disabled={!f.account_id} onChange={(e) => setF({ ...f, contact_id: e.target.value })}>
              <option value="">No contact</option>{contacts.data?.map((c) => <option key={c.id} value={c.id}>{c.first_name} {c.last_name}{c.email ? ` · ${c.email}` : ""}</option>)}
            </Select></div>
          <div><Label htmlFor="nc-subject">Subject</Label><Input id="nc-subject" required minLength={2} maxLength={300} value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })} /></div>
          <div><Label htmlFor="nc-desc">Description</Label><Textarea id="nc-desc" rows={3} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></div>
          <div className="grid grid-cols-3 gap-2">
            <div><Label htmlFor="nc-priority">Priority</Label><Select id="nc-priority" value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value })}>
              {meta.priorities.map((p) => <option key={p} value={p}>{p[0].toUpperCase() + p.slice(1)}</option>)}</Select></div>
            <div><Label htmlFor="nc-channel">Channel</Label><Select id="nc-channel" value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}>
              {meta.channels.map((c) => <option key={c} value={c}>{c[0].toUpperCase() + c.slice(1)}</option>)}</Select></div>
            <div><Label htmlFor="nc-queue">Queue</Label><Select id="nc-queue" value={f.queue_id} onChange={(e) => setF({ ...f, queue_id: e.target.value })}>
              <option value="">Default</option>{meta.queues.map((q) => <option key={q.id} value={q.id}>{q.name}</option>)}</Select></div>
          </div>
          <div><Label htmlFor="nc-category">Category</Label><Input id="nc-category" maxLength={60} placeholder="e.g. Access, Billing, Integrations" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} /></div>
          {sla && <p className="text-[12px] text-subtle">Targets for this priority: first response in {sla.first_response_hours}h, resolve in {sla.resolve_hours}h.</p>}
          {create.isError && <p className="text-sm text-destructive">{errorMessage(create.error)}</p>}
          <div className="flex justify-end"><Button type="submit" size="sm" loading={create.isPending}>Create case</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
