"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FilePlus2, Package, PieChart, Plus, Trash2, Users } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Table, Td, fmtMoney } from "@/components/ui/extra";
import { Input, Select } from "@/components/ui/input";
import { Avatar } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { LineItem, Product, Split, TeamMember, UUID } from "@/lib/types";
import { fmtNumber } from "@/lib/utils";

export interface Selling {
  amount_source: "manual" | "lines"; line_items: LineItem[]; line_items_total: number; team: TeamMember[]; splits: Split[]; team_roles: string[]; can_edit?: boolean;
}

export function ProductsCard({ dealId, currency, selling, canEdit, canQuote }: { dealId: UUID; currency: string; selling: Selling; canEdit: boolean; canQuote: boolean }) {
  const qc = useQueryClient();
  const router = useRouter();
  const t = useT();
  const [edit, setEdit] = useState<LineItem[] | null>(null);
  const [fromLines, setFromLines] = useState(selling.amount_source === "lines");
  useEffect(() => setFromLines(selling.amount_source === "lines"), [selling.amount_source]);
  const products = useQuery({ queryKey: ["products"], queryFn: () => get<Product[]>("/products"), enabled: edit !== null });
  const save = useMutation({
    mutationFn: async (body: { lines: LineItem[]; amount_source: "manual" | "lines" }) =>
      (await api.put(`/deals/${dealId}/products`, { amount_source: body.amount_source, lines: body.lines.map((l) => ({
        product_id: l.product_id, quantity: l.quantity, unit_price: l.unit_price === null || Number.isNaN(l.unit_price) ? null : l.unit_price,
        discount_pct: l.discount_pct, term_months: l.term_months, description: l.description || null })) })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["deal", dealId] }); setEdit(null); toast.success("Products saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const quote = useMutation({
    mutationFn: async () => (await api.post<{ id: UUID; quote_number: string }>(`/deals/${dealId}/products/quote`)).data,
    onSuccess: (q) => { toast.success(`Quote ${q.quote_number} created`); router.push(`/quotes/${q.id}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const lines = selling.line_items;
  const setLine = (k: number, patch: Partial<LineItem>) => setEdit(edit!.map((l, j) => (j === k ? { ...l, ...patch } : l)));
  return (
    <Card>
      <CardHeader title={t("deal.products")} icon={<Package className="h-4 w-4 text-muted-foreground" />}
        description={selling.amount_source === "lines" ? "The deal amount is the total of these products." : "Products expected on this deal; the amount is entered by hand."}
        action={<div className="flex gap-1.5">
          {canQuote && lines.length > 0 && edit === null && <Button size="sm" variant="outline" loading={quote.isPending} onClick={() => quote.mutate()}><FilePlus2 className="h-3.5 w-3.5" />Create quote</Button>}
          {canEdit && edit === null && <Button size="sm" variant="ghost" onClick={() => setEdit(lines.map((l) => ({ ...l })))}>{lines.length ? "Edit" : <><Plus className="h-3.5 w-3.5" />Add products</>}</Button>}
        </div>} />
      {edit === null ? (
        lines.length === 0 ? <p className="px-5 pb-5 text-[13px] text-muted-foreground">No products yet.</p> : (
          <Table head={["Product", "Qty", "Sales price", "Discount", "Term", "Total"]} minWidth={560}>
            {lines.map((l) => (
              <tr key={l.id}>
                <Td className="text-[13px]"><span className="font-medium">{l.name}</span><span className="block text-[11.5px] text-muted-foreground">{l.sku} · {l.unit}</span></Td>
                <Td className="tabular text-[13px]">{fmtNumber(l.quantity, 2)}</Td>
                <Td className="tabular text-[13px]">{fmtMoney(l.unit_price ?? 0, currency)}</Td>
                <Td className="tabular text-[13px]">{l.discount_pct ? `${fmtNumber(l.discount_pct, 2)}%` : "—"}</Td>
                <Td className="tabular text-[13px]">{l.billing_type === "recurring" ? `${l.term_months} mo` : "one-time"}</Td>
                <Td className="tabular text-[13px] font-medium">{fmtMoney(l.total ?? 0, currency)}</Td>
              </tr>
            ))}
            <tr><Td /><Td /><Td /><Td /><Td className="text-right text-[12.5px] text-muted-foreground">Total</Td><Td className="tabular text-[13px] font-semibold">{fmtMoney(selling.line_items_total, currency)}</Td></tr>
          </Table>
        )
      ) : (
        <CardBody className="space-y-2.5">
          {edit.map((l, k) => {
            const p = products.data?.find((x) => x.id === l.product_id);
            return (
              <div key={k} className="grid grid-cols-2 items-end gap-2 rounded-md border p-2.5 sm:grid-cols-[1fr_80px_110px_80px_80px_32px]">
                <Select aria-label="Product" id={`dl-${k}-product`} className="col-span-2 sm:col-span-1" value={l.product_id} onChange={(e) => setLine(k, { product_id: e.target.value, unit_price: null })}>
                  <option value="">Choose a product…</option>
                  {products.data?.filter((x) => x.active).map((x) => <option key={x.id} value={x.id}>{x.name} ({x.sku})</option>)}
                </Select>
                <Input aria-label="Quantity" id={`dl-${k}-qty`} type="number" min={0.01} step="any" value={l.quantity} onChange={(e) => setLine(k, { quantity: Number(e.target.value) })} />
                <Input aria-label="Sales price (blank = price book)" id={`dl-${k}-price`} type="number" min={0} step="any" placeholder="Price book" value={l.unit_price ?? ""}
                  onChange={(e) => setLine(k, { unit_price: e.target.value === "" ? null : Number(e.target.value) })} />
                <Input aria-label="Discount %" id={`dl-${k}-disc`} type="number" min={0} max={100} step="any" value={l.discount_pct} onChange={(e) => setLine(k, { discount_pct: Number(e.target.value) })} />
                <Input aria-label="Term in months" id={`dl-${k}-term`} type="number" min={1} max={120} disabled={p?.billing_type === "one_time"} value={l.term_months}
                  onChange={(e) => setLine(k, { term_months: Number(e.target.value) || 12 })} />
                <button type="button" aria-label="Remove product" className="flex h-9 items-center justify-center text-muted-foreground hover:text-destructive" onClick={() => setEdit(edit.filter((_, j) => j !== k))}>
                  <Trash2 className="h-4 w-4" /></button>
              </div>
            );
          })}
          <p className="text-[11.5px] text-subtle">Qty · sales price per unit (blank uses the account&apos;s price book in {currency}) · discount % · term in months</p>
          <div className="flex flex-wrap items-center gap-3">
            <button type="button" className="inline-flex items-center gap-1 text-[13px] text-primary hover:underline"
              onClick={() => setEdit([...edit, { product_id: "", quantity: 1, unit_price: null, discount_pct: 0, term_months: 12 }])}><Plus className="h-3.5 w-3.5" />Add product</button>
            <label className="flex items-center gap-2 text-[13px]"><input id="dl-from-lines" type="checkbox" checked={fromLines} onChange={(e) => setFromLines(e.target.checked)} />
              Deal amount = total of products</label>
            <span className="flex-1" />
            <Button size="sm" variant="ghost" onClick={() => setEdit(null)}>{t("common.cancel")}</Button>
            <Button size="sm" loading={save.isPending} disabled={edit.some((l) => !l.product_id || !(l.quantity > 0))}
              onClick={() => save.mutate({ lines: edit, amount_source: fromLines ? "lines" : "manual" })}>{t("common.save")}</Button>
          </div>
        </CardBody>
      )}
    </Card>
  );
}

export function TeamCard({ dealId, owner, amount, currency, selling, canEdit }: {
  dealId: UUID; owner: { id: UUID; full_name: string } | null; amount: number; currency: string; selling: Selling; canEdit: boolean;
}) {
  const qc = useQueryClient();
  const t = useT();
  const users = useQuery({ queryKey: ["users"], queryFn: () => get<{ id: UUID; full_name: string; role: string }[]>("/users"), enabled: canEdit });
  const [adding, setAdding] = useState<{ user_id: string; role: string; access: "read" | "edit" } | null>(null);
  const [splits, setSplits] = useState<{ user_id: UUID; split_type: "revenue" | "overlay"; percent: number }[] | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["deal", dealId] });
  const member = useMutation({
    mutationFn: async (b: { user_id: string; role: string; access: string }) => (await api.put(`/deals/${dealId}/team`, b)).data,
    onSuccess: () => { refresh(); setAdding(null); }, onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: async (userId: string) => (await api.delete(`/deals/${dealId}/team/${userId}`)).data,
    onSuccess: refresh, onError: (e) => toast.error(errorMessage(e)),
  });
  const saveSplits = useMutation({
    mutationFn: async () => (await api.put(`/deals/${dealId}/splits`, { splits })).data,
    onSuccess: () => { refresh(); setSplits(null); toast.success("Splits saved"); }, onError: (e) => toast.error(errorMessage(e)),
  });
  const people = [...(owner ? [{ id: owner.id, full_name: owner.full_name }] : []), ...selling.team.map((m) => m.user)];
  const revenueTotal = (splits ?? []).filter((s) => s.split_type === "revenue").reduce((n, s) => n + (s.percent || 0), 0);
  return (
    <Card>
      <CardHeader title={t("deal.team")} icon={<Users className="h-4 w-4 text-muted-foreground" />}
        action={canEdit && !adding && <Button variant="ghost" size="sm" onClick={() => setAdding({ user_id: "", role: selling.team_roles[0], access: "read" })}><Plus className="h-3.5 w-3.5" /></Button>} />
      <CardBody className="space-y-2.5">
        {owner && (
          <div className="flex items-center gap-2.5"><Avatar name={owner.full_name} size={28} />
            <div className="min-w-0 flex-1"><p className="truncate text-[13px] font-medium">{owner.full_name}</p><p className="text-[11.5px] text-muted-foreground">Owner</p></div></div>
        )}
        {selling.team.map((m) => (
          <div key={m.user.id} className="flex items-center gap-2.5">
            <Avatar name={m.user.full_name} size={28} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px] font-medium">{m.user.full_name}</p>
              {canEdit ? (
                <div className="mt-0.5 flex gap-1.5">
                  <Select aria-label="Team role" id={`team-${m.user.id}-role`} className="h-7 text-[12px]" value={m.role}
                    onChange={(e) => member.mutate({ user_id: m.user.id, role: e.target.value, access: m.access })}>
                    {selling.team_roles.map((r) => <option key={r} value={r}>{r}</option>)}</Select>
                  <Select aria-label="Access" id={`team-${m.user.id}-access`} className="h-7 w-auto text-[12px]" value={m.access}
                    onChange={(e) => member.mutate({ user_id: m.user.id, role: m.role, access: e.target.value })}>
                    <option value="read">Read only</option><option value="edit">Can edit</option></Select>
                </div>
              ) : <p className="text-[11.5px] text-muted-foreground">{m.role} · {m.access === "edit" ? "can edit" : "read only"}</p>}
            </div>
            {canEdit && <button type="button" aria-label={`Remove ${m.user.full_name}`} className="text-muted-foreground hover:text-destructive" onClick={() => remove.mutate(m.user.id)}><Trash2 className="h-3.5 w-3.5" /></button>}
          </div>
        ))}
        {adding && (
          <div className="space-y-1.5 rounded-md border p-2.5">
            <Select aria-label="Person" id="team-add-user" value={adding.user_id} onChange={(e) => setAdding({ ...adding, user_id: e.target.value })}>
              <option value="">Choose a person…</option>
              {users.data?.filter((u) => u.id !== owner?.id && !selling.team.some((m) => m.user.id === u.id)).map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
            </Select>
            <div className="flex gap-1.5">
              <Select aria-label="Team role" id="team-add-role" value={adding.role} onChange={(e) => setAdding({ ...adding, role: e.target.value })}>
                {selling.team_roles.map((r) => <option key={r} value={r}>{r}</option>)}</Select>
              <Select aria-label="Access" id="team-add-access" className="w-auto" value={adding.access} onChange={(e) => setAdding({ ...adding, access: e.target.value as "read" | "edit" })}>
                <option value="read">Read only</option><option value="edit">Can edit</option></Select>
            </div>
            <div className="flex justify-end gap-1.5">
              <Button size="sm" variant="ghost" onClick={() => setAdding(null)}>{t("common.cancel")}</Button>
              <Button size="sm" disabled={!adding.user_id} loading={member.isPending} onClick={() => member.mutate(adding)}>{t("common.add")}</Button>
            </div>
          </div>
        )}
        {!selling.team.length && !adding && <p className="text-[12.5px] text-muted-foreground">Add sales engineers, specialists or executives. Team members can see this deal and its account.</p>}

        <div className="border-t pt-3">
          <div className="mb-1.5 flex items-center gap-2">
            <PieChart className="h-3.5 w-3.5 text-muted-foreground" /><p className="flex-1 text-[13px] font-medium">{t("deal.splits")}</p>
            {canEdit && splits === null && <Button variant="ghost" size="sm" onClick={() => setSplits(selling.splits.map((s) => ({ user_id: s.user.id, split_type: s.split_type, percent: s.percent })))}>Edit</Button>}
          </div>
          {splits === null ? (
            selling.splits.length === 0 ? <p className="text-[12.5px] text-muted-foreground">No splits: {owner?.full_name ?? "the owner"} gets full credit.</p> : (
              <ul className="space-y-1">
                {selling.splits.map((s) => (
                  <li key={s.id} className="flex items-center gap-2 text-[13px]">
                    <span className="flex-1 truncate">{s.user.full_name}</span>
                    <Badge tone={s.split_type === "revenue" ? "neutral" : "outline"}>{s.split_type}</Badge>
                    <span className="tabular w-12 text-right">{fmtNumber(s.percent, 2)}%</span>
                    <span className="tabular w-24 text-right text-muted-foreground">{fmtMoney(s.amount ?? 0, currency, true)}</span>
                  </li>
                ))}
              </ul>
            )
          ) : (
            <div className="space-y-1.5">
              {splits.map((s, k) => (
                <div key={k} className="grid grid-cols-[1fr_92px_64px_24px] items-center gap-1.5">
                  <Select aria-label="Person" id={`split-${k}-user`} className="h-8 text-[12.5px]" value={s.user_id} onChange={(e) => setSplits(splits.map((x, j) => j === k ? { ...x, user_id: e.target.value } : x))}>
                    {people.map((p) => <option key={p.id} value={p.id}>{p.full_name}</option>)}</Select>
                  <Select aria-label="Split type" id={`split-${k}-type`} className="h-8 text-[12.5px]" value={s.split_type}
                    onChange={(e) => setSplits(splits.map((x, j) => j === k ? { ...x, split_type: e.target.value as "revenue" | "overlay" } : x))}>
                    <option value="revenue">Revenue</option><option value="overlay">Overlay</option></Select>
                  <Input aria-label="Percent" id={`split-${k}-pct`} className="h-8" type="number" min={0} max={100} step="any" value={s.percent}
                    onChange={(e) => setSplits(splits.map((x, j) => j === k ? { ...x, percent: Number(e.target.value) } : x))} />
                  <button type="button" aria-label="Remove split" className="text-muted-foreground hover:text-destructive" onClick={() => setSplits(splits.filter((_, j) => j !== k))}><Trash2 className="h-3.5 w-3.5" /></button>
                </div>
              ))}
              <button type="button" className="inline-flex items-center gap-1 text-[12.5px] text-primary hover:underline"
                onClick={() => setSplits([...splits, { user_id: people[0]?.id ?? "", split_type: "revenue", percent: Math.max(0, 100 - revenueTotal) }])}><Plus className="h-3 w-3" />Add split</button>
              <p className={revenueTotal === 100 || !splits.some((s) => s.split_type === "revenue") ? "text-[12px] text-muted-foreground" : "text-[12px] text-destructive"}>
                Revenue splits total {fmtNumber(revenueTotal, 2)}% (must be 100%) · {fmtMoney(amount * revenueTotal / 100, currency, true)}. Overlay splits are extra credit.</p>
              <div className="flex justify-end gap-1.5">
                <Button size="sm" variant="ghost" onClick={() => setSplits(null)}>{t("common.cancel")}</Button>
                <Button size="sm" loading={saveSplits.isPending} onClick={() => saveSplits.mutate()}>{t("common.save")}</Button>
              </div>
            </div>
          )}
        </div>
      </CardBody>
    </Card>
  );
}
