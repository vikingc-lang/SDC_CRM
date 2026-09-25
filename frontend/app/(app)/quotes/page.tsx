"use client";

import { useQuery } from "@tanstack/react-query";
import { FileSignature } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { PageHeader } from "@/components/AppShell";
import { Card } from "@/components/ui/card";
import { StatusPill, Table, Td, Tabs, fmtMoney } from "@/components/ui/extra";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { get } from "@/lib/api";
import type { Quote } from "@/lib/types";
import { relativeDays } from "@/lib/utils";

const FILTERS = { all: undefined, draft: "draft,rejected", pending: "pending_approval", approved: "approved,sent", accepted: "accepted" } as const;

export default function QuotesPage() {
  const [tab, setTab] = useState<keyof typeof FILTERS>("all");
  const { data, isLoading } = useQuery({ queryKey: ["quotes", tab], queryFn: () => get<Quote[]>("/quotes", { status: FILTERS[tab] }) });
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader title="Quotes" description="Configure, price and quote: tiered rate cards, TCV and approval routing. Create quotes from a deal." />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "all", label: "All" }, { value: "draft", label: "Drafts" }, { value: "pending", label: "Awaiting approval" },
        { value: "approved", label: "Approved / sent" }, { value: "accepted", label: "Accepted" }]} />
      <Card className="overflow-hidden">
        {isLoading ? <Skeleton className="m-4 h-40" /> : !data?.length ? (
          <EmptyState icon={<FileSignature className="h-4 w-4" />} title="No quotes here" description="Open a deal and choose New quote." />
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
