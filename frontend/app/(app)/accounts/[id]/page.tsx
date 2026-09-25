"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, ExternalLink, Plus, Sparkles, UserPlus } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { ActivityTimeline } from "@/components/ActivityTimeline";
import { HealthBreakdown } from "@/components/charts";
import { NewContactDialog, NewDealDialog, NewTaskDialog } from "@/components/forms";
import { HealthRing, RiskBadge, RoleBadge } from "@/components/indicators";
import { TaskRow } from "@/components/TaskList";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Avatar, Skeleton } from "@/components/ui/misc";
import { get } from "@/lib/api";
import { ui } from "@/lib/store";
import type { Account360 } from "@/lib/types";
import { money, shortDate } from "@/lib/utils";

const OPEN_STAGES = ["Discovery", "Pain Fit", "Solution Demo", "Proposal/InfoSec"];

export default function Account360Page() {
  const { id } = useParams<{ id: string }>();
  const [dialog, setDialog] = useState<"deal" | "contact" | "task" | null>(null);
  const { data, isLoading, isError } = useQuery({ queryKey: ["account360", id], queryFn: () => get<Account360>(`/accounts/${id}/360`) });
  const brief = useQuery({ queryKey: ["account-brief", id], queryFn: () => get<{ brief: string; engine: string }>(`/ai/accounts/${id}/brief`) });

  if (isError) return <p className="text-sm text-muted-foreground">Account not found. <Link href="/accounts" className="text-primary hover:underline">Back to accounts</Link></p>;
  if (isLoading || !data) return <div className="mx-auto max-w-7xl space-y-4"><Skeleton className="h-20 w-full" /><Skeleton className="h-64 w-full" /></div>;

  const { account, contacts, deals, recent_activities, tasks, summary } = data;
  const openTasks = tasks.filter((t) => !t.completed);

  return (
    <div className="mx-auto max-w-7xl">
      <Link href="/accounts" className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />Accounts</Link>

      <div className="mb-6 flex flex-wrap items-center gap-5">
        <HealthRing score={account.health_score} size={72} />
        <div className="min-w-0 flex-1">
          <h1 className="text-[24px] font-semibold tracking-tight">{account.name}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-muted-foreground">
            <a href={`https://${account.domain}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:text-foreground">{account.domain}<ExternalLink className="h-3 w-3" /></a>
            <Badge tone="outline">{account.tier}</Badge>
            {account.industry && <span>{account.industry}</span>}
            {account.owner && <span className="flex items-center gap-1.5"><Avatar name={account.owner.full_name} size={18} />{account.owner.full_name}</span>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => setDialog("contact")}><UserPlus className="h-4 w-4" />Contact</Button>
          <Button variant="outline" size="sm" onClick={() => setDialog("deal")}><Plus className="h-4 w-4" />Deal</Button>
          <Button variant="ai" size="sm" onClick={() => ui.openQuickLog({ accountId: account.id })}><Sparkles className="h-4 w-4" />Log activity</Button>
        </div>
      </div>

      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        {[
          { label: "Open pipeline", value: money(summary.open_pipeline, { compact: true }) },
          { label: "Weighted", value: money(summary.weighted_pipeline, { compact: true }) },
          { label: "Won revenue", value: money(summary.won_revenue, { compact: true }) },
          { label: "Buying committee", value: `${contacts.length} people` },
        ].map((k) => (
          <div key={k.label} className="rounded-lg border bg-surface px-4 py-3 shadow-card">
            <p className="text-[12px] text-muted-foreground">{k.label}</p>
            <p className="mt-1 text-lg font-semibold">{k.value}</p>
          </div>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <div className="ai-border rounded-xl p-4 shadow-card">
            <p className="flex items-center gap-1.5 text-[11.5px] font-medium uppercase tracking-wide text-ai"><Sparkles className="h-3.5 w-3.5" />Account brief</p>
            {brief.data ? <p className="mt-2 text-[14px] leading-relaxed">{brief.data.brief}</p> : <div className="mt-2 space-y-2"><Skeleton className="w-full" /><Skeleton className="w-3/4" /></div>}
          </div>

          <Card>
            <CardHeader title="Deals" description={`${deals.length} total`} />
            <CardBody className="space-y-3">
              {deals.length === 0 && <p className="text-sm text-muted-foreground">No deals yet.</p>}
              {deals.map((d) => {
                const idx = OPEN_STAGES.indexOf(d.stage);
                return (
                  <Link key={d.id} href={`/deals/${d.id}`} className="block rounded-lg border p-3.5 transition-colors hover:border-primary/40">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{d.title}</span>
                      {idx >= 0 && <RiskBadge score={d.risk_score} />}
                      {d.stage === "Closed-Won" && <Badge tone="good">Won</Badge>}
                      {d.stage === "Closed-Lost" && <Badge tone="critical">Lost · {d.loss_reason?.replace("_", " ")}</Badge>}
                      <span className="ml-auto text-[15px] font-semibold">{money(d.amount)}</span>
                    </div>
                    {idx >= 0 && (
                      <div className="mt-3 flex items-center gap-1" aria-label={`Stage ${d.stage}`}>
                        {OPEN_STAGES.map((s, i) => (
                          <div key={s} className="flex-1">
                            <div className={`h-1.5 rounded-full ${i <= idx ? "bg-series-1" : "bg-muted"}`} />
                            <p className={`mt-1 truncate text-[11px] ${i === idx ? "font-medium text-foreground" : "text-subtle"}`}>{s}</p>
                          </div>
                        ))}
                      </div>
                    )}
                    <p className="mt-2 text-[12px] text-muted-foreground">
                      {d.probability}% · weighted {money(d.weighted_value, { compact: true })}{d.target_close_date && ` · closes ${shortDate(d.target_close_date, true)}`}
                    </p>
                  </Link>
                );
              })}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Activity timeline" action={<Button variant="ghost" size="sm" onClick={() => ui.openQuickLog({ accountId: account.id })}><Plus className="h-3.5 w-3.5" />Log</Button>} />
            <CardBody><ActivityTimeline activities={recent_activities} /></CardBody>
          </Card>
        </div>

        <div className="space-y-6">
          {account.health_breakdown && (
            <Card>
              <CardHeader title="Health drivers" description="H = 0.40 R + 0.35 S + 0.25 V" />
              <CardBody><HealthBreakdown breakdown={account.health_breakdown} /></CardBody>
            </Card>
          )}
          <Card>
            <CardHeader title="Buying committee" action={<Button variant="ghost" size="sm" onClick={() => setDialog("contact")}><Plus className="h-3.5 w-3.5" /></Button>} />
            <CardBody className="space-y-3">
              {!summary.has_champion && (
                <div className="flex gap-2 rounded-md border p-2.5 text-[12.5px]" style={{ borderColor: "color-mix(in srgb, var(--status-warning) 50%, transparent)" }}>
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color: "var(--status-serious)" }} />
                  <span>No Champion or Decision Maker mapped. This adds 40 points to every deal&apos;s risk.</span>
                </div>
              )}
              {contacts.map((c) => (
                <div key={c.id} className="flex items-center gap-3">
                  <Avatar name={c.name} size={32} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13.5px] font-medium">{c.name}</p>
                    <p className="truncate text-[12px] text-muted-foreground">{c.job_title ?? c.email ?? "—"}</p>
                  </div>
                  <RoleBadge role={c.buying_role} />
                </div>
              ))}
              {contacts.length === 0 && <p className="text-sm text-muted-foreground">No contacts yet.</p>}
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Tasks" description={`${openTasks.length} open`} action={<Button variant="ghost" size="sm" onClick={() => setDialog("task")}><Plus className="h-3.5 w-3.5" /></Button>} />
            <CardBody className="px-3">
              {tasks.length === 0 && <p className="px-2 text-sm text-muted-foreground">No tasks.</p>}
              {tasks.slice(0, 8).map((t) => <TaskRow key={t.id} task={t} showContext={false} />)}
            </CardBody>
          </Card>
        </div>
      </div>

      <NewDealDialog open={dialog === "deal"} onOpenChange={(o) => setDialog(o ? "deal" : null)} accountId={account.id} />
      <NewContactDialog open={dialog === "contact"} onOpenChange={(o) => setDialog(o ? "contact" : null)} accountId={account.id} />
      <NewTaskDialog open={dialog === "task"} onOpenChange={(o) => setDialog(o ? "task" : null)} accountId={account.id} />
    </div>
  );
}
