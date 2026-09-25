"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownToLine, ArrowUpFromLine, Ban, Landmark, Radio, Send, Wallet } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/AppShell";
import { AgingBars, StatTile } from "@/components/charts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { StatusPill, Table, Tabs, Td, fmtMoney } from "@/components/ui/extra";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import type { ArSummary } from "@/lib/types";
import { relativeDays } from "@/lib/utils";

interface Aging { totals: ArSummary["buckets"]; open_balance: number; credit_holds: number; accounts: (Omit<ArSummary, "invoices"> & { account: { id: string; name: string } })[] }
interface Runs { connector: string; runs: { id: string; connector: string; direction: string; status: string; stats: Record<string, number>; error: string | null; started_at: string; finished_at: string | null }[] }
interface Events { next_after_id: number; events: { id: number; type: string; entity: string; entity_id: string; payload: Record<string, unknown>; targets: string[]; created_at: string; delivered_at: string | null }[] }

const MODULES: Record<string, string> = { promo: "promo · trade promotions", yield: "Yield · pricing", deduct: "deduct · deductions", nexora: "nexora · data fabric" };

export default function FinancePage() {
  const [tab, setTab] = useState<"ar" | "erp" | "events">("ar");
  const aging = useQuery({ queryKey: ["finance", "aging"], queryFn: () => get<Aging>("/finance/ar-aging") });
  const overdue = aging.data ? aging.data.open_balance - aging.data.totals.current : 0;
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Finance & ERP" description="Customer master, receivables and credit status synced from the ERP; commercial events published to neighbouring SDC modules." />
      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Open receivables" value={fmtMoney(aging.data?.open_balance, "USD", true)} icon={<Wallet className="h-4 w-4" />} />
        <StatTile label="Overdue" value={fmtMoney(overdue, "USD", true)} icon={<Landmark className="h-4 w-4" />}
          sub={aging.data && aging.data.open_balance ? `${Math.round((overdue / aging.data.open_balance) * 100)}% of open balance` : undefined} />
        <StatTile label="90+ days" value={fmtMoney(aging.data?.totals["90_plus"], "USD", true)} sub="drives automatic credit holds" />
        <StatTile label="Credit holds" value={String(aging.data?.credit_holds ?? 0)} icon={<Ban className="h-4 w-4" />} sub="new quotes need finance approval" />
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "ar", label: "A/R aging" }, { value: "erp", label: "ERP sync" }, { value: "events", label: "Ecosystem feed" }]} />
      {tab === "ar" && <ArTab data={aging.data} loading={aging.isLoading} />}
      {tab === "erp" && <ErpTab />}
      {tab === "events" && <EventsTab />}
    </div>
  );
}

