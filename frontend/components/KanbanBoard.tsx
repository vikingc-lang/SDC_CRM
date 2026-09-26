"use client";

import {
  DndContext, DragOverlay, KeyboardSensor, PointerSensor, useDraggable, useDroppable, useSensor, useSensors,
  type DragEndEvent, type DragStartEvent,
} from "@dnd-kit/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { AxiosError } from "axios";
import { CalendarDays, CheckCircle2, CircleX, Clock, Sparkles, Swords, Trophy } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { RiskBadge } from "@/components/indicators";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Avatar } from "@/components/ui/misc";
import { api, errorMessage } from "@/lib/api";
import { useMe } from "@/lib/me";
import type { Deal, GateCheck, Kanban, KanbanColumn, LossReason } from "@/lib/types";
import { fmtMoney } from "@/components/ui/extra";
import { cn, money, shortDate } from "@/lib/utils";

export const LOSS_REASONS: { value: LossReason; label: string }[] = [
  { value: "competitor", label: "Lost to competitor" },
  { value: "budget_frozen", label: "Budget frozen" },
  { value: "feature_gap", label: "Feature gap" },
  { value: "champion_departed", label: "Champion departed" },
  { value: "price", label: "Price" },
  { value: "no_decision", label: "No decision / status quo" },
  { value: "timing", label: "Timing" },
  { value: "other", label: "Other" },
];

interface LossInput { reason?: LossReason; debrief?: string; competitor?: string }

interface PendingMove { deal: Deal; to: KanbanColumn; gates?: GateCheck[]; needsReason?: boolean }

/** Shared hook: move a deal across a stage gate, handling gates, loss reasons and toasts. */
export function useStageMove(onSettled?: () => void) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<PendingMove | null>(null);
  const mutation = useMutation({
    mutationFn: async (v: { deal: Deal; to: KanbanColumn; loss?: LossInput; override_gates?: boolean }) =>
      (await api.patch(`/deals/${v.deal.id}/stage`, {
        stage_id: v.to.id, loss_reason: v.loss?.reason ?? null, loss_debrief: v.loss?.debrief ?? null,
        loss_competitor: v.loss?.competitor || null, override_gates: !!v.override_gates,
      })).data,
    onSuccess: (res: { forecast_delta: number; triggered_action: string | null; deal: Deal }) => {
      setPending(null);
      const delta = res.forecast_delta;
      toast.success(`Moved to ${res.deal.stage}`, {
        description: [
          `Weighted forecast ${delta >= 0 ? "+" : "−"}${money(Math.abs(delta))}`,
          res.triggered_action ? `AI: ${res.triggered_action}` : null,
        ].filter(Boolean).join(" · "),
      });
      qc.invalidateQueries();
      setTimeout(() => qc.invalidateQueries(), 1500); // pick up background AI insights
    },
    onError: (e: AxiosError<{ gates?: GateCheck[] }>, v) => {
      if (e.response?.status === 409 && e.response.data?.gates) {
        setPending({ deal: v.deal, to: v.to, gates: e.response.data.gates, needsReason: v.to.is_closed_lost });
      } else {
        toast.error(errorMessage(e, "Could not move deal"));
        setPending(null);
      }
      qc.invalidateQueries({ queryKey: ["kanban"] });
    },
    onSettled,
  });

  const request = (deal: Deal, to: KanbanColumn) => {
    if (deal.stage_id === to.id) return;
    if (to.is_closed_lost) setPending({ deal, to, needsReason: true });
    else mutation.mutate({ deal, to });
  };

  const dialog = (
    <StageMoveDialog
      pending={pending}
      busy={mutation.isPending}
      onCancel={() => { setPending(null); qc.invalidateQueries({ queryKey: ["kanban"] }); }}
      onConfirm={(loss, override) => pending && mutation.mutate({ deal: pending.deal, to: pending.to, loss, override_gates: override })}
    />
  );
  return { request, dialog, busy: mutation.isPending };
}

