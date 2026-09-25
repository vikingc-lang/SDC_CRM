"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Table, Tabs, Td } from "@/components/ui/extra";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import type { CustomFieldDef, GateRule, PipelineFull } from "@/lib/types";

export function CustomFieldsPanel() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["custom-fields"], queryFn: () => get<CustomFieldDef[]>("/admin/custom-fields") });
  const [f, setF] = useState({ entity: "account", key: "", label: "", field_type: "text", options: "", required: false });
  const create = useMutation({
    mutationFn: async () => (await api.post("/admin/custom-fields", { ...f, options: f.options.split(",").map((s) => s.trim()).filter(Boolean) })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["custom-fields"] }); setF({ ...f, key: "", label: "", options: "" }); toast.success("Field added"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: async (id: string) => api.delete(`/admin/custom-fields/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["custom-fields"] }); toast.success("Field removed; stored values are kept"); },
  });
  return (
    <Card>
      <CardHeader title="Custom fields" description="Typed fields stored in each record's JSONB metadata, validated on save and searchable." />
      <form className="grid gap-2 border-b px-5 pb-4 sm:grid-cols-[110px_1fr_1fr_110px_1fr_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <div><Label htmlFor="cf-e">Record</Label><Select id="cf-e" value={f.entity} onChange={(e) => setF({ ...f, entity: e.target.value })}>{["account", "contact", "deal"].map((x) => <option key={x}>{x}</option>)}</Select></div>
        <div><Label htmlFor="cf-l">Label</Label><Input id="cf-l" required value={f.label} onChange={(e) => setF({ ...f, label: e.target.value, key: f.key || "" })} /></div>
        <div><Label htmlFor="cf-k">Key</Label><Input id="cf-k" required pattern="[a-z][a-z0-9_]*" placeholder={f.label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "snake_case"} value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })} /></div>
        <div><Label htmlFor="cf-t">Type</Label><Select id="cf-t" value={f.field_type} onChange={(e) => setF({ ...f, field_type: e.target.value })}>{["text", "number", "date", "select", "boolean", "url"].map((x) => <option key={x}>{x}</option>)}</Select></div>
        <div><Label htmlFor="cf-o">Options (select)</Label><Input id="cf-o" disabled={f.field_type !== "select"} placeholder="A, B, C" value={f.options} onChange={(e) => setF({ ...f, options: e.target.value })} /></div>
        <Button type="submit" size="sm" loading={create.isPending}><Plus className="h-3.5 w-3.5" />Add</Button>
      </form>
      {!data ? <Skeleton className="m-5 h-24" /> : (
        <Table head={["Record", "Label", "Key", "Type", "Options", ""]}>
          {data.map((d) => (
            <tr key={d.id}>
              <Td className="capitalize">{d.entity}</Td><Td className="font-medium">{d.label}</Td><Td className="font-mono text-[12px]">{d.key}</Td>
              <Td><Badge>{d.field_type}</Badge></Td><Td className="text-[12.5px]">{d.options.join(", ") || "—"}</Td>
              <Td><Button size="sm" variant="ghost" aria-label={`Delete ${d.label}`} onClick={() => remove.mutate(d.id!)}><Trash2 className="h-3.5 w-3.5" /></Button></Td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}

const RULE_HELP: Record<string, string> = {
  min_contacts: '{"type":"min_contacts","count":2}', domain_verified: '{"type":"domain_verified"}', role_mapped: '{"type":"role_mapped","role":"Economic Buyer"}',
  activity_logged: '{"type":"activity_logged","activity_type":"meeting"}', field_present: '{"type":"field_present","field":"target_close_date"}',
  amount_approved: '{"type":"amount_approved"}', keyword: '{"type":"keyword","any":["security review"]}', pain_identified: '{"type":"pain_identified"}',
  signed_document: '{"type":"signed_document","doc_type":"order_form"}', any_of: '{"type":"any_of","rules":[...]}', loss_reason: '{"type":"loss_reason"}',
};

export function GatesPanel() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["pipelines"], queryFn: () => get<PipelineFull[]>("/pipelines") });
  const [pid, setPid] = useState("");
  const pipeline = data?.find((p) => p.id === pid) ?? data?.[0];
  const [stageId, setStageId] = useState("");
  const stage = pipeline?.stages.find((s) => s.id === stageId) ?? pipeline?.stages[0];
  const [json, setJson] = useState("");
  const [prob, setProb] = useState(0);
  useEffect(() => { if (stage) { setJson(JSON.stringify(stage.gate_rules, null, 2)); setProb(stage.default_probability); } }, [stage]);
  let parsed: GateRule[] | null = null;
  try { const v = JSON.parse(json); parsed = Array.isArray(v) ? v : null; } catch { parsed = null; }
  const save = useMutation({
    mutationFn: async () => (await api.put(`/pipelines/stages/${stage!.id}/gates`, { gate_rules: parsed, default_probability: prob })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["pipelines"] }); toast.success(`Gates saved for ${stage!.name}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (!data || !pipeline || !stage) return <Skeleton className="h-80 w-full" />;
  return (
    <Card>
      <CardHeader title="Pipelines & stage gates" description="Declarative rules evaluated before a deal can enter a stage. Managers can override with a logged reason." />
      <div className="px-5">
        <Tabs value={pipeline.id} onChange={(v) => { setPid(v); setStageId(""); }} tabs={data.map((p) => ({ value: p.id, label: p.name }))} className="mb-4" />
      </div>
      <div className="grid gap-4 px-5 pb-5 lg:grid-cols-[240px_1fr]">
        <ul className="space-y-1">
          {pipeline.stages.map((s) => (
            <li key={s.id}>
              <button onClick={() => setStageId(s.id)} className={`flex w-full items-center justify-between rounded-md px-3 py-1.5 text-left text-[13px] ${s.id === stage.id ? "bg-muted font-medium" : "hover:bg-muted/60"}`}>
                <span>{s.stage_order}. {s.name}</span><span className="text-[11.5px] text-muted-foreground">{s.gate_rules.length} rule{s.gate_rules.length === 1 ? "" : "s"}</span>
              </button>
            </li>
          ))}
        </ul>
        <div className="space-y-3">
          <div className="flex items-end gap-3">
            <div><Label htmlFor="g-prob">Default probability %</Label><Input id="g-prob" type="number" min={0} max={100} className="w-28" value={prob} onChange={(e) => setProb(Number(e.target.value))} /></div>
            <Button size="sm" disabled={!parsed} loading={save.isPending} onClick={() => save.mutate()}><Save className="h-3.5 w-3.5" />Save gates</Button>
            {!parsed && <span className="text-[12px] text-[color:var(--status-critical)]">Must be a JSON array</span>}
          </div>
          <Textarea aria-label="Gate rules JSON" rows={12} className="font-mono text-[12.5px]" value={json} onChange={(e) => setJson(e.target.value)} />
          <details className="text-[12px] text-muted-foreground">
            <summary className="cursor-pointer">Rule reference</summary>
            <ul className="mt-2 space-y-0.5 font-mono">{Object.values(RULE_HELP).map((r) => <li key={r}>{r}</li>)}</ul>
          </details>
        </div>
      </div>
    </Card>
  );
}
