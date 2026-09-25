"use client";

import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle, ArrowUpRight, CalendarClock, CheckSquare, CircleDollarSign, HeartPulse, Plus, Sparkles, Target, TrendingUp,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { ActivityTimeline } from "@/components/ActivityTimeline";
import { PageHeader } from "@/components/AppShell";
import { ForecastByMonth, ForecastByStage, StatTile } from "@/components/charts";
import { NewDealDialog } from "@/components/forms";
import { RiskBadge } from "@/components/indicators";
import { TaskRow } from "@/components/TaskList";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { get } from "@/lib/api";
import { ui } from "@/lib/store";
import type { Activity, Briefing, DashboardSummary, Deal, Task, User } from "@/lib/types";
import { money } from "@/lib/utils";

const PRIORITY_ICON = { task: CheckSquare, risk: AlertTriangle, closing: CalendarClock, health: HeartPulse };

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export default function DashboardPage() {
  const [newDeal, setNewDeal] = useState(false);
  const me = useQuery({ queryKey: ["me"], queryFn: () => get<User>("/users/me") });
  const summary = useQuery({ queryKey: ["dashboard"], queryFn: () => get<DashboardSummary>("/dashboard/summary") });
  const briefing = useQuery({ queryKey: ["briefing"], queryFn: () => get<Briefing>("/ai/briefing") });
  const deals = useQuery({ queryKey: ["deals", "open"], queryFn: () => get<Deal[]>("/deals", { status: "open" }) });
  const tasks = useQuery({ queryKey: ["tasks", "open"], queryFn: () => get<Task[]>("/tasks", { status: "open" }) });
  const activity = useQuery({ queryKey: ["activities", "recent"], queryFn: () => get<Activity[]>("/activities", { limit: 8, include_system: false }) });

  const s = summary.data;
  const atRisk = (deals.data ?? []).filter((d) => d.risk_score >= 30).sort((a, b) => b.risk_score - a.risk_score || b.amount - a.amount).slice(0, 5);
  const firstName = me.data?.full_name.split(" ")[0];

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title={`${greeting()}${firstName ? `, ${firstName}` : ""}`}
        description={new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })}
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => setNewDeal(true)}><Plus className="h-4 w-4" />New deal</Button>
            <Button variant="ai" size="sm" onClick={() => ui.openQuickLog()}><Sparkles className="h-4 w-4" />Log a conversation</Button>
          </>
        }
      />

      {/* AI briefing */}
      <div className="ai-border mb-6 rounded-xl p-5 shadow-card">
        <div className="flex items-start gap-3">
          <span className="ai-gradient flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"><Sparkles className="h-4 w-4 text-white" /></span>
          <div className="min-w-0 flex-1">
            <p className="text-[11.5px] font-medium uppercase tracking-wide text-ai">Your AI briefing</p>
            {briefing.isLoading ? <Skeleton className="mt-2 h-5 w-2/3" /> : <p className="mt-1 text-[15px] font-medium leading-snug">{briefing.data?.headline}</p>}
          </div>
        </div>
        {!!briefing.data?.priorities.length && (
          <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {briefing.data.priorities.slice(0, 6).map((p) => {
              const Icon = PRIORITY_ICON[p.kind];
              return (
                <Link key={`${p.kind}-${p.id}`} href={p.href} className="group flex min-w-0 items-start gap-2.5 rounded-lg border bg-surface-2/50 p-3 transition-colors hover:border-primary/40 hover:bg-surface">
                  <Icon className="mt-0.5 h-4 w-4 shrink-0" style={{ color: p.severity === "high" ? "var(--status-critical)" : "var(--status-serious)" }} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-medium">{p.title}</span>
                    <span className="block truncate text-[12px] text-muted-foreground">{p.detail}</span>
                  </span>
                  <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-subtle opacity-0 transition-opacity group-hover:opacity-100" />
                </Link>
              );
            })}
          </div>
        )}
      </div>

      {/* KPIs */}
      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {s ? (
          <>
            <StatTile emphasis label="Weighted forecast" value={money(s.weighted_pipeline, { compact: true })} sub="Risk-adjusted, open deals" icon={<Target className="h-4 w-4" />} />
            <StatTile label="Open pipeline" value={money(s.total_pipeline, { compact: true })} sub={`${s.open_deals} deals · avg ${money(s.avg_deal_size, { compact: true })}`} icon={<CircleDollarSign className="h-4 w-4" />} />
            <StatTile label="Win rate" value={s.win_rate == null ? "—" : `${s.win_rate}%`} sub={`${money(s.won_this_quarter, { compact: true })} won this quarter`} icon={<TrendingUp className="h-4 w-4" />} />
            <StatTile label="Deals at high risk" value={String(s.at_risk_deals)} sub={`${s.overdue_tasks} overdue task${s.overdue_tasks === 1 ? "" : "s"}`} icon={<AlertTriangle className="h-4 w-4" />} />
          </>
        ) : (
          Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-[108px] rounded-lg border bg-surface p-4"><Skeleton className="w-24" /><Skeleton className="mt-4 h-7 w-28" /></div>)
        )}
      </div>

      <div className="mb-6 grid gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader title="Forecast by stage" description="Weighted = amount × stage probability × (1 − risk ÷ 200)" />
          <CardBody>{s ? (s.by_stage.length ? <ForecastByStage data={s.by_stage} /> : <p className="text-sm text-muted-foreground">No open deals.</p>) : <Skeleton className="h-40 w-full" />}</CardBody>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader title="Weighted forecast by close month" />
          <CardBody>{s ? <ForecastByMonth data={s.by_close_month} /> : <Skeleton className="h-40 w-full" />}</CardBody>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card>
          <CardHeader title="Needs attention" description="Highest deal risk first" action={<Link href="/pipeline" className="text-[12.5px] font-medium text-primary hover:underline">Pipeline</Link>} />
          <CardBody className="space-y-1 px-3">
            {atRisk.length === 0 && !deals.isLoading && <EmptyState icon={<Target className="h-4 w-4" />} title="Every deal is on track" />}
            {atRisk.map((d) => (
              <Link key={d.id} href={`/deals/${d.id}`} className="flex items-center gap-3 rounded-md px-2 py-2 hover:bg-muted/60">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13.5px] font-medium">{d.title}</p>
                  <p className="truncate text-[12px] text-muted-foreground">{d.account.name} · {d.stage} · {money(d.amount, { compact: true })}</p>
                </div>
                <RiskBadge score={d.risk_score} compact />
              </Link>
            ))}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Up next" description={`${s?.open_tasks ?? "…"} open tasks`} action={<Link href="/tasks" className="text-[12.5px] font-medium text-primary hover:underline">All tasks</Link>} />
          <CardBody className="px-3">
            {tasks.data?.length === 0 && <EmptyState icon={<CheckSquare className="h-4 w-4" />} title="Inbox zero" description="Action items from your notes will show up here." />}
            {tasks.data?.slice(0, 6).map((t) => <TaskRow key={t.id} task={t} />)}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Recent activity" />
          <CardBody>
            {activity.data ? <ActivityTimeline activities={activity.data.slice(0, 5)} showAccount /> : <Skeleton className="h-40 w-full" />}
          </CardBody>
        </Card>
      </div>
      <NewDealDialog open={newDeal} onOpenChange={setNewDeal} />
    </div>
  );
}
