"use client";

import { useQuery } from "@tanstack/react-query";
import { Download, MessageSquareQuote, Swords, Target, Trophy, TrendingDown } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/AppShell";
import { ForecastByMonth, ForecastByStage, StatTile } from "@/components/charts";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Table, Tabs, Td, fmtMoney } from "@/components/ui/extra";
import { Select } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { downloadFile, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import type { PipelineFull } from "@/lib/types";

interface Debrief { deal_id: string; title: string; account: string; debrief: string }
interface WinLoss {
  won: { count: number; amount: number }; lost: { count: number; amount: number }; win_rate: number;
  loss_reasons: { reason: string; label: string; count: number; amount: number; debriefs: Debrief[] }[];
  competitors: { competitor: string; count: number; amount: number }[]; win_debriefs: Debrief[];
}
interface Forecast {
  total_pipeline: number; weighted_pipeline: number; open_deals: number; at_risk_deals: number; won_this_quarter: number; win_rate: number; avg_deal_size: number;
  by_stage: { stage: string; count: number; total: number; weighted: number }[];
  by_pipeline: { pipeline: string; count: number; total: number; weighted: number }[];
  by_close_month: { month: string; weighted: number }[];
}

export default function ReportsPage() {
  const [tab, setTab] = useState<"forecast" | "winloss">("forecast");
  const [pipelineId, setPipelineId] = useState("");
  const { can } = useMe();
  const pipelines = useQuery({ queryKey: ["pipelines"], queryFn: () => get<PipelineFull[]>("/pipelines") });
  const fc = useQuery({ queryKey: ["reports", "forecast", pipelineId], queryFn: () => get<Forecast>("/reports/forecast", { pipeline_id: pipelineId || undefined }) });
  const wl = useQuery({ queryKey: ["reports", "winloss"], queryFn: () => get<WinLoss>("/reports/win-loss") });
  const exportDeals = () => downloadFile("/admin/export/deals", `deals-${new Date().toISOString().slice(0, 10)}.csv`, { format: "csv" }).catch((e) => toast.error(errorMessage(e)));
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Reports" description="Risk-adjusted forecast across every pipeline, plus a structured win/loss taxonomy with rep debriefs."
        actions={can("deals", "export") && <Button size="sm" variant="outline" onClick={exportDeals}><Download className="h-3.5 w-3.5" />Export deals</Button>} />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "forecast", label: "Forecast" }, { value: "winloss", label: "Win / loss" }]} />
      {tab === "forecast" && (
        <>
          <div className="mb-4 flex items-center gap-2">
            <Select aria-label="Pipeline" className="w-auto" value={pipelineId} onChange={(e) => setPipelineId(e.target.value)}>
              <option value="">All pipelines</option>
              {pipelines.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </Select>
            <span className="text-[12.5px] text-muted-foreground">All amounts converted to USD</span>
          </div>
          {!fc.data ? <Skeleton className="h-72 w-full" /> : (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <StatTile label="Weighted forecast" value={fmtMoney(fc.data.weighted_pipeline, "USD", true)} emphasis sub={`of ${fmtMoney(fc.data.total_pipeline, "USD", true)} open`} />
                <StatTile label="Won this quarter" value={fmtMoney(fc.data.won_this_quarter, "USD", true)} icon={<Trophy className="h-4 w-4" />} />
                <StatTile label="Win rate" value={`${fc.data.win_rate}%`} icon={<Target className="h-4 w-4" />} sub={`avg deal ${fmtMoney(fc.data.avg_deal_size, "USD", true)}`} />
                <StatTile label="Open deals at risk" value={`${fc.data.at_risk_deals} / ${fc.data.open_deals}`} icon={<TrendingDown className="h-4 w-4" />} />
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                <Card><CardHeader title="By stage" description="Track = open value; fill = weighted" /><CardBody><ForecastByStage data={fc.data.by_stage} /></CardBody></Card>
                <Card><CardHeader title="Weighted by close month" /><CardBody><ForecastByMonth data={fc.data.by_close_month} /></CardBody></Card>
              </div>
              {!pipelineId && (
                <Card>
                  <CardHeader title="By pipeline" />
                  <Table head={["Pipeline", "Open deals", "Open value", "Weighted", "Coverage"]}>
                    {fc.data.by_pipeline.map((p) => (
                      <tr key={p.pipeline}>
                        <Td className="font-medium">{p.pipeline}</Td><Td>{p.count}</Td>
                        <Td className="tabular-nums">{fmtMoney(p.total)}</Td><Td className="tabular-nums">{fmtMoney(p.weighted)}</Td>
                        <Td className="w-48"><div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary" style={{ width: `${p.total ? (p.weighted / p.total) * 100 : 0}%` }} /></div></Td>
                      </tr>
                    ))}
                  </Table>
                </Card>
              )}
            </div>
          )}
        </>
      )}
      {tab === "winloss" && (!wl.data ? <Skeleton className="h-72 w-full" /> : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="Win rate" value={`${wl.data.win_rate}%`} emphasis />
            <StatTile label="Won" value={fmtMoney(wl.data.won.amount, "USD", true)} sub={`${wl.data.won.count} deals`} />
            <StatTile label="Lost" value={fmtMoney(wl.data.lost.amount, "USD", true)} sub={`${wl.data.lost.count} deals`} />
            <StatTile label="Top loss reason" value={wl.data.loss_reasons[0]?.label ?? "—"} />
          </div>
          <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
            <Card>
              <CardHeader title="Loss reasons" description="Every Closed-Lost requires a taxonomy reason and a rep debrief." />
              {!wl.data.loss_reasons.length ? <EmptyState icon={<TrendingDown className="h-4 w-4" />} title="No lost deals yet" /> : (
                <CardBody className="space-y-4">
                  {wl.data.loss_reasons.map((r) => {
                    const max = Math.max(...wl.data!.loss_reasons.map((x) => x.amount), 1);
                    return (
                      <div key={r.reason}>
                        <div className="flex items-center justify-between text-[13px]"><span className="font-medium">{r.label}</span><span className="tabular-nums text-muted-foreground">{r.count} · {fmtMoney(r.amount, "USD", true)}</span></div>
                        <div className="mt-1 h-2 overflow-hidden rounded-full bg-muted"><div className="h-full" style={{ width: `${(r.amount / max) * 100}%`, background: "var(--status-critical)" }} /></div>
                        {r.debriefs.map((d) => (
                          <blockquote key={d.deal_id} className="mt-2 border-l-2 pl-3 text-[12.5px]">
                            <span className="text-muted-foreground">“{d.debrief}”</span>
                            <Link href={`/deals/${d.deal_id}`} className="block text-[12px] text-primary hover:underline">{d.account}: {d.title}</Link>
                          </blockquote>
                        ))}
                      </div>
                    );
                  })}
                </CardBody>
              )}
            </Card>
            <div className="space-y-4">
              <Card>
                <CardHeader icon={<Swords className="h-4 w-4" />} title="Competitors" />
                {!wl.data.competitors.length ? <p className="px-5 pb-5 text-[13px] text-muted-foreground">No competitive losses recorded.</p> : (
                  <ul className="divide-y">{wl.data.competitors.map((c) => <li key={c.competitor} className="flex justify-between px-5 py-2 text-[13px]"><span>{c.competitor}</span><span className="tabular-nums text-muted-foreground">{c.count} · {fmtMoney(c.amount, "USD", true)}</span></li>)}</ul>
                )}
              </Card>
              <Card>
                <CardHeader icon={<MessageSquareQuote className="h-4 w-4" />} title="Why we won" />
                {!wl.data.win_debriefs.length ? <p className="px-5 pb-5 text-[13px] text-muted-foreground">Win debriefs appear here when reps add them at Closed-Won.</p> : (
                  <ul className="space-y-2 px-5 pb-5">{wl.data.win_debriefs.map((d) => <li key={d.deal_id} className="text-[12.5px]">“{d.debrief}” <Link href={`/deals/${d.deal_id}`} className="text-primary hover:underline">{d.account}</Link></li>)}</ul>
                )}
              </Card>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
