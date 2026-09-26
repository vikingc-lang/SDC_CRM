"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Ban, RefreshCcw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Fragment, useState } from "react";
import { toast } from "sonner";
import { ErpStatus, formatAddress } from "@/components/orders";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Field, Table, Td, fmtMoney } from "@/components/ui/extra";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import type { Order } from "@/lib/types";
import { shortDate } from "@/lib/utils";

const STEPS = [["submitted", "Queued"], ["sent_to_erp", "Sent to ERP"], ["acknowledged", "Sales order created"]] as const;

export default function OrderPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const { can } = useMe();
  const [open, setOpen] = useState<number | null>(null);
  const [payload, setPayload] = useState(false);
  const { data: o, isLoading } = useQuery({ queryKey: ["order", id], queryFn: () => get<Order>(`/orders/${id}`) });
  const act = useMutation({
    mutationFn: async (path: "submit" | "cancel") => (await api.post<Order>(`/orders/${id}/${path}`)).data,
    onSuccess: (r) => { qc.invalidateQueries(); toast.success(r.status === "acknowledged" ? `ERP sales order ${r.erp_order_id}` : `Order ${r.status.replace(/_/g, " ")}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (isLoading || !o) return <div className="mx-auto max-w-6xl space-y-4"><Skeleton className="h-20 w-full" /><Skeleton className="h-64 w-full" /></div>;
  const reached = STEPS.findIndex(([s]) => s === o.status);

  return (
    <div className="mx-auto max-w-6xl">
      <Link href="/orders" className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />Orders</Link>
      <div className="mb-5 flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2"><h1 className="text-[24px] font-semibold tracking-tight">{o.order_number}</h1><ErpStatus order={o} /></div>
          <p className="mt-1 text-[13.5px] text-muted-foreground">
            <Link href={`/accounts/${o.account.id}`} className="font-medium text-foreground hover:underline">{o.account.name}</Link>
            {o.deal_id && <> · <Link href={`/deals/${o.deal_id}`} className="hover:underline">deal</Link></>}
            {o.quote_id && <> · <Link href={`/quotes/${o.quote_id}`} className="hover:underline">primary quote</Link></>}
          </p>
        </div>
        <p className="text-[28px] font-semibold leading-8 tracking-tight">{fmtMoney(o.total, o.currency)}</p>
        {can("orders", "update") && ["submitted", "failed"].includes(o.status) && (
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => act.mutate("cancel")}><Ban className="h-3.5 w-3.5" />Cancel</Button>
            <Button size="sm" loading={act.isPending} onClick={() => act.mutate("submit")}><RefreshCcw className="h-3.5 w-3.5" />{o.status === "failed" ? "Retry push" : "Push now"}</Button>
          </div>
        )}
      </div>

      <Card className="mb-6 p-4">
        <ol className="flex flex-wrap items-center gap-2 text-[13px]">
          {STEPS.map(([s, label], i) => (
            <Fragment key={s}>
              {i > 0 && <span className="h-px w-8 bg-border" />}
              <li className={i <= reached ? "font-medium" : "text-subtle"}>{i + 1}. {label}</li>
            </Fragment>
          ))}
        </ol>
        <p className="mt-2 text-[12.5px] text-muted-foreground">
          {o.erp_attempts} push attempt{o.erp_attempts === 1 ? "" : "s"}{o.erp_sent_at && ` · last sent ${shortDate(o.erp_sent_at, true)}`}
          {o.erp_acknowledged_at && ` · acknowledged ${shortDate(o.erp_acknowledged_at, true)}`}{o.erp_message && ` · ${o.erp_message}`}
        </p>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2 overflow-hidden">
          <CardHeader title="Order lines" description="Agreed prices from the locked primary quote, with the billing schedule the ERP invoices against." />
          <Table head={["#", "SKU", "Description", "Qty", "List", "Disc.", "Net unit", "Line total"]} minWidth={760}>
            {o.lines.map((l) => (
              <Fragment key={l.line_no}>
                <tr className="cursor-pointer hover:bg-muted/50" onClick={() => setOpen(open === l.line_no ? null : l.line_no)}>
                  <Td className="tabular text-muted-foreground">{l.parent_line_no ? `↳ ${l.line_no}` : l.line_no}</Td>
                  <Td className="font-mono text-[12.5px]">{l.sku}</Td>
                  <Td>{l.name}<p className="text-[12px] text-muted-foreground">{l.billing_type === "one_time" ? "one-time" : "recurring"} · {l.billing_schedule.length} invoice{l.billing_schedule.length === 1 ? "" : "s"}</p></Td>
                  <Td className="tabular">{l.quantity}</Td>
                  <Td className="tabular">{fmtMoney(l.unit_list_price, o.currency)}</Td>
                  <Td className="tabular">{l.discount_pct}%</Td>
                  <Td className="tabular">{fmtMoney(l.net_unit_price, o.currency)}</Td>
                  <Td className="tabular font-medium">{l.parent_line_no ? <span className="text-muted-foreground">included</span> : fmtMoney(l.line_total, o.currency)}</Td>
                </tr>
                {open === l.line_no && l.billing_schedule.length > 0 && (
                  <tr><td colSpan={8} className="bg-surface-2/50 px-4 py-2">
                    <ul className="grid grid-cols-1 gap-1 text-[12.5px] sm:grid-cols-2">
                      {l.billing_schedule.map((b) => <li key={b.invoice_date} className="flex justify-between gap-3"><span className="text-muted-foreground">{b.period}</span><span className="tabular">{fmtMoney(b.amount, o.currency)}</span></li>)}
                    </ul>
                  </td></tr>
                )}
              </Fragment>
            ))}
          </Table>
        </Card>
        <div className="space-y-6">
          <Card>
            <CardHeader title="Header" />
            <CardBody className="grid grid-cols-2 gap-3">
              <Field label="Customer PO">{o.po_number}</Field>
              <Field label="ERP customer">{o.account.erp_customer_id}</Field>
              <Field label="Payment terms">{o.payment_terms}</Field>
              <Field label="Billing"><span className="capitalize">{o.billing_frequency}</span> · {o.term_months} mo</Field>
              <Field label="Start">{shortDate(o.start_date, true)}</Field>
              <Field label="Requested delivery">{o.requested_delivery_date ? shortDate(o.requested_delivery_date, true) : null}</Field>
              <Field label="Incoterms">{o.incoterms}</Field>
              <Field label="Tax">{o.tax_exempt ? "Exempt (certificate on file)" : "Taxable"}</Field>
              <Field label="Bill to" className="col-span-2">{formatAddress(o.bill_to)}</Field>
              <Field label="Ship to" className="col-span-2">{formatAddress(o.ship_to)}</Field>
            </CardBody>
          </Card>
          {o.erp_payload && (
            <Card>
              <CardHeader title="ERP payload" action={<Button variant="ghost" size="sm" onClick={() => setPayload(!payload)}>{payload ? "Hide" : "Show"}</Button>} />
              {payload && <CardBody><pre className="max-h-96 overflow-auto rounded bg-muted p-3 text-[11.5px]">{JSON.stringify(o.erp_payload, null, 2)}</pre></CardBody>}
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
