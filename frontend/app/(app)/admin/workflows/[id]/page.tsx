"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Bell, FlaskConical, Globe, ListChecks, MessageSquare, PenLine, Plus, Radio, Save, Trash2, Zap } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { TEMPLATES, type WorkflowRule as Rule, type WorkflowAction as Action } from "@/components/admin/workflows";
import { type CatalogueField, FilterRow, IconX, nice } from "@/components/filters";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StatusPill, Table, Td } from "@/components/ui/extra";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { ROLE_LABELS } from "@/lib/me";
import { cn, relativeDays } from "@/lib/utils";

interface Entity {
  key: string; label: string; plural: string; watch: { key: string; label: string }[];
  settable: { key: string; label: string; kind: "user" | "enum"; options: string[] | null }[]; fields: CatalogueField[];
}
interface Meta { entities: Entity[]; periods: string[]; roles: string[]; priorities: string[]; users: { id: string; name: string; role: string }[] }
interface Run { id: string; record: string | null; trigger: string; status: string; detail: { action: string; ok: boolean; detail: string }[]; created_at: string }
interface DryRun {
  matched?: boolean; results?: { action: string; ok: boolean; detail: string }[];
  matching_count?: number; capped?: boolean; sample?: { id: string; name: string; results: { action: string; ok: boolean; detail: string }[] }[];
}

const ACTION_META: Record<Action["type"], { label: string; icon: typeof Bell }> = {
  create_task: { label: "Create a task", icon: ListChecks },
  notify: { label: "Send a notification", icon: Bell },
  update_field: { label: "Update a field", icon: PenLine },
  emit_event: { label: "Send an event to connected systems", icon: Radio },
  http_request: { label: "Call a webhook (HTTP POST)", icon: Globe },
  post_message: { label: "Post to Slack or Microsoft Teams", icon: MessageSquare },
};

function blank(entity: Entity): Omit<Rule, "id"> {
  return { name: "", description: "", enabled: false, source: entity.key, trigger: { type: "created" }, conditions: [],
    actions: [{ type: "notify", to: ["owner"], title: "" }] };
}

function newAction(type: Action["type"], entity: Entity): Action {
  if (type === "create_task") return { type, title: "", due_in_days: 1, priority: "normal", assign_to: "owner" };
  if (type === "notify") return { type, to: ["owner"], title: "" };
  if (type === "update_field") { const s = entity.settable[0]; return { type, field: s?.key ?? "", value: s?.options?.[0] ?? "" }; }
  if (type === "http_request") return { type, url: "" };
  if (type === "post_message") return { type, channel: "slack", webhook_url: "", text: "" };
  return { type, event: "" };
}

