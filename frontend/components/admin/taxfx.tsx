"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Calculator, DownloadCloud, Landmark, Percent, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Table, Td, fmtMoney } from "@/components/ui/extra";
import { Input, Label, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { fmtNumber, shortDate } from "@/lib/utils";

interface FxRow { currency: string; rate_to_usd: number; history: { effective_date: string; rate_to_usd: number; source: string }[] }
interface TaxPolicy { engine: string; seller: Record<string, string>; gst: { default_rate: number; rates_by_code: Record<string, number> } }
interface TaxRate { id: string; country: string; region: string | null; tax_code: string | null; name: string; rate: number; active: boolean }
interface TaxSettings { policy: TaxPolicy; engines: Record<string, string>; rates: TaxRate[]; avalara: { configured: boolean; environment: string }; can_edit: boolean }
interface TaxResult { engine_label: string; total: number; summary: { name: string; rate: number; amount: number }[]; note: string | null; error: string | null }

export function TaxCurrencyPanel() {
  return <div className="space-y-6"><FxCard /><TaxCard /></div>;
}

function FxCard() {
  const qc = useQueryClient();
  const fx = useQuery({ queryKey: ["fx"], queryFn: () => get<{ current: FxRow[]; feed: { configured: boolean }; can_edit: boolean }>("/finance/fx") });
  const [open, setOpen] = useState<string | null>(null);
  const [form, setForm] = useState({ currency: "", rate_to_usd: "", effective_date: "" });
  const add = useMutation({
    mutationFn: async () => (await api.post("/finance/fx", { currency: form.currency, rate_to_usd: Number(form.rate_to_usd), effective_date: form.effective_date || null })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["fx"] }); setForm({ currency: "", rate_to_usd: "", effective_date: "" }); toast.success("Rate saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const feed = useMutation({
    mutationFn: async () => (await api.post<{ date: string; updated: string[] }>("/finance/fx/import")).data,
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["fx"] }); toast.success(`Loaded ${r.date} rates for ${r.updated.join(", ") || "no currencies in use"}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (!fx.data) return <Skeleton className="h-48" />;
  return (
    <Card>
      <CardHeader title="Exchange rates" icon={<Landmark className="h-4 w-4" />}
        description="US dollars per one unit of each currency. Open pipeline converts at today's rate; won and lost deals convert at the rate in effect on their close date, so past results don't move with the market."
        action={fx.data.feed.configured && fx.data.can_edit && <Button size="sm" variant="outline" loading={feed.isPending} onClick={() => feed.mutate()}><DownloadCloud className="h-3.5 w-3.5" />Load reference rates</Button>} />
      <Table head={["Currency", "Today", "Since", "History"]} minWidth={480}>
        {fx.data.current.map((r) => (
          <tr key={r.currency} className="align-top">
            <Td className="font-medium">{r.currency}</Td>
            <Td className="tabular text-[13px]">{fmtNumber(r.rate_to_usd, 6)}</Td>
            <Td className="text-[12.5px] text-muted-foreground">{r.history[0] ? shortDate(r.history[0].effective_date, true) : "—"}{r.history[0] && <Badge tone="outline" className="ml-1.5">{r.history[0].source}</Badge>}</Td>
            <Td className="text-[12.5px]">
              {r.history.length > 1 ? (
                <button type="button" className="text-primary hover:underline" onClick={() => setOpen(open === r.currency ? null : r.currency)}>
                  {open === r.currency ? "Hide" : `${r.history.length} rates`}</button>) : <span className="text-muted-foreground">—</span>}
              {open === r.currency && (
                <ul className="mt-1.5 space-y-0.5">{r.history.map((h) => <li key={h.effective_date} className="tabular">{shortDate(h.effective_date, true)}: {fmtNumber(h.rate_to_usd, 6)} <span className="text-subtle">{h.source}</span></li>)}</ul>)}
            </Td>
          </tr>
        ))}
      </Table>
      {fx.data.can_edit && (
        <CardBody className="grid items-end gap-2 border-t sm:grid-cols-[100px_1fr_1fr_auto]">
          <div><Label htmlFor="fx-cur">Currency</Label><Input id="fx-cur" maxLength={3} placeholder="EUR" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value.toUpperCase() })} /></div>
          <div><Label htmlFor="fx-rate">USD per unit</Label><Input id="fx-rate" type="number" min={0} step="any" value={form.rate_to_usd} onChange={(e) => setForm({ ...form, rate_to_usd: e.target.value })} /></div>
          <div><Label htmlFor="fx-date">Effective from</Label><Input id="fx-date" type="date" value={form.effective_date} onChange={(e) => setForm({ ...form, effective_date: e.target.value })} /></div>
          <Button size="sm" disabled={form.currency.length !== 3 || !(Number(form.rate_to_usd) > 0)} loading={add.isPending} onClick={() => add.mutate()}><Plus className="h-3.5 w-3.5" />Add rate</Button>
        </CardBody>
      )}
    </Card>
  );
}

function TaxCard() {
  const qc = useQueryClient();
  const data = useQuery({ queryKey: ["tax"], queryFn: () => get<TaxSettings>("/finance/tax") });
  const [pol, setPol] = useState<TaxPolicy | null>(null);
  useEffect(() => { if (data.data) setPol(data.data.policy); }, [data.data]);
  const [rate, setRate] = useState({ country: "", region: "", tax_code: "", name: "VAT", rate: "" });
  const [preview, setPreview] = useState({ amount: "1000", country: "", region: "", city: "", tax_code: "" });
  const [result, setResult] = useState<TaxResult | null>(null);
  const [code, setCode] = useState({ code: "", rate: "" });
  const save = useMutation({
    mutationFn: async () => (await api.put("/finance/tax", { policy: pol })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["tax"] }); toast.success("Tax settings saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const addRate = useMutation({
    mutationFn: async () => (await api.post("/finance/tax/rates", { ...rate, rate: Number(rate.rate), region: rate.region || null, tax_code: rate.tax_code || null })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["tax"] }); setRate({ ...rate, region: "", tax_code: "", rate: "" }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const delRate = useMutation({
    mutationFn: async (id: string) => api.delete(`/finance/tax/rates/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tax"] }), onError: (e) => toast.error(errorMessage(e)),
  });
  const run = useMutation({
    mutationFn: async () => (await api.post<TaxResult>("/finance/tax/preview", { ...preview, amount: Number(preview.amount), region: preview.region || null,
      city: preview.city || null, tax_code: preview.tax_code || null })).data,
    onSuccess: setResult, onError: (e) => toast.error(errorMessage(e)),
  });
  if (!data.data || !pol) return <Skeleton className="h-64" />;
  const d = data.data, ro = !d.can_edit;
  const seller = (k: string, label: string, placeholder?: string) => (
    <div><Label htmlFor={`tax-seller-${k}`}>{label}</Label><Input id={`tax-seller-${k}`} disabled={ro} placeholder={placeholder} value={pol.seller[k] ?? ""}
      onChange={(e) => setPol({ ...pol, seller: { ...pol.seller, [k]: e.target.value } })} /></div>
  );
  return (
    <Card>
      <CardHeader title="Tax" icon={<Percent className="h-4 w-4" />}
        description="Tax is calculated on every quote (and carried onto its order) from the deal's ship-to, bill-to or account billing address. Tax-exempt deals are never taxed."
        action={d.can_edit && <Button size="sm" loading={save.isPending} onClick={() => save.mutate()}>Save</Button>} />
      <CardBody className="space-y-5">
        <div className="grid gap-3 sm:grid-cols-3">
          <div><Label htmlFor="tax-engine">Engine</Label>
            <Select id="tax-engine" disabled={ro} value={pol.engine} onChange={(e) => setPol({ ...pol, engine: e.target.value })}>
              {Object.entries(d.engines).map(([k, l]) => <option key={k} value={k}>{l}{k === "avalara" && !d.avalara.configured ? " (credentials not set)" : ""}</option>)}
            </Select></div>
          {seller("country", "Seller country", "US")}
          {seller("region", "Seller state / region", pol.engine === "india_gst" ? "MH" : "CA")}
          {seller("postal_code", "Seller postal code")}
          {seller("city", "Seller city")}
          {seller("line1", "Seller street")}
        </div>
        {pol.engine === "avalara" && (
          <p className="text-[12.5px] text-muted-foreground">Avalara AvaTax ({d.avalara.environment}): {d.avalara.configured ? "credentials configured on the server." : "set AVALARA_ACCOUNT_ID and AVALARA_LICENSE_KEY on the server."} Product tax codes are sent as Avalara tax codes (default SW054000, SaaS).</p>
        )}
        {pol.engine === "india_gst" && (
          <div className="space-y-2">
            <p className="text-[12.5px] text-muted-foreground">Same state as the seller: CGST + SGST (half each). Other state, or state unknown: IGST. Outside India: zero-rated export.</p>
            <div className="grid items-end gap-2 sm:grid-cols-[160px_1fr]">
              <div><Label htmlFor="gst-default">Default GST rate (%)</Label><Input id="gst-default" type="number" min={0} max={100} step="any" disabled={ro} value={pol.gst.default_rate}
                onChange={(e) => setPol({ ...pol, gst: { ...pol.gst, default_rate: Number(e.target.value) } })} /></div>
              <div className="flex flex-wrap items-center gap-1.5">
                {Object.entries(pol.gst.rates_by_code).map(([c, r]) => (
                  <Badge key={c} tone="outline" className="gap-1">HSN/SAC {c}: {r}%{!ro && <button type="button" aria-label={`Remove ${c}`} onClick={() => {
                    const next = { ...pol.gst.rates_by_code }; delete next[c]; setPol({ ...pol, gst: { ...pol.gst, rates_by_code: next } }); }}>×</button>}</Badge>
                ))}
                {!ro && <>
                  <Input aria-label="HSN/SAC code" id="gst-code" className="h-8 w-28" placeholder="998314" value={code.code} onChange={(e) => setCode({ ...code, code: e.target.value })} />
                  <Input aria-label="Rate" id="gst-code-rate" className="h-8 w-20" type="number" placeholder="%" value={code.rate} onChange={(e) => setCode({ ...code, rate: e.target.value })} />
                  <Button size="sm" variant="outline" disabled={!code.code || code.rate === ""} onClick={() => {
                    setPol({ ...pol, gst: { ...pol.gst, rates_by_code: { ...pol.gst.rates_by_code, [code.code]: Number(code.rate) } } }); setCode({ code: "", rate: "" }); }}>Add code</Button>
                </>}
              </div>
            </div>
          </div>
        )}
        {pol.engine === "builtin" && (
          <div>
            <p className="mb-1.5 text-[12.5px] text-muted-foreground">The most specific matching rate applies to each quote line: country, then state / region, then product tax code.</p>
            <Table head={["Country", "Region", "Tax code", "Name", "Rate", ""]} minWidth={520}>
              {d.rates.map((r) => (
                <tr key={r.id}>
                  <Td className="text-[13px]">{r.country}</Td><Td className="text-[13px]">{r.region ?? "any"}</Td><Td className="text-[13px]">{r.tax_code ?? "any"}</Td>
                  <Td className="text-[13px]">{r.name}</Td><Td className="tabular text-[13px]">{fmtNumber(r.rate, 3)}%</Td>
                  <Td>{d.can_edit && <button type="button" aria-label={`Delete ${r.name} ${r.country}`} className="text-muted-foreground hover:text-destructive" onClick={() => delRate.mutate(r.id)}><Trash2 className="h-3.5 w-3.5" /></button>}</Td>
                </tr>
              ))}
            </Table>
            {d.can_edit && (
              <div className="mt-2 grid items-end gap-2 sm:grid-cols-[80px_90px_110px_1fr_90px_auto]">
                <Input aria-label="Country" id="rate-country" maxLength={2} placeholder="GB" value={rate.country} onChange={(e) => setRate({ ...rate, country: e.target.value.toUpperCase() })} />
                <Input aria-label="Region" id="rate-region" maxLength={10} placeholder="Region" value={rate.region} onChange={(e) => setRate({ ...rate, region: e.target.value.toUpperCase() })} />
                <Input aria-label="Tax code" id="rate-code" maxLength={20} placeholder="Tax code" value={rate.tax_code} onChange={(e) => setRate({ ...rate, tax_code: e.target.value })} />
                <Input aria-label="Name" id="rate-name" maxLength={60} value={rate.name} onChange={(e) => setRate({ ...rate, name: e.target.value })} />
                <Input aria-label="Rate %" id="rate-rate" type="number" min={0} max={100} step="any" placeholder="%" value={rate.rate} onChange={(e) => setRate({ ...rate, rate: e.target.value })} />
                <Button size="sm" variant="outline" disabled={rate.country.length !== 2 || rate.rate === "" || !rate.name} loading={addRate.isPending} onClick={() => addRate.mutate()}><Plus className="h-3.5 w-3.5" />Add</Button>
              </div>
            )}
          </div>
        )}
        <div className="rounded-lg border p-3">
          <p className="mb-2 flex items-center gap-1.5 text-[13px] font-medium"><Calculator className="h-3.5 w-3.5" />Try it (uses the saved settings)</p>
          <div className="grid items-end gap-2 sm:grid-cols-[110px_90px_90px_1fr_110px_auto]">
            <Input aria-label="Amount" id="tax-try-amount" type="number" min={0} value={preview.amount} onChange={(e) => setPreview({ ...preview, amount: e.target.value })} />
            <Input aria-label="Ship-to country" id="tax-try-country" placeholder="Country" value={preview.country} onChange={(e) => setPreview({ ...preview, country: e.target.value })} />
            <Input aria-label="Ship-to region" id="tax-try-region" placeholder="Region" value={preview.region} onChange={(e) => setPreview({ ...preview, region: e.target.value })} />
            <Input aria-label="Ship-to city" id="tax-try-city" placeholder="City" value={preview.city} onChange={(e) => setPreview({ ...preview, city: e.target.value })} />
            <Input aria-label="Tax code" id="tax-try-code" placeholder="Tax code" value={preview.tax_code} onChange={(e) => setPreview({ ...preview, tax_code: e.target.value })} />
            <Button size="sm" variant="outline" disabled={!preview.country || !(Number(preview.amount) > 0)} loading={run.isPending} onClick={() => run.mutate()}>Calculate</Button>
          </div>
          {result && (
            <div className="mt-2 text-[13px]">
              {result.summary.map((s) => <p key={`${s.name}${s.rate}`} className="tabular">{s.name} {s.rate}%: {fmtMoney(s.amount, "USD")}</p>)}
              <p className="font-medium">Tax {fmtMoney(result.total, "USD")} · {result.engine_label}</p>
              {result.note && <p className="text-muted-foreground">{result.note}</p>}
              {result.error && <p className="text-destructive">{result.error}</p>}
            </div>
          )}
        </div>
      </CardBody>
    </Card>
  );
}
