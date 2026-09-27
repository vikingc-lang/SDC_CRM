"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Target, Trophy } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { StatTile } from "@/components/charts";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Table, Tabs, Td } from "@/components/ui/extra";
import { Input, Select } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { cn, money, shortDate } from "@/lib/utils";

export interface Tier { from_pct: number; rate: number }
export interface Plan { id: string; name: string; description: string | null; base_rate: number; tiers: Tier[]; roles: string[]; member_ids: string[]; active: boolean }
interface Line { from_pct: number; to_pct: number | null; rate: number; bookings: number; amount: number }
interface StatementRow { id: string; title: string; account: string; amount_usd: number; closed_at: string | null; commission: number; effective_rate: number; attainment_after: number | null }
interface Card_ {
  user: { id: string; name: string; role: string }; quota: number; closed: number; attainment_pct: number | null; commit_call: number;
  best_case_call: number; projected_pct: number | null; gap: number; open_pipeline: number; coverage: number | null; won_count: number;
  plan: Plan | null; commission: { total: number; lines: Line[] };
}
interface MyView extends Card_ { period: string; statement: StatementRow[]; is_manager: boolean }
interface TeamView { period: string; rows: Card_[]; team: { quota: number; closed: number; attainment_pct: number | null; commit_call: number; commission: number; at_or_above: number; no_quota: number } }
interface Periods { periods: { period: string; label: string; current: boolean }[] }

const pct = (v: number | null | undefined) => (v == null ? "—" : `${v.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`);

/** Attainment against quota; the fill turns to the "good" status color at 100%. */
export function AttainmentBar({ value, className }: { value: number | null; className?: string }) {
  if (value == null) return <span className="text-[12.5px] text-subtle">No quota</span>;
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="relative h-2 min-w-[80px] flex-1 overflow-hidden rounded-full bg-muted" role="meter" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={Math.round(value)} aria-label="Quota attainment">
        <div className="h-full rounded-full" style={{ width: `${Math.min(value, 100)}%`, background: value >= 100 ? "var(--status-good)" : "hsl(var(--primary))" }} />
      </div>
      <span className="w-14 text-right text-[12.5px] tabular">{pct(value)}</span>
    </div>
  );
}

export function planSummary(p: Pick<Plan, "base_rate" | "tiers">) {
  const tiers = [...p.tiers].sort((a, b) => a.from_pct - b.from_pct);
  if (!tiers.length) return `${p.base_rate}% of bookings`;
  return [`${p.base_rate}% up to ${tiers[0].from_pct}% of quota`, ...tiers.map((t) => `${t.rate}% from ${t.from_pct}%`)].join(" · ");
}

export function PerformanceView() {
  const periods = useQuery({ queryKey: ["performance", "periods"], queryFn: () => get<Periods>("/performance/periods") });
  const [period, setPeriod] = useState("");
  const [view, setView] = useState<"me" | "team">("me");
  useEffect(() => { if (!period && periods.data) setPeriod(periods.data.periods.find((p) => p.current)!.period); }, [period, periods.data]);
  const mine = useQuery({ queryKey: ["performance", "me", period], queryFn: () => get<MyView>("/performance/me", { period }), enabled: !!period });
  if (!period || !mine.data) return <Skeleton className="h-96" />;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Select aria-label="Quarter" className="w-auto" value={period} onChange={(e) => setPeriod(e.target.value)}>
          {periods.data!.periods.map((p) => <option key={p.period} value={p.period}>{p.label}{p.current ? " (this quarter)" : ""}</option>)}
        </Select>
        {mine.data.is_manager && <Tabs className="mb-0 border-b-0" value={view} onChange={setView} tabs={[{ value: "me", label: "My performance" }, { value: "team", label: "Team & quotas" }]} />}
        <span className="text-[12.5px] text-muted-foreground">Attainment is closed-won bookings in the quarter, in USD.</span>
      </div>
      {view === "me" ? <Mine data={mine.data} /> : <Team period={period} />}
    </div>
  );
}