export default function WorkflowEditorPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const isNew = id === "new";
  const meta = useQuery({ queryKey: ["workflows", "meta"], queryFn: () => get<Meta>("/workflows/meta") });
  const existing = useQuery({ queryKey: ["workflows", id], queryFn: () => get<Rule>(`/workflows/${id}`), enabled: !isNew });
  const runs = useQuery({ queryKey: ["workflows", id, "runs"], queryFn: () => get<Run[]>(`/workflows/${id}/runs`), enabled: !isNew });
  const [rule, setRule] = useState<Omit<Rule, "id"> | null>(null);
  const [dry, setDry] = useState<DryRun | null>(null);

  useEffect(() => {
    if (rule || !meta.data) return;
    if (!isNew && existing.data) setRule(existing.data);
    if (isNew) {
      const t = new URLSearchParams(window.location.search).get("template");
      setRule(t && TEMPLATES[t] ? structuredClone(TEMPLATES[t]) : blank(meta.data.entities[0]));
    }
  }, [rule, meta.data, existing.data, isNew]);

  const save = useMutation({
    mutationFn: async () => isNew ? (await api.post<Rule>("/workflows", rule)).data : (await api.put<Rule>(`/workflows/${id}`, rule)).data,
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["workflows"] }); toast.success(`Saved "${r.name}"`); if (isNew) router.replace(`/admin/workflows/${r.id}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const test = useMutation({
    mutationFn: async () => (await api.post<DryRun>(`/workflows/${id}/test`, {})).data,
    onSuccess: setDry, onError: (e) => toast.error(errorMessage(e)),
  });
  const del = useMutation({
    mutationFn: async () => api.delete(`/workflows/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["workflows"] }); toast.success("Workflow deleted"); router.push("/admin?tab=workflows"); },
  });

  if (!meta.data || !rule) return <Skeleton className="h-[600px]" />;
  const entity = meta.data.entities.find((e) => e.key === rule.source)!;
  const set = (patch: Partial<Rule>) => setRule({ ...rule, ...patch });
  const setAction = (k: number, a: Action) => { const as = [...rule.actions]; as[k] = a; set({ actions: as }); };
  const people = [{ value: "owner", label: `The ${entity.label.toLowerCase()}'s owner` }, { value: "manager", label: "The owner's manager" },
    ...meta.data.users.map((u) => ({ value: `user:${u.id}`, label: u.name }))];
  const fieldKeys = entity.fields.map((f) => f.key);

  return (
    <div className="mx-auto max-w-4xl">
      <Link href="/admin?tab=workflows" className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />Workflows</Link>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold tracking-tight">{isNew ? "New workflow" : rule.name}</h1>
          <p className="mt-0.5 text-[13px] text-muted-foreground">When something happens to a record and it meets your conditions, Cirra runs the actions.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="mr-1 flex items-center gap-2 text-[13px]"><input id="wf-enabled" type="checkbox" checked={rule.enabled} onChange={(e) => set({ enabled: e.target.checked })} />On</label>
          {!isNew && <Button variant="ghost" size="sm" loading={del.isPending} onClick={() => del.mutate()}><Trash2 className="h-3.5 w-3.5" />Delete</Button>}
          {!isNew && <Button variant="outline" size="sm" loading={test.isPending} onClick={() => test.mutate()} title="Uses the saved version"><FlaskConical className="h-3.5 w-3.5" />Test</Button>}
          <Button size="sm" loading={save.isPending} onClick={() => save.mutate()}><Save className="h-3.5 w-3.5" />Save</Button>
        </div>
      </div>

      <div className="space-y-4">
        <Card>
          <CardBody className="grid gap-3 sm:grid-cols-2">
            <div><Label htmlFor="wf-name">Name</Label><Input id="wf-name" required maxLength={150} value={rule.name} onChange={(e) => set({ name: e.target.value })} placeholder="Follow up new web-form leads" /></div>
            <div><Label htmlFor="wf-desc">Description</Label><Input id="wf-desc" value={rule.description ?? ""} onChange={(e) => set({ description: e.target.value })} /></div>
          </CardBody>
        </Card>

        <Step n="When" title="Trigger">
          <div className="grid gap-3 sm:grid-cols-2">
            <div><Label htmlFor="wf-source">Record type</Label>
              <Select id="wf-source" value={rule.source} onChange={(e) => { const ent = meta.data!.entities.find((x) => x.key === e.target.value)!; setRule({ ...blank(ent), name: rule.name, description: rule.description, enabled: rule.enabled }); }}>
                {meta.data.entities.map((e) => <option key={e.key} value={e.key}>{e.plural}</option>)}
              </Select></div>
            <div><Label htmlFor="wf-trigger">Runs when</Label>
              <Select id="wf-trigger" value={rule.trigger.type} onChange={(e) => {
                const t = e.target.value as Rule["trigger"]["type"];
                set({ trigger: t === "updated" ? { type: t, fields: entity.watch.slice(0, 1).map((w) => w.key) } : t === "schedule" ? { type: t, repeat_after_days: null } : { type: t } });
              }}>
                <option value="created">A {entity.label.toLowerCase()} is created</option>
                {entity.watch.length > 0 && <option value="updated">Chosen fields change</option>}
                <option value="schedule">Hourly check of matching records</option>
              </Select></div>
          </div>
          {rule.trigger.type === "updated" && (
            <div className="mt-3 flex flex-wrap gap-2">
              {entity.watch.map((w) => (
                <label key={w.key} className="flex items-center gap-2 rounded-md border px-3 py-1.5 text-[13px]">
                  <input id={`wf-watch-${w.key}`} type="checkbox" checked={rule.trigger.fields?.includes(w.key) ?? false}
                    onChange={(e) => { const cur = rule.trigger.fields ?? []; set({ trigger: { ...rule.trigger, fields: e.target.checked ? [...cur, w.key] : cur.filter((x) => x !== w.key) } }); }} />{w.label}
                </label>
              ))}
            </div>
          )}
          {rule.trigger.type === "schedule" && (
            <div className="mt-3 flex flex-wrap items-center gap-2 text-[13px]">
              <span>Act on each matching record</span>
              <Select aria-label="Repeat" className="h-8 w-auto text-[13px]" value={rule.trigger.repeat_after_days ? "repeat" : "once"}
                onChange={(e) => set({ trigger: { ...rule.trigger, repeat_after_days: e.target.value === "repeat" ? 7 : null } })}>
                <option value="once">only once</option><option value="repeat">again after</option>
              </Select>
              {rule.trigger.repeat_after_days ? <><Input aria-label="Days" type="number" min={1} max={365} className="h-8 w-20" value={rule.trigger.repeat_after_days}
                onChange={(e) => set({ trigger: { ...rule.trigger, repeat_after_days: Number(e.target.value) || 1 } })} /><span>days while it still matches</span></> : null}
            </div>
          )}
        </Step>

        <Step n="If" title="Conditions" hint={rule.conditions.length ? "All must be true." : rule.trigger.type === "schedule" ? "Scheduled workflows need at least one condition." : "No conditions: runs for every record."}>
          <div className="space-y-2">
            {rule.conditions.map((f, k) => <FilterRow key={k} f={f} src={entity} periods={meta.data!.periods}
              onChange={(nf) => { const cs = [...rule.conditions]; cs[k] = nf; set({ conditions: cs }); }} onRemove={() => set({ conditions: rule.conditions.filter((_, j) => j !== k) })} />)}
            <button type="button" className="inline-flex items-center gap-1 text-[13px] text-primary hover:underline"
              onClick={() => { const f = entity.fields[0]; set({ conditions: [...rule.conditions, { field: f.key, op: f.ops[0], value: "" }] }); }}><Plus className="h-3.5 w-3.5" />Add condition</button>
          </div>
        </Step>

        <Step n="Then" title="Actions" hint={`Insert record values with {{field}}, e.g. {{${fieldKeys[0]}}}. Available: ${fieldKeys.join(", ")}.`}>
          <div className="space-y-3">
            {rule.actions.map((a, k) => {
              const Icon = ACTION_META[a.type].icon;
              return (
                <div key={k} className="rounded-lg border p-3">
                  <div className="mb-2 flex items-center gap-2">
                    <Icon className="h-4 w-4 text-primary" />
                    <Select aria-label="Action type" className="h-8 w-auto text-[13px]" value={a.type} onChange={(e) => setAction(k, newAction(e.target.value as Action["type"], entity))}>
                      {(Object.keys(ACTION_META) as Action["type"][]).filter((t) => t !== "update_field" || entity.settable.length).map((t) => <option key={t} value={t}>{ACTION_META[t].label}</option>)}
                    </Select>
                    <span className="flex-1" />
                    {rule.actions.length > 1 && <IconX label="Remove action" onClick={() => set({ actions: rule.actions.filter((_, j) => j !== k) })} />}
                  </div>
                  <ActionFields idp={`wf-a${k}`} a={a} entity={entity} people={people} meta={meta.data!} onChange={(na) => setAction(k, na)} />
                </div>
              );
            })}
            {rule.actions.length < 10 && (
              <button type="button" className="inline-flex items-center gap-1 text-[13px] text-primary hover:underline" onClick={() => set({ actions: [...rule.actions, newAction("create_task", entity)] })}>
                <Plus className="h-3.5 w-3.5" />Add action</button>
            )}
          </div>
        </Step>

        {dry && <DryRunCard dry={dry} onClose={() => setDry(null)} />}

        {!isNew && (
          <Card>
            <CardHeader title="Recent runs" description={`${existing.data?.enabled ? "On" : "Off"} · last 50 runs`} />
            {!runs.data ? <Skeleton className="m-5 h-24" /> : !runs.data.length ? <p className="px-5 pb-5 text-[13px] text-muted-foreground">No runs yet.</p> : (
              <Table head={["When", "Record", "Trigger", "Result", "Details"]} minWidth={720}>
                {runs.data.map((r) => (
                  <tr key={r.id}>
                    <Td className="whitespace-nowrap text-[12.5px] text-muted-foreground">{relativeDays(r.created_at)}</Td>
                    <Td className="text-[13px]">{r.record ?? "—"}</Td>
                    <Td className="text-[13px]">{nice(r.trigger)}</Td>
                    <Td><StatusPill status={r.status === "done" ? "completed" : "failed"} /></Td>
                    <Td className="text-[12.5px] text-muted-foreground">{r.detail.map((d) => d.detail).join(" · ")}</Td>
                  </tr>
                ))}
              </Table>
            )}
          </Card>
        )}
      </div>
    </div>
  );
}

