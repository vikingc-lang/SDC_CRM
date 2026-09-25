"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, Check, Link2, Siren, Sparkles } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import { api, errorMessage } from "@/lib/api";
import type { Task } from "@/lib/types";
import { cn, dueLabel } from "@/lib/utils";

const PRIORITY_COLOR: Record<string, string> = { urgent: "var(--status-critical)", high: "var(--status-warning)" };

export function TaskRow({ task, showContext = true, showPeople = false }: { task: Task; showContext?: boolean; showPeople?: boolean }) {
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: async () => (await api.patch(`/tasks/${task.id}`, { completed: !task.completed })).data,
    onSuccess: () => {
      qc.invalidateQueries();
      if (!task.completed) toast.success("Nice work. Task completed.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const due = dueLabel(task.due_date);
  return (
    <div className="group flex items-start gap-3 rounded-md px-2 py-2 hover:bg-muted/60">
      <button
        onClick={() => toggle.mutate()}
        disabled={toggle.isPending || (!!task.blocked && !task.completed)}
        title={task.blocked && !task.completed ? `Blocked by ${task.depends_on?.title}` : undefined}
        className={cn(
          "mt-0.5 flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full border-[1.5px] transition-colors",
          task.completed ? "border-primary bg-primary text-primary-foreground" : "border-input hover:border-primary",
        )}
        aria-label={task.completed ? "Mark as not done" : "Mark as done"}
      >
        {task.completed && <Check className="h-3 w-3" strokeWidth={3} />}
      </button>
      <div className="min-w-0 flex-1">
        <p className={cn("text-[13.5px] leading-snug", task.completed && "text-muted-foreground line-through")}>{task.title}</p>
        {showContext && (task.account || task.deal) && (
          <p className="mt-0.5 truncate text-[12px] text-muted-foreground">
            {task.deal ? <Link href={`/deals/${task.deal.id}`} className="hover:underline">{task.deal.title}</Link> : task.account && <Link href={`/accounts/${task.account.id}`} className="hover:underline">{task.account.name}</Link>}
          </p>
        )}
        {(task.blocked || (task.escalation_level ?? 0) > 0 || (showPeople && task.assignee && task.owner && task.assignee.id !== task.owner.id)) && !task.completed && (
          <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[12px]">
            {task.blocked && task.depends_on && <span className="inline-flex items-center gap-1 text-muted-foreground"><Link2 className="h-3 w-3" />Blocked by “{task.depends_on.title}”</span>}
            {(task.escalation_level ?? 0) > 0 && <span className="inline-flex items-center gap-1 font-medium" style={{ color: "var(--status-critical)" }}><Siren className="h-3 w-3" />Escalated L{task.escalation_level}</span>}
            {showPeople && task.assignee && task.owner && task.assignee.id !== task.owner.id && <span className="inline-flex items-center gap-1 text-muted-foreground"><ArrowUpRight className="h-3 w-3" />{task.owner.full_name} → {task.assignee.full_name}</span>}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {task.priority && PRIORITY_COLOR[task.priority] && !task.completed && (
          <span className="rounded-full px-1.5 text-[11px] font-medium capitalize" style={{ color: PRIORITY_COLOR[task.priority], background: `color-mix(in srgb, ${PRIORITY_COLOR[task.priority]} 12%, transparent)` }}>{task.priority}</span>
        )}
        {task.source === "ai" && <span title="Created by AI"><Sparkles className="h-3 w-3 text-ai" /></span>}
        {!task.completed && (
          <span
            className={cn("text-[12px]", due.tone === "muted" ? "text-subtle" : "text-muted-foreground", due.tone === "critical" && "font-medium text-foreground")}
          >
            {due.tone === "critical" && <span className="mr-1 inline-block h-1.5 w-1.5 rounded-full align-middle" style={{ background: "var(--status-critical)" }} />}
            {due.label}
          </span>
        )}
      </div>
    </div>
  );
}
