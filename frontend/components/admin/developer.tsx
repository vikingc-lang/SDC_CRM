"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, KeyRound, Plus, RefreshCw, Send, Trash2, Webhook } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { CopyButton } from "@/components/leads";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Table, Td } from "@/components/ui/extra";
import { Input, Label, Select } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { API_URL, api, errorMessage, get } from "@/lib/api";
import { ROLE_LABELS } from "@/lib/me";
import { relativeDays, shortDate } from "@/lib/utils";

interface ApiKey { id: string; name: string; prefix: string; user_id: string; user: string | null; read_only: boolean; expires_at: string | null; last_used_at: string | null; created_at: string; created_by: string | null; state: "active" | "revoked" | "expired" }
interface Hook { id: string; name: string; url: string; event_types: string[]; active: boolean; disabled_reason: string | null; consecutive_failures: number; last_success_at: string | null; last_failure_at: string | null; stats: Record<string, number> }
interface Delivery { id: number; event_id: number | null; event_type: string; status: "pending" | "success" | "failed" | "dead"; attempts: number; next_attempt_at: string | null; response_code: number | null; error: string | null; duration_ms: number | null; created_at: string; delivered_at: string | null }
type U = { id: string; full_name: string; role: string };

const STATE_TONE = { active: "good", revoked: "neutral", expired: "warning" } as const;
const DELIVERY_TONE = { success: "good", pending: "neutral", failed: "warning", dead: "critical" } as const;

/** Shown once: the secret can't be retrieved again. */
function SecretOnce({ label, value, onDone }: { label: string; value: string; onDone: () => void }) {
  return (
    <Dialog open onOpenChange={(o) => !o && onDone()}>
      <DialogContent title={label} className="max-w-lg">
        <div className="space-y-3 p-5">
          <h2 className="text-[15px] font-semibold">{label}</h2>
          <p className="text-[13px] text-muted-foreground">Copy it now. For security it is stored only as a hash and won’t be shown again.</p>
          <div className="flex items-center gap-2 rounded-md border bg-muted/40 p-2"><code className="min-w-0 flex-1 break-all font-mono text-[12.5px]">{value}</code><CopyButton text={value} /></div>
          <div className="flex justify-end"><Button size="sm" onClick={onDone}>Done</Button></div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ---- API keys ----------------------------------------------------------------------------------------------

export function ApiKeysPanel() {
  const qc = useQueryClient();
  const keys = useQuery({ queryKey: ["developer", "keys"], queryFn: () => get<ApiKey[]>("/developer/api-keys") });
  const users = useQuery({ queryKey: ["users"], queryFn: () => get<U[]>("/users") });
  const [f, setF] = useState<{ name: string; user_id: string; read_only: boolean; expires_at: string } | null>(null);
  const [issued, setIssued] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: async () => (await api.post<{ key: string }>("/developer/api-keys", { name: f!.name, user_id: f!.user_id, read_only: f!.read_only,
      expires_at: f!.expires_at ? new Date(`${f!.expires_at}T23:59:59`).toISOString() : null })).data,
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["developer", "keys"] }); setF(null); setIssued(r.key); },
  });
  const revoke = useMutation({
    mutationFn: async (id: string) => (await api.post(`/developer/api-keys/${id}/revoke`)).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["developer", "keys"] }); toast.success("Key revoked. Calls using it now fail."); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card className="overflow-hidden">
      <CardHeader title="API keys" icon={<KeyRound className="h-4 w-4" />}
        description="A key acts as the person it belongs to, with their role's permissions and record scope. Send it as a Bearer token or an X-API-Key header."
        action={<div className="flex gap-2">
          <a href={`${API_URL}/docs`} target="_blank" rel="noreferrer"><Button size="sm" variant="outline"><BookOpen className="h-3.5 w-3.5" />API reference</Button></a>
          <Button size="sm" onClick={() => setF({ name: "", user_id: "", read_only: true, expires_at: "" })}><Plus className="h-3.5 w-3.5" />New key</Button></div>} />
      {!keys.data ? <Skeleton className="m-5 h-24" /> : !keys.data.length ? <EmptyState icon={<KeyRound className="h-4 w-4" />} title="No API keys" description="Create one for each system that connects, so you can revoke them separately." /> : (
        <Table head={["Key", "Acts as", "Access", "Last used", "Expires", "State", ""]} minWidth={860}>
          {keys.data.map((k) => (
            <tr key={k.id}>
              <Td><span className="font-medium">{k.name}</span><code className="block font-mono text-[12px] text-muted-foreground">{k.prefix}…</code></Td>
              <Td className="text-[13px]">{k.user}</Td>
              <Td>{k.read_only ? <Badge tone="neutral">Read-only</Badge> : <Badge tone="primary">Read & write</Badge>}</Td>
              <Td className="text-[12.5px] text-muted-foreground">{k.last_used_at ? relativeDays(k.last_used_at) : "Never"}</Td>
              <Td className="text-[12.5px] text-muted-foreground">{k.expires_at ? shortDate(k.expires_at, true) : "Never"}</Td>
              <Td><Badge tone={STATE_TONE[k.state]}>{k.state[0].toUpperCase() + k.state.slice(1)}</Badge></Td>
              <Td>{k.state === "active" && <Button size="sm" variant="ghost" onClick={() => confirm(`Revoke ${k.name}? Systems using it stop working.`) && revoke.mutate(k.id)}>Revoke</Button>}</Td>
            </tr>
          ))}
        </Table>
      )}
      {f && (
        <Dialog open onOpenChange={(o) => !o && setF(null)}>
          <DialogContent title="New API key" className="max-w-md">
            <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
              <h2 className="text-[15px] font-semibold">New API key</h2>
              <div><Label htmlFor="ak-name">Name</Label><Input id="ak-name" required minLength={2} maxLength={100} placeholder="e.g. Data warehouse sync" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
              <div><Label htmlFor="ak-user">Acts as</Label><Select id="ak-user" required value={f.user_id} onChange={(e) => setF({ ...f, user_id: e.target.value })}>
                <option value="">Choose a person…</option>{users.data?.map((u) => <option key={u.id} value={u.id}>{u.full_name} · {ROLE_LABELS[u.role] ?? u.role}</option>)}</Select>
                <p className="mt-1 text-[12px] text-subtle">Tip: create a dedicated integration user with only the access the system needs.</p></div>
              <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={f.read_only} onChange={(e) => setF({ ...f, read_only: e.target.checked })} />Read-only (GET requests only)</label>
              <div className="w-48"><Label htmlFor="ak-exp">Expires (optional)</Label><Input id="ak-exp" type="date" value={f.expires_at} onChange={(e) => setF({ ...f, expires_at: e.target.value })} /></div>
              {create.isError && <p className="text-sm text-destructive">{errorMessage(create.error)}</p>}
              <div className="flex justify-end gap-2"><Button type="button" variant="ghost" size="sm" onClick={() => setF(null)}>Cancel</Button><Button type="submit" size="sm" loading={create.isPending}>Create key</Button></div>
            </form>
          </DialogContent>
        </Dialog>
      )}
      {issued && <SecretOnce label="Your new API key" value={issued} onDone={() => setIssued(null)} />}
    </Card>
  );
}

