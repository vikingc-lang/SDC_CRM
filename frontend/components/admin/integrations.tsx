"use client";

import { useMutation } from "@tanstack/react-query";
import { AlarmClock, CalendarClock, Gauge, GitMerge, Landmark, Map, Play, Radar, RefreshCw, Search, Webhook, Workflow } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { api, errorMessage } from "@/lib/api";

const JOBS = [
  { key: "risk_scan", icon: Radar, label: "Risk & slippage scan", when: "Hourly", desc: "Flags deals stagnant > 14 days, pushed close dates and sentiment drift; raises alerts to owners." },
  { key: "escalations", icon: Gauge, label: "Task SLA escalation", when: "Hourly", desc: "Escalates overdue tasks to the owner's manager, then to admins." },
  { key: "renewals", icon: CalendarClock, label: "Renewal generation", when: "Daily", desc: "Opens renewal deals 90–120 days before contract expiry, carrying the original terms." },
  { key: "rescore", icon: RefreshCw, label: "Re-score all accounts", when: "Nightly", desc: "Recomputes health, relationship strength and churn risk (recency decays daily)." },
  { key: "auto_dedup", icon: GitMerge, label: "Autonomous de-duplication", when: "Nightly", desc: "Merges high-confidence duplicate accounts and contacts with full merge logs." },
  { key: "erp_sync", icon: Landmark, label: "ERP sync", when: "Every 4 hours", desc: "Pulls customer masters, invoices and credit status; pushes new customers." },
  { key: "erp_orders", icon: Landmark, label: "ERP sales-order push", when: "Every 2 minutes", desc: "Pushes queued orders to the ERP, retries failures (max 5) and reads acknowledgements." },
  { key: "lead_rescore", icon: RefreshCw, label: "Lead re-scoring", when: "Nightly", desc: "Applies engagement decay to open leads and promotes new MQLs." },
  { key: "reindex", icon: Search, label: "Rebuild search index", when: "Nightly", desc: "Re-embeds activity for hybrid (keyword + vector) retrieval." },
  { key: "workflows", icon: Workflow, label: "Scheduled workflows", when: "Hourly", desc: "Runs workflow rules with an hourly trigger against the records that match." },
  { key: "case_sla", icon: AlarmClock, label: "Case SLA breach scan", when: "Every 10 minutes", desc: "Flags cases past their response or resolution target and alerts the owner and their manager." },
  { key: "territories", icon: Map, label: "Territory realignment", when: "Nightly", desc: "Re-applies territory rules so every account sits in the right territory." },
  { key: "webhooks", icon: Webhook, label: "Webhook delivery", when: "Every minute", desc: "Sends new events to webhook subscribers and retries failed deliveries with back-off." },
];

export function JobsPanel() {
  const run = useMutation({
    mutationFn: async (job: string) => (await api.post<Record<string, unknown>>(`/admin/jobs/${job}`)).data,
    onSuccess: (r, job) => toast.success(`${JOBS.find((j) => j.key === job)?.label}: ${Object.entries(r).filter(([, v]) => typeof v !== "object").map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ") || "done"}`),
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader title="Background jobs" description="Scheduled by Celery beat in production. Run any of them now." />
      <ul className="divide-y">
        {JOBS.map(({ key, icon: Icon, label, when, desc }) => (
          <li key={key} className="flex items-start gap-3 px-5 py-3">
            <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1"><p className="text-[13.5px] font-medium">{label} <span className="ml-1 text-[12px] font-normal text-muted-foreground">{when}</span></p><p className="text-[12.5px] text-muted-foreground">{desc}</p></div>
            <Button size="sm" variant="outline" loading={run.isPending && run.variables === key} onClick={() => run.mutate(key)}><Play className="h-3.5 w-3.5" />Run</Button>
          </li>
        ))}
      </ul>
    </Card>
  );
}
