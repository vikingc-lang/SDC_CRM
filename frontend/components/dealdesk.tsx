"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Table, Td, fmtMoney } from "@/components/ui/extra";
import { Input, Label, Select } from "@/components/ui/input";
import { api, errorMessage, get } from "@/lib/api";
import type { AccountListItem, Product } from "@/lib/types";
import { shortDate } from "@/lib/utils";

interface Book { id: string; name: string; kind: "customer" | "regional"; account_id: string | null; account_name: string | null; region: string | null; active: boolean;
  valid_from: string | null; valid_to: string | null; entries: { product_id: string; currency: string; tiers: { min_qty: number; unit_price: number }[] }[] }
interface Promo { id: string; code: string; name: string; discount_pct: number; product_ids: string[]; min_quantity: number; valid_from: string | null; valid_to: string | null; active: boolean }

export function PriceBooksPanel({ products, canEdit }: { products: Product[]; canEdit: boolean }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["price-books"], queryFn: () => get<Book[]>("/price-books") });
  const { data: accounts } = useQuery({ queryKey: ["accounts", "all"], queryFn: () => get<AccountListItem[]>("/accounts", { limit: 200 }), enabled: canEdit });
  const byId = Object.fromEntries(products.map((p) => [p.id, p]));
  const [f, setF] = useState({ name: "", kind: "regional", region: "EMEA", account_id: "", valid_to: "", product_id: "", currency: "EUR", unit_price: "" });
  const create = useMutation({
    mutationFn: async () => (await api.post("/price-books", {
      name: f.name, kind: f.kind, region: f.kind === "regional" ? f.region : null, account_id: f.kind === "customer" ? f.account_id : null, valid_to: f.valid_to || null,
      entries: f.product_id ? [{ product_id: f.product_id, currency: f.currency, tiers: [{ min_qty: 1, unit_price: Number(f.unit_price) }] }] : [],
    })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["price-books"] }); toast.success("Price book created"); setF({ ...f, name: "" }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({ mutationFn: async (id: string) => (await api.delete(`/price-books/${id}`)).data, onSuccess: () => qc.invalidateQueries({ queryKey: ["price-books"] }) });
  return (
    <div className="space-y-6">
      <Card className="overflow-hidden">
        <CardHeader title="Price books" description="Quotes resolve each product's price from the customer's book, then the account region's book, then the list price." />
        <Table head={["Book", "Applies to", "Prices", "Valid", ""]} minWidth={760}>
          {data?.map((b) => (
            <tr key={b.id}>
              <Td className="font-medium">{b.name}<p><Badge tone={b.kind === "customer" ? "primary" : "neutral"}>{b.kind}</Badge></p></Td>
              <Td>{b.kind === "customer" ? b.account_name : `Region ${b.region}`}</Td>
              <Td className="space-y-1 text-[12.5px]">{b.entries.map((e) => (
                <div key={`${e.product_id}-${e.currency}`}>{byId[e.product_id]?.name ?? "Product"}: {e.tiers.map((t) => `${t.min_qty}+ ${fmtMoney(t.unit_price, e.currency)}`).join(" · ")}</div>
              ))}</Td>
              <Td className="text-muted-foreground">{b.valid_from ? shortDate(b.valid_from, true) : "—"} → {b.valid_to ? shortDate(b.valid_to, true) : "open"}</Td>
              <Td>{canEdit && <Button variant="ghost" size="icon" aria-label="Delete price book" onClick={() => remove.mutate(b.id)}><Trash2 className="h-3.5 w-3.5" /></Button>}</Td>
            </tr>
          ))}
        </Table>
      </Card>
      {canEdit && (
        <Card>
          <CardHeader title="New price book" />
          <CardBody>
            <form className="grid gap-3 md:grid-cols-4" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
              <div className="md:col-span-2"><Label>Name</Label><Input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
              <div><Label>Kind</Label><Select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}><option value="regional">Regional</option><option value="customer">Customer</option></Select></div>
              {f.kind === "regional" ? (
                <div><Label>Region</Label><Select value={f.region} onChange={(e) => setF({ ...f, region: e.target.value })}>{["NA", "EMEA", "APAC", "LATAM"].map((r) => <option key={r}>{r}</option>)}</Select></div>
              ) : (
                <div><Label>Account</Label><Select required value={f.account_id} onChange={(e) => setF({ ...f, account_id: e.target.value })}>
                  <option value="">Choose…</option>{accounts?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</Select></div>
              )}
              <div><Label>Product</Label><Select value={f.product_id} onChange={(e) => setF({ ...f, product_id: e.target.value })}>
                <option value="">Add entries later</option>{products.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select></div>
              <div><Label>Currency</Label><Select value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value })}>{["USD", "EUR", "GBP"].map((c) => <option key={c}>{c}</option>)}</Select></div>
              <div><Label>Unit price</Label><Input type="number" step="0.01" min={0} required={!!f.product_id} value={f.unit_price} onChange={(e) => setF({ ...f, unit_price: e.target.value })} /></div>
              <div><Label>Valid to</Label><Input type="date" value={f.valid_to} onChange={(e) => setF({ ...f, valid_to: e.target.value })} /></div>
              <div className="md:col-span-4 flex justify-end"><Button type="submit" size="sm" loading={create.isPending}><Plus className="h-3.5 w-3.5" />Create</Button></div>
            </form>
          </CardBody>
        </Card>
      )}
    </div>
  );
}