function Mine({ data }: { data: MyView }) {
  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Quota" value={data.quota ? money(data.quota, { compact: true }) : "Not set"} sub={data.quota ? `Gap ${money(data.gap, { compact: true })}` : "Ask your manager to set one"} />
        <StatTile label="Closed" value={money(data.closed, { compact: true })} emphasis sub={<AttainmentBar value={data.attainment_pct} />} />
        <StatTile label="Commit call" value={money(data.commit_call, { compact: true })} sub={data.quota ? `Projects to ${pct(data.projected_pct)} of quota` : undefined} />
        <StatTile label="Commission earned" value={money(data.commission.total)} sub={data.plan ? data.plan.name : "No commission plan"} />
      </div>
      <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
        <Card className="min-w-0">
          <CardHeader title="Commission statement" description="Each won deal earns what it adds to your total, so deals closed above quota earn the accelerated rate." />
          {!data.statement.length ? <EmptyState icon={<Trophy className="h-4 w-4" />} title="No closed-won deals in this quarter yet" /> : (
            <Table head={["Deal", "Closed", "Amount", "Rate", "Commission", "Attainment after"]} minWidth={720}>
              {data.statement.map((s) => (
                <tr key={s.id}>
                  <Td><Link href={`/deals/${s.id}`} className="font-medium hover:text-primary hover:underline">{s.title}</Link><span className="block text-[12px] text-muted-foreground">{s.account}</span></Td>
                  <Td className="whitespace-nowrap text-[13px]">{shortDate(s.closed_at)}</Td>
                  <Td className="tabular text-[13px]">{money(s.amount_usd)}</Td>
                  <Td className="tabular text-[13px]">{pct(s.effective_rate)}</Td>
                  <Td className="tabular text-[13px] font-medium">{money(s.commission)}</Td>
                  <Td className="tabular text-[13px]">{pct(s.attainment_after)}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader title="Your plan" icon={<Trophy className="h-4 w-4" />} />
            <CardBody className="space-y-3 text-[13px]">
              {!data.plan ? <p className="text-muted-foreground">You aren’t on a commission plan. Admins assign plans by role or by person.</p> : <>
                <p className="font-medium">{data.plan.name}</p>
                <p className="text-muted-foreground">{planSummary(data.plan)}</p>
                {data.commission.lines.length > 0 && (
                  <table className="w-full text-[12.5px]">
                    <thead><tr className="text-left text-muted-foreground"><th className="py-1 font-normal">Band</th><th className="font-normal">Rate</th><th className="text-right font-normal">Earned</th></tr></thead>
                    <tbody>{data.commission.lines.map((l, i) => (
                      <tr key={i} className="border-t">
                        <td className="py-1">{l.to_pct == null ? `${l.from_pct}%+` : `${l.from_pct}–${l.to_pct}%`}</td>
                        <td className="tabular">{l.rate}%</td><td className="text-right tabular">{money(l.amount)}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                )}
              </>}
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Coverage" icon={<Target className="h-4 w-4" />} />
            <CardBody className="space-y-1.5 text-[13px]">
              <p className="flex justify-between"><span className="text-muted-foreground">Open pipeline this quarter</span><span className="tabular">{money(data.open_pipeline, { compact: true })}</span></p>
              <p className="flex justify-between"><span className="text-muted-foreground">Still to close</span><span className="tabular">{money(data.gap, { compact: true })}</span></p>
              <p className="flex justify-between"><span className="text-muted-foreground">Pipeline coverage</span>
                <span className="tabular">{data.coverage == null ? (data.quota ? "Quota met" : "—") : `${data.coverage}×`}</span></p>
              {data.coverage != null && data.coverage < 3 && <p className="text-[12px] text-subtle">Most teams aim for 3× coverage of the remaining gap.</p>}
            </CardBody>
          </Card>
        </div>
      </div>
    </>
  );
}

function Team({ period }: { period: string }) {
  const qc = useQueryClient();
  const team = useQuery({ queryKey: ["performance", "team", period], queryFn: () => get<TeamView>("/performance/team", { period }) });
  const setQuota = useMutation({
    mutationFn: async (v: { user_id: string; amount: number }) => (await api.put("/performance/quotas", [{ period, ...v }])).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["performance"] }); toast.success("Quota saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (!team.data) return <Skeleton className="h-80" />;
  const t = team.data.team;
  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Team quota" value={money(t.quota, { compact: true })} sub={t.no_quota ? `${t.no_quota} without a quota` : "Everyone has a quota"} />
        <StatTile label="Team closed" value={money(t.closed, { compact: true })} emphasis sub={<AttainmentBar value={t.attainment_pct} />} />
        <StatTile label="Team commit call" value={money(t.commit_call, { compact: true })} />
        <StatTile label="At or above quota" value={`${t.at_or_above} of ${team.data.rows.length}`} sub={`Commission ${money(t.commission, { compact: true })}`} />
      </div>
      <Card className="overflow-hidden">
        <CardHeader title="Leaderboard" description="Set each person's quota for the quarter; it saves when you leave the field." />
        {!team.data.rows.length ? <EmptyState icon={<Target className="h-4 w-4" />} title="Nobody reports to you yet" /> : (
          <Table head={["Seller", "Quota (USD)", "Closed", "Attainment", "Commit call", "Coverage", "Commission"]} minWidth={900}>
            {team.data.rows.map((r) => (
              <tr key={r.user.id}>
                <Td><span className="font-medium">{r.user.name}</span>{r.plan && <span className="block text-[12px] text-muted-foreground">{r.plan.name}</span>}</Td>
                <Td><Input key={`${r.user.id}-${r.quota}`} aria-label={`Quota for ${r.user.name}`} type="number" min={0} step={1000} className="h-8 w-32"
                  defaultValue={r.quota || ""} placeholder="Not set"
                  onBlur={(e) => { const v = Number(e.target.value || 0); if (v !== r.quota) setQuota.mutate({ user_id: r.user.id, amount: v }); }} /></Td>
                <Td className="tabular text-[13px]">{money(r.closed, { compact: true })}<span className="block text-[12px] text-muted-foreground">{r.won_count} won</span></Td>
                <Td className="min-w-[160px]"><AttainmentBar value={r.attainment_pct} /></Td>
                <Td className="tabular text-[13px]">{money(r.commit_call, { compact: true })}</Td>
                <Td className="tabular text-[13px]">{r.coverage == null ? (r.quota ? <Badge tone="good">Met</Badge> : "—") : `${r.coverage}×`}</Td>
                <Td className="tabular text-[13px]">{money(r.commission.total)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}
