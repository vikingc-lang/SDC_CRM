"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { FileSignature, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { PageHeader } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { StatusPill, Table, Td, Tabs, fmtMoney } from "@/components/ui/extra";
import { Input, Label } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import type { Quote } from "@/lib/types";
import { cn, relativeDays } from "@/lib/utils";

interface DealOption { id: string; title: string; amount: number; currency: string; account: { name: string } }

const FILTERS = { all: undefined, draft: "draft,rejected", pending: "pending_approval", approved: "approved,sent", accepted: "accepted" } as const;

export default function QuotesPage() {
  const [tab, setTab] = useState<keyof typeof FILTERS>("all");
  const { data, isLoading } = useQuery({ queryKey: ["quotes", tab], queryFn: () => get<Quote[]>("/quotes", { status: FILTERS[tab] }) });
  const { can } = useMe();
  const [picking, setPicking] = useState(false);
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader title="Quotes" description="Configure, price and quote: tiered rate cards, TCV and approval routing. Every quote belongs to an open deal."
        actions={can("quotes", "create") && <Button size="sm" onClick={() => setPicking(true)}><Plus className="h-3.5 w-3.5" />New quote</Button>} />
      {picking && <NewQuoteDialog onClose={() => setPicking(false)} />}
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "all", label: "All" }, { value: "draft", label: "Drafts" }, { value: "pending", label: "Awaiting approval" },
        { value: "approved", label: "Approved / sent" }, { value: "accepted", label: "Accepted" }]} />
      <Card className="overflow-hidden">
        {isLoading ? <Skeleton className="m-4 h-40" /> : !data?.length ? (
          <EmptyState icon={<FileSignature className="h-4 w-4" />} title="No quotes here" description={can("quotes", "create") ? "Choose New quote and pick the deal it's for." : undefined} />
        ) : (
          <Table head={["Quote", "Account / deal", "Term", "Max discount", "ACV", "TCV", "Status", "Created"]} minWidth={860}>
            {data.map((q) => (
              <tr key={q.id} className="hover:bg-muted/50">
                <Td><Link href={`/quotes/${q.id}`} className="font-medium hover:underline">{q.quote_number}</Link><p className="text-[12px] text-muted-foreground">{q.name}</p></Td>
                <Td>{q.deal?.account.name}<p className="text-[12px] text-muted-foreground">{q.deal?.title}</p></Td>
                <Td className="text-muted-foreground">{q.term_months} mo · {q.payment_terms}</Td>
                <Td className="tabular">{q.max_discount_pct}%</Td>
                <Td className="tabular">{fmtMoney(q.acv, q.currency)}</Td>
                <Td className="tabular font-medium">{fmtMoney(q.tcv, q.currency)}</Td>
                <Td><StatusPill status={q.status} /></Td>
                <Td className="text-muted-foreground">{relativeDays(q.created_at)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}

/** Pick the open deal a quote is for (pricing, currency and approvals come from its account), then open the new draft. */
function NewQuoteDialog({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [dealId, setDealId] = useState("");
  useEffect(() => { const t = setTimeout(() => setSearch(q.trim()), 250); return () => clearTimeout(t); }, [q]);
  const deals = useQuery({ queryKey: ["deals", "open", "pick", search], placeholderData: (p) => p,
    queryFn: () => get<DealOption[]>("/deals", { status: "open", search: search || undefined }) });
  const create = useMutation({
    mutationFn: async () => (await api.post<Quote>(`/deals/${dealId}/quotes`, { lines: [] })).data,
    onSuccess: (quote) => router.push(`/quotes/${quote.id}`),
  });
  const rows = (deals.data ?? []).slice(0, 50);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title="New quote" className="max-w-lg">
        <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); if (dealId) create.mutate(); }}>
          <h2 className="text-[15px] font-semibold">New quote</h2>
          <p className="text-[13px] text-muted-foreground">Choose the open deal this quote is for. Its account sets the price book, currency and approval routing.</p>
          <div><Label htmlFor="nq-search">Deal</Label>
            <Input id="nq-search" autoFocus placeholder="Search deals or accounts" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <div role="listbox" aria-label="Open deals" className="max-h-72 overflow-y-auto rounded-md border">
            {deals.isLoading ? <Skeleton className="m-3 h-24" /> : !rows.length ? (
              <p className="p-3 text-[13px] text-muted-foreground">No open deals match. Quotes can only be created for deals you can see that aren’t closed.</p>
            ) : rows.map((d) => (
              <button key={d.id} type="button" role="option" aria-selected={dealId === d.id} onClick={() => setDealId(d.id)}
                className={cn("flex w-full items-center justify-between gap-3 border-b px-3 py-2 text-left text-[13px] last:border-b-0 hover:bg-muted/60",
                  dealId === d.id && "bg-primary-soft")}>
                <span className="min-w-0"><span className="block truncate font-medium">{d.title}</span>
                  <span className="block truncate text-[12px] text-muted-foreground">{d.account.name}</span></span>
                <span className="shrink-0 tabular text-[12.5px] text-muted-foreground">{fmtMoney(d.amount, d.currency)}</span>
              </button>
            ))}
          </div>
          {create.isError && <p className="text-sm text-destructive">{errorMessage(create.error)}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
            <Button type="submit" size="sm" disabled={!dealId} loading={create.isPending}>Create quote</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