export function PromotionsPanel({ products, canEdit }: { products: Product[]; canEdit: boolean }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["promotions"], queryFn: () => get<Promo[]>("/promotions") });
  const byId = Object.fromEntries(products.map((p) => [p.id, p]));
  const [f, setF] = useState({ code: "", name: "", discount_pct: "10", product_id: "", min_quantity: "0", valid_to: "" });
  const create = useMutation({
    mutationFn: async () => (await api.post("/promotions", { code: f.code, name: f.name, discount_pct: Number(f.discount_pct), min_quantity: Number(f.min_quantity),
      product_ids: f.product_id ? [f.product_id] : [], valid_to: f.valid_to || null })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["promotions"] }); toast.success("Promotion created"); setF({ ...f, code: "", name: "" }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const toggle = useMutation({
    mutationFn: async (v: { id: string; active: boolean }) => (await api.patch(`/promotions/${v.id}`, null, { params: { active: v.active } })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["promotions"] }),
  });
  return (
    <div className="space-y-6">
      <Card className="overflow-hidden">
        <CardHeader title="Promotions" description="Promo codes apply a pre-approved discount on top of the resolved price; they do not trigger discount approvals." />
        <Table head={["Code", "Offer", "Products", "Min qty", "Valid", "Status"]} minWidth={720}>
          {data?.map((p) => (
            <tr key={p.id}>
              <Td className="font-mono font-medium">{p.code}</Td>
              <Td>{p.name}<p className="text-[12px] text-muted-foreground">{p.discount_pct}% off</p></Td>
              <Td className="text-[12.5px]">{p.product_ids.length ? p.product_ids.map((id) => byId[id]?.name ?? "Product").join(", ") : "All products"}</Td>
              <Td className="tabular">{p.min_quantity || "—"}</Td>
              <Td className="text-muted-foreground">{p.valid_from ? shortDate(p.valid_from, true) : "—"} → {p.valid_to ? shortDate(p.valid_to, true) : "open"}</Td>
              <Td>{canEdit ? <Button variant="outline" size="sm" onClick={() => toggle.mutate({ id: p.id, active: !p.active })}>{p.active ? "Active" : "Paused"}</Button> : p.active ? "Active" : "Paused"}</Td>
            </tr>
          ))}
        </Table>
      </Card>
      {canEdit && (
        <Card>
          <CardHeader title="New promotion" />
          <CardBody>
            <form className="grid gap-3 md:grid-cols-6" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
              <div><Label>Code</Label><Input required pattern="[A-Za-z0-9_-]{3,40}" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value.toUpperCase() })} /></div>
              <div className="md:col-span-2"><Label>Name</Label><Input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
              <div><Label>Discount %</Label><Input type="number" min={1} max={99} value={f.discount_pct} onChange={(e) => setF({ ...f, discount_pct: e.target.value })} /></div>
              <div><Label>Min quantity</Label><Input type="number" min={0} value={f.min_quantity} onChange={(e) => setF({ ...f, min_quantity: e.target.value })} /></div>
              <div><Label>Valid to</Label><Input type="date" value={f.valid_to} onChange={(e) => setF({ ...f, valid_to: e.target.value })} /></div>
              <div className="md:col-span-3"><Label>Product</Label><Select value={f.product_id} onChange={(e) => setF({ ...f, product_id: e.target.value })}>
                <option value="">All products</option>{products.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select></div>
              <div className="md:col-span-3 flex items-end justify-end"><Button type="submit" size="sm" loading={create.isPending}><Plus className="h-3.5 w-3.5" />Create</Button></div>
            </form>
          </CardBody>
        </Card>
      )}
    </div>
  );
}

interface Config { components: { component_id: string; sku: string; name: string; quantity: number }[];
  rules: { id: string; rule_type: "requires" | "excludes"; target_product_id: string; target: string; message: string | null }[] }

