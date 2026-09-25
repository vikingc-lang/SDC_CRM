"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, FilePlus2, Plus, Save, Send, Stamp, Trash2, XCircle } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StatusPill, fmtMoney } from "@/components/ui/extra";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import type { DocumentSummary, Product, Quote } from "@/lib/types";
import { shortDate } from "@/lib/utils";

type Line = { product_id: string; quantity: number; discount_pct: number };

function tierPrice(p: Product | undefined, currency: string, qty: number) {
  const tiers = p?.prices.find((x) => x.currency === currency)?.tiers ?? [];
  let price: number | null = tiers[0]?.unit_price ?? null;
  for (const t of tiers) if (qty >= t.min_qty) price = t.unit_price;
  return price;
}

export default function QuotePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const { can } = useMe();
  const { data: quote, isLoading } = useQuery({ queryKey: ["quote", id], queryFn: () => get<Quote>(`/quotes/${id}`) });
  const { data: products } = useQuery({ queryKey: ["products"], queryFn: () => get<Product[]>("/products") });
  const [form, setForm] = useState({ name: "", currency: "USD", term_months: 12, payment_terms: "NET30", notes: "" });
  const [lines, setLines] = useState<Line[]>([]);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (quote && !dirty) {
      setForm({ name: quote.name, currency: quote.currency, term_months: quote.term_months, payment_terms: quote.payment_terms, notes: quote.notes ?? "" });
      setLines(quote.lines.map((l) => ({ product_id: l.product_id, quantity: l.quantity, discount_pct: l.discount_pct })));
    }
  }, [quote, dirty]);

  const byId = useMemo(() => Object.fromEntries((products ?? []).map((p) => [p.id, p])), [products]);
  const preview = useMemo(() => {
    let tcv = 0, list = 0, monthly = 0;
    const rows = lines.map((l) => {
      const p = byId[l.product_id];
      const unit = tierPrice(p, form.currency, l.quantity);
      const periods = p?.billing_type === "recurring" ? form.term_months : 1;
      const net = unit === null ? null : unit * (1 - l.discount_pct / 100);
      const total = net === null ? null : net * l.quantity * periods;
      if (unit !== null && total !== null) {
        tcv += total;
        list += unit * l.quantity * periods;
        if (p?.billing_type === "recurring") monthly += (net ?? 0) * l.quantity;
      }
      return { unit, net, total };
    });
    return { rows, tcv, list, acv: monthly * 12, discount: list - tcv };
  }, [lines, byId, form]);

  const editable = quote && !["sent", "accepted"].includes(quote.status) && can("quotes", "update");
  const save = useMutation({
    mutationFn: async () => (await api.put<Quote>(`/quotes/${id}`, { ...form, lines })).data,
    onSuccess: (q) => { qc.setQueryData(["quote", id], q); qc.invalidateQueries({ queryKey: ["quote", id] }); setDirty(false); toast.success("Quote saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const submit = useMutation({
    mutationFn: async () => {
      if (dirty) await api.put(`/quotes/${id}`, { ...form, lines });
      return (await api.post<Quote>(`/quotes/${id}/submit`)).data;
    },
    onSuccess: (q) => {
      setDirty(false);
      qc.invalidateQueries();
      toast.success(q.status === "approved" ? "Within policy: auto-approved" : "Submitted for approval", {
        description: q.status === "pending_approval" ? q.approvals.filter((a) => a.status === "pending").map((a) => a.required_role.replace("_", " ")).join(" + ") : undefined,
      });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const orderForm = useMutation({
    mutationFn: async () => (await api.post<DocumentSummary>("/documents", { doc_type: "order_form", deal_id: quote?.deal_id, quote_id: id })).data,
    onSuccess: (d) => router.push(`/documents/${d.id}`),
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (isLoading || !quote) return <div className="mx-auto max-w-6xl space-y-4"><Skeleton className="h-16 w-full" /><Skeleton className="h-72 w-full" /></div>;
  const update = (i: number, patch: Partial<Line>) => { setLines(lines.map((l, j) => (j === i ? { ...l, ...patch } : l))); setDirty(true); };
  const cur = form.currency;

  return (
    <div className="mx-auto max-w-6xl">
      {quote.deal && <Link href={`/deals/${quote.deal.id}`} className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />{quote.deal.title}</Link>}
      <div className="mb-5 flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-[22px] font-semibold tracking-tight">{quote.quote_number}</h1>
            <StatusPill status={quote.status} />
            {quote.deal?.account.credit_hold && <Badge tone="critical">Account on credit hold</Badge>}
          </div>
          <p className="mt-1 text-[13px] text-muted-foreground">{quote.deal?.account.name}{quote.valid_until && ` · valid until ${shortDate(quote.valid_until, true)}`}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {editable && <Button variant="outline" size="sm" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate()}><Save className="h-4 w-4" />Save</Button>}
          {editable && ["draft", "rejected"].includes(quote.status) && (
            <Button size="sm" loading={submit.isPending} disabled={!lines.length} onClick={() => submit.mutate()}><Send className="h-4 w-4" />Submit for approval</Button>
          )}
          {["approved", "sent"].includes(quote.status) && can("documents", "create") && (
            <Button variant="ai" size="sm" loading={orderForm.isPending} onClick={() => orderForm.mutate()}><FilePlus2 className="h-4 w-4" />Generate Order Form</Button>
          )}
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card>
            <CardHeader title="Terms" />
            <CardBody className="grid gap-3 sm:grid-cols-4">
              <div className="sm:col-span-4"><Label>Quote name</Label><Input disabled={!editable} value={form.name} onChange={(e) => { setForm({ ...form, name: e.target.value }); setDirty(true); }} /></div>
              <div><Label>Currency</Label><Select disabled={!editable} value={form.currency} onChange={(e) => { setForm({ ...form, currency: e.target.value }); setDirty(true); }}>{["USD", "EUR", "GBP"].map((c) => <option key={c}>{c}</option>)}</Select></div>
              <div><Label>Term (months)</Label><Input disabled={!editable} type="number" min={1} max={120} value={form.term_months} onChange={(e) => { setForm({ ...form, term_months: Number(e.target.value) || 1 }); setDirty(true); }} /></div>
              <div className="sm:col-span-2"><Label>Payment terms</Label>
                <Select disabled={!editable} value={form.payment_terms} onChange={(e) => { setForm({ ...form, payment_terms: e.target.value }); setDirty(true); }}>
                  {["NET15", "NET30", "NET45", "NET60", "NET90"].map((t) => <option key={t} value={t}>{t}{["NET60", "NET90"].includes(t) ? " (non-standard)" : ""}</option>)}
                </Select>
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Line items" description="Volume tiers apply automatically; discounts above policy route for approval" />
            <CardBody className="space-y-2">
              <div className="hidden grid-cols-[1fr_80px_80px_110px_120px_28px] gap-2 px-1 text-[11.5px] font-medium text-muted-foreground md:grid">
                <span>Product</span><span>Qty</span><span>Disc %</span><span className="text-right">Net unit</span><span className="text-right">Line total</span><span />
              </div>
              {lines.map((l, i) => {
                const p = byId[l.product_id];
                const row = preview.rows[i];
                return (
                  <div key={i} className="grid grid-cols-2 items-center gap-2 rounded-md border p-2 md:grid-cols-[1fr_80px_80px_110px_120px_28px] md:border-0 md:p-0">
                    <Select disabled={!editable} className="col-span-2 md:col-span-1" value={l.product_id} onChange={(e) => update(i, { product_id: e.target.value })} aria-label="Product">
                      {products?.map((pr) => <option key={pr.id} value={pr.id}>{pr.name} · {pr.sku}</option>)}
                    </Select>
                    <Input disabled={!editable} type="number" min={1} value={l.quantity} onChange={(e) => update(i, { quantity: Number(e.target.value) || 1 })} aria-label="Quantity" />
                    <Input disabled={!editable} type="number" min={0} max={100} value={l.discount_pct} onChange={(e) => update(i, { discount_pct: Math.min(100, Number(e.target.value) || 0) })} aria-label="Discount %" />
                    <span className="tabular text-right text-[13px]">{row?.net == null ? <span className="text-destructive">No {cur} price</span> : fmtMoney(row.net, cur)}<span className="block text-[11px] text-muted-foreground">{p?.unit}</span></span>
                    <span className="tabular text-right text-[13px] font-medium">{row?.total == null ? "—" : fmtMoney(row.total, cur)}</span>
                    {editable ? <button aria-label="Remove line" onClick={() => { setLines(lines.filter((_, j) => j !== i)); setDirty(true); }} className="flex h-8 w-7 items-center justify-center rounded text-subtle hover:bg-muted hover:text-foreground"><Trash2 className="h-3.5 w-3.5" /></button> : <span />}
                  </div>
                );
              })}
              {!lines.length && <p className="py-4 text-center text-[13px] text-muted-foreground">Add products from the catalog.</p>}
              {editable && products?.length ? (
                <Button variant="ghost" size="sm" onClick={() => { setLines([...lines, { product_id: products[0].id, quantity: 10, discount_pct: 0 }]); setDirty(true); }}>
                  <Plus className="h-3.5 w-3.5" />Add line
                </Button>
              ) : null}
              <div className="pt-2"><Label>Notes</Label><Textarea disabled={!editable} value={form.notes} onChange={(e) => { setForm({ ...form, notes: e.target.value }); setDirty(true); }} className="min-h-[60px]" /></div>
            </CardBody>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Totals" description={dirty ? "Unsaved preview" : "Server-calculated"} />
            <CardBody className="space-y-2 text-[13.5px]">
              {[
                ["List value", dirty ? preview.list : quote.list_total],
                ["Discount", -(dirty ? preview.discount : quote.discount_total)],
                ["ACV (annual recurring)", dirty ? preview.acv : quote.acv],
              ].map(([k, v]) => (
                <div key={k as string} className="flex justify-between"><span className="text-muted-foreground">{k}</span><span className="tabular">{fmtMoney(v as number, cur)}</span></div>
              ))}
              <div className="flex justify-between border-t pt-2 text-[15px] font-semibold"><span>TCV</span><span className="tabular">{fmtMoney(dirty ? preview.tcv : quote.tcv, cur)}</span></div>
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Approval routing" icon={<Stamp className="h-4 w-4 text-muted-foreground" />} />
            <CardBody className="space-y-2.5">
              {quote.approvals.filter((a) => a.status !== "superseded").map((a) => (
                <div key={a.id} className="rounded-md border p-2.5 text-[13px]">
                  <div className="flex items-center gap-2">
                    {a.status === "approved" ? <CheckCircle2 className="h-4 w-4" style={{ color: "var(--status-good)" }} /> : a.status === "rejected" ? <XCircle className="h-4 w-4" style={{ color: "var(--status-critical)" }} /> : <Stamp className="h-4 w-4 text-muted-foreground" />}
                    <span className="font-medium capitalize">{a.required_role.replace("_", " ")}</span>
                    <span className="ml-auto"><StatusPill status={a.status} /></span>
                  </div>
                  <p className="mt-1 text-[12px] text-muted-foreground">{a.reason}</p>
                  {a.decided_by && <p className="mt-1 text-[12px]">{a.decided_by.full_name}{a.comment && `: "${a.comment}"`}</p>}
                </div>
              ))}
              {quote.status === "draft" && (quote.required_approvals?.length ? (
                <div className="text-[12.5px] text-muted-foreground">On submit this will need:
                  <ul className="mt-1 list-disc pl-4">{quote.required_approvals.map((r) => <li key={r.required_role}><span className="capitalize">{r.required_role.replace("_", " ")}</span>: {r.reason}</li>)}</ul>
                </div>
              ) : <p className="text-[12.5px] text-muted-foreground">Within policy: submitting auto-approves.</p>)}
              {quote.status === "approved" && <p className="text-[12.5px] text-muted-foreground">Approved {shortDate(quote.approved_at, true)}. The deal amount now reflects this TCV.</p>}
            </CardBody>
          </Card>
          {!!quote.documents?.length && (
            <Card>
              <CardHeader title="Documents" />
              <CardBody className="space-y-2">
                {quote.documents.map((d) => (
                  <Link key={d.id} href={`/documents/${d.id}`} className="flex items-center justify-between rounded-md border px-3 py-2 text-[13px] hover:border-primary/40">
                    {d.title}<StatusPill status={d.status} />
                  </Link>
                ))}
              </CardBody>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
