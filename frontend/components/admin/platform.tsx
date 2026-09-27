"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Pencil, Plus, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { type CatalogueField, FilterRow, NO_VALUE } from "@/components/filters";
import type { CustomObjectDef } from "@/components/objects";
import type { Filter } from "@/components/reportviz";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Table, Td } from "@/components/ui/extra";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { api, downloadFile, errorMessage, get } from "@/lib/api";
import { ROLE_LABELS, useMe } from "@/lib/me";
import type { CustomFieldDef } from "@/lib/types";

/* Platform setup: custom objects, field security, validation rules, account sharing rules and moving the
   configuration between environments. Conditions use the report builder's filter rows over the live catalogue. */

interface Catalogue { sources: { key: string; label: string; fields: CatalogueField[] }[]; periods: string[] }
const useCatalogue = () => useQuery({ queryKey: ["analytics", "sources"], queryFn: () => get<Catalogue>("/analytics/sources") });
const finished = (fs: Filter[]) => fs.filter((f) => NO_VALUE.includes(f.op) || (Array.isArray(f.value) ? f.value.every((v) => v !== "") : f.value !== "" && f.value != null));
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^[^a-z]+/, "").slice(0, 30);
const SECURABLE_ROLES = Object.keys(ROLE_LABELS).filter((r) => r !== "super_admin" && r !== "partner");

function Conditions({ fields, periods, value, onChange, addLabel = "Add condition" }: {
  fields: CatalogueField[]; periods: string[]; value: Filter[]; onChange: (v: Filter[]) => void; addLabel?: string;
}) {
  if (!fields.length) return <p className="text-[12.5px] text-muted-foreground">Loading fields…</p>;
  return (
    <div className="space-y-1.5">
      {value.map((f, k) => <FilterRow key={k} f={f} src={{ fields }} periods={periods}
        onChange={(nf) => { const v = [...value]; v[k] = nf; onChange(v); }} onRemove={() => onChange(value.filter((_, j) => j !== k))} />)}
      <Button type="button" size="sm" variant="ghost" onClick={() => onChange([...value, { field: fields[0].key, op: fields[0].ops[0], value: "" }])}>
        <Plus className="h-3.5 w-3.5" />{addLabel}
      </Button>
    </div>
  );
}

// ---- custom objects -------------------------------------------------------------------------------------