// ---- webhooks ----------------------------------------------------------------------------------------------

export function WebhooksPanel() {
  const qc = useQueryClient();
  const hooks = useQuery({ queryKey: ["developer", "hooks"], queryFn: () => get<Hook[]>("/developer/webhooks") });
  const events = useQuery({ queryKey: ["developer", "events"], queryFn: () => get<{ type: string; description: string }[]>("/developer/events") });
  const [edit, setEdit] = useState<Hook | "new" | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const done = () => qc.invalidateQueries({ queryKey: ["developer"] });
  const test = useMutation({
    mutationFn: async (id: string) => (await api.post<Delivery>(`/developer/webhooks/${id}/test`)).data,
    onSuccess: (d) => { done(); d.status === "success" ? toast.success(`Test delivered (HTTP ${d.response_code}, ${d.duration_ms} ms)`) : toast.error(`Test failed: ${d.error ?? `HTTP ${d.response_code}`}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const toggle = useMutation({
    mutationFn: async (h: Hook) => (await api.put(`/developer/webhooks/${h.id}`, { name: h.name, url: h.url, event_types: h.event_types, active: !h.active })).data,
    onSuccess: done, onError: (e) => toast.error(errorMessage(e)),
  });
  const rotate = useMutation({
    mutationFn: async (id: string) => (await api.post<{ secret: string }>(`/developer/webhooks/${id}/rotate-secret`)).data,
    onSuccess: (r) => setSecret(r.secret),
  });
  const del = useMutation({ mutationFn: async (id: string) => (await api.delete(`/developer/webhooks/${id}`)).data, onSuccess: done });
  const run = useMutation({
    mutationFn: async () => (await api.post<{ queued: number; attempted: number; succeeded: number }>("/developer/webhooks/run")).data,
    onSuccess: (r) => { done(); toast.success(`${r.queued} queued, ${r.succeeded} of ${r.attempted} delivered`); },
  });
  return (
    <Card className="overflow-hidden">
      <CardHeader title="Webhooks" icon={<Webhook className="h-4 w-4" />}
        description="Cirra POSTs matching events as JSON, signed in X-Cirra-Signature (HMAC-SHA256 of timestamp.body). Failures retry after 1 min, 5 min, 30 min, 2 h and 12 h."
        action={<div className="flex gap-2">
          <Button size="sm" variant="outline" loading={run.isPending} onClick={() => run.mutate()}><RefreshCw className="h-3.5 w-3.5" />Deliver now</Button>
          <Button size="sm" onClick={() => setEdit("new")}><Plus className="h-3.5 w-3.5" />New webhook</Button></div>} />
      {!hooks.data ? <Skeleton className="m-5 h-24" /> : !hooks.data.length ? <EmptyState icon={<Webhook className="h-4 w-4" />} title="No webhooks" description="Send events such as deal.closed_won or case.created to other systems as they happen." /> : (
        <Table head={["Endpoint", "Events", "Last 24 h", "Health", "On", ""]} minWidth={900}>
          {hooks.data.map((h) => (
            <tr key={h.id}>
              <Td><button className="text-left font-medium hover:text-primary hover:underline" onClick={() => setOpen(open === h.id ? null : h.id)}>{h.name}</button>
                <span className="block max-w-[280px] truncate font-mono text-[12px] text-muted-foreground">{h.url}</span></Td>
              <Td className="text-[12.5px]">{h.event_types.join(", ")}</Td>
              <Td className="text-[12.5px] tabular">{(h.stats.success ?? 0)} ok{h.stats.failed ? ` · ${h.stats.failed} retrying` : ""}{h.stats.dead ? ` · ${h.stats.dead} dead` : ""}</Td>
              <Td className="text-[12.5px]">{h.disabled_reason ? <span className="text-destructive">{h.disabled_reason}</span>
                : h.consecutive_failures ? <Badge tone="warning">{h.consecutive_failures} failing</Badge>
                : h.last_success_at ? `OK ${relativeDays(h.last_success_at)}` : "No deliveries yet"}</Td>
              <Td><input type="checkbox" aria-label={`${h.active ? "Turn off" : "Turn on"} ${h.name}`} checked={h.active} disabled={toggle.isPending} onChange={() => toggle.mutate(h)} /></Td>
              <Td><span className="flex justify-end gap-1">
                <Button size="sm" variant="ghost" loading={test.isPending && test.variables === h.id} onClick={() => test.mutate(h.id)}><Send className="h-3.5 w-3.5" />Test</Button>
                <Button size="sm" variant="ghost" onClick={() => setEdit(h)}>Edit</Button>
                <Button size="sm" variant="ghost" onClick={() => confirm("Rotate the signing secret? The receiver must switch to the new one.") && rotate.mutate(h.id)}>Rotate secret</Button>
                <Button variant="ghost" size="icon" aria-label={`Delete ${h.name}`} onClick={() => confirm(`Delete ${h.name}?`) && del.mutate(h.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
              </span></Td>
            </tr>
          ))}
        </Table>
      )}
      {open && <Deliveries id={open} />}
      {edit && <HookDialog hook={edit === "new" ? null : edit} catalogue={events.data ?? []} onClose={() => setEdit(null)} onSecret={setSecret} />}
      {secret && <SecretOnce label="Signing secret" value={secret} onDone={() => setSecret(null)} />}
    </Card>
  );
}

function Deliveries({ id }: { id: string }) {
  const qc = useQueryClient();
  const rows = useQuery({ queryKey: ["developer", "deliveries", id], queryFn: () => get<Delivery[]>(`/developer/webhooks/${id}/deliveries`) });
  const retry = useMutation({
    mutationFn: async (d: number) => (await api.post<Delivery>(`/developer/deliveries/${d}/retry`)).data,
    onSuccess: (d) => { qc.invalidateQueries({ queryKey: ["developer"] }); d.status === "success" ? toast.success("Delivered") : toast.error(d.error ?? `HTTP ${d.response_code}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <div className="border-t bg-muted/20">
      <p className="px-5 pt-3 text-[12px] font-medium uppercase tracking-wide text-muted-foreground">Recent deliveries</p>
      {!rows.data ? <Skeleton className="m-5 h-16" /> : !rows.data.length ? <p className="px-5 py-3 text-[13px] text-muted-foreground">Nothing sent yet.</p> : (
        <Table head={["#", "Event", "Status", "Attempts", "Response", "When", ""]} minWidth={760}>
          {rows.data.map((d) => (
            <tr key={d.id}>
              <Td className="tabular text-[12.5px]">{d.id}</Td>
              <Td className="font-mono text-[12.5px]">{d.event_type}</Td>
              <Td><Badge tone={DELIVERY_TONE[d.status]}>{d.status}</Badge>{d.next_attempt_at && <span className="block text-[11.5px] text-subtle">retry {relativeDays(d.next_attempt_at)}</span>}</Td>
              <Td className="tabular text-[12.5px]">{d.attempts}</Td>
              <Td className="max-w-[220px] truncate text-[12.5px]" >{d.response_code ? `HTTP ${d.response_code}` : ""}{d.error ? ` ${d.error}` : ""}{d.duration_ms != null ? ` · ${d.duration_ms} ms` : ""}</Td>
              <Td className="text-[12.5px] text-muted-foreground">{relativeDays(d.created_at)}</Td>
              <Td>{d.event_id != null && d.status !== "success" && <Button size="sm" variant="ghost" loading={retry.isPending && retry.variables === d.id} onClick={() => retry.mutate(d.id)}>Retry</Button>}</Td>
            </tr>
          ))}
        </Table>
      )}
    </div>
  );
}

function HookDialog({ hook, catalogue, onClose, onSecret }: { hook: Hook | null; catalogue: { type: string; description: string }[]; onClose: () => void; onSecret: (s: string) => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ name: hook?.name ?? "", url: hook?.url ?? "", events: hook?.event_types ?? ["*"], active: hook?.active ?? true });
  const [custom, setCustom] = useState("");
  const all = f.events.includes("*");
  const toggle = (t: string) => setF({ ...f, events: f.events.includes(t) ? f.events.filter((x) => x !== t) : [...f.events.filter((x) => x !== "*"), t] });
  const save = useMutation({
    mutationFn: async () => {
      const body = { name: f.name, url: f.url, event_types: f.events, active: f.active };
      return hook ? (await api.put(`/developer/webhooks/${hook.id}`, body)).data : (await api.post<{ secret: string }>("/developer/webhooks", body)).data;
    },
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["developer"] }); onClose(); if (!hook) onSecret((r as { secret: string }).secret); else toast.success("Webhook saved"); },
  });
  const groups = Array.from(new Set(catalogue.map((e) => e.type.split(".")[0])));
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={hook ? "Edit webhook" : "New webhook"} className="max-w-xl">
        <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <h2 className="text-[15px] font-semibold">{hook ? `Edit ${hook.name}` : "New webhook"}</h2>
          <div><Label htmlFor="wh-name">Name</Label><Input id="wh-name" required minLength={2} maxLength={100} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
          <div><Label htmlFor="wh-url">Endpoint URL</Label><Input id="wh-url" required type="url" placeholder="https://example.com/hooks/cirra" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} /></div>
          <div>
            <p className="mb-1 text-[13px] font-medium">Events</p>
            <label className="mb-2 flex items-center gap-2 text-[13px]"><input type="checkbox" checked={all} onChange={() => setF({ ...f, events: all ? [] : ["*"] })} />All events, including custom workflow events</label>
            {!all && <div className="max-h-56 space-y-2 overflow-y-auto rounded-md border p-2">
              {groups.map((g) => (
                <div key={g}>
                  <label className="flex items-center gap-2 text-[12.5px] font-medium"><input type="checkbox" checked={f.events.includes(`${g}.*`)} onChange={() => toggle(`${g}.*`)} />Every {g} event</label>
                  {!f.events.includes(`${g}.*`) && <div className="ml-5 grid gap-0.5">{catalogue.filter((e) => e.type.startsWith(`${g}.`)).map((e) => (
                    <label key={e.type} className="flex items-center gap-2 text-[12.5px]"><input type="checkbox" checked={f.events.includes(e.type)} onChange={() => toggle(e.type)} />
                      <code className="font-mono">{e.type}</code><span className="text-muted-foreground">{e.description}</span></label>))}</div>}
                </div>
              ))}
              <div className="flex gap-2 pt-1"><Input aria-label="Custom event pattern" className="h-8" placeholder="Custom, e.g. deal.big_stage_move" value={custom} onChange={(e) => setCustom(e.target.value)} />
                <Button type="button" size="sm" variant="outline" disabled={!custom.trim()} onClick={() => { toggle(custom.trim().toLowerCase()); setCustom(""); }}>Add</Button></div>
              {f.events.filter((e) => !catalogue.some((c) => c.type === e) && !e.endsWith(".*")).map((e) => <Badge key={e} tone="outline">{e}<button type="button" className="ml-1" aria-label={`Remove ${e}`} onClick={() => toggle(e)}>×</button></Badge>)}
            </div>}
          </div>
          {hook && <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} />Active</label>}
          {save.isError && <p className="text-sm text-destructive">{errorMessage(save.error)}</p>}
          <div className="flex justify-end gap-2"><Button type="button" variant="ghost" size="sm" onClick={onClose}>Cancel</Button><Button type="submit" size="sm" disabled={!f.events.length} loading={save.isPending}>Save</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
