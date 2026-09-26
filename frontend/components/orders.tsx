"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, PackageCheck, Send, Upload } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StatusPill, fmtMoney } from "@/components/ui/extra";
import { Input, Label } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api";
import type { Address, DealDetail, Order } from "@/lib/types";
import { cn, relativeDays } from "@/lib/utils";

const ADDRESS_FIELDS: [keyof Address, string][] = [["line1", "Street"], ["city", "City"], ["region", "State / region"], ["postal_code", "Postal code"], ["country", "Country"]];

export function formatAddress(a?: Address | null) {
  if (!a || !Object.keys(a).length) return null;
  return [a.attention, a.line1, a.line2, [a.postal_code, a.city].filter(Boolean).join(" "), a.region, a.country].filter(Boolean).join(", ");
}

export function ErpStatus({ order }: { order: Order }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <StatusPill status={order.status} />
      {order.erp_order_id && <span className="font-mono text-[12px]">{order.erp_order_id}</span>}
    </span>
  );
}

function AddressEditor({ label, value, onChange, disabled }: { label: string; value: Address; onChange: (a: Address) => void; disabled?: boolean }) {
  return (
    <fieldset className="rounded-md border p-3">
      <legend className="px-1 text-[12px] font-medium text-muted-foreground">{label}</legend>
      <div className="grid grid-cols-2 gap-2">
        {ADDRESS_FIELDS.map(([k, l]) => (
          <Input key={k} aria-label={`${label} ${l}`} placeholder={l} disabled={disabled} className={cn("h-8", k === "line1" && "col-span-2")}
            value={value[k] ?? ""} onChange={(e) => onChange({ ...value, [k]: e.target.value })} />
        ))}
      </div>
    </fieldset>
  );
}

