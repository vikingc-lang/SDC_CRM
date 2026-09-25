"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CalendarClock, HeartHandshake, RefreshCw, Rocket, UserMinus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/AppShell";
import { StatTile } from "@/components/charts";
import { HealthMeter } from "@/components/indicators";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StatusPill, Table, Tabs, Td, fmtMoney } from "@/components/ui/extra";
import { Select } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import type { Contract, Milestone, OnboardingProject } from "@/lib/types";
import { shortDate } from "@/lib/utils";

interface ChurnRow {
  id: string; name: string; churn_risk: number; health_score: number; relationship_strength: number; owner: { full_name: string } | null;
  churn_factors: { utilization_pct?: number; active_user_trend_pct?: number; open_high_tickets?: number; open_critical_tickets?: number; champions_departed?: string[] };
}

function churnTone(v: number) {
  return v >= 60 ? "var(--status-critical)" : v >= 30 ? "var(--status-warning)" : "var(--status-good)";
}

export default function SuccessPage() {
  const [tab, setTab] = useState<"onboarding" | "churn" | "renewals">("onboarding");
  const onboarding = useQuery({ queryKey: ["success", "onboarding"], queryFn: () => get<OnboardingProject[]>("/success/onboarding") });
  const churn = useQuery({ queryKey: ["success", "churn"], queryFn: () => get<ChurnRow[]>("/success/churn") });
  const renewals = useQuery({ queryKey: ["success", "renewals"], queryFn: () => get<Contract[]>("/success/renewals", { days: 180 }) });

  const active = onboarding.data?.filter((p) => p.status !== "completed") ?? [];
  const atRisk = churn.data?.filter((c) => c.churn_risk >= 60) ?? [];
  const renewalAcv = renewals.data?.reduce((a, c) => a + c.acv, 0) ?? 0;

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Customer success" description="Onboarding hand-offs from Closed-Won, churn early warning and renewal coverage in one place." />
      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Active onboardings" value={String(active.length)} icon={<Rocket className="h-4 w-4" />}
          sub={`${active.reduce((a, p) => a + p.overdue, 0)} overdue milestone(s)`} />
        <StatTile label="Churn watchlist" value={String(atRisk.length)} icon={<AlertTriangle className="h-4 w-4" />} sub="customers at 60+ churn risk" />
        <StatTile label="Renewals ≤ 180 days" value={String(renewals.data?.length ?? 0)} icon={<CalendarClock className="h-4 w-4" />} sub={`${fmtMoney(renewalAcv, "USD", true)} ACV up for renewal`} />
        <StatTile label="Renewal deals opened" value={String(renewals.data?.filter((r) => r.renewal_deal_id).length ?? 0)} icon={<RefreshCw className="h-4 w-4" />} sub="auto-created 90–120 days out" />
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[
        { value: "onboarding", label: "Onboarding", count: onboarding.data?.length },
        { value: "churn", label: "Churn early warning", count: atRisk.length },
        { value: "renewals", label: "Renewals", count: renewals.data?.length },
      ]} />
      {tab === "onboarding" && <Onboarding projects={onboarding.data} loading={onboarding.isLoading} />}
      {tab === "churn" && <Churn rows={churn.data} loading={churn.isLoading} />}
      {tab === "renewals" && <Renewals rows={renewals.data} loading={renewals.isLoading} />}
    </div>
  );
}

