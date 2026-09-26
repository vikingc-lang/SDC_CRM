"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Boxes, Package, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/AppShell";
import { ConfigureDialog, PriceBooksPanel, PromotionsPanel } from "@/components/dealdesk";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Table, Tabs, Td, fmtMoney } from "@/components/ui/extra";
import { Input, Label, Select } from "@/components/ui/input";
import { EmptyState } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import type { Product } from "@/lib/types";

type Draft = Omit<Product, "id"> & { id?: string };
const EMPTY: Draft = { sku: "", name: "", description: "", family: "", billing_type: "recurring", unit: "user / month", active: true,
  prices: [{ currency: "USD", tiers: [{ min_qty: 1, unit_price: 0 }] }] };

export default function ProductsPage() {
  const qc = useQueryClient();
  const { can } = useMe();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [tab, setTab] = useState<"catalog" | "books" | "promos">("catalog");
  const [configure, setConfigure] = useState<Product | null>(null);
  const { data } = useQuery({ queryKey: ["products", "all"], queryFn: () => get<Product[]>("/products", { include_inactive: true }) });
  const save = useMutation({
    mutationFn: async (d: Draft) => (d.id ? api.put(`/products/${d.id}`, d) : api.post("/products", d)),
    onSuccess: () => { qc.invalidateQueries(); setDraft(null); toast.success("Rate card saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const setPrice = (ci: number, ti: number, patch: Partial<{ min_qty: number; unit_price: number }>) => draft && setDraft({
    ...draft, prices: draft.prices.map((p, i) => (i !== ci ? p : { ...p, tiers: p.tiers.map((t, j) => (j === ti ? { ...t, ...patch } : t)) })) });

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader title="Product catalog" description="Multi-currency SKUs with volume-tiered rate cards"
        actions={can("products", "create") && <Button size="sm" onClick={() => setDraft(structuredClone(EMPTY))}><Plus className="h-4 w-4" />New product</Button>} />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "catalog", label: "Catalog" }, { value: "books", label: "Price books" }, { value: "promos", label: "Promotions" }]} />
      {tab === "books" && <PriceBooksPanel products={data ?? []} canEdit={can("products", "update")} />}
      {tab === "promos" && <PromotionsPanel products={data ?? []} canEdit={can("products", "update")} />}
      {tab === "catalog" && <Card className="overflow-hidden">
        {!data?.length ? <EmptyState icon={<Package className="h-4 w-4" />} title="No products yet" /> : (
          <Table head={["SKU", "Product", "Billing", "Rate cards (volume tiers)", ""]} minWidth={820}>
            {data.map((p) => (
              <tr key={p.id} className={p.active ? "" : "opacity-60"}>
                <Td className="font-mono text-[12.5px]">{p.sku}</Td>
                <Td><span className="font-medium">{p.name}</span>{p.product_type === "bundle" && <Badge tone="primary" className="ml-1.5">Bundle</Badge>}<p className="text-[12px] text-muted-foreground">{p.family} · {p.description}</p></Td>
                <Td><Badge tone={p.billing_type === "recurring" ? "primary" : "neutral"}>{p.billing_type === "recurring" ? "Recurring" : "One-time"}</Badge><p className="mt-1 text-[12px] text-muted-foreground">per {p.unit}</p></Td>
                <Td>
                  <div className="space-y-1">
                    {p.prices.map((pr) => (
                      <div key={pr.currency} className="flex flex-wrap gap-1.5 text-[12.5px]">
                        <span className="w-9 font-medium">{pr.currency}</span>
                        {pr.tiers.map((t) => <span key={t.min_qty} className="tabular rounded bg-muted px-1.5">{t.min_qty}+ · {fmtMoney(t.unit_price, pr.currency)}</span>)}
                      </div>
                    ))}
                  </div>
                </Td>
                <Td className="whitespace-nowrap">
                  <Button size="icon" variant="ghost" aria-label={`Bundle and rules for ${p.name}`} title="Bundle components and rules" onClick={() => setConfigure(p)}><Boxes className="h-3.5 w-3.5" /></Button>
                  {can("products", "update") && <Button size="icon" variant="ghost" aria-label={`Edit ${p.name}`} onClick={() => setDraft(structuredClone(p))}><Pencil className="h-3.5 w-3.5" /></Button>}
                </Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>}
      <ConfigureDialog product={configure} products={data ?? []} onClose={() => setConfigure(null)} canEdit={can("products", "update")} />

      <Dialog open={!!draft} onOpenChange={(o) => !o && setDraft(null)}>
        <DialogContent title="Product" className="max-w-2xl">
          {draft && (
            <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); save.mutate(draft); }}>
              <h2 className="text-[15px] font-semibold">{draft.id ? "Edit product" : "New product"}</h2>
              <div className="grid gap-3 sm:grid-cols-3">
                <div><Label>SKU</Label><Input required disabled={!!draft.id} value={draft.sku} onChange={(e) => setDraft({ ...draft, sku: e.target.value.toUpperCase() })} /></div>
                <div className="sm:col-span-2"><Label>Name</Label><Input required value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></div>
                <div><Label>Family</Label><Input value={draft.family ?? ""} onChange={(e) => setDraft({ ...draft, family: e.target.value })} /></div>
                <div><Label>Billing</Label><Select value={draft.billing_type} onChange={(e) => setDraft({ ...draft, billing_type: e.target.value as Draft["billing_type"] })}><option value="recurring">Recurring (monthly)</option><option value="one_time">One-time</option></Select></div>
                <div><Label>Unit</Label><Input value={draft.unit} onChange={(e) => setDraft({ ...draft, unit: e.target.value })} /></div>
                <div className="sm:col-span-3"><Label>Description</Label><Input value={draft.description ?? ""} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></div>
              </div>
              <div className="space-y-3">
                <Label>Rate cards</Label>
                {draft.prices.map((pr, ci) => (
                  <div key={ci} className="rounded-md border p-3">
                    <div className="mb-2 flex items-center gap-2">
                      <Select className="h-8 w-24" value={pr.currency} onChange={(e) => setDraft({ ...draft, prices: draft.prices.map((p, i) => (i === ci ? { ...p, currency: e.target.value } : p)) })}>
                        {["USD", "EUR", "GBP", "CAD", "AUD"].map((c) => <option key={c}>{c}</option>)}
                      </Select>
                      <span className="text-[12px] text-muted-foreground">Whole quantity is priced at the highest tier reached</span>
                    </div>
                    {pr.tiers.map((t, ti) => (
                      <div key={ti} className="mb-1.5 grid grid-cols-[1fr_1fr_28px] gap-2">
                        <Input type="number" min={0} value={t.min_qty} onChange={(e) => setPrice(ci, ti, { min_qty: Number(e.target.value) })} aria-label="From quantity" />
                        <Input type="number" min={0} step="0.01" value={t.unit_price} onChange={(e) => setPrice(ci, ti, { unit_price: Number(e.target.value) })} aria-label="Unit price" />
                        <button type="button" aria-label="Remove tier" className="text-subtle hover:text-foreground" onClick={() => setDraft({ ...draft, prices: draft.prices.map((p, i) => (i === ci ? { ...p, tiers: p.tiers.filter((_, j) => j !== ti) } : p)) })}><Trash2 className="h-3.5 w-3.5" /></button>
                      </div>
                    ))}
                    <button type="button" className="text-[12.5px] font-medium text-primary" onClick={() => setDraft({ ...draft, prices: draft.prices.map((p, i) => (i === ci ? { ...p, tiers: [...p.tiers, { min_qty: (p.tiers.at(-1)?.min_qty ?? 0) + 100, unit_price: p.tiers.at(-1)?.unit_price ?? 0 }] } : p)) })}>+ Add tier</button>
                  </div>
                ))}
                <button type="button" className="text-[12.5px] font-medium text-primary" onClick={() => setDraft({ ...draft, prices: [...draft.prices, { currency: "EUR", tiers: [{ min_qty: 1, unit_price: 0 }] }] })}>+ Add currency</button>
              </div>
              <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={draft.active} onChange={(e) => setDraft({ ...draft, active: e.target.checked })} />Active (available on new quotes)</label>
              <div className="flex justify-end"><Button type="submit" size="sm" loading={save.isPending}>Save</Button></div>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