/** Closed-Won validation and order hand-off panel on the deal page (lead-to-order, step 7). */
export function OrderPanel({ deal, canEdit, canOrder }: { deal: DealDetail; canEdit: boolean; canOrder: boolean }) {
  const qc = useQueryClient();
  const d = deal.order_details;
  const [f, setF] = useState({ po_number: "", incoterms: "", requested_delivery_date: "", tax_exempt: false, bill_to: {} as Address, ship_to: {} as Address });
  const [sameAsBill, setSameAsBill] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!d) return;
    setF({ po_number: d.po_number ?? "", incoterms: d.incoterms ?? "", requested_delivery_date: d.requested_delivery_date ?? "", tax_exempt: d.tax_exempt,
      bill_to: d.bill_to ?? {}, ship_to: d.ship_to ?? {} });
  }, [d]);
  const refresh = () => qc.invalidateQueries({ queryKey: ["deal", deal.id] });
  const save = useMutation({
    mutationFn: async () => (await api.patch(`/deals/${deal.id}`, { ...f, incoterms: f.incoterms || null, requested_delivery_date: f.requested_delivery_date || null,
      ship_to: sameAsBill ? f.bill_to : f.ship_to })).data,
    onSuccess: () => { refresh(); toast.success("Order details saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const upload = useMutation({
    mutationFn: async (file: File) => {
      const body = new FormData();
      body.append("file", file);
      body.append("deal_id", deal.id);
      body.append("note", "Tax-exemption certificate");
      const att = (await api.post<{ id: string }>(`/accounts/${deal.account.id}/files`, body)).data;
      return (await api.patch(`/deals/${deal.id}`, { tax_exempt: true, tax_exempt_cert_id: att.id })).data;
    },
    onSuccess: () => { refresh(); toast.success("Certificate attached"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const create = useMutation({
    mutationFn: async () => (await api.post<Order>(`/deals/${deal.id}/orders`)).data,
    onSuccess: (o) => { refresh(); toast.success(`${o.order_number} created and queued for the ERP`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (!d || !deal.order_readiness) return null;
  const ready = deal.order_readiness.every((c) => c.met);
  const orders = deal.orders ?? [];
  const live = orders.find((o) => o.status !== "cancelled");
  const won = deal.stage === "Closed-Won";

  return (
    <Card>
      <CardHeader title="Order readiness" icon={<PackageCheck className="h-4 w-4 text-muted-foreground" />}
        description="Closed-Won validation: what the ERP needs to raise the sales order."
        action={ready ? <span className="text-[12.5px] font-medium" style={{ color: "var(--status-good)" }}>Ready to order</span> :
          <span className="text-[12.5px] text-muted-foreground">{deal.order_readiness.filter((c) => c.met).length}/{deal.order_readiness.length} met</span>} />
      <CardBody className="space-y-4">
        <ul className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
          {deal.order_readiness.map((c) => (
            <li key={c.criterion} className="flex items-center gap-2 text-[13px]">
              {c.met ? <CheckCircle2 className="h-4 w-4 shrink-0 text-primary" /> : <CircleDashed className="h-4 w-4 shrink-0 text-subtle" />}
              <span className={cn(!c.met && "text-muted-foreground")}>{c.criterion}</span>
            </li>
          ))}
        </ul>

        {live ? (
          <div className="rounded-md border p-3 text-[13px]">
            <div className="flex flex-wrap items-center gap-2">
              <Link href={`/orders/${live.id}`} className="font-medium hover:underline">{live.order_number}</Link>
              <ErpStatus order={live} />
              <span className="ml-auto tabular font-medium">{fmtMoney(live.total, live.currency)}</span>
            </div>
            <p className="mt-1 text-[12px] text-muted-foreground">
              PO {live.po_number ?? "—"} · {live.lines.length} lines · {live.billing_frequency} billing · created {relativeDays(live.created_at)}
              {live.erp_acknowledged_at && ` · ERP acknowledged ${relativeDays(live.erp_acknowledged_at)}`}
            </p>
          </div>
        ) : (
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div><Label htmlFor="po">Customer PO number</Label><Input id="po" disabled={!canEdit} value={f.po_number} onChange={(e) => setF({ ...f, po_number: e.target.value })} /></div>
              <div><Label htmlFor="rdd">Requested delivery</Label><Input id="rdd" type="date" disabled={!canEdit} value={f.requested_delivery_date} onChange={(e) => setF({ ...f, requested_delivery_date: e.target.value })} /></div>
              <div><Label htmlFor="inco">Incoterms</Label><Input id="inco" disabled={!canEdit} placeholder="e.g. DAP" maxLength={10} value={f.incoterms} onChange={(e) => setF({ ...f, incoterms: e.target.value.toUpperCase() })} /></div>
            </div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <AddressEditor label="Bill to" value={f.bill_to} disabled={!canEdit} onChange={(bill_to) => setF({ ...f, bill_to })} />
              {sameAsBill ? <div className="flex items-center rounded-md border p-3 text-[13px] text-muted-foreground">Ship to the billing address</div> :
                <AddressEditor label="Ship to" value={f.ship_to} disabled={!canEdit} onChange={(ship_to) => setF({ ...f, ship_to })} />}
            </div>
            <div className="flex flex-wrap items-center gap-4 text-[13px]">
              <label className="flex items-center gap-2"><input type="checkbox" checked={sameAsBill} disabled={!canEdit} onChange={(e) => setSameAsBill(e.target.checked)} />Ship to billing address</label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={f.tax_exempt} disabled={!canEdit} onChange={(e) => setF({ ...f, tax_exempt: e.target.checked })} />Tax exempt</label>
              {f.tax_exempt && (
                d.tax_exempt_cert_id ? <span className="text-muted-foreground">Certificate on file</span> : canEdit && (
                  <>
                    <input ref={fileRef} type="file" className="hidden" accept=".pdf,.png,.jpg" onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} />
                    <Button type="button" variant="outline" size="sm" loading={upload.isPending} onClick={() => fileRef.current?.click()}><Upload className="h-3.5 w-3.5" />Upload certificate</Button>
                  </>
                )
              )}
            </div>
            <div className="flex flex-wrap justify-end gap-2">
              {canEdit && <Button type="submit" variant="outline" size="sm" loading={save.isPending}>Save order details</Button>}
              {canOrder && won && (
                <Button type="button" size="sm" disabled={!ready} loading={create.isPending} onClick={() => create.mutate()}
                  title={ready ? "Create the order from the primary quote" : "Complete the checklist first"}><Send className="h-3.5 w-3.5" />Create order</Button>
              )}
            </div>
            {!won && <p className="text-[12px] text-muted-foreground">Moving the deal to Closed-Won locks the primary quote and raises the order automatically once every check passes.</p>}
          </form>
        )}
      </CardBody>
    </Card>
  );
}