function StageMoveDialog({ pending, busy, onCancel, onConfirm }: {
  pending: PendingMove | null; busy: boolean; onCancel: () => void; onConfirm: (loss: LossInput | undefined, override: boolean) => void;
}) {
  const [reason, setReason] = useState<LossReason | "">("");
  const [debrief, setDebrief] = useState("");
  const [competitor, setCompetitor] = useState("");
  const debriefOk = debrief.trim().length >= 15;
  const unmet = pending?.gates?.filter((g) => !g.met) ?? [];
  const { me } = useMe();
  const canOverride = me?.role === "sales_manager" || me?.role === "super_admin";
  return (
    <Dialog open={!!pending} onOpenChange={(o) => !o && onCancel()}>
      <DialogContent title="Stage gate" className="max-w-md">
        {pending && (
          <div className="p-5">
            <div className="mb-1 flex items-center gap-2">
              {pending.to.is_closed_lost ? <CircleX className="h-4 w-4" style={{ color: "var(--status-critical)" }} /> : <Sparkles className="h-4 w-4 text-ai" />}
              <h2 className="text-[15px] font-semibold">{pending.needsReason ? "Close as lost" : `Entry criteria for ${pending.to.name}`}</h2>
            </div>
            <p className="text-[13px] text-muted-foreground">{pending.deal.title} · {pending.deal.account.name}</p>

            {pending.gates && !pending.needsReason && (
              <ul className="mt-4 space-y-2">
                {pending.gates.map((g) => (
                  <li key={g.criterion} className="flex items-start gap-2 text-[13.5px]">
                    {g.met ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" style={{ color: "var(--status-good)" }} /> : <CircleX className="mt-0.5 h-4 w-4 shrink-0" style={{ color: "var(--status-critical)" }} />}
                    <span>{g.criterion}<span className="ml-1.5 text-[12px] text-muted-foreground">{g.met ? "Met" : "Not yet"}</span></span>
                  </li>
                ))}
              </ul>
            )}
            {pending.needsReason && (
              <div className="mt-4 space-y-3">
                <div>
                  <Label htmlFor="loss-reason">Loss reason (required)</Label>
                  <Select id="loss-reason" value={reason} onChange={(e) => setReason(e.target.value as LossReason)}>
                    <option value="" disabled>Select a reason…</option>
                    {LOSS_REASONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                  </Select>
                </div>
                {reason === "competitor" && (
                  <div>
                    <Label htmlFor="loss-competitor">Which competitor?</Label>
                    <Input id="loss-competitor" value={competitor} onChange={(e) => setCompetitor(e.target.value)} placeholder="e.g. Salesforce" />
                  </div>
                )}
                <div>
                  <Label htmlFor="loss-debrief">Rep debrief (required)</Label>
                  <Textarea id="loss-debrief" value={debrief} onChange={(e) => setDebrief(e.target.value)} className="min-h-[88px]"
                    placeholder="What happened, what signals did we miss, what would we do differently?" />
                  <p className="mt-1 text-[12px] text-muted-foreground">{debriefOk ? "Saved to win/loss attribution and vector memory." : `${Math.max(0, 15 - debrief.trim().length)} more characters`}</p>
                </div>
              </div>
            )}
            {unmet.length > 0 && !pending.needsReason && (
              <p className="mt-4 rounded-md bg-muted px-3 py-2 text-[12.5px] text-muted-foreground">
                {canOverride ? "Moving anyway is recorded in the audit trail as a gate override." : "Complete the criteria above, or ask your sales manager to override the gate."}
              </p>
            )}
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
              {pending.needsReason ? (
                <Button variant="destructive" size="sm" disabled={!reason || !debriefOk} loading={busy}
                  onClick={() => onConfirm({ reason: reason as LossReason, debrief, competitor }, true)}>Close as lost</Button>
              ) : (
                canOverride && <Button size="sm" loading={busy} onClick={() => onConfirm(undefined, true)}>Move anyway</Button>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function DealCard({ deal, dragging, overlay }: { deal: Deal; dragging?: boolean; overlay?: boolean }) {
  const closed = deal.stage === "Closed-Won" || deal.stage === "Closed-Lost";
  const competitors = deal.ai_insights?.competitors ?? [];
  return (
    <div
      className={cn(
        "group rounded-lg border bg-surface p-3 shadow-card transition-shadow",
        !overlay && "hover:shadow-pop",
        dragging && "opacity-40",
        overlay && "rotate-[1.5deg] cursor-grabbing shadow-pop ring-2 ring-primary/30",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <Link href={`/deals/${deal.id}`} className="text-[13.5px] font-medium leading-snug hover:underline" onPointerDown={(e) => e.stopPropagation()}>
          {deal.title}
        </Link>
        {!closed && <RiskBadge score={deal.risk_score} compact />}
      </div>
      <Link href={`/accounts/${deal.account.id}`} className="mt-0.5 block text-[12.5px] text-muted-foreground hover:text-foreground" onPointerDown={(e) => e.stopPropagation()}>
        {deal.account.name}
      </Link>
      <div className="mt-2.5 flex items-baseline justify-between">
        <span className="text-[15px] font-semibold">{fmtMoney(deal.amount, deal.currency, true)}</span>
        {!closed && <span className="tabular text-[12px] text-muted-foreground">wtd {money(deal.weighted_value, { compact: true })}</span>}
      </div>
      <div className="mt-2.5 flex items-center gap-3 border-t pt-2.5 text-[11.5px] text-muted-foreground">
        {deal.target_close_date && <span className="flex items-center gap-1"><CalendarDays className="h-3 w-3" />{shortDate(deal.target_close_date)}</span>}
        {!closed && <span className={cn("flex items-center gap-1", deal.days_in_stage > 21 && "font-medium text-foreground")}><Clock className="h-3 w-3" />{deal.days_in_stage}d</span>}
        {competitors.length > 0 && <span className="flex items-center gap-1" title={`Competitors: ${competitors.join(", ")}`}><Swords className="h-3 w-3" />{competitors[0]}</span>}
        {deal.stage === "Closed-Won" && <span className="flex items-center gap-1 text-foreground"><Trophy className="h-3 w-3" style={{ color: "var(--status-good)" }} />Won</span>}
        {deal.loss_reason && <span className="capitalize">{deal.loss_reason.replace(/_/g, " ")}</span>}
        {deal.account.credit_hold && <span className="font-medium text-foreground" title="Account on ERP credit hold">Credit hold</span>}
        {deal.owner && <Avatar name={deal.owner.full_name} size={20} className="ml-auto" />}
      </div>
    </div>
  );
}

function DraggableDeal({ deal }: { deal: Deal }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: deal.id, data: { deal } });
  return (
    <div ref={setNodeRef} {...listeners} {...attributes} className="cursor-grab touch-none active:cursor-grabbing" aria-roledescription="Draggable deal">
      <DealCard deal={deal} dragging={isDragging} />
    </div>
  );
}

function Column({ column, children }: { column: KanbanColumn; children: React.ReactNode }) {
  const { setNodeRef, isOver } = useDroppable({ id: column.id, data: { column } });
  const accent = column.is_closed_won ? "var(--status-good)" : column.is_closed_lost ? "var(--status-critical)" : "var(--series-1)";
  return (
    <div className="flex w-[272px] shrink-0 flex-col">
      <div className="mb-2 px-1">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full" style={{ background: accent }} />
          <h3 className="text-[13px] font-semibold">{column.name}</h3>
          <span className="rounded-full bg-muted px-1.5 text-[11px] font-medium text-muted-foreground">{column.metrics.count}</span>
          <span className="ml-auto text-[11.5px] text-subtle">{column.probability}%</span>
        </div>
        <div className="mt-1 flex gap-2 text-[12px] text-muted-foreground">
          <span className="tabular">{money(column.metrics.total, { compact: true })}</span>
          {!column.is_closed_won && !column.is_closed_lost && <span className="tabular text-subtle">· {money(column.metrics.weighted, { compact: true })} weighted</span>}
        </div>
      </div>
      <div
        ref={setNodeRef}
        className={cn(
          "flex min-h-[160px] flex-1 flex-col gap-2 rounded-xl border border-transparent bg-surface-2/70 p-2 transition-colors",
          isOver && "border-primary/40 bg-primary-soft/60",
        )}
      >
        {children}
      </div>
    </div>
  );
}

export function KanbanBoard({ board, onOptimisticMove }: { board: Kanban; onOptimisticMove: (dealId: string, toStageId: string) => void }) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor));
  const [active, setActive] = useState<Deal | null>(null);
  const { request, dialog } = useStageMove();

  const onDragStart = (e: DragStartEvent) => setActive((e.active.data.current as { deal: Deal }).deal);
  const onDragEnd = (e: DragEndEvent) => {
    setActive(null);
    const deal = (e.active.data.current as { deal: Deal } | undefined)?.deal;
    const to = (e.over?.data.current as { column: KanbanColumn } | undefined)?.column;
    if (!deal || !to || deal.stage_id === to.id) return;
    if (!to.is_closed_lost) onOptimisticMove(deal.id, to.id);
    request(deal, to);
  };

  return (
    <>
      <DndContext sensors={sensors} onDragStart={onDragStart} onDragEnd={onDragEnd} onDragCancel={() => setActive(null)}>
        <div className="-mx-4 flex gap-3 overflow-x-auto px-4 pb-4 scrollbar-thin lg:-mx-8 lg:px-8">
          {board.columns.map((col) => (
            <Column key={col.id} column={col}>
              {col.deals.map((d) => <DraggableDeal key={d.id} deal={d} />)}
              {col.deals.length === 0 && <p className="py-6 text-center text-[12px] text-subtle">Drop deals here</p>}
            </Column>
          ))}
        </div>
        <DragOverlay dropAnimation={null}>
          {active ? <div className="w-[256px]"><DealCard deal={active} overlay /></div> : null}
        </DragOverlay>
      </DndContext>
      {dialog}
    </>
  );
}
