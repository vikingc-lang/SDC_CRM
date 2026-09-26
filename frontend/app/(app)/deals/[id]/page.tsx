"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ArrowLeft, CheckCircle2, CircleDashed, Copy, Gauge, History, Lightbulb, Mail, Plus, Swords, Target,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { ActivityTimeline } from "@/components/ActivityTimeline";
import { AlertsBanner, DocumentsCard, PartnersCard, QuotesCard } from "@/components/panels";
import { fmtMoney } from "@/components/ui/extra";
import { useMe } from "@/lib/me";
import { NewTaskDialog } from "@/components/forms";
import { RiskBadge, RoleBadge, riskTone } from "@/components/indicators";
import { useStageMove } from "@/components/KanbanBoard";
import { TaskRow } from "@/components/TaskList";
import { Badge } from "@/components/ui/badge";
import { AidenAvatar } from "@/components/Brand";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/input";
import { Avatar, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { ui } from "@/lib/store";
import type { DealDetail, KanbanColumn } from "@/lib/types";
import { cn, money, relativeDays, shortDate } from "@/lib/utils";

const RISK_ROWS = [
  { key: "stale", label: "No activity in 14 days", points: 30 },
  { key: "sentiment_drop", label: "Latest interaction negative", points: 30 },
  { key: "no_champion", label: "No Champion / Decision Maker", points: 40 },
] as const;

export default function DealPage() {
  const { id } = useParams<{ id: string }>();
  const [taskOpen, setTaskOpen] = useState(false);
  const [draft, setDraft] = useState<string | null>(null);
  const { data: deal, isLoading, isError } = useQuery({ queryKey: ["deal", id], queryFn: () => get<DealDetail>(`/deals/${id}`) });
  const { request, dialog } = useStageMove();
  const { can } = useMe();
  const draftEmail = useMutation({
    mutationFn: async () => (await api.post<{ draft: string }>(`/ai/deals/${id}/draft-email`)).data,
    onSuccess: (d) => setDraft(d.draft),
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (isError) return <p className="text-sm text-muted-foreground">Deal not found. <Link href="/pipeline" className="text-primary hover:underline">Back to pipeline</Link></p>;
  if (isLoading || !deal) return <div className="mx-auto max-w-7xl space-y-4"><Skeleton className="h-20 w-full" /><Skeleton className="h-64 w-full" /></div>;

  const closed = deal.stage === "Closed-Won" || deal.stage === "Closed-Lost";
  const current = deal.stages.find((s) => s.id === deal.stage_id);
  const toColumn = (s: DealDetail["stages"][number]): KanbanColumn => ({ ...s, deals: [], metrics: { count: 0, total: 0, weighted: 0 } });
  const ins = deal.ai_insights ?? {};
  const f = deal.risk_factors ?? {};

  return (
    <div className="mx-auto max-w-7xl">
      <Link href="/pipeline" className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />Pipeline</Link>

      <div className="mb-5 flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-[24px] font-semibold tracking-tight">{deal.title}</h1>
            {!closed && <RiskBadge score={deal.risk_score} />}
            {deal.stage === "Closed-Won" && <Badge tone="good">Closed-Won</Badge>}
            {deal.stage === "Closed-Lost" && <Badge tone="critical">Closed-Lost · {deal.loss_reason?.replace(/_/g, " ")}</Badge>}
            <Badge tone="outline">{deal.pipeline.name}</Badge>
            {deal.deal_type && deal.deal_type !== "new_business" && <Badge tone="primary" className="capitalize">{deal.deal_type.replace("_", " ")}</Badge>}
          </div>
          <p className="mt-1 text-[13.5px] text-muted-foreground">
            <Link href={`/accounts/${deal.account.id}`} className="font-medium text-foreground hover:underline">{deal.account.name}</Link>
            {deal.owner && <> · owned by {deal.owner.full_name}</>}
            {deal.target_close_date && <> · target close {shortDate(deal.target_close_date, true)}</>}
            {!!deal.close_date_pushes && <span className="font-medium text-foreground"> · pushed {deal.close_date_pushes}× (was {shortDate(deal.original_close_date)})</span>}
          </p>
        </div>
        <div className="text-right">
          <p className="text-[28px] font-semibold leading-8 tracking-tight">{fmtMoney(deal.amount, deal.currency)}</p>
          {!closed && <p className="tabular text-[12.5px] text-muted-foreground">{deal.probability}% · weighted {money(deal.weighted_value)} USD</p>}
        </div>
      </div>

      {deal.account.credit_hold && (
        <div className="mb-4 rounded-lg border px-3.5 py-2.5 text-[13px]" style={{ borderColor: "color-mix(in srgb, var(--status-critical) 45%, transparent)" }}>
          <span className="font-medium">Credit hold:</span> {deal.account.name} is on credit hold in the ERP. New quotes route to finance approval. See the account&apos;s Finance tab.
        </div>
      )}
      <AlertsBanner alerts={deal.alerts} />

      {/* Stage stepper */}
      <Card className="mb-6 p-2">
        <div className="flex gap-1 overflow-x-auto scrollbar-thin">
          {deal.stages.map((s) => {
            const active = s.id === deal.stage_id;
            const done = current && !current.is_closed_lost && s.stage_order < current.stage_order && !s.is_closed_lost;
            return (
              <button
                key={s.id}
                onClick={() => !active && request(deal, toColumn(s))}
                className={cn(
                  "flex min-w-[128px] flex-1 items-center gap-2 rounded-md px-3 py-2 text-left text-[13px] transition-colors",
                  active ? "bg-primary text-primary-foreground" : "hover:bg-muted",
                )}
                title={active ? "Current stage" : `Move to ${s.name}`}
              >
                {done ? <CheckCircle2 className="h-4 w-4 shrink-0 text-primary" /> : active ? <Target className="h-4 w-4 shrink-0" /> : <CircleDashed className="h-4 w-4 shrink-0 text-subtle" />}
                <span className="min-w-0">
                  <span className="block truncate font-medium">{s.name}</span>
                  <span className={cn("block text-[11px]", active ? "opacity-80" : "text-subtle")}>{s.probability}%</span>
                </span>
              </button>
            );
          })}
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {!closed && (
            <Card>
              <CardHeader title="Deal risk" icon={<Gauge className="h-4 w-4 text-muted-foreground" />} description="R = 30·Stale + 30·SentimentDrop + 40·NoChampion" />
              <CardBody>
                <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
                  <div className="space-y-2.5">
                    {RISK_ROWS.map((r) => {
                      const on = !!f[r.key];
                      return (
                        <div key={r.key} className="flex items-center gap-2.5 text-[13.5px]">
                          <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: on ? "var(--status-critical)" : "var(--status-good)" }} />
                          <span className={cn("flex-1", !on && "text-muted-foreground")}>{r.label}</span>
                          <span className="tabular text-[12.5px] font-medium">{on ? `+${r.points}` : "0"}</span>
                        </div>
                      );
                    })}
                    <div className="flex items-center gap-2.5 border-t pt-2.5 text-[13.5px] font-semibold">
                      <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: `var(--status-${riskTone(deal.risk_score)})` }} />
                      <span className="flex-1">Risk index</span>
                      <span className="tabular">{deal.risk_score}/100</span>
                    </div>
                    <p className="text-[12px] text-muted-foreground">
                      Last activity {f.days_since_activity != null ? `${Math.round(f.days_since_activity)}d ago` : "never"} · {deal.days_in_stage}d in stage
                    </p>
                  </div>
                  <div>
                    <p className="mb-2 flex items-center gap-1.5 text-[12px] font-medium uppercase tracking-wide text-ai"><Lightbulb className="h-3.5 w-3.5" />Next best actions</p>
                    {deal.next_best_actions.length === 0 ? (
                      <p className="text-[13px] text-muted-foreground">No risk drivers. Keep momentum and confirm the close plan.</p>
                    ) : (
                      <ul className="space-y-2">
                        {deal.next_best_actions.map((a) => (
                          <li key={a.action} className="rounded-md border bg-surface-2/50 p-2.5">
                            <p className="text-[13px] font-medium">{a.action}</p>
                            <p className="text-[12px] text-muted-foreground">{a.why}</p>
                            {a.message && (
                              <div className="mt-2 flex items-start gap-2 rounded bg-surface px-2 py-1.5 text-[12.5px]">
                                <span className="flex-1 italic">{a.message}</span>
                                <button className="shrink-0 text-subtle hover:text-foreground" aria-label="Copy suggested message"
                                  onClick={() => { navigator.clipboard?.writeText(a.message ?? ""); toast.success("Suggested message copied"); }}><Copy className="h-3.5 w-3.5" /></button>
                              </div>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              </CardBody>
            </Card>
          )}

          <div className="ai-border rounded-xl p-4 shadow-card">
            <div className="flex flex-wrap items-center gap-2">
              <p className="flex items-center gap-1.5 text-[11.5px] font-medium uppercase tracking-wide text-ai"><AidenAvatar size={16} />Aiden’s insights</p>
              {ins.last_trigger && <span className="text-[11.5px] text-subtle">Last stage action: {ins.last_trigger.stage}, {relativeDays(ins.last_trigger.at)}</span>}
              <Button variant="outline" size="sm" className="ml-auto" loading={draftEmail.isPending} onClick={() => draftEmail.mutate()}><Mail className="h-3.5 w-3.5" />Draft follow-up</Button>
            </div>
            <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2">
              <div>
                <p className="mb-1.5 text-[12px] font-medium text-muted-foreground">Pain points</p>
                {(ins.pain_points ?? []).length ? (
                  <ul className="space-y-1.5 text-[13px]">{ins.pain_points!.map((p) => <li key={p} className="flex gap-2"><span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-foreground/50" />{p}</li>)}</ul>
                ) : <p className="text-[13px] text-subtle">None captured yet.</p>}
              </div>
              <div>
                <p className="mb-1.5 text-[12px] font-medium text-muted-foreground">Competitive landscape</p>
                {(ins.competitors ?? []).length ? (
                  <div className="flex flex-wrap gap-1.5">{ins.competitors!.map((c) => <Badge key={c} tone="warning"><Swords className="h-3 w-3" />{c}</Badge>)}</div>
                ) : <p className="text-[13px] text-subtle">No competitors mentioned.</p>}
                {ins.velocity && (
                  <p className="mt-3 text-[13px]">
                    Velocity: <span className="font-medium">{ins.velocity.days_in_pipeline}d</span> in pipeline vs {ins.velocity.benchmark_days}d benchmark ·{" "}
                    <span className="font-medium">{ins.velocity.status === "on_pace" ? "on pace" : "stagnating"}</span>
                  </p>
                )}
              </div>
            </div>
            {ins.postmortem && <p className="mt-3 rounded-md bg-muted p-3 text-[13px]">{ins.postmortem}</p>}
            {ins.recap_email && !draft && (
              <button onClick={() => setDraft(ins.recap_email!)} className="mt-3 text-[12.5px] font-medium text-primary hover:underline">View AI-drafted demo recap email</button>
            )}
          </div>

          {deal.is_lost && (
            <Card>
              <CardHeader title="Loss debrief" description={`${deal.loss_taxonomy[deal.loss_reason ?? "other"] ?? deal.loss_reason}${deal.loss_competitor ? ` · ${deal.loss_competitor}` : ""}`} />
              <CardBody><p className="text-[13.5px] leading-relaxed">{deal.loss_debrief ?? "No debrief recorded."}</p></CardBody>
            </Card>
          )}
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            <QuotesCard dealId={deal.id} quotes={deal.quotes} canCreate={can("quotes", "create") && !closed} />
            <DocumentsCard dealId={deal.id} documents={deal.documents} canCreate={can("documents", "create")}
              hasApprovedQuote={deal.quotes.some((q) => ["approved", "sent", "accepted"].includes(q.status))} />
          </div>

          <Card>
            <CardHeader title="Activity" action={<Button variant="ghost" size="sm" onClick={() => ui.openQuickLog({ accountId: deal.account.id })}><Plus className="h-3.5 w-3.5" />Log</Button>} />
            <CardBody><ActivityTimeline activities={deal.activities} /></CardBody>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Tasks" action={<Button variant="ghost" size="sm" onClick={() => setTaskOpen(true)}><Plus className="h-3.5 w-3.5" /></Button>} />
            <CardBody className="px-3">
              {deal.tasks.length === 0 && <p className="px-2 text-sm text-muted-foreground">No tasks for this deal.</p>}
              {deal.tasks.map((t) => <TaskRow key={t.id} task={t} showContext={false} />)}
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Buying committee" />
            <CardBody className="space-y-3">
              {deal.contacts.map((c) => (
                <div key={c.id} className="flex items-center gap-3">
                  <Avatar name={c.name} size={30} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13.5px] font-medium">{c.name}{deal.primary_contact?.id === c.id && <span className="ml-1.5 text-[11px] font-normal text-muted-foreground">primary</span>}</p>
                    <p className="truncate text-[12px] text-muted-foreground">{c.job_title ?? "—"}</p>
                  </div>
                  <RoleBadge role={c.buying_role} />
                </div>
              ))}
            </CardBody>
          </Card>
          <PartnersCard dealId={deal.id} partners={deal.partners} canEdit={can("deals", "update")} />
          <Card>
            <CardHeader title="Stage-gate audit trail" icon={<History className="h-4 w-4 text-muted-foreground" />} />
            <CardBody>
              <ol className="space-y-3">
                {[...deal.history].reverse().map((h, i) => (
                  <li key={i} className="text-[13px]">
                    <p><span className="text-muted-foreground">{h.from ?? "Created"} →</span> <span className="font-medium">{h.to}</span>{h.gate_overridden && <Badge tone="warning" className="ml-1.5">gate override</Badge>}</p>
                    <p className="text-[12px] text-muted-foreground">
                      {shortDate(h.at, true)}{h.by && ` · ${h.by.full_name}`}{h.forecast_delta !== 0 && ` · ${h.forecast_delta > 0 ? "+" : "−"}${money(Math.abs(h.forecast_delta), { compact: true })} forecast`}
                    </p>
                  </li>
                ))}
              </ol>
            </CardBody>
          </Card>
        </div>
      </div>

      <Dialog open={draft !== null} onOpenChange={(o) => !o && setDraft(null)}>
        <DialogContent title="Aiden email draft" className="max-w-xl">
          <div className="p-5">
            <p className="mb-3 flex items-center gap-1.5 text-sm font-semibold"><AidenAvatar size={18} />Aiden drafted this follow-up</p>
            <Textarea value={draft ?? ""} onChange={(e) => setDraft(e.target.value)} className="min-h-[280px] font-sans text-[13.5px]" />
            <div className="mt-3 flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setDraft(null)}>Close</Button>
              <Button size="sm" onClick={() => { navigator.clipboard?.writeText(draft ?? ""); toast.success("Copied to clipboard"); }}><Copy className="h-3.5 w-3.5" />Copy</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
      <NewTaskDialog open={taskOpen} onOpenChange={setTaskOpen} accountId={deal.account.id} dealId={deal.id} />
      {dialog}
    </div>
  );
}
