"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Briefcase, Copy, Mail, MessageSquare, Play, Plug, Trash2, Users } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Label, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { API_URL, api, errorMessage, get } from "@/lib/api";
import { ROLE_LABELS } from "@/lib/me";
import { relativeDays } from "@/lib/utils";

interface FieldSpec { key: string; label: string; secret?: boolean; required?: boolean; placeholder?: string; kind?: "segment" | "role" | "bool" }
interface Spec { kind: string; label: string; category: string; summary: string; fields: FieldSpec[]; events: boolean }
interface Catalog { connectors: Spec[]; events: { key: string; label: string }[] }
interface Connector {
  id: string; kind: string; label: string; name: string; config: Record<string, unknown>; active: boolean; secrets_set: string[];
  state: Record<string, unknown>; last_run_at: string | null; last_error: string | null; created_at: string;
}

const ICONS: Record<string, typeof Plug> = { slack: MessageSquare, teams: Users, mailchimp: Mail, bamboohr: Briefcase };

function ConnectorDialog({ spec, catalog, existing, onClose }: { spec: Spec; catalog: Catalog; existing?: Connector; onClose: () => void }) {
  const qc = useQueryClient();
  const segments = useQuery({ queryKey: ["segments"], queryFn: () => get<{ id: string; name: string; object: string }[]>("/segments"),
    enabled: spec.fields.some((f) => f.kind === "segment") });
  const [name, setName] = useState(existing?.name ?? spec.label);
  const [values, setValues] = useState<Record<string, unknown>>({ ...(existing?.config ?? {}), events: existing?.config.events ?? ["deal.closed_won"] });
  const save = useMutation({
    mutationFn: async () => (existing
      ? await api.patch<Connector & { message: string | null }>(`/admin/connectors/${existing.id}`, { name, values })
      : await api.post<Connector & { message: string }>("/admin/connectors", { kind: spec.kind, name, values })).data,
    onSuccess: (c) => { qc.invalidateQueries({ queryKey: ["connectors"] }); toast.success(c.message ?? "Connector saved"); onClose(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const events = (values.events as string[]) ?? [];
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={`Connect ${spec.label}`} className="max-w-lg">
        <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <h2 className="text-[15px] font-semibold">{existing ? `Edit ${existing.name}` : `Connect ${spec.label}`}</h2>
          <p className="text-[13px] text-muted-foreground">{spec.summary} Cirra checks the connection before saving; secrets are encrypted and never shown again.</p>
          <div><Label htmlFor="cn-name">Name</Label><Input id="cn-name" required value={name} onChange={(e) => setName(e.target.value)} /></div>
          {spec.fields.map((f) => (
            <div key={f.key}>
              {f.kind === "bool" ? (
                <label className="flex items-center gap-2 text-[13px]">
                  <input type="checkbox" checked={!!values[f.key]} onChange={(e) => setValues({ ...values, [f.key]: e.target.checked })} />{f.label}
                </label>
              ) : (
                <>
                  <Label htmlFor={`cn-${f.key}`}>{f.label}{f.required ? "" : " (optional)"}</Label>
                  {f.kind === "segment" ? (
                    <Select id={`cn-${f.key}`} required={f.required} value={String(values[f.key] ?? "")} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}>
                      <option value="">Choose a contact segment…</option>
                      {segments.data?.filter((s) => s.object === "contact").map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                    </Select>
                  ) : f.kind === "role" ? (
                    <Select id={`cn-${f.key}`} value={String(values[f.key] ?? "sdr")} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}>
                      {Object.entries(ROLE_LABELS).filter(([k]) => !["super_admin", "partner"].includes(k)).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </Select>
                  ) : (
                    <Input id={`cn-${f.key}`} type={f.secret ? "password" : "text"} autoComplete="off"
                      required={f.required && !(f.secret && existing?.secrets_set.includes(f.key))}
                      placeholder={f.secret && existing?.secrets_set.includes(f.key) ? "Stored. Leave empty to keep it" : f.placeholder}
                      value={String(values[f.key] ?? "")} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
                  )}
                </>
              )}
            </div>
          ))}
          {spec.events && (
            <fieldset>
              <legend className="mb-1 text-[13px] font-medium">Post these events</legend>
              <div className="grid grid-cols-2 gap-1">
                {catalog.events.map((ev) => (
                  <label key={ev.key} className="flex items-center gap-2 text-[12.5px]">
                    <input type="checkbox" checked={events.includes(ev.key)}
                      onChange={(e) => setValues({ ...values, events: e.target.checked ? [...events, ev.key] : events.filter((x) => x !== ev.key) })} />{ev.label}
                  </label>
                ))}
              </div>
            </fieldset>
          )}
          {spec.kind === "slack" && (
            <p className="rounded bg-muted p-2 text-[12px]">For the <code>/cirra</code> command, create a slash command in your Slack app pointing to <code>{API_URL}/api/v1/public/slack/command</code>. People are matched to Cirra users by email and see only what their role allows.</p>
          )}
          <div className="flex justify-end gap-2"><Button type="button" variant="ghost" size="sm" onClick={onClose}>Cancel</Button><Button type="submit" size="sm" loading={save.isPending}>Test and save</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function stateLine(c: Connector): string {
  const s = c.state as Record<string, unknown>;
  if (c.kind === "slack" || c.kind === "teams") return `${Number(s.posted ?? 0)} updates posted`;
  if (c.kind === "mailchimp") return `${Number(s.pushed ?? 0)} synced · ${Number(s.skipped ?? 0)} without consent skipped · ${Number(s.opted_out ?? 0)} unsubscribes brought back`;
  if (c.kind === "bamboohr" && s.last_sync) {
    const x = s.last_sync as Record<string, number>;
    return `${x.created} created · ${x.deactivated} deactivated · ${x.managers} reporting lines updated${x.over_limit ? ` · ${x.over_limit} over the user limit` : ""}`;
  }
  return "";
}

export function ConnectorsPanel() {
  const qc = useQueryClient();
  const catalog = useQuery({ queryKey: ["connector-catalog"], queryFn: () => get<Catalog>("/admin/connectors/catalog") });
  const list = useQuery({ queryKey: ["connectors"], queryFn: () => get<Connector[]>("/admin/connectors") });
  const [open, setOpen] = useState<{ spec: Spec; existing?: Connector } | null>(null);
  const run = useMutation({
    mutationFn: async (id: string) => (await api.post<Connector & { result: Record<string, unknown> }>(`/admin/connectors/${id}/run`)).data,
    onSuccess: (c) => { qc.invalidateQueries({ queryKey: ["connectors"] }); c.last_error ? toast.error(c.last_error) : toast.success(`${c.name}: done`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const toggle = useMutation({
    mutationFn: async (c: Connector) => (await api.patch(`/admin/connectors/${c.id}`, { active: !c.active })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["connectors"] }),
  });
  const remove = useMutation({
    mutationFn: async (id: string) => api.delete(`/admin/connectors/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["connectors"] }); toast.success("Connector removed"); },
  });
  if (!catalog.data) return <Skeleton className="h-64 w-full" />;
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader title="Available connectors" icon={<Plug className="h-4 w-4 text-muted-foreground" />}
          description="Pre-built integrations. Anything else can use the REST API, webhooks and API keys (API & webhooks tab)." />
        <CardBody className="grid gap-3 sm:grid-cols-2">
          {catalog.data.connectors.map((s) => {
            const Icon = ICONS[s.kind] ?? Plug;
            const count = list.data?.filter((c) => c.kind === s.kind).length ?? 0;
            return (
              <div key={s.kind} className="flex flex-col gap-2 rounded-md border p-3">
                <div className="flex items-center gap-2"><Icon className="h-4 w-4 text-primary" /><span className="font-medium">{s.label}</span><Badge tone="outline">{s.category}</Badge>
                  {count > 0 && <Badge tone="good">connected</Badge>}</div>
                <p className="flex-1 text-[12.5px] text-muted-foreground">{s.summary}</p>
                <Button size="sm" variant="outline" className="self-start" onClick={() => setOpen({ spec: s })}>Connect {s.label}</Button>
              </div>
            );
          })}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Connected" />
        <CardBody>
          {list.data && !list.data.length && <p className="text-[13px] text-muted-foreground">Nothing connected yet.</p>}
          <ul className="divide-y">
            {list.data?.map((c) => {
              const spec = catalog.data!.connectors.find((s) => s.kind === c.kind)!;
              const Icon = ICONS[c.kind] ?? Plug;
              return (
                <li key={c.id} className="flex flex-wrap items-center gap-3 py-3">
                  <Icon className="h-4 w-4 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <p className="text-[13.5px] font-medium">{c.name} <span className="text-[12px] font-normal text-muted-foreground">· {c.label}</span>{!c.active && <Badge tone="outline" className="ml-1.5">paused</Badge>}</p>
                    <p className="text-[12px] text-muted-foreground">{stateLine(c)}{c.last_run_at ? ` · last run ${relativeDays(c.last_run_at)}` : ""}</p>
                    {c.last_error && <p className="text-[12px] text-destructive">{c.last_error}</p>}
                  </div>
                  <div className="flex gap-1">
                    <Button size="sm" variant="outline" loading={run.isPending && run.variables === c.id} onClick={() => run.mutate(c.id)}><Play className="h-3.5 w-3.5" />Run now</Button>
                    <Button size="sm" variant="outline" onClick={() => setOpen({ spec, existing: c })}>Edit</Button>
                    <Button size="sm" variant="ghost" onClick={() => toggle.mutate(c)}>{c.active ? "Pause" : "Resume"}</Button>
                    <Button size="icon" variant="ghost" aria-label={`Remove ${c.name}`} onClick={() => remove.mutate(c.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
                  </div>
                </li>
              );
            })}
          </ul>
          {list.data?.some((c) => c.kind === "slack") && (
            <div className="mt-3 flex items-center gap-2 text-[12px] text-muted-foreground">
              Slash command URL: <code className="truncate">{API_URL}/api/v1/public/slack/command</code>
              <Button size="icon" variant="ghost" aria-label="Copy slash command URL" onClick={() => navigator.clipboard?.writeText(`${API_URL}/api/v1/public/slack/command`)}><Copy className="h-3.5 w-3.5" /></Button>
            </div>
          )}
        </CardBody>
      </Card>
      {open && <ConnectorDialog spec={open.spec} catalog={catalog.data} existing={open.existing} onClose={() => setOpen(null)} />}
    </div>
  );
}