function ArTab({ data, loading }: { data?: Aging; loading: boolean }) {
  if (loading) return <Skeleton className="h-60 w-full" />;
  if (!data?.accounts.length) return <Card><EmptyState icon={<Landmark className="h-4 w-4" />} title="No ERP-linked customers" description="Run an ERP sync to match customer masters by tax ID or domain." /></Card>;
  return (
    <div className="space-y-4">
      <Card className="p-5"><p className="mb-3 text-sm font-semibold">Portfolio aging</p><AgingBars buckets={data.totals} /></Card>
      <Card>
        <Table head={["Account", "ERP ID", "Open", "Overdue", "Current", "1–30", "31–60", "61–90", "90+", "Credit"]} minWidth={980}>
          {data.accounts.map((r) => (
            <tr key={r.account.id}>
              <Td><Link href={`/accounts/${r.account.id}`} className="font-medium hover:underline">{r.account.name}</Link></Td>
              <Td className="font-mono text-[12px]">{r.erp_customer_id}</Td>
              <Td className="tabular-nums">{fmtMoney(r.open_balance)}</Td>
              <Td className="tabular-nums font-medium" >{fmtMoney(r.overdue_balance)}</Td>
              {(["current", "1_30", "31_60", "61_90", "90_plus"] as const).map((b) => <Td key={b} className="tabular-nums text-[12.5px] text-muted-foreground">{r.buckets[b] ? fmtMoney(r.buckets[b]) : "—"}</Td>)}
              <Td>{r.credit_hold ? <Badge tone="critical">Credit hold</Badge> : <span className="text-[12px] text-muted-foreground">{fmtMoney(r.credit_available, "USD", true)} of {fmtMoney(r.credit_limit, "USD", true)} free</span>}</Td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}

function ErpTab() {
  const qc = useQueryClient();
  const { can } = useMe();
  const runs = useQuery({ queryKey: ["finance", "runs"], queryFn: () => get<Runs>("/integrations/erp/runs") });
  const sync = useMutation({
    mutationFn: async (direction: "inbound" | "outbound") => (await api.post<{ status: string; error: string | null }>("/integrations/erp/sync", null, { params: { direction } })).data,
    onSuccess: (r) => { qc.invalidateQueries(); r.status === "succeeded" ? toast.success("ERP sync complete") : toast.error(r.error ?? "Sync failed"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader title="ERP connector" description={<>Active connector: <span className="font-medium">{runs.data?.connector ?? "…"}</span>. Inbound pulls customer masters (legal name, tax ID, billing address, credit limit) and open invoices; outbound pushes new customers from Closed-Won.</>}
        action={can("finance", "update") && (
          <div className="flex gap-2">
            <Button size="sm" variant="outline" loading={sync.isPending && sync.variables === "outbound"} onClick={() => sync.mutate("outbound")}><ArrowUpFromLine className="h-3.5 w-3.5" />Push</Button>
            <Button size="sm" loading={sync.isPending && sync.variables === "inbound"} onClick={() => sync.mutate("inbound")}><ArrowDownToLine className="h-3.5 w-3.5" />Pull from ERP</Button>
          </div>
        )} />
      {runs.isLoading ? <Skeleton className="m-5 h-24" /> : !runs.data?.runs.length ? <EmptyState icon={<Landmark className="h-4 w-4" />} title="No sync runs yet" /> : (
        <Table head={["Started", "Direction", "Status", "Result"]}>
          {runs.data.runs.map((r) => (
            <tr key={r.id}>
              <Td className="text-[12.5px]">{new Date(r.started_at).toLocaleString()}</Td>
              <Td className="capitalize">{r.direction}</Td>
              <Td><StatusPill status={r.status} /></Td>
              <Td className="text-[12.5px] text-muted-foreground">{r.error ?? Object.entries(r.stats ?? {}).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(" · ")}</Td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}

function EventsTab() {
  const qc = useQueryClient();
  const { can } = useMe();
  const [target, setTarget] = useState("");
  const events = useQuery({ queryKey: ["finance", "events", target], queryFn: () => get<Events>("/integrations/events", { limit: 200, target: target || undefined }) });
  const deliver = useMutation({
    mutationFn: async () => (await api.post<Record<string, number>>("/integrations/events/deliver")).data,
    onSuccess: (s) => { qc.invalidateQueries({ queryKey: ["finance", "events"] }); toast.success(`Delivery run: ${Object.entries(s).map(([k, v]) => `${v} ${k}`).join(", ")}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const rows = [...(events.data?.events ?? [])].reverse();
  return (
    <Card>
      <CardHeader icon={<Radio className="h-4 w-4 text-primary" />} title="Outbound event feed"
        description="Approved quotes, contracts and wins are published for promo, Yield, deduct and nexora. Modules poll the cursor feed or receive webhooks; nothing leaves your network unless a webhook is configured."
        action={can("admin", "update") && <Button size="sm" variant="outline" loading={deliver.isPending} onClick={() => deliver.mutate()}><Send className="h-3.5 w-3.5" />Deliver webhooks</Button>} />
      <div className="flex flex-wrap gap-1.5 px-5 pb-3">
        {["", ...Object.keys(MODULES)].map((t) => (
          <button key={t} onClick={() => setTarget(t)} className={`rounded-full border px-2.5 py-0.5 text-[12px] ${target === t ? "border-primary bg-primary/10 text-foreground" : "text-muted-foreground hover:text-foreground"}`}>
            {t ? MODULES[t] : "All modules"}
          </button>
        ))}
      </div>
      {events.isLoading ? <Skeleton className="m-5 h-24" /> : !rows.length ? <EmptyState icon={<Radio className="h-4 w-4" />} title="No events yet" /> : (
        <Table head={["#", "Event", "Targets", "Summary", "Created", "Delivered"]} minWidth={860}>
          {rows.map((e) => (
            <tr key={e.id}>
              <Td className="font-mono text-[12px] text-muted-foreground">{e.id}</Td>
              <Td className="font-mono text-[12.5px]">{e.type}</Td>
              <Td><div className="flex flex-wrap gap-1">{e.targets.map((t) => <Badge key={t}>{t}</Badge>)}</div></Td>
              <Td className="max-w-[280px] truncate text-[12px] text-muted-foreground" >
                {["quote_number", "contract_number", "account_name", "currency", "acv", "tcv"].filter((k) => e.payload[k] !== undefined).map((k) => `${k}: ${e.payload[k]}`).join(" · ")}
              </Td>
              <Td className="text-[12px]">{relativeDays(e.created_at)}</Td>
              <Td className="text-[12px]">{e.delivered_at ? relativeDays(e.delivered_at) : <span className="text-muted-foreground">queued</span>}</Td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}