export function ConfigureDialog({ product, products, onClose, canEdit }: { product: Product | null; products: Product[]; onClose: () => void; canEdit: boolean }) {
  const qc = useQueryClient();
  const key = ["product-config", product?.id];
  const { data } = useQuery({ queryKey: key, queryFn: () => get<Config>(`/products/${product!.id}/configuration`), enabled: !!product });
  const [comp, setComp] = useState({ component_id: "", quantity: "1" });
  const [rule, setRule] = useState({ rule_type: "requires", target_product_id: "", message: "" });
  const setComponents = useMutation({
    mutationFn: async (list: { component_id: string; quantity: number }[]) => (await api.put(`/products/${product!.id}/components`, list)).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: key }); qc.invalidateQueries({ queryKey: ["products"] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const addRule = useMutation({
    mutationFn: async () => (await api.post("/product-rules", { product_id: product!.id, ...rule, message: rule.message || null })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: key }); setRule({ ...rule, message: "" }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const delRule = useMutation({ mutationFn: async (id: string) => (await api.delete(`/product-rules/${id}`)).data, onSuccess: () => qc.invalidateQueries({ queryKey: key }) });
  const others = products.filter((p) => p.id !== product?.id);
  const current = data?.components.map((c) => ({ component_id: c.component_id, quantity: c.quantity })) ?? [];
  return (
    <Dialog open={!!product} onOpenChange={(o) => !o && onClose()}>
      <DialogContent title="Configure product" className="max-w-2xl">
        {product && (
          <div className="space-y-5 p-5">
            <h2 className="text-[15px] font-semibold">{product.name} <span className="font-mono text-[12px] text-muted-foreground">{product.sku}</span></h2>
            <section>
              <p className="text-[13px] font-medium">Bundle components</p>
              <p className="mb-2 text-[12px] text-muted-foreground">Adding components makes this a bundle: quoting it adds each component as an included line at no extra charge.</p>
              <ul className="space-y-1.5">
                {data?.components.map((c) => (
                  <li key={c.component_id} className="flex items-center gap-2 text-[13px]">
                    <span className="flex-1">{c.name} <span className="text-muted-foreground">× {c.quantity} per bundle unit</span></span>
                    {canEdit && <Button variant="ghost" size="icon" aria-label="Remove component" onClick={() => setComponents.mutate(current.filter((x) => x.component_id !== c.component_id))}><Trash2 className="h-3.5 w-3.5" /></Button>}
                  </li>
                ))}
              </ul>
              {canEdit && (
                <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); comp.component_id && setComponents.mutate([...current, { component_id: comp.component_id, quantity: Number(comp.quantity) }]); }}>
                  <Select className="h-8 flex-1" value={comp.component_id} onChange={(e) => setComp({ ...comp, component_id: e.target.value })}>
                    <option value="">Add component…</option>{others.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select>
                  <Input className="h-8 w-20" type="number" min={1} value={comp.quantity} onChange={(e) => setComp({ ...comp, quantity: e.target.value })} aria-label="Quantity" />
                  <Button size="sm" variant="outline" type="submit">Add</Button>
                </form>
              )}
            </section>
            <section>
              <p className="text-[13px] font-medium">Configuration rules</p>
              <p className="mb-2 text-[12px] text-muted-foreground">Checked on every quote save: dependencies must be present, exclusions must not be combined.</p>
              <ul className="space-y-1.5">
                {data?.rules.map((r) => (
                  <li key={r.id} className="flex items-center gap-2 text-[13px]">
                    <Badge tone={r.rule_type === "requires" ? "primary" : "warning"}>{r.rule_type}</Badge>
                    <span className="flex-1">{r.target}{r.message && <span className="text-muted-foreground"> · {r.message}</span>}</span>
                    {canEdit && <Button variant="ghost" size="icon" aria-label="Delete rule" onClick={() => delRule.mutate(r.id)}><Trash2 className="h-3.5 w-3.5" /></Button>}
                  </li>
                ))}
              </ul>
              {canEdit && (
                <form className="mt-2 grid gap-2 sm:grid-cols-[120px_1fr]" onSubmit={(e) => { e.preventDefault(); rule.target_product_id && addRule.mutate(); }}>
                  <Select className="h-8" value={rule.rule_type} onChange={(e) => setRule({ ...rule, rule_type: e.target.value })}><option value="requires">Requires</option><option value="excludes">Excludes</option></Select>
                  <Select className="h-8" value={rule.target_product_id} onChange={(e) => setRule({ ...rule, target_product_id: e.target.value })}>
                    <option value="">Product…</option>{others.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select>
                  <Input className="h-8 sm:col-span-2" placeholder="Message shown to the rep (optional)" value={rule.message} onChange={(e) => setRule({ ...rule, message: e.target.value })} />
                  <div className="sm:col-span-2 flex justify-end"><Button size="sm" variant="outline" type="submit">Add rule</Button></div>
                </form>
              )}
            </section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
