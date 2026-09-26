"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Workflow } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import type { Filter } from "@/components/reportviz";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Table, Td } from "@/components/ui/extra";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { relativeDays } from "@/lib/utils";

export type WorkflowAction =
  | { type: "create_task"; title: string; description?: string; due_in_days: number; priority: string; assign_to: string }
  | { type: "notify"; to: string[]; title: string; body?: string }
  | { type: "update_field"; field: string; value: string }
  | { type: "emit_event"; event: string };
export interface WorkflowRule {
  id?: string; name: string; description: string | null; enabled: boolean; source: string;
  trigger: { type: "created" | "updated" | "schedule"; fields?: string[]; repeat_after_days?: number | null };
  conditions: Filter[]; actions: WorkflowAction[];
}
interface RuleRow extends WorkflowRule { id: string; run_count: number; last_run_at: string | null; created_by: string | null }

/* Starting points offered from Admin → Workflows → New. They open switched off, for review. */
export const TEMPLATES: Record<string, Omit<WorkflowRule, "id">> = {
  webform: {
    name: "Follow up new web-form leads", description: "A call task for the lead owner as soon as a web-form lead arrives.", enabled: false, source: "leads",
    trigger: { type: "created" }, conditions: [{ field: "source", op: "eq", value: "web_form" }],
    actions: [{ type: "create_task", title: "Call {{name}} at {{company}}", due_in_days: 1, priority: "high", assign_to: "owner" }],
  },
  bigdeal: {
    name: "Tell managers when a big deal moves", description: "Notify Sales Managers when an opportunity over $250k changes stage.", enabled: false, source: "deals",
    trigger: { type: "updated", fields: ["stage"] }, conditions: [{ field: "amount_usd", op: "gte", value: 250000 }],
    actions: [{ type: "notify", to: ["role:sales_manager"], title: "{{title}} moved to {{stage}}", body: "{{account}} · {{amount_usd}} · owner {{owner}}" }],
  },
  stalled: {
    name: "Rescue plan for risky deals", description: "Each hour, give the owner's manager a task for open deals with a high risk score.", enabled: false, source: "deals",
    trigger: { type: "schedule", repeat_after_days: 14 }, conditions: [{ field: "status", op: "eq", value: "Open" }, { field: "risk_score", op: "gte", value: 70 }],
    actions: [{ type: "create_task", title: "Review risk on {{title}} ({{account}})", due_in_days: 2, priority: "high", assign_to: "manager" }],
  },
};

const SOURCE_LABEL: Record<string, string> = { deals: "Opportunities", leads: "Leads", accounts: "Accounts", contacts: "Contacts", activities: "Activities", tasks: "Tasks", quotes: "Quotes", orders: "Orders", cases: "Cases" };

function triggerText(r: WorkflowRule) {
  if (r.trigger.type === "created") return "When created";
  if (r.trigger.type === "updated") return `When ${(r.trigger.fields ?? []).join(", ").replace(/_/g, " ")} change${(r.trigger.fields ?? []).length === 1 ? "s" : ""}`;
  return r.trigger.repeat_after_days ? `Hourly, repeats after ${r.trigger.repeat_after_days}d` : "Hourly, once per record";
}

export function WorkflowsPanel() {
  const qc = useQueryClient();
  const rules = useQuery({ queryKey: ["workflows", "list"], queryFn: () => get<RuleRow[]>("/workflows") });
  const toggle = useMutation({
    mutationFn: async (id: string) => (await api.post<RuleRow>(`/workflows/${id}/toggle`)).data,
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["workflows"] }); toast.success(`${r.name} is ${r.enabled ? "on" : "off"}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Workflows" icon={<Workflow className="h-4 w-4" />}
          description="Automate follow-ups: when a record is created, a field changes, or on an hourly check, create tasks, notify people, update fields or send events."
          action={<Link href="/admin/workflows/new"><Button size="sm"><Plus className="h-3.5 w-3.5" />New workflow</Button></Link>} />
        {!rules.data ? <Skeleton className="m-5 h-32" /> : !rules.data.length ? (
          <EmptyState icon={<Workflow className="h-4 w-4" />} title="No workflows yet" description="Start from scratch or from one of the templates below." />
        ) : (
          <Table head={["Workflow", "Records", "Trigger", "Actions · runs", "Last run", "On"]} minWidth={760}>
            {rules.data.map((r) => (
              <tr key={r.id}>
                <Td><Link href={`/admin/workflows/${r.id}`} className="font-medium hover:text-primary hover:underline">{r.name}</Link>
                  {r.description && <span className="block text-[12px] text-muted-foreground">{r.description}</span>}</Td>
                <Td className="text-[13px]">{SOURCE_LABEL[r.source] ?? r.source}</Td>
                <Td className="text-[13px]">{triggerText(r)}</Td>
                <Td className="tabular text-[13px]">{r.actions.length} · {r.run_count} run{r.run_count === 1 ? "" : "s"}</Td>
                <Td className="text-[12.5px] text-muted-foreground">{r.last_run_at ? relativeDays(r.last_run_at) : "Never"}</Td>
                <Td><label className="flex items-center gap-2 text-[13px]"><input id={`wf-on-${r.id}`} type="checkbox" checked={r.enabled}
                  disabled={toggle.isPending} onChange={() => toggle.mutate(r.id)} aria-label={`Turn ${r.name} ${r.enabled ? "off" : "on"}`} />{r.enabled ? "On" : "Off"}</label></Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      <div>
        <p className="mb-2 text-[12px] font-medium uppercase tracking-wide text-muted-foreground">Templates</p>
        <div className="grid gap-3 sm:grid-cols-3">
          {Object.entries(TEMPLATES).map(([k, t]) => (
            <Link key={k} href={`/admin/workflows/new?template=${k}`} className="rounded-lg border bg-surface p-4 shadow-card transition-colors hover:border-primary/40">
              <p className="text-[14px] font-medium">{t.name}</p>
              <p className="mt-1 text-[12.5px] text-muted-foreground">{t.description}</p>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