export function ObjectsPanel({ onFields }: { onFields: () => void }) {
  const qc = useQueryClient();
  const objects = useQuery({ queryKey: ["objects"], queryFn: () => get<CustomObjectDef[]>("/objects") });
  const [f, setF] = useState({ label: "", plural_label: "", key: "", description: "" });
  const [confirm, setConfirm] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: async () => (await api.post("/objects", { ...f, key: f.key || slug(f.label), plural_label: f.plural_label || `${f.label}s`, description: f.description || null })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["objects"] }); qc.invalidateQueries({ queryKey: ["analytics", "sources"] }); setF({ label: "", plural_label: "", key: "", description: "" }); toast.success("Object created: add its fields next"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: async (key: string) => api.delete(`/objects/${key}`, { params: { confirm: true } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["objects"] }); qc.invalidateQueries({ queryKey: ["custom-fields"] }); setConfirm(null); toast.success("Object deleted with its records, fields, rules and views"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader title="Custom objects" description="New record types with their own fields. Each gets a list and record pages, reports, dashboards, list views, validation rules and API access." />
      <form className="grid gap-2 border-b px-5 pb-4 sm:grid-cols-[1fr_1fr_1fr_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <div><Label htmlFor="ob-label">Name (singular)</Label><Input id="ob-label" required maxLength={80} placeholder="e.g. Site visit" value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} /></div>
        <div><Label htmlFor="ob-plural">Plural</Label><Input id="ob-plural" maxLength={80} placeholder={f.label ? `${f.label}s` : "Site visits"} value={f.plural_label} onChange={(e) => setF({ ...f, plural_label: e.target.value })} /></div>
        <div><Label htmlFor="ob-key">Key</Label><Input id="ob-key" pattern="[a-z][a-z0-9_]{1,30}" placeholder={slug(f.label) || "site_visit"} value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })} /></div>
        <Button type="submit" size="sm" loading={create.isPending}><Plus className="h-3.5 w-3.5" />Create</Button>
        <div className="sm:col-span-4"><Label htmlFor="ob-desc">Description</Label><Input id="ob-desc" maxLength={2000} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></div>
      </form>
      {!objects.data ? <Skeleton className="m-5 h-24" /> : !objects.data.length ? (
        <p className="px-5 py-6 text-[13px] text-muted-foreground">No custom objects yet.</p>
      ) : (
        <Table head={["Object", "Key", "Fields", ""]}>
          {objects.data.map((o) => (
            <tr key={o.key}>
              <Td><a href={`/objects/${o.key}`} className="font-medium hover:underline">{o.plural_label}</a><p className="text-[12px] text-muted-foreground">{o.description}</p></Td>
              <Td className="font-mono text-[12px]">{o.key}</Td>
              <Td><Button size="sm" variant="ghost" onClick={onFields}>{o.fields.length} field{o.fields.length === 1 ? "" : "s"} · manage</Button></Td>
              <Td className="text-right">{confirm === o.key
                ? <Button size="sm" variant="destructive" loading={remove.isPending} onClick={() => remove.mutate(o.key)}>Delete {o.plural_label} and all records</Button>
                : <Button size="sm" variant="ghost" aria-label={`Delete ${o.plural_label}`} onClick={() => setConfirm(o.key)}><Trash2 className="h-3.5 w-3.5" /></Button>}</Td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}

// ---- field security -------------------------------------------------------------------------------------

export function accessSummary(access?: Record<string, string>) {
  const parts = Object.entries(access ?? {}).map(([r, l]) => `${ROLE_LABELS[r] ?? r}: ${l === "hidden" ? "hidden" : "read-only"}`);
  return parts.length ? parts.join(" · ") : "Everyone can edit";
}

export function FieldSecurityDialog({ field, onClose }: { field: CustomFieldDef; onClose: () => void }) {
  const qc = useQueryClient();
  const [label, setLabel] = useState(field.label);
  const [required, setRequired] = useState(!!field.required);
  const [options, setOptions] = useState(field.options.join(", "));
  const [access, setAccess] = useState<Record<string, "read" | "hidden">>(field.access ?? {});
  const save = useMutation({
    mutationFn: async () => (await api.put(`/admin/custom-fields/${field.id}`, { label, required, access,
      options: options.split(",").map((s) => s.trim()).filter(Boolean) })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["custom-fields"] }); qc.invalidateQueries({ queryKey: ["objects"] }); toast.success("Field saved"); onClose(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title="Edit field" className="max-w-lg">
        <form className="space-y-4 p-5" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <div><h2 className="text-[15px] font-semibold">{field.label}</h2><p className="text-[12.5px] text-muted-foreground font-mono">{field.entity} · {field.key} · {field.field_type}</p></div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div><Label htmlFor="fs-label">Label</Label><Input id="fs-label" required value={label} onChange={(e) => setLabel(e.target.value)} /></div>
            <label className="flex items-end gap-2 pb-2 text-[13px]"><input type="checkbox" className="h-4 w-4" checked={required} onChange={(e) => setRequired(e.target.checked)} />Required</label>
            {field.field_type === "select" && <div className="sm:col-span-2"><Label htmlFor="fs-opt">Options</Label><Input id="fs-opt" value={options} onChange={(e) => setOptions(e.target.value)} /></div>}
          </div>
          <div>
            <p className="mb-1 text-[13px] font-medium">Field security</p>
            <p className="mb-2 text-[12px] text-subtle">Hidden fields are never shown, exported or reportable for that role, and its saves keep their values. Super Admins always have full access.</p>
            <div className="divide-y rounded-md border">
              {SECURABLE_ROLES.map((r) => (
                <div key={r} className="flex items-center justify-between gap-3 px-3 py-1.5 text-[13px]">
                  <span>{ROLE_LABELS[r]}</span>
                  <Select aria-label={`${ROLE_LABELS[r]} access`} className="h-8 w-36 text-[13px]" value={access[r] ?? "edit"}
                    onChange={(e) => { const n = { ...access }; if (e.target.value === "edit") delete n[r]; else n[r] = e.target.value as "read" | "hidden"; setAccess(n); }}>
                    <option value="edit">Can edit</option><option value="read">Read-only</option><option value="hidden">Hidden</option>
                  </Select>
                </div>
              ))}
            </div>
          </div>
          <div className="flex justify-end gap-2"><Button type="button" size="sm" variant="ghost" onClick={onClose}>Cancel</Button><Button type="submit" size="sm" loading={save.isPending}>Save</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---- validation rules -----------------------------------------------------------------------------------

interface VRule { id: string; entity: string; name: string; description: string | null; conditions: Filter[]; message: string; applies_on: "create" | "update" | "both"; active: boolean }
const WHEN: Record<VRule["applies_on"], string> = { both: "Create & edit", create: "Create only", update: "Edit only" };

export function ValidationRulesPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["validation-rules"], queryFn: () => get<{ rules: VRule[]; entities: { key: string; label: string }[] }>("/admin/validation-rules") });
  const [editing, setEditing] = useState<VRule | "new" | null>(null);
  const toggle = useMutation({
    mutationFn: async (r: VRule) => api.put(`/admin/validation-rules/${r.id}`, { ...r, active: !r.active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["validation-rules"] }), onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: async (id: string) => api.delete(`/admin/validation-rules/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["validation-rules"] }); toast.success("Rule deleted"); },
  });
  const label = (k: string) => q.data?.entities.find((e) => e.key === k)?.label ?? k;
  return (
    <Card>
      <CardHeader title="Validation rules" description="Block a save when a record matches every condition, with your message. Applies to every screen, import and API call made by people; automation and background jobs are exempt."
        action={<Button size="sm" onClick={() => setEditing("new")}><Plus className="h-3.5 w-3.5" />New rule</Button>} />
      {!q.data ? <Skeleton className="m-5 h-24" /> : !q.data.rules.length ? <p className="px-5 py-6 text-[13px] text-muted-foreground">No rules yet.</p> : (
        <Table head={["Record", "Rule", "Message", "When", "Active", ""]}>
          {q.data.rules.map((r) => (
            <tr key={r.id}>
              <Td>{label(r.entity)}</Td><Td className="font-medium">{r.name}</Td><Td className="text-[13px]">{r.message}</Td>
              <Td className="text-[12.5px]">{WHEN[r.applies_on]}</Td>
              <Td><input type="checkbox" aria-label={`${r.name} active`} className="h-4 w-4" checked={r.active} onChange={() => toggle.mutate(r)} /></Td>
              <Td className="whitespace-nowrap text-right">
                <Button size="sm" variant="ghost" aria-label={`Edit ${r.name}`} onClick={() => setEditing(r)}><Pencil className="h-3.5 w-3.5" /></Button>
                <Button size="sm" variant="ghost" aria-label={`Delete ${r.name}`} onClick={() => remove.mutate(r.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
              </Td>
            </tr>
          ))}
        </Table>
      )}
      {editing && q.data && <RuleEditor rule={editing === "new" ? null : editing} entities={q.data.entities} onClose={() => setEditing(null)} />}
    </Card>
  );
}

function RuleEditor({ rule, entities, onClose }: { rule: VRule | null; entities: { key: string; label: string }[]; onClose: () => void }) {
  const qc = useQueryClient();
  const cat = useCatalogue();
  const [f, setF] = useState({ entity: rule?.entity ?? entities[0]?.key ?? "accounts", name: rule?.name ?? "", message: rule?.message ?? "",
    description: rule?.description ?? "", applies_on: rule?.applies_on ?? "both", active: rule?.active ?? true });
  const [conds, setConds] = useState<Filter[]>(rule?.conditions ?? []);
  const [check, setCheck] = useState<string | null>(null);
  const fields = cat.data?.sources.find((s) => s.key === f.entity)?.fields ?? [];
  const body = () => ({ ...f, description: f.description || null, conditions: finished(conds) });
  const save = useMutation({
    mutationFn: async () => (rule ? api.put(`/admin/validation-rules/${rule.id}`, body()) : api.post("/admin/validation-rules", body())),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["validation-rules"] }); toast.success("Rule saved"); onClose(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const test = useMutation({
    mutationFn: async () => (await api.post<{ count: number; examples: string[] }>("/admin/validation-rules/test", { entity: f.entity, conditions: finished(conds) })).data,
    onSuccess: (r) => setCheck(r.count ? `${r.count} existing record${r.count === 1 ? "" : "s"} already break this rule (e.g. ${r.examples.join(", ")}). They'll need fixing at their next edit.` : "No existing records break this rule."),
    onError: (e) => setCheck(errorMessage(e)),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={rule ? "Edit validation rule" : "New validation rule"} className="max-w-2xl">
        <form className="max-h-[85vh] space-y-4 overflow-y-auto p-5" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <h2 className="text-[15px] font-semibold">{rule ? `Edit “${rule.name}”` : "New validation rule"}</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            <div><Label htmlFor="vr-entity">Record type</Label>
              <Select id="vr-entity" value={f.entity} disabled={!!rule} onChange={(e) => { setF({ ...f, entity: e.target.value }); setConds([]); setCheck(null); }}>
                {entities.map((e) => <option key={e.key} value={e.key}>{e.label}</option>)}</Select></div>
            <div><Label htmlFor="vr-when">Check when a record is</Label>
              <Select id="vr-when" value={f.applies_on} onChange={(e) => setF({ ...f, applies_on: e.target.value as VRule["applies_on"] })}>
                <option value="both">Created or edited</option><option value="create">Created</option><option value="update">Edited</option></Select></div>
            <div className="sm:col-span-2"><Label htmlFor="vr-name">Rule name</Label><Input id="vr-name" required maxLength={150} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
          </div>
          <section>
            <p className="mb-1.5 text-[13px] font-medium">Block saving when <span className="font-normal text-subtle">(all conditions match)</span></p>
            <Conditions fields={fields} periods={cat.data?.periods ?? []} value={conds} onChange={(v) => { setConds(v); setCheck(null); }} />
          </section>
          <div><Label htmlFor="vr-msg">Message shown to the user</Label><Input id="vr-msg" required maxLength={300} placeholder="e.g. Enterprise accounts need an industry" value={f.message} onChange={(e) => setF({ ...f, message: e.target.value })} /></div>
          <div><Label htmlFor="vr-desc">Notes for admins</Label><Textarea id="vr-desc" rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></div>
          {check && <p className="rounded-md bg-muted px-3 py-2 text-[12.5px]">{check}</p>}
          <div className="flex flex-wrap justify-between gap-2 border-t pt-4">
            <Button type="button" size="sm" variant="outline" disabled={!finished(conds).length} loading={test.isPending} onClick={() => test.mutate()}>Check existing records</Button>
            <div className="flex gap-2"><Button type="button" size="sm" variant="ghost" onClick={onClose}>Cancel</Button><Button type="submit" size="sm" loading={save.isPending}>Save rule</Button></div>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---- sharing rules --------------------------------------------------------------------------------------

interface SRule { id: string; name: string; description: string | null; criteria: Filter[]; roles: string[]; active: boolean }

export function SharingRulesPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["sharing-rules"], queryFn: () => get<{ rules: SRule[]; roles: { key: string; label: string }[] }>("/admin/sharing-rules") });
  const [editing, setEditing] = useState<SRule | "new" | null>(null);
  const remove = useMutation({
    mutationFn: async (id: string) => api.delete(`/admin/sharing-rules/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sharing-rules"] }); toast.success("Sharing rule deleted"); },
  });
  return (
    <Card>
      <CardHeader title="Account sharing rules" description="Roles limited to their own records also see accounts matching a rule, with everything on them (contacts, opportunities, cases, quotes…), under their usual permissions."
        action={<Button size="sm" onClick={() => setEditing("new")}><Plus className="h-3.5 w-3.5" />New sharing rule</Button>} />
      {!q.data ? <Skeleton className="m-5 h-24" /> : !q.data.rules.length ? <p className="px-5 py-6 text-[13px] text-muted-foreground">No sharing rules: own-scope roles see only their own accounts.</p> : (
        <Table head={["Rule", "Shared with", "Status", ""]}>
          {q.data.rules.map((r) => (
            <tr key={r.id}>
              <Td><span className="font-medium">{r.name}</span><p className="text-[12px] text-muted-foreground">{r.description}</p></Td>
              <Td className="text-[13px]">{r.roles.map((x) => ROLE_LABELS[x] ?? x).join(", ")}</Td>
              <Td><Badge tone={r.active ? "good" : "neutral"}>{r.active ? "Active" : "Off"}</Badge></Td>
              <Td className="whitespace-nowrap text-right">
                <Button size="sm" variant="ghost" aria-label={`Edit ${r.name}`} onClick={() => setEditing(r)}><Pencil className="h-3.5 w-3.5" /></Button>
                <Button size="sm" variant="ghost" aria-label={`Delete ${r.name}`} onClick={() => remove.mutate(r.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
              </Td>
            </tr>
          ))}
        </Table>
      )}
      {editing && q.data && <ShareEditor rule={editing === "new" ? null : editing} roles={q.data.roles} onClose={() => setEditing(null)} />}
    </Card>
  );
}

function ShareEditor({ rule, roles, onClose }: { rule: SRule | null; roles: { key: string; label: string }[]; onClose: () => void }) {
  const qc = useQueryClient();
  const cat = useCatalogue();
  const [f, setF] = useState({ name: rule?.name ?? "", description: rule?.description ?? "", roles: rule?.roles ?? [], active: rule?.active ?? true });
  const [crit, setCrit] = useState<Filter[]>(rule?.criteria ?? []);
  const [preview, setPreview] = useState<string | null>(null);
  const fields = cat.data?.sources.find((s) => s.key === "accounts")?.fields ?? [];
  const body = () => ({ ...f, description: f.description || null, criteria: finished(crit) });
  const save = useMutation({
    mutationFn: async () => (rule ? api.put(`/admin/sharing-rules/${rule.id}`, body()) : api.post("/admin/sharing-rules", body())),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sharing-rules"] }); toast.success("Sharing rule saved"); onClose(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const test = useMutation({
    mutationFn: async () => (await api.post<{ count: number; examples: string[] }>("/admin/sharing-rules/test", { criteria: finished(crit) })).data,
    onSuccess: (r) => setPreview(`${r.count} account${r.count === 1 ? "" : "s"} match today${r.examples.length ? ` (e.g. ${r.examples.join(", ")})` : ""}.`),
    onError: (e) => setPreview(errorMessage(e)),
  });
  const toggleRole = (k: string) => setF({ ...f, roles: f.roles.includes(k) ? f.roles.filter((x) => x !== k) : [...f.roles, k] });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={rule ? "Edit sharing rule" : "New sharing rule"} className="max-w-2xl">
        <form className="max-h-[85vh] space-y-4 overflow-y-auto p-5" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <h2 className="text-[15px] font-semibold">{rule ? `Edit “${rule.name}”` : "New account sharing rule"}</h2>
          <div><Label htmlFor="sr-name">Name</Label><Input id="sr-name" required maxLength={150} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="e.g. EMEA accounts for EMEA reps" /></div>
          <section>
            <p className="mb-1.5 text-[13px] font-medium">Share accounts where <span className="font-normal text-subtle">(all conditions match)</span></p>
            <Conditions fields={fields} periods={cat.data?.periods ?? []} value={crit} onChange={(v) => { setCrit(v); setPreview(null); }} />
          </section>
          <section>
            <p className="mb-1.5 text-[13px] font-medium">With</p>
            <div className="flex flex-wrap gap-x-4 gap-y-1.5">
              {roles.map((r) => <label key={r.key} className="flex items-center gap-1.5 text-[13px]"><input type="checkbox" className="h-4 w-4" checked={f.roles.includes(r.key)} onChange={() => toggleRole(r.key)} />{r.label}</label>)}
            </div>
          </section>
          <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" className="h-4 w-4" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} />Active</label>
          {preview && <p className="rounded-md bg-muted px-3 py-2 text-[12.5px]">{preview}</p>}
          <div className="flex flex-wrap justify-between gap-2 border-t pt-4">
            <Button type="button" size="sm" variant="outline" disabled={!finished(crit).length} loading={test.isPending} onClick={() => test.mutate()}>Preview matching accounts</Button>
            <div className="flex gap-2"><Button type="button" size="sm" variant="ghost" onClick={onClose}>Cancel</Button><Button type="submit" size="sm" loading={save.isPending}>Save rule</Button></div>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---- configuration export / import --------------------------------------------------------------------

interface ImportResult { applied: boolean; dry_run: boolean; summary: Record<"create" | "update" | "unchanged" | "error", number>;
  items: { section: string; key: string; action: "create" | "update" | "unchanged" | "error"; detail: string }[] }
const TONE = { create: "good", update: "primary", unchanged: "neutral", error: "critical" } as const;

export function ConfigTransferPanel() {
  const { me } = useMe();
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [bundle, setBundle] = useState<Record<string, unknown> | null>(null);
  const [fileName, setFileName] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);
  const [showAll, setShowAll] = useState(false);
  const run = useMutation({
    mutationFn: async ({ b, dry }: { b: Record<string, unknown>; dry: boolean }) => (await api.post<ImportResult>("/admin/config/import", { bundle: b, dry_run: dry })).data,
    onSuccess: (r) => {
      setResult(r);
      if (r.applied) { toast.success(`Configuration imported: ${r.summary.create} created, ${r.summary.update} updated`); qc.invalidateQueries(); }
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const load = async (file: File) => {
    try {
      const parsed = JSON.parse(await file.text());
      setBundle(parsed); setFileName(file.name); setResult(null);
      run.mutate({ b: parsed, dry: true });
    } catch { toast.error("That file isn't valid JSON"); }
  };
  const superAdmin = me?.role === "super_admin";
  const rows = (result?.items ?? []).filter((i) => showAll || i.action !== "unchanged");
  return (
    <Card>
      <CardHeader title="Move configuration between environments"
        description="Export custom objects and fields (with field security), validation and sharing rules, workflows, role permissions and team-shared reports, dashboards and list views. Records are never included." />
      <CardBody className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => downloadFile("/admin/config/export", `cirra-config-${new Date().toISOString().slice(0, 10)}.json`).catch((e) => toast.error(errorMessage(e)))}>
            <Download className="h-3.5 w-3.5" />Export configuration</Button>
          {superAdmin ? <>
            <input ref={fileRef} type="file" accept="application/json,.json" className="hidden" aria-label="Configuration file"
              onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) load(f); }} />
            <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()}><Upload className="h-3.5 w-3.5" />Import a bundle…</Button>
          </> : <span className="text-[12.5px] text-muted-foreground">Only a Super Admin can import.</span>}
        </div>
        {bundle && (
          <div className="space-y-3 rounded-md border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-[13px]"><span className="font-medium">{fileName}</span>{result && (result.applied ? " · imported" : result.dry_run ? " · preview (nothing changed yet)" : " · not imported")}</p>
              {result && <div className="flex flex-wrap gap-1.5 text-[12px]">
                {(["create", "update", "unchanged", "error"] as const).map((k) => <Badge key={k} tone={TONE[k]}>{result.summary[k]} {k === "unchanged" ? "unchanged" : k === "error" ? "errors" : k === "create" ? "new" : "changed"}</Badge>)}
              </div>}
            </div>
            {run.isPending && <Skeleton className="h-16" />}
            {result && (
              <>
                <label className="flex items-center gap-2 text-[12.5px] text-muted-foreground"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />Show unchanged items</label>
                {rows.length ? (
                  <div className="max-h-80 overflow-y-auto">
                    <Table head={["Section", "Item", "Result", "Detail"]}>
                      {rows.map((i, k) => <tr key={k}><Td className="text-[12.5px]">{i.section.replace(/_/g, " ")}</Td><Td className="font-mono text-[12px]">{i.key}</Td>
                        <Td><Badge tone={TONE[i.action]}>{i.action}</Badge></Td><Td className="text-[12.5px]">{i.detail}</Td></tr>)}
                    </Table>
                  </div>
                ) : <p className="text-[13px] text-muted-foreground">Nothing would change.</p>}
                {!result.applied && (
                  <div className="flex items-center justify-end gap-2">
                    {result.summary.error > 0 && <span className="text-[12.5px] text-destructive">Fix the errors first: nothing is imported while any item fails.</span>}
                    <Button size="sm" disabled={result.summary.error > 0 || result.summary.create + result.summary.update === 0} loading={run.isPending} onClick={() => run.mutate({ b: bundle, dry: false })}>
                      Apply {result.summary.create + result.summary.update} change{result.summary.create + result.summary.update === 1 ? "" : "s"}</Button>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
