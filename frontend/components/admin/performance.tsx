"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Map, Pencil, Plus, Trash2, Trophy } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { type Plan, type Tier, planSummary } from "@/components/performance";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Table, Td } from "@/components/ui/extra";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { ROLE_LABELS } from "@/lib/me";
import { money } from "@/lib/utils";

interface Criteria { regions?: string[]; countries?: string[]; industries?: string[]; tiers?: string[]; min_employees?: number; max_employees?: number }
interface Territory { id: string; name: string; description: string | null; parent_id: string | null; parent: string | null; manager_id: string | null; member_ids: string[]; criteria: Criteria; priority: number; accounts: number }
interface Realign { applied: boolean; changes: { account_id: string; account: string; from: string | null; to: string | null }[]; unassigned: number }
type U = { id: string; full_name: string; role: string };

const REGIONS = ["NA", "EMEA", "APAC", "LATAM"];
const TIERS = ["SMB", "Mid-Market", "Enterprise"];
const csv = (v: string) => v.split(",").map((x) => x.trim()).filter(Boolean);
const useUsers = () => useQuery({ queryKey: ["users"], queryFn: () => get<U[]>("/users") });

function criteriaText(c: Criteria) {
  const parts = [
    c.regions?.length && c.regions.join(", "), c.countries?.length && c.countries.join(", "), c.industries?.length && c.industries.join(", "),
    c.tiers?.length && c.tiers.join(", "),
    (c.min_employees != null || c.max_employees != null) && `${c.min_employees ?? 0}–${c.max_employees ?? "∞"} employees`,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "Everything (catch-all)";
}

// ---- territories -------------------------------------------------------------------------------------------

export function TerritoriesPanel() {
  const qc = useQueryClient();
  const { data: users } = useUsers();
  const list = useQuery({ queryKey: ["territories"], queryFn: () => get<{ territories: Territory[]; unassigned: number }>("/performance/territories") });
  const [edit, setEdit] = useState<Territory | "new" | null>(null);
  const [preview, setPreview] = useState<Realign | null>(null);
  const realign = useMutation({
    mutationFn: async (apply: boolean) => (await api.post<Realign>("/performance/territories/realign", null, { params: { apply } })).data,
    onSuccess: (r) => {
      if (r.applied) { setPreview(null); qc.invalidateQueries({ queryKey: ["territories"] }); toast.success(`Realigned: ${r.changes.length} accounts moved`); }
      else setPreview(r);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const del = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/performance/territories/${id}`)).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["territories"] }); toast.success("Territory deleted; its accounts are unassigned until the next realignment"); },
  });
  const names = new globalThis.Map((users ?? []).map((u) => [u.id, u.full_name]));
  if (!list.data) return <Skeleton className="h-64 w-full" />;
  return (
    <div className="space-y-4">
      <Card className="overflow-hidden">
        <CardHeader title="Territories" icon={<Map className="h-4 w-4" />}
          description="Accounts fall into the first matching territory, lowest priority number first. Realigning never changes account owners."
          action={<div className="flex gap-2">
            <Button size="sm" variant="outline" loading={realign.isPending && !realign.variables} onClick={() => realign.mutate(false)}>Preview realignment</Button>
            <Button size="sm" onClick={() => setEdit("new")}><Plus className="h-3.5 w-3.5" />New territory</Button></div>} />
        {!list.data.territories.length ? <EmptyState icon={<Map className="h-4 w-4" />} title="No territories yet" description="Create territories by region, country, industry, tier or company size." /> : (
          <Table head={["Priority", "Territory", "Rules", "Team", "Accounts", ""]} minWidth={860}>
            {list.data.territories.map((t) => (
              <tr key={t.id}>
                <Td className="tabular text-[13px]">{t.priority}</Td>
                <Td><span className="font-medium">{t.name}</span>{t.parent && <span className="block text-[12px] text-muted-foreground">Under {t.parent}</span>}</Td>
                <Td className="text-[13px]">{criteriaText(t.criteria)}</Td>
                <Td className="text-[13px]">{t.manager_id && <span className="block">{names.get(t.manager_id)} (manager)</span>}
                  <span className="text-muted-foreground">{t.member_ids.map((m) => names.get(m)).filter(Boolean).join(", ") || "No members"}</span></Td>
                <Td className="tabular text-[13px]">{t.accounts}</Td>
                <Td><span className="flex justify-end gap-1">
                  <Button variant="ghost" size="icon" aria-label={`Edit ${t.name}`} onClick={() => setEdit(t)}><Pencil className="h-3.5 w-3.5" /></Button>
                  <Button variant="ghost" size="icon" aria-label={`Delete ${t.name}`} onClick={() => confirm(`Delete ${t.name}?`) && del.mutate(t.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
                </span></Td>
              </tr>
            ))}
          </Table>
        )}
        <p className="border-t px-5 py-3 text-[12.5px] text-muted-foreground">{list.data.unassigned} account{list.data.unassigned === 1 ? "" : "s"} in no territory.</p>
      </Card>
      {preview && (
        <Card>
          <CardHeader title={`Realignment preview: ${preview.changes.length} account${preview.changes.length === 1 ? "" : "s"} would move`}
            description={`${preview.unassigned} would match no territory.`}
            action={<div className="flex gap-2"><Button size="sm" variant="ghost" onClick={() => setPreview(null)}>Close</Button>
              <Button size="sm" disabled={!preview.changes.length} loading={realign.isPending && !!realign.variables} onClick={() => realign.mutate(true)}>Apply</Button></div>} />
          {preview.changes.length > 0 && (
            <Table head={["Account", "From", "To"]} minWidth={520}>
              {preview.changes.slice(0, 100).map((c) => (
                <tr key={c.account_id}><Td className="font-medium">{c.account}</Td><Td className="text-[13px] text-muted-foreground">{c.from ?? "None"}</Td><Td className="text-[13px]">{c.to ?? "None"}</Td></tr>
              ))}
            </Table>
          )}
        </Card>
      )}
      {edit && <TerritoryDialog territory={edit === "new" ? null : edit} all={list.data.territories} users={users ?? []} onClose={() => setEdit(null)} />}
    </div>
  );
}

function TerritoryDialog({ territory, all, users, onClose }: { territory: Territory | null; all: Territory[]; users: U[]; onClose: () => void }) {
  const qc = useQueryClient();
  const c = territory?.criteria ?? {};
  const [f, setF] = useState({
    name: territory?.name ?? "", description: territory?.description ?? "", parent_id: territory?.parent_id ?? "", manager_id: territory?.manager_id ?? "",
    member_ids: territory?.member_ids ?? [], priority: territory?.priority ?? 100, regions: c.regions ?? [], tiers: c.tiers ?? [],
    countries: (c.countries ?? []).join(", "), industries: (c.industries ?? []).join(", "),
    min_employees: c.min_employees?.toString() ?? "", max_employees: c.max_employees?.toString() ?? "",
  });
  const toggle = (k: "regions" | "tiers" | "member_ids", v: string) => setF({ ...f, [k]: f[k].includes(v) ? f[k].filter((x) => x !== v) : [...f[k], v] });
  const save = useMutation({
    mutationFn: async () => {
      const body = { name: f.name, description: f.description || null, parent_id: f.parent_id || null, manager_id: f.manager_id || null,
        member_ids: f.member_ids, priority: Number(f.priority), criteria: { regions: f.regions, tiers: f.tiers, countries: csv(f.countries),
          industries: csv(f.industries), min_employees: f.min_employees || null, max_employees: f.max_employees || null } };
      return territory ? (await api.put(`/performance/territories/${territory.id}`, body)).data : (await api.post("/performance/territories", body)).data;
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["territories"] }); toast.success("Territory saved. Preview a realignment to apply the rules."); onClose(); },
  });
  const sellers = users.filter((u) => ["account_executive", "sdr", "sales_manager"].includes(u.role));
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={territory ? "Edit territory" : "New territory"} className="max-w-xl">
        <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <h2 className="text-[15px] font-semibold">{territory ? `Edit ${territory.name}` : "New territory"}</h2>
          <div className="grid grid-cols-[1fr_110px] gap-2">
            <div><Label htmlFor="tr-name">Name</Label><Input id="tr-name" required minLength={2} maxLength={100} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
            <div><Label htmlFor="tr-priority">Priority</Label><Input id="tr-priority" type="number" min={0} max={10000} value={f.priority} onChange={(e) => setF({ ...f, priority: Number(e.target.value) })} /></div>
          </div>
          <div><Label htmlFor="tr-desc">Description</Label><Textarea id="tr-desc" rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></div>
          <div className="grid grid-cols-2 gap-2">
            <div><Label htmlFor="tr-parent">Parent</Label><Select id="tr-parent" value={f.parent_id} onChange={(e) => setF({ ...f, parent_id: e.target.value })}>
              <option value="">None</option>{all.filter((t) => t.id !== territory?.id).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</Select></div>
            <div><Label htmlFor="tr-manager">Manager</Label><Select id="tr-manager" value={f.manager_id} onChange={(e) => setF({ ...f, manager_id: e.target.value })}>
              <option value="">None</option>{users.filter((u) => ["sales_manager", "super_admin"].includes(u.role)).map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}</Select></div>
          </div>
          <fieldset><legend className="mb-1 text-[13px] font-medium">Members</legend>
            <div className="flex flex-wrap gap-3 text-[13px]">{sellers.map((u) => (
              <label key={u.id} className="flex items-center gap-1.5"><input type="checkbox" checked={f.member_ids.includes(u.id)} onChange={() => toggle("member_ids", u.id)} />{u.full_name}</label>
            ))}</div></fieldset>
          <p className="pt-1 text-[13px] font-medium">Rules <span className="font-normal text-muted-foreground">(leave all empty for a catch-all)</span></p>
          <fieldset><legend className="sr-only">Regions</legend><div className="flex flex-wrap gap-3 text-[13px]"><span className="text-muted-foreground">Regions:</span>{REGIONS.map((r) => (
            <label key={r} className="flex items-center gap-1.5"><input type="checkbox" checked={f.regions.includes(r)} onChange={() => toggle("regions", r)} />{r}</label>))}</div></fieldset>
          <fieldset><legend className="sr-only">Tiers</legend><div className="flex flex-wrap gap-3 text-[13px]"><span className="text-muted-foreground">Tiers:</span>{TIERS.map((r) => (
            <label key={r} className="flex items-center gap-1.5"><input type="checkbox" checked={f.tiers.includes(r)} onChange={() => toggle("tiers", r)} />{r}</label>))}</div></fieldset>
          <div className="grid grid-cols-2 gap-2">
            <div><Label htmlFor="tr-countries">Countries</Label><Input id="tr-countries" placeholder="Germany, Austria" value={f.countries} onChange={(e) => setF({ ...f, countries: e.target.value })} /></div>
            <div><Label htmlFor="tr-industries">Industries</Label><Input id="tr-industries" placeholder="Retail, Logistics" value={f.industries} onChange={(e) => setF({ ...f, industries: e.target.value })} /></div>
            <div><Label htmlFor="tr-min">Min employees</Label><Input id="tr-min" type="number" min={0} value={f.min_employees} onChange={(e) => setF({ ...f, min_employees: e.target.value })} /></div>
            <div><Label htmlFor="tr-max">Max employees</Label><Input id="tr-max" type="number" min={0} value={f.max_employees} onChange={(e) => setF({ ...f, max_employees: e.target.value })} /></div>
          </div>
          {save.isError && <p className="text-sm text-destructive">{errorMessage(save.error)}</p>}
          <div className="flex justify-end gap-2"><Button type="button" variant="ghost" size="sm" onClick={onClose}>Cancel</Button><Button type="submit" size="sm" loading={save.isPending}>Save</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---- commission plans ---------------------------------------------------------------------------------------

export function CommissionPlansPanel() {
  const qc = useQueryClient();
  const plans = useQuery({ queryKey: ["commission-plans"], queryFn: () => get<Plan[]>("/performance/plans") });
  const [edit, setEdit] = useState<Plan | "new" | null>(null);
  const del = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/performance/plans/${id}`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["commission-plans"] }),
  });
  if (!plans.data) return <Skeleton className="h-48 w-full" />;
  return (
    <Card className="overflow-hidden">
      <CardHeader title="Commission plans" icon={<Trophy className="h-4 w-4" />}
        description="A plan pays its base rate on bookings up to the first tier, then each tier's rate on the bookings above its threshold. Plans apply by person first, then by role."
        action={<Button size="sm" onClick={() => setEdit("new")}><Plus className="h-3.5 w-3.5" />New plan</Button>} />
      {!plans.data.length ? <EmptyState icon={<Trophy className="h-4 w-4" />} title="No commission plans" /> : (
        <Table head={["Plan", "Rates", "Applies to", "Status", ""]} minWidth={760}>
          {plans.data.map((p) => (
            <tr key={p.id}>
              <Td><span className="font-medium">{p.name}</span>{p.description && <span className="block text-[12px] text-muted-foreground">{p.description}</span>}</Td>
              <Td className="text-[13px]">{planSummary(p)}</Td>
              <Td className="text-[13px]">{[...p.roles.map((r) => ROLE_LABELS[r] ?? r), p.member_ids.length ? `${p.member_ids.length} named` : ""].filter(Boolean).join(", ") || "Nobody"}</Td>
              <Td>{p.active ? <Badge tone="good">Active</Badge> : <Badge tone="neutral">Off</Badge>}</Td>
              <Td><span className="flex justify-end gap-1">
                <Button variant="ghost" size="icon" aria-label={`Edit ${p.name}`} onClick={() => setEdit(p)}><Pencil className="h-3.5 w-3.5" /></Button>
                <Button variant="ghost" size="icon" aria-label={`Delete ${p.name}`} onClick={() => confirm(`Delete ${p.name}?`) && del.mutate(p.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
              </span></Td>
            </tr>
          ))}
        </Table>
      )}
      {edit && <PlanDialog plan={edit === "new" ? null : edit} onClose={() => setEdit(null)} />}
    </Card>
  );
}

function PlanDialog({ plan, onClose }: { plan: Plan | null; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: users } = useUsers();
  const [f, setF] = useState({ name: plan?.name ?? "", description: plan?.description ?? "", base_rate: plan?.base_rate ?? 5, active: plan?.active ?? true,
    roles: plan?.roles ?? ["account_executive"], member_ids: plan?.member_ids ?? [], tiers: plan?.tiers ?? [{ from_pct: 100, rate: 8 }] as Tier[] });
  const [sim, setSim] = useState({ quota: 300000, bookings: 360000 });
  const preview = useQuery({ queryKey: ["plan-preview", f.base_rate, f.tiers, sim], placeholderData: (p) => p,
    queryFn: async () => (await api.post<{ total: number }>("/performance/plans/preview", { base_rate: f.base_rate, tiers: f.tiers, ...sim })).data, retry: false });
  const save = useMutation({
    mutationFn: async () => {
      const body = { ...f, description: f.description || null };
      return plan ? (await api.put(`/performance/plans/${plan.id}`, body)).data : (await api.post("/performance/plans", body)).data;
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["commission-plans"] }); qc.invalidateQueries({ queryKey: ["performance"] }); toast.success("Plan saved"); onClose(); },
  });
  const setTier = (i: number, t: Partial<Tier>) => setF({ ...f, tiers: f.tiers.map((x, j) => (j === i ? { ...x, ...t } : x)) });
  const roles = ["account_executive", "sdr", "sales_manager"];
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={plan ? "Edit plan" : "New plan"} className="max-w-lg">
        <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <h2 className="text-[15px] font-semibold">{plan ? `Edit ${plan.name}` : "New commission plan"}</h2>
          <div><Label htmlFor="pl-name">Name</Label><Input id="pl-name" required minLength={2} maxLength={100} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
          <div><Label htmlFor="pl-desc">Description</Label><Input id="pl-desc" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></div>
          <div className="w-40"><Label htmlFor="pl-base">Base rate %</Label><Input id="pl-base" type="number" min={0} max={100} step="0.1" value={f.base_rate} onChange={(e) => setF({ ...f, base_rate: Number(e.target.value) })} /></div>
          <div className="space-y-2">
            <p className="text-[13px] font-medium">Accelerators</p>
            {f.tiers.map((t, i) => (
              <div key={i} className="flex items-center gap-2 text-[13px]">
                <span>From</span><Input aria-label={`Tier ${i + 1} threshold`} className="h-8 w-20" type="number" min={1} value={t.from_pct} onChange={(e) => setTier(i, { from_pct: Number(e.target.value) })} />
                <span>% of quota pay</span><Input aria-label={`Tier ${i + 1} rate`} className="h-8 w-20" type="number" min={0} max={100} step="0.1" value={t.rate} onChange={(e) => setTier(i, { rate: Number(e.target.value) })} /><span>%</span>
                <Button type="button" variant="ghost" size="icon" aria-label={`Remove tier ${i + 1}`} onClick={() => setF({ ...f, tiers: f.tiers.filter((_, j) => j !== i) })}><Trash2 className="h-3.5 w-3.5" /></Button>
              </div>
            ))}
            <Button type="button" variant="outline" size="sm" onClick={() => setF({ ...f, tiers: [...f.tiers, { from_pct: (f.tiers.at(-1)?.from_pct ?? 100) + 25, rate: (f.tiers.at(-1)?.rate ?? f.base_rate) + 2 }] })}>
              <Plus className="h-3.5 w-3.5" />Add tier</Button>
          </div>
          <fieldset><legend className="mb-1 text-[13px] font-medium">Applies to roles</legend>
            <div className="flex flex-wrap gap-3 text-[13px]">{roles.map((r) => (
              <label key={r} className="flex items-center gap-1.5"><input type="checkbox" checked={f.roles.includes(r)}
                onChange={() => setF({ ...f, roles: f.roles.includes(r) ? f.roles.filter((x) => x !== r) : [...f.roles, r] })} />{ROLE_LABELS[r]}</label>))}</div></fieldset>
          <div><Label htmlFor="pl-people">And these people (overrides their role’s plan)</Label>
            <Select id="pl-people" value="" onChange={(e) => e.target.value && setF({ ...f, member_ids: [...f.member_ids, e.target.value] })}>
              <option value="">Add a person…</option>{users?.filter((u) => !f.member_ids.includes(u.id)).map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}</Select>
            <div className="mt-1 flex flex-wrap gap-1">{f.member_ids.map((id) => (
              <Badge key={id} tone="outline">{users?.find((u) => u.id === id)?.full_name ?? "User"}
                <button type="button" aria-label="Remove" className="ml-1" onClick={() => setF({ ...f, member_ids: f.member_ids.filter((x) => x !== id) })}>×</button></Badge>))}</div></div>
          <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} />Active</label>
          <CardBody className="rounded-md border bg-muted/40 p-3 text-[13px]">
            <div className="flex flex-wrap items-center gap-2">
              <span>At quota</span><Input aria-label="Example quota" className="h-8 w-28" type="number" value={sim.quota} onChange={(e) => setSim({ ...sim, quota: Number(e.target.value) })} />
              <span>and bookings</span><Input aria-label="Example bookings" className="h-8 w-28" type="number" value={sim.bookings} onChange={(e) => setSim({ ...sim, bookings: Number(e.target.value) })} />
              <span>this plan pays <strong className="tabular">{preview.isError ? "—" : money(preview.data?.total ?? 0)}</strong></span>
            </div>
            {preview.isError && <p className="mt-1 text-[12px] text-destructive">{errorMessage(preview.error)}</p>}
          </CardBody>
          {save.isError && <p className="text-sm text-destructive">{errorMessage(save.error)}</p>}
          <div className="flex justify-end gap-2"><Button type="button" variant="ghost" size="sm" onClick={onClose}>Cancel</Button><Button type="submit" size="sm" loading={save.isPending}>Save plan</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
