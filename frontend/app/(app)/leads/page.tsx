"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Magnet, Plus, Search } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/AppShell";
import { SOURCES, ScoreBar } from "@/components/leads";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { StatusPill, Table, Td, Tabs } from "@/components/ui/extra";
import { Input, Label, Select } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import type { Lead } from "@/lib/types";
import { relativeDays } from "@/lib/utils";

const FILTERS = { open: "new,working,mql,sql,recycled", mql: "mql", sql: "sql", converted: "converted", disqualified: "disqualified", all: undefined } as const;
const FUNNEL = [["new", "New"], ["working", "Working"], ["mql", "MQL"], ["sql", "SQL"], ["converted", "Converted"]] as const;

interface Summary { by_status: Record<string, number>; by_source: Record<string, number>; mql_threshold: number; total: number }

export default function LeadsPage() {
  const [tab, setTab] = useState<keyof typeof FILTERS>("open");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const { can } = useMe();
  const { data: summary } = useQuery({ queryKey: ["leads", "summary"], queryFn: () => get<Summary>("/leads/summary") });
  const { data, isLoading } = useQuery({ queryKey: ["leads", tab, q], queryFn: () => get<Lead[]>("/leads", { status: FILTERS[tab], q: q || undefined }) });
  const threshold = summary?.mql_threshold ?? 60;
  const top = Math.max(1, ...FUNNEL.map(([k]) => summary?.by_status[k] ?? 0));

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader title="Leads" description="Every channel lands here: deduplicated, enriched, scored on fit and engagement, and routed to the right owner."
        actions={can("leads", "create") ? <Button size="sm" onClick={() => setOpen(true)}><Plus className="h-3.5 w-3.5" />New lead</Button> : undefined} />

      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-5">
        {FUNNEL.map(([k, label], i) => {
          const n = summary?.by_status[k] ?? 0;
          return (
            <Card key={k} className="p-3.5">
              <p className="text-[12px] text-muted-foreground">{i + 1}. {label}</p>
              <p className="tabular mt-0.5 text-[22px] font-semibold leading-7">{n}</p>
              <div className="mt-2 h-1 rounded-full bg-muted"><div className="h-1 rounded-full bg-primary" style={{ width: `${(100 * n) / top}%` }} /></div>
            </Card>
          );
        })}
      </div>
      {summary && (
        <p className="-mt-3 mb-5 text-[12.5px] text-muted-foreground">
          By source: {Object.entries(summary.by_source).sort((a, b) => b[1] - a[1]).map(([s, n]) => `${s.replace("_", " ")} ${n}`).join(" · ")}
          {" "}· {summary.by_status.disqualified ?? 0} disqualified · MQL threshold {threshold}
        </p>
      )}

      <div className="flex flex-wrap items-end gap-3">
        <Tabs className="mb-0 flex-1" value={tab} onChange={setTab} tabs={[{ value: "open", label: "Open" }, { value: "mql", label: "MQL" },
          { value: "sql", label: "SQL" }, { value: "converted", label: "Converted" }, { value: "disqualified", label: "Disqualified" }, { value: "all", label: "All" }]} />
        <div className="relative mb-2 w-full sm:w-64">
          <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-subtle" />
          <Input className="h-8 pl-8" placeholder="Name, email or company" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>
      <Card className="mt-3 overflow-hidden">
        {isLoading ? <Skeleton className="m-4 h-40" /> : !data?.length ? (
          <EmptyState icon={<Magnet className="h-4 w-4" />} title="No leads here" description="Share a hosted web form or connect a webhook from Admin → Lead management." />
        ) : (
          <Table head={["Lead", "Company", "Score", "Fit / engagement", "Qualification", "Source", "Owner", "Status", "Created"]} minWidth={1060}>
            {data.map((l) => (
              <tr key={l.id} className="hover:bg-muted/50">
                <Td>
                  <Link href={`/leads/${l.id}`} className="font-medium hover:underline">{l.full_name || l.email}</Link>
                  <p className="text-[12px] text-muted-foreground">{l.job_title ?? l.email}</p>
                </Td>
                <Td>
                  {l.company_name ?? "—"}
                  <p className="text-[12px] text-muted-foreground">{[l.industry, l.region].filter(Boolean).join(" · ")}</p>
                  {l.account_match && <Badge tone="primary" className="mt-1">Existing account</Badge>}
                  {l.duplicates > 0 && <Badge tone="warning" className="ml-1 mt-1">{l.duplicates} possible duplicate{l.duplicates > 1 ? "s" : ""}</Badge>}
                </Td>
                <Td><ScoreBar score={l.score} threshold={threshold} /></Td>
                <Td className="tabular text-muted-foreground">{l.fit_score} / {l.engagement_score}</Td>
                <Td className="text-[12.5px]"><span className="uppercase">{l.qualification.framework}</span> {l.qualification.met}/{l.qualification.total}</Td>
                <Td className="capitalize text-muted-foreground">{l.source.replace("_", " ")}{l.campaign && <p className="text-[12px] normal-case">{l.campaign}</p>}</Td>
                <Td>{l.owner?.full_name ?? <span className="text-subtle">Unassigned</span>}</Td>
                <Td><StatusPill status={l.status} /></Td>
                <Td className="text-muted-foreground">{relativeDays(l.created_at)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      <NewLeadDialog open={open} onOpenChange={setOpen} />
    </div>
  );
}

function NewLeadDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const empty = { first_name: "", last_name: "", email: "", job_title: "", company_name: "", country: "", industry: "", employee_count: "", source: "outbound", campaign: "", consent: "unknown" };
  const [f, setF] = useState(empty);
  const qc = useQueryClient();
  const router = useRouter();
  const m = useMutation({
    mutationFn: async () => (await api.post<Lead & { merged: boolean }>("/leads", {
      ...Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v === "" ? null : v])),
      employee_count: f.employee_count ? Number(f.employee_count) : null, source: f.source, consent: f.consent,
    })).data,
    onSuccess: (l) => {
      qc.invalidateQueries({ queryKey: ["leads"] });
      onOpenChange(false);
      setF(empty);
      toast.success(l.merged ? "Matched an open lead: details merged" : `Lead created and routed to ${l.owner?.full_name ?? "the queue"}`);
      router.push(`/leads/${l.id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const input = (k: keyof typeof empty, label: string, props: React.InputHTMLAttributes<HTMLInputElement> = {}) => (
    <div><Label htmlFor={`lead-${k}`}>{label}</Label><Input id={`lead-${k}`} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} {...props} /></div>
  );
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="New lead" className="max-w-lg">
        <form className="p-5" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <h2 className="text-[15px] font-semibold">New lead</h2>
          <p className="mt-0.5 text-[13px] text-muted-foreground">Checked for duplicates against leads, contacts and accounts, then enriched, scored and routed.</p>
          <div className="mt-4 grid grid-cols-2 gap-3">
            {input("first_name", "First name")}
            {input("last_name", "Last name")}
            <div className="col-span-2">{input("email", "Email", { type: "email", required: true })}</div>
            {input("job_title", "Job title")}
            {input("company_name", "Company")}
            {input("country", "Country")}
            {input("industry", "Industry")}
            {input("employee_count", "Employees", { type: "number", min: 0 })}
            <div><Label htmlFor="lead-source">Source</Label><Select id="lead-source" value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })}>
              {SOURCES.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}</Select></div>
            {input("campaign", "Campaign")}
            <div><Label htmlFor="lead-consent">Email consent</Label><Select id="lead-consent" value={f.consent} onChange={(e) => setF({ ...f, consent: e.target.value })}>
              {["unknown", "granted", "denied"].map((s) => <option key={s}>{s}</option>)}</Select></div>
          </div>
          <div className="mt-5 flex justify-end"><Button type="submit" size="sm" loading={m.isPending}>Create lead</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