function Onboarding({ projects, loading }: { projects?: OnboardingProject[]; loading: boolean }) {
  const qc = useQueryClient();
  const { can } = useMe();
  const update = useMutation({
    mutationFn: async (v: { id: string; status: Milestone["status"] }) => (await api.patch(`/success/milestones/${v.id}`, { status: v.status })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["success"] }); toast.success("Milestone updated, account health rescored"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (loading) return <Skeleton className="h-48 w-full" />;
  if (!projects?.length) return <Card><EmptyState icon={<Rocket className="h-4 w-4" />} title="No onboarding projects yet" description="Moving a deal to Closed Won provisions a workspace with the pre-sales context attached." /></Card>;
  return (
    <div className="space-y-4">
      {projects.map((p) => (
        <Card key={p.id}>
          <CardHeader
            icon={<Rocket className="h-4 w-4 text-primary" />}
            title={<Link href={`/accounts/${p.account.id}`} className="hover:underline">{p.name}</Link>}
            description={<>Owner {p.owner?.full_name ?? "unassigned"} · kickoff {shortDate(p.kickoff_date)} · go-live {shortDate(p.target_go_live)}{p.deal_id && <> · <Link href={`/deals/${p.deal_id}`} className="underline">source deal</Link></>}</>}
            action={<StatusPill status={p.status} />}
          />
          <CardBody>
            <div className="mb-4 flex items-center gap-3">
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${p.progress}%` }} /></div>
              <span className="text-[12.5px] tabular-nums text-muted-foreground">{p.progress}% complete</span>
            </div>
            <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
              <ul className="divide-y rounded-md border">
                {p.milestones.map((m) => (
                  <li key={m.id} className="flex flex-wrap items-center gap-3 px-3 py-2">
                    <span className="min-w-0 flex-1 text-[13.5px]">{m.title}</span>
                    <span className={m.overdue ? "text-[12px] font-medium text-[color:var(--status-critical)]" : "text-[12px] text-muted-foreground"}>
                      {m.overdue ? "Overdue · " : ""}{shortDate(m.due_date)}
                    </span>
                    {can("success", "update") ? (
                      <Select aria-label={`Status of ${m.title}`} className="h-7 w-32 text-[12.5px]" value={m.status}
                        onChange={(e) => update.mutate({ id: m.id, status: e.target.value as Milestone["status"] })}>
                        {["pending", "in_progress", "done", "blocked"].map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
                      </Select>
                    ) : <StatusPill status={m.status} />}
                  </li>
                ))}
              </ul>
              <div className="space-y-3 rounded-md bg-surface-2/60 p-3 text-[12.5px]">
                <p className="font-medium">Pre-sales hand-off</p>
                {p.scope.buyer_priorities?.length ? <div><p className="text-muted-foreground">Buyer priorities</p><ul className="list-disc pl-4">{p.scope.buyer_priorities.map((x) => <li key={x}>{x}</li>)}</ul></div> : null}
                {p.scope.products?.length ? <div><p className="text-muted-foreground">Products</p><p>{p.scope.products.map((x) => `${x.name} × ${x.quantity}`).join(", ")}</p></div> : null}
                {p.scope.stakeholders?.length ? <div><p className="text-muted-foreground">Stakeholders</p><p>{p.scope.stakeholders.map((s) => `${s.name} (${s.role})`).join(", ")}</p></div> : null}
                {p.scope.competitive_context?.length ? <div><p className="text-muted-foreground">Competitive context</p><p>{p.scope.competitive_context.join("; ")}</p></div> : null}
              </div>
            </div>
          </CardBody>
        </Card>
      ))}
    </div>
  );
}

function Churn({ rows, loading }: { rows?: ChurnRow[]; loading: boolean }) {
  if (loading) return <Skeleton className="h-48 w-full" />;
  if (!rows?.length) return <Card><EmptyState icon={<HeartHandshake className="h-4 w-4" />} title="No customers yet" /></Card>;
  return (
    <Card>
      <Table head={["Customer", "Churn risk", "Health", "Adoption", "Tickets", "Champion turnover", "Owner"]} minWidth={860}>
        {rows.map((r) => {
          const f = r.churn_factors ?? {};
          return (
            <tr key={r.id}>
              <Td><Link href={`/accounts/${r.id}`} className="font-medium hover:underline">{r.name}</Link></Td>
              <Td><span className="inline-flex items-center gap-2 font-semibold tabular-nums" style={{ color: churnTone(r.churn_risk) }}>{r.churn_risk}</span></Td>
              <Td className="w-36"><HealthMeter score={r.health_score} /></Td>
              <Td className="text-[12.5px]">{f.utilization_pct ?? "—"}% licences used{f.active_user_trend_pct !== undefined && <span className="block text-muted-foreground">{f.active_user_trend_pct > 0 ? "+" : ""}{f.active_user_trend_pct}% active users (30d)</span>}</Td>
              <Td className="text-[12.5px]">{(f.open_critical_tickets ?? 0) + (f.open_high_tickets ?? 0) ? `${f.open_critical_tickets ?? 0} critical · ${f.open_high_tickets ?? 0} high` : "None open"}</Td>
              <Td className="text-[12.5px]">{f.champions_departed?.length ? <span className="inline-flex items-center gap-1 text-[color:var(--status-critical)]"><UserMinus className="h-3.5 w-3.5" />{f.champions_departed.join(", ")}</span> : "Stable"}</Td>
              <Td className="text-[12.5px]">{r.owner?.full_name ?? "—"}</Td>
            </tr>
          );
        })}
      </Table>
    </Card>
  );
}

function Renewals({ rows, loading }: { rows?: Contract[]; loading: boolean }) {
  const qc = useQueryClient();
  const { can } = useMe();
  const run = useMutation({
    mutationFn: async () => (await api.post<Record<string, number>>("/success/renewals/run")).data,
    onSuccess: (s) => { qc.invalidateQueries(); toast.success(`Renewal scan complete: ${Object.entries(s).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ")}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (loading) return <Skeleton className="h-48 w-full" />;
  return (
    <Card>
      <CardHeader title="Contracts expiring in the next 180 days" description="Renewal opportunities are created 90–120 days before expiry and carry the original terms."
        action={can("contracts", "create") && <Button size="sm" variant="outline" loading={run.isPending} onClick={() => run.mutate()}><RefreshCw className="h-3.5 w-3.5" />Run renewal scan</Button>} />
      {!rows?.length ? <EmptyState icon={<CalendarClock className="h-4 w-4" />} title="No contracts expiring soon" /> : (
        <Table head={["Contract", "Account", "Ends", "ACV", "Terms", "Renewal deal"]} minWidth={820}>
          {rows.map((c) => (
            <tr key={c.id}>
              <Td><span className="font-medium">{c.contract_number}</span><span className="block text-[12px] text-muted-foreground">{c.name}</span></Td>
              <Td><Link href={`/accounts/${c.account.id}`} className="hover:underline">{c.account.name}</Link></Td>
              <Td><span className={c.days_to_expiry <= 90 ? "font-medium text-[color:var(--status-warning)]" : ""}>{shortDate(c.end_date, true)}</span><span className="block text-[12px] text-muted-foreground">in {c.days_to_expiry} days</span></Td>
              <Td className="tabular-nums">{fmtMoney(c.acv, c.currency)}</Td>
              <Td className="text-[12.5px]">{c.payment_terms}{c.auto_renew ? " · auto-renew" : ""}</Td>
              <Td>{c.renewal_deal_id ? <Link href={`/deals/${c.renewal_deal_id}`} className="text-[13px] text-primary hover:underline">Open renewal →</Link> : <span className="text-[12.5px] text-muted-foreground">Created at 120 days</span>}</Td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}