function Step({ n, title, hint, children }: { n: string; title: string; hint?: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardBody>
        <div className="mb-3 flex items-baseline gap-2">
          <span className="rounded bg-primary-soft px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-primary">{n}</span>
          <h2 className="text-[14px] font-semibold">{title}</h2>
        </div>
        {hint && <p className="-mt-1 mb-3 break-words text-[12px] text-subtle">{hint}</p>}
        {children}
      </CardBody>
    </Card>
  );
}

function ActionFields({ idp, a, entity, people, meta, onChange }: {
  idp: string; a: Action; entity: Entity; people: { value: string; label: string }[]; meta: Meta; onChange: (a: Action) => void;
}) {
  if (a.type === "create_task") return (
    <div className="grid gap-2 sm:grid-cols-2">
      <div className="sm:col-span-2"><Label htmlFor={`${idp}-task-title`}>Task title</Label><Input id={`${idp}-task-title`} value={a.title} onChange={(e) => onChange({ ...a, title: e.target.value })} placeholder="Call {{name}}" /></div>
      <div className="sm:col-span-2"><Label htmlFor={`${idp}-task-desc`}>Description</Label><Textarea id={`${idp}-task-desc`} rows={2} value={a.description ?? ""} onChange={(e) => onChange({ ...a, description: e.target.value })} /></div>
      <div><Label htmlFor={`${idp}-task-assign`}>Assign to</Label><Select id={`${idp}-task-assign`} value={a.assign_to} onChange={(e) => onChange({ ...a, assign_to: e.target.value })}>{people.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}</Select></div>
      <div className="grid grid-cols-2 gap-2">
        <div><Label htmlFor={`${idp}-task-due`}>Due in (days)</Label><Input id={`${idp}-task-due`} type="number" min={0} max={365} value={a.due_in_days} onChange={(e) => onChange({ ...a, due_in_days: Math.max(0, Number(e.target.value) || 0) })} /></div>
        <div><Label htmlFor={`${idp}-task-prio`}>Priority</Label><Select id={`${idp}-task-prio`} value={a.priority} onChange={(e) => onChange({ ...a, priority: e.target.value })}>{meta.priorities.map((p) => <option key={p} value={p}>{nice(p)}</option>)}</Select></div>
      </div>
    </div>
  );
  if (a.type === "notify") {
    const roles = meta.roles.filter((r) => r !== "partner").map((r) => ({ value: `role:${r}`, label: `Everyone who is a ${ROLE_LABELS[r] ?? r}` }));
    const options = [...people.slice(0, 2), ...roles, ...people.slice(2)];
    return (
      <div className="space-y-2">
        <div><Label>Send to</Label>
          <div className="flex flex-wrap gap-1.5">
            {a.to.map((t) => (
              <Badge key={t} tone="outline" className="gap-1">{options.find((o) => o.value === t)?.label ?? t}
                <button type="button" aria-label="Remove recipient" className="text-muted-foreground hover:text-foreground" onClick={() => onChange({ ...a, to: a.to.filter((x) => x !== t) })}>×</button></Badge>
            ))}
            <Select aria-label="Add recipient" className="h-7 w-auto text-[12.5px]" value="" onChange={(e) => e.target.value && onChange({ ...a, to: [...a.to, e.target.value] })}>
              <option value="">Add…</option>{options.filter((o) => !a.to.includes(o.value)).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </Select>
          </div></div>
        <div><Label htmlFor={`${idp}-msg`}>Message</Label><Input id={`${idp}-msg`} value={a.title} onChange={(e) => onChange({ ...a, title: e.target.value })} placeholder="{{title}} needs attention" /></div>
        <div><Label htmlFor={`${idp}-body`}>Details (optional)</Label><Input id={`${idp}-body`} value={a.body ?? ""} onChange={(e) => onChange({ ...a, body: e.target.value })} /></div>
      </div>
    );
  }
  if (a.type === "update_field") {
    const spec = entity.settable.find((s) => s.key === a.field) ?? entity.settable[0];
    return (
      <div className="grid gap-2 sm:grid-cols-2">
        <div><Label htmlFor={`${idp}-field`}>Field</Label><Select id={`${idp}-field`} value={a.field} onChange={(e) => { const s = entity.settable.find((x) => x.key === e.target.value)!; onChange({ ...a, field: s.key, value: s.options?.[0] ?? "" }); }}>
          {entity.settable.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}</Select></div>
        <div><Label htmlFor={`${idp}-value`}>New value</Label>
          <Select id={`${idp}-value`} value={a.value} onChange={(e) => onChange({ ...a, value: e.target.value })}>
            {spec?.kind === "user" ? <><option value="">Choose a user…</option>{meta.users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</>
              : spec?.options?.map((o) => <option key={o} value={o}>{nice(o)}</option>)}
          </Select></div>
      </div>
    );
  }
  if (a.type === "http_request") return (
    <div>
      <Label htmlFor={`${idp}-url`}>Endpoint URL</Label>
      <Input id={`${idp}-url`} type="url" value={a.url} onChange={(e) => onChange({ ...a, url: e.target.value.trim() })} placeholder="https://erp.example.com/hooks/cirra" />
      <p className="mt-1 text-[12px] text-subtle">Cirra POSTs the record as JSON: workflow name, record id, trigger and every field. Any 2xx response counts as success; the response is shown in the run log.</p>
    </div>
  );
  if (a.type === "post_message") return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-[150px_1fr]">
        <div><Label htmlFor={`${idp}-chan`}>Where</Label>
          <Select id={`${idp}-chan`} value={a.channel} onChange={(e) => onChange({ ...a, channel: e.target.value as "slack" | "teams" })}>
            <option value="slack">Slack</option><option value="teams">Microsoft Teams</option></Select></div>
        <div><Label htmlFor={`${idp}-hook`}>Incoming webhook URL</Label>
          <Input id={`${idp}-hook`} type="url" value={a.webhook_url} onChange={(e) => onChange({ ...a, webhook_url: e.target.value.trim() })}
            placeholder={a.channel === "slack" ? "https://hooks.slack.com/services/…" : "https://….webhook.office.com/…"} /></div>
      </div>
      <div><Label htmlFor={`${idp}-text`}>Message</Label>
        <Textarea id={`${idp}-text`} rows={2} value={a.text} onChange={(e) => onChange({ ...a, text: e.target.value })} placeholder="{{title}} moved to {{stage}} ({{amount_usd}})" /></div>
      <p className="text-[12px] text-subtle">Create the incoming webhook in the {a.channel === "slack" ? "Slack app's settings" : "Teams channel's Workflows"} and paste its https URL. Placeholders fill in record values.</p>
    </div>
  );
  return (
    <div>
      <Label htmlFor={`${idp}-event`}>Event name</Label>
      <div className="flex items-center gap-1 text-[13px]"><span className="text-muted-foreground">workflow.</span>
        <Input id={`${idp}-event`} value={a.event} onChange={(e) => onChange({ ...a, event: e.target.value.toLowerCase() })} placeholder="deal.big_stage_move" /></div>
      <p className="mt-1 text-[12px] text-subtle">Delivered through the integration outbox to connected SDC modules and any configured webhooks.</p>
    </div>
  );
}

function DryRunCard({ dry, onClose }: { dry: DryRun; onClose: () => void }) {
  const lines = (rs: { action: string; ok: boolean; detail: string }[]) => (
    <ul className="space-y-0.5">{rs.map((r, k) => <li key={k} className={cn("text-[13px]", !r.ok && "text-destructive")}><Zap className="mr-1 inline h-3 w-3" />{r.detail}</li>)}</ul>
  );
  return (
    <Card className="border-primary/40">
      <CardHeader title="Test run" icon={<FlaskConical className="h-4 w-4" />} description="Nothing was changed. This is what the saved workflow would do right now."
        action={<Button variant="ghost" size="sm" onClick={onClose}>Close</Button>} />
      <CardBody className="space-y-3">
        {dry.matching_count !== undefined && (
          <>
            <p className="text-[13px]">{dry.matching_count}{dry.capped ? "+" : ""} record{dry.matching_count === 1 ? "" : "s"} match the conditions now.{dry.sample?.length ? " First few:" : ""}</p>
            {dry.sample?.map((s) => <div key={s.id} className="rounded-md bg-muted p-2.5"><p className="mb-1 text-[13px] font-medium">{s.name}</p>{lines(s.results)}</div>)}
          </>
        )}
      </CardBody>
    </Card>
  );
}
