"use client";

import { useQuery } from "@tanstack/react-query";
import { PackageCheck } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { PageHeader } from "@/components/AppShell";
import { ErpStatus } from "@/components/orders";
import { Card } from "@/components/ui/card";
import { Table, Td, Tabs, fmtMoney } from "@/components/ui/extra";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { get } from "@/lib/api";
import type { Order } from "@/lib/types";
import { relativeDays, shortDate } from "@/lib/utils";

const FILTERS = { all: undefined, queued: "submitted", sent: "sent_to_erp", acknowledged: "acknowledged", failed: "failed" } as const;

export default function OrdersPage() {
  const [tab, setTab] = useState<keyof typeof FILTERS>("all");
  const { data, isLoading } = useQuery({ queryKey: ["orders", tab], queryFn: () => get<Order[]>("/orders", { status: FILTERS[tab] }), refetchInterval: 15_000 });
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader title="Orders" description="Orders raised from Closed-Won deals and pushed asynchronously to the ERP as sales orders." />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "all", label: "All" }, { value: "queued", label: "Queued" }, { value: "sent", label: "Awaiting ERP ack" },
        { value: "acknowledged", label: "In ERP" }, { value: "failed", label: "Failed" }]} />
      <Card className="overflow-hidden">
        {isLoading ? <Skeleton className="m-4 h-40" /> : !data?.length ? (
          <EmptyState icon={<PackageCheck className="h-4 w-4" />} title="No orders here" description="Orders are created when a solution deal closes with a signed Order Form, PO and addresses." />
        ) : (
          <Table head={["Order", "Customer", "PO", "Billing", "Start", "Total", "ERP", "Created"]} minWidth={940}>
            {data.map((o) => (
              <tr key={o.id} className="hover:bg-muted/50">
                <Td><Link href={`/orders/${o.id}`} className="font-medium hover:underline">{o.order_number}</Link></Td>
                <Td>{o.account.name}<p className="text-[12px] text-muted-foreground">{o.account.erp_customer_id ? `ERP customer ${o.account.erp_customer_id}` : "New customer master"}</p></Td>
                <Td className="font-mono text-[12.5px]">{o.po_number ?? "—"}</Td>
                <Td className="capitalize text-muted-foreground">{o.billing_frequency} · {o.term_months} mo · {o.payment_terms}</Td>
                <Td className="text-muted-foreground">{shortDate(o.start_date, true)}</Td>
                <Td className="tabular font-medium">{fmtMoney(o.total, o.currency)}</Td>
                <Td><ErpStatus order={o} />{o.status === "failed" && <p className="mt-1 max-w-[220px] truncate text-[12px] text-muted-foreground" title={o.erp_message ?? ""}>{o.erp_message}</p>}</Td>
                <Td className="text-muted-foreground">{relativeDays(o.created_at)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
