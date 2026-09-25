"use client";

import { useQuery } from "@tanstack/react-query";
import { CalendarDays, CheckSquare, Plus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { PageHeader } from "@/components/AppShell";
import { NewTaskDialog } from "@/components/forms";
import { TaskRow } from "@/components/TaskList";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { get } from "@/lib/api";
import type { Task } from "@/lib/types";
import { cn } from "@/lib/utils";

function bucket(t: Task) {
  if (!t.due_date) return "Someday";
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((new Date(`${t.due_date}T00:00:00`).getTime() - today.getTime()) / 86_400_000);
  if (diff < 0) return "Overdue";
  if (diff === 0) return "Today";
  if (diff <= 7) return "Next 7 days";
  return "Later";
}
const ORDER = ["Overdue", "Today", "Next 7 days", "Later", "Someday"];

export default function TasksPage() {
  const [status, setStatus] = useState<"open" | "done">("open");
  const [creating, setCreating] = useState(false);
  const [mine, setMine] = useState(false);
  const { data, isLoading } = useQuery({ queryKey: ["tasks", status, mine], queryFn: () => get<Task[]>("/tasks", { status, assignee: mine ? "me" : "all" }) });
  const escalated = data?.filter((t) => (t.escalation_level ?? 0) > 0 && !t.completed).length ?? 0;
  const groups = ORDER.map((g) => ({ name: g, tasks: (data ?? []).filter((t) => bucket(t) === g) })).filter((g) => g.tasks.length);

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="Tasks"
        description={escalated ? `${escalated} escalated past SLA · dependencies and delegation tracked` : "Action items you logged, delegated work and ones relate extracted for you"}
        actions={
          <>
            <div className="flex rounded-md border bg-surface p-0.5 text-[13px]">
              {(["open", "done"] as const).map((s) => (
                <button key={s} onClick={() => setStatus(s)} className={cn("rounded px-3 py-1 capitalize", status === s ? "bg-muted font-medium" : "text-muted-foreground")}>{s}</button>
              ))}
            </div>
            <div className="flex rounded-md border bg-surface p-0.5 text-[13px]">
              {([false, true] as const).map((m) => (
                <button key={String(m)} onClick={() => setMine(m)} className={cn("rounded px-3 py-1", mine === m ? "bg-muted font-medium" : "text-muted-foreground")}>{m ? "Assigned to me" : "All visible"}</button>
              ))}
            </div>
            <Link href="/settings" className="hidden items-center gap-1 text-[12.5px] text-muted-foreground hover:text-foreground sm:inline-flex"><CalendarDays className="h-3.5 w-3.5" />Subscribe (iCal)</Link>
            <Button size="sm" onClick={() => setCreating(true)}><Plus className="h-4 w-4" />New task</Button>
          </>
        }
      />
      {isLoading && <Skeleton className="h-60 w-full" />}
      {!isLoading && !data?.length && (
        <Card><EmptyState icon={<CheckSquare className="h-4 w-4" />} title={status === "open" ? "Nothing on your plate" : "No completed tasks yet"} description="Log a meeting with ⌘K and relate will pull out the next steps." /></Card>
      )}
      <div className="space-y-4">
        {status === "done"
          ? data?.length ? <Card><CardBody className="px-3 pt-3">{data.map((t) => <TaskRow key={t.id} task={t} />)}</CardBody></Card> : null
          : groups.map((g) => (
              <Card key={g.name}>
                <CardHeader title={<span className="flex items-center gap-2">{g.name === "Overdue" && <span className="h-2 w-2 rounded-full" style={{ background: "var(--status-critical)" }} />}{g.name}<span className="text-[12px] font-normal text-muted-foreground">{g.tasks.length}</span></span>} />
                <CardBody className="px-3">{g.tasks.map((t) => <TaskRow key={t.id} task={t} showPeople />)}</CardBody>
              </Card>
            ))}
      </div>
      <NewTaskDialog open={creating} onOpenChange={setCreating} />
    </div>
  );
}
