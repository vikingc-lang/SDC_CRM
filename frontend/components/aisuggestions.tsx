"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Check, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { StatusPill, Tabs } from "@/components/ui/extra";
import { Input } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { relativeDays, shortDate } from "@/lib/utils";

interface Suggestion {
  id: string; agent_label: string; action_label: string; action_type: string; title: string; rationale: string | null; status: string; mode: "auto" | "approve";
  result: string | null; created_at: string; decided_at: string | null; can_decide: boolean; payload: Record<string, string>;
  owner: { full_name: string | null } | null; decided_by: { full_name: string | null } | null;
  deal: { id: string; title?: string; account?: string };
}

const STATUS: Record<string, string> = { pending: "pending", applied: "completed", rejected: "rejected", failed: "failed", expired: "expired" };

function Change({ s }: { s: Suggestion }) {
  if (s.action_type === "update_deal" && s.payload.field === "target_close_date")
    return <p className="mt-1 text-[13px]">Close date: <span className="line-through text-muted-foreground">{shortDate(s.payload.previous, true)}</span> → <span className="font-medium">{shortDate(s.payload.value, true)}</span></p>;
  if (s.action_type === "create_task") return <p className="mt-1 text-[13px]">New task: <span className="font-medium">{s.payload.title}</span></p>;
  return null;
}

export function AiSuggestions() {
  const qc = useQueryClient();
  const t = useT();
  const [tab, setTab] = useState<"pending" | "">("pending");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const data = useQuery({ queryKey: ["ai", "actions", tab], queryFn: () => get<{ actions: Suggestion[]; counts: Record<string, number> }>("/ai/actions", { status: tab }) });
  const decide = useMutation({
    mutationFn: async (v: { id: string; approve: boolean }) => (await api.post(`/ai/actions/${v.id}/decide`, { approve: v.approve, note: notes[v.id] || null })).data,
    onSuccess: (_, v) => { qc.invalidateQueries(); toast.success(v.approve ? "Approved and applied" : "Rejected"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const waiting = data.data?.counts?.pending ?? 0;
  return (
    <div>
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "pending", label: "Waiting", count: waiting }, { value: "", label: "All activity" }]} />
      {!data.data ? <Skeleton className="h-40" /> : !data.data.actions.length ? (
        <Card><EmptyState icon={<Bot className="h-4 w-4" />} title={tab ? "Nothing waiting for you" : "No AI activity yet"}
          description="When an agent that needs approval wants to change one of your deals, it shows up here with its reasons." /></Card>
      ) : (
        <div className="space-y-3">
          {data.data.actions.map((s) => (
            <Card key={s.id} className="p-4">
              <div className="flex flex-wrap items-start gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Bot className="h-4 w-4 text-ai" />
                    <span className="font-medium">{s.title}</span>
                    <StatusPill status={STATUS[s.status] ?? s.status} />
                    {s.mode === "auto" && <Badge tone="outline">applied automatically</Badge>}
                  </div>
                  <p className="mt-0.5 text-[12.5px] text-muted-foreground">
                    {s.agent_label} · {s.action_label} · {s.deal.title ? <Link href={`/deals/${s.deal.id}`} className="hover:underline">{s.deal.account}: {s.deal.title}</Link> : "deal removed"} · {relativeDays(s.created_at)}
                  </p>
                  <Change s={s} />
                  {s.rationale && <p className="mt-1.5 text-[13px] text-muted-foreground">{s.rationale}</p>}
                  {s.result && s.status !== "pending" && <p className="mt-1 text-[12.5px]">{s.decided_by?.full_name ? `${s.decided_by.full_name}: ` : ""}{s.result}</p>}
                </div>
                {s.status === "pending" && (s.can_decide ? (
                  <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
                    <Input className="h-8 w-full sm:w-48" placeholder="Note (optional)" aria-label="Note" value={notes[s.id] ?? ""} onChange={(e) => setNotes({ ...notes, [s.id]: e.target.value })} />
                    <Button size="sm" variant="outline" disabled={decide.isPending} onClick={() => decide.mutate({ id: s.id, approve: false })}><X className="h-3.5 w-3.5" />{t("common.reject")}</Button>
                    <Button size="sm" disabled={decide.isPending} onClick={() => decide.mutate({ id: s.id, approve: true })}><Check className="h-3.5 w-3.5" />{t("common.approve")}</Button>
                  </div>
                ) : <span className="text-[12px] text-muted-foreground">For {s.owner?.full_name ?? "the owner"} to decide</span>)}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
