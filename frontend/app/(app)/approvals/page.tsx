"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Stamp, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { StatusPill, Tabs, fmtMoney } from "@/components/ui/extra";
import { Input } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { relativeDays } from "@/lib/utils";

interface ApprovalItem {
  id: string; required_role: string; reason: string; status: string; comment: string | null; created_at: string; decided_at: string | null;
  decided_by: { full_name: string } | null; can_decide: boolean;
  quote: { id: string; quote_number: string; name: string; currency: string; tcv: number; acv: number; max_discount_pct: number; payment_terms: string;
    deal: { id: string; title: string; account: string } };
}

export default function ApprovalsPage() {
  const [tab, setTab] = useState<"pending" | "decided">("pending");
  const [comments, setComments] = useState<Record<string, string>>({});
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["approvals", tab], queryFn: () => get<ApprovalItem[]>("/approvals", { status: tab }) });
  const decide = useMutation({
    mutationFn: async (v: { id: string; approve: boolean }) => (await api.post(`/approvals/${v.id}/decide`, { approve: v.approve, comment: comments[v.id] || null })).data,
    onSuccess: (_, v) => { qc.invalidateQueries(); toast.success(v.approve ? "Approved" : "Rejected"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Approvals" description="Discounts, non-standard payment terms, credit holds and large deals route here automatically." />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "pending", label: "Waiting" }, { value: "decided", label: "Decided" }]} />
      {isLoading && <Skeleton className="h-40 w-full" />}
      {data && !data.length && <Card><EmptyState icon={<Stamp className="h-4 w-4" />} title={tab === "pending" ? "Nothing waiting on approval" : "No decisions yet"} /></Card>}
      <div className="space-y-3">
        {data?.map((a) => (
          <Card key={a.id} className="p-4">
            <div className="flex flex-wrap items-start gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Link href={`/quotes/${a.quote.id}`} className="font-medium hover:underline">{a.quote.quote_number}</Link>
                  <span className="text-muted-foreground">·</span>
                  <Link href={`/deals/${a.quote.deal.id}`} className="text-[13.5px] hover:underline">{a.quote.deal.account}: {a.quote.deal.title}</Link>
                  <StatusPill status={a.status} />
                </div>
                <p className="mt-1 text-[13px]"><span className="font-medium capitalize">{a.required_role.replace("_", " ")} approval:</span> {a.reason}</p>
                <p className="mt-1 text-[12px] text-muted-foreground">
                  TCV {fmtMoney(a.quote.tcv, a.quote.currency)} · ACV {fmtMoney(a.quote.acv, a.quote.currency)} · max discount {a.quote.max_discount_pct}% · {a.quote.payment_terms} · requested {relativeDays(a.created_at)}
                </p>
                {a.decided_by && <p className="mt-1 text-[12.5px]">{a.decided_by.full_name}{a.comment && `: "${a.comment}"`}</p>}
              </div>
              {a.status === "pending" && (a.can_decide ? (
                <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
                  <Input className="h-8 w-full sm:w-52" placeholder="Comment (optional)" value={comments[a.id] ?? ""} onChange={(e) => setComments({ ...comments, [a.id]: e.target.value })} />
                  <Button size="sm" variant="outline" onClick={() => decide.mutate({ id: a.id, approve: false })} disabled={decide.isPending}><X className="h-3.5 w-3.5" />Reject</Button>
                  <Button size="sm" onClick={() => decide.mutate({ id: a.id, approve: true })} disabled={decide.isPending}><Check className="h-3.5 w-3.5" />Approve</Button>
                </div>
              ) : <span className="text-[12px] text-muted-foreground">Needs a {a.required_role === "finance" ? "finance (Super Admin)" : "sales manager"} approver</span>)}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
