"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { PriorityPill, type Priority, type ServiceMeta } from "@/components/service";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Table, Td } from "@/components/ui/extra";
import { Input, Label, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";

type Sla = ServiceMeta["sla"];
interface Queue {
  id: string; name: string; description: string | null; member_ids: string[]; auto_assign: boolean; is_default: boolean; open_cases: number;
  email_address: string | null; routing: "least_loaded" | "presence";
}
type QueueBody = Omit<Queue, "id" | "open_cases">;

const PRIORITIES: Priority[] = ["critical", "high", "medium", "low"];
const body = (q: Queue, patch: Partial<QueueBody> = {}): QueueBody =>
  ({ name: q.name, description: q.description, member_ids: q.member_ids, auto_assign: q.auto_assign, is_default: q.is_default,
     email_address: q.email_address, routing: q.routing, ...patch });

// ---- SLA targets ------------------------------------------------------------------------------------------------

function SlaCard() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["service", "sla"], queryFn: () => get<Sla>("/service/sla") });
  const [draft, setDraft] = useState<Record<string, { first_response_hours: string; resolve_hours: string }>>({});
  useEffect(() => {
    if (data) setDraft(Object.fromEntries(PRIORITIES.map((p) => [p, {
      first_response_hours: String(data[p]?.first_response_hours ?? ""), resolve_hours: String(data[p]?.resolve_hours ?? ""),
    }])));
  }, [data]);
  const save = useMutation({
    mutationFn: async () => (await api.put("/service/sla", Object.fromEntries(PRIORITIES.map((p) => [p, {
      first_response_hours: Number(draft[p].first_response_hours), resolve_hours: Number(draft[p].resolve_hours),
    }])))).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["service"] }); qc.invalidateQueries({ queryKey: ["cases"] }); toast.success("SLA targets saved. They apply to new cases and priority changes."); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (!data || !draft.critical) return <Skeleton className="h-64 w-full" />;
  const set = (p: Priority, k: "first_response_hours" | "resolve_hours", v: string) => setDraft({ ...draft, [p]: { ...draft[p], [k]: v } });
  return (
    <Card className="overflow-hidden">
      <CardHeader title="SLA targets" description="Hours from when a case opens. The first public reply stops the response clock; resolving the case stops the resolution clock."
        action={<Button size="sm" onClick={() => save.mutate()} loading={save.isPending}><Save className="h-3.5 w-3.5" />Save targets</Button>} />
      <Table head={["Priority", "First response (hours)", "Resolution (hours)"]} minWidth={520}>
        {PRIORITIES.map((p) => (
          <tr key={p}>
            <Td><PriorityPill p={p} /></Td>
            <Td><Input id={`sla-${p}-first`} aria-label={`${p} first response hours`} type="number" min={0.1} step="any" className="h-8 w-32"
              value={draft[p].first_response_hours} onChange={(e) => set(p, "first_response_hours", e.target.value)} /></Td>
            <Td><Input id={`sla-${p}-resolve`} aria-label={`${p} resolution hours`} type="number" min={0.1} step="any" className="h-8 w-32"
              value={draft[p].resolve_hours} onChange={(e) => set(p, "resolve_hours", e.target.value)} /></Td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

// ---- queues -----------------------------------------------------------------------------------------------------

function QueuesCard() {
  const qc = useQueryClient();
  const { data: queues } = useQuery({ queryKey: ["service", "queues"], queryFn: () => get<Queue[]>("/service/queues") });
  const { data: meta } = useQuery({ queryKey: ["cases", "meta"], queryFn: () => get<ServiceMeta>("/cases/meta") });
  const [f, setF] = useState({ name: "", description: "" });
  const done = (msg?: string) => { qc.invalidateQueries({ queryKey: ["service"] }); qc.invalidateQueries({ queryKey: ["cases", "meta"] }); if (msg) toast.success(msg); };
  const update = useMutation({
    mutationFn: async (v: { id: string; body: QueueBody }) => (await api.put(`/service/queues/${v.id}`, v.body)).data,
    onSuccess: () => done(), onError: (e) => toast.error(errorMessage(e)),
  });
  const create = useMutation({
    mutationFn: async () => (await api.post("/service/queues", { name: f.name, description: f.description || null, member_ids: [], auto_assign: true, is_default: false })).data,
    onSuccess: () => { setF({ name: "", description: "" }); done("Queue added. Add agents so cases can be auto-assigned."); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/service/queues/${id}`)).data,
    onSuccess: () => done("Queue deleted. Its cases keep their owner and are no longer in a queue."),
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (!queues || !meta) return <Skeleton className="h-64 w-full" />;
  const name = (id: string) => meta.agents.find((a) => a.id === id)?.name ?? "Inactive user";
  return (
    <Card>
      <CardHeader title="Queues" description="New cases land in the default queue unless another is chosen, and email to a queue's support address opens cases there. With auto-assign on, each case goes to the member with the fewest open cases; with presence routing only to members who are Available and under their capacity (otherwise it waits and is pushed to the next free agent)." />
      <CardBody className="space-y-3">
        {queues.map((q) => (
          <div key={q.id} className="grid gap-3 rounded-md border p-3 md:grid-cols-[220px_1fr_auto]">
            <div>
              <p className="flex items-center gap-2 text-[13.5px] font-medium">{q.name}{q.is_default && <Badge tone="primary">Default</Badge>}</p>
              {q.description && <p className="text-[12px] text-muted-foreground">{q.description}</p>}
              <p className="mt-1 text-[12px] text-muted-foreground tabular">{q.open_cases} open case{q.open_cases === 1 ? "" : "s"}</p>
            </div>
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-1.5">
                {q.member_ids.map((id) => (
                  <Badge key={id} tone="outline">{name(id)}
                    <button aria-label={`Remove ${name(id)} from ${q.name}`} className="ml-1"
                      onClick={() => update.mutate({ id: q.id, body: body(q, { member_ids: q.member_ids.filter((m) => m !== id) }) })}>×</button>
                  </Badge>
                ))}
                <Select aria-label={`Add agent to ${q.name}`} className="h-7 w-44 text-[12px]" value=""
                  onChange={(e) => e.target.value && update.mutate({ id: q.id, body: body(q, { member_ids: [...q.member_ids, e.target.value] }) })}>
                  <option value="">Add agent…</option>
                  {meta.agents.filter((a) => !q.member_ids.includes(a.id)).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </Select>
              </div>
              <div className="flex flex-wrap gap-4 text-[13px]">
                <label className="flex items-center gap-2"><input id={`queue-auto-${q.id}`} type="checkbox" checked={q.auto_assign} disabled={update.isPending}
                  onChange={() => update.mutate({ id: q.id, body: body(q, { auto_assign: !q.auto_assign }) })} />Auto-assign</label>
                <label className="flex items-center gap-2"><input id={`queue-default-${q.id}`} type="radio" name="default-queue" checked={q.is_default} disabled={update.isPending}
                  onChange={() => update.mutate({ id: q.id, body: body(q, { is_default: true }) })} />Default queue</label>
                <label className="flex items-center gap-2">Routing
                  <Select aria-label={`Routing for ${q.name}`} className="h-7 w-44 text-[12px]" value={q.routing} disabled={update.isPending}
                    onChange={(e) => update.mutate({ id: q.id, body: body(q, { routing: e.target.value as Queue["routing"] }) })}>
                    <option value="least_loaded">Least loaded</option><option value="presence">Presence &amp; capacity</option>
                  </Select></label>
              </div>
              <QueueEmail q={q} onSave={(email_address) => update.mutate({ id: q.id, body: body(q, { email_address }) })} saving={update.isPending} />
            </div>
            <div>
              <Button variant="ghost" size="icon" aria-label={`Delete ${q.name}`} disabled={q.is_default}
                title={q.is_default ? "Make another queue the default first" : undefined}
                onClick={() => confirm(`Delete the ${q.name} queue?`) && remove.mutate(q.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
            </div>
          </div>
        ))}
      </CardBody>
      <form className="grid gap-3 border-t p-4 md:grid-cols-[1fr_2fr_auto] md:items-end" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <div><Label htmlFor="queue-name">Name</Label><Input id="queue-name" required minLength={2} maxLength={100} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
        <div><Label htmlFor="queue-desc">Description</Label><Input id="queue-desc" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></div>
        <Button size="sm" type="submit" loading={create.isPending}><Plus className="h-3.5 w-3.5" />Add queue</Button>
      </form>
    </Card>
  );
}

export function ServicePanel() {
  return (
    <div className="space-y-6">
      <SlaCard />
      <QueuesCard />
    </div>
  );
}

function QueueEmail({ q, onSave, saving }: { q: Queue; onSave: (v: string | null) => void; saving: boolean }) {
  const [v, setV] = useState(q.email_address ?? "");
  useEffect(() => setV(q.email_address ?? ""), [q.email_address]);
  const dirty = v.trim() !== (q.email_address ?? "");
  return (
    <form className="flex flex-wrap items-center gap-2 text-[13px]" onSubmit={(e) => { e.preventDefault(); onSave(v.trim() || null); }}>
      <label htmlFor={`queue-email-${q.id}`} className="text-muted-foreground">Support address</label>
      <Input id={`queue-email-${q.id}`} type="email" className="h-7 w-64 text-[12.5px]" placeholder="support@yourcompany.com" value={v} onChange={(e) => setV(e.target.value)} />
      {dirty && <Button type="submit" size="sm" variant="outline" className="h-7" loading={saving}>Save</Button>}
    </form>
  );
}
