"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Code2, Copy, Filter, Globe, Plus, RefreshCw, Send, Trash2, Users } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import { relativeDays } from "@/lib/utils";

type Obj = "contact" | "lead";
interface FieldCond { type: "field"; field: string; op: string; value?: string | number | string[] }
interface EventCond { type: "event"; event: string; url_contains?: string | null; min_count: number; within_days: number; negate: boolean }
type Cond = FieldCond | EventCond;
interface Rules { match: "all" | "any"; conditions: Cond[] }
export interface Segment { id: string; name: string; description: string | null; object: Obj; rules: Rules; member_count: number; refreshed_at: string | null; active: boolean }
interface Catalog { fields: Record<Obj, { key: string; label: string; kind: "text" | "number" }[]>; ops: string[]; events: { key: string; label: string }[] }
interface Preview { count: number; sample: { id: string; name: string; email: string | null; detail: string | null; href: string }[] }

const OP_LABEL: Record<string, string> = {
  eq: "is", neq: "is not", in: "is one of", not_in: "is none of", contains: "contains", gte: "is at least", lte: "is at most", is_set: "is set", not_set: "is empty",
};

function newField(obj: Obj): FieldCond {
  return { type: "field", field: obj === "contact" ? "account.industry" : "lead.status", op: "eq", value: "" };
}
function newEvent(): EventCond {
  return { type: "event", event: "page_view", url_contains: "", min_count: 1, within_days: 30, negate: false };
}

function useDebounced<T>(value: T, ms = 400): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

/** The rule builder with a live count and a sample of who matches. */
export function SegmentBuilder({ segment, onClose }: { segment?: Segment; onClose: () => void }) {
  const qc = useQueryClient();
  const catalog = useQuery({ queryKey: ["segment-catalog"], queryFn: () => get<Catalog>("/segments/catalog"), staleTime: 300_000 });
  const [name, setName] = useState(segment?.name ?? "");
  const [description, setDescription] = useState(segment?.description ?? "");
  const [obj, setObj] = useState<Obj>(segment?.object ?? "contact");
  const [rules, setRules] = useState<Rules>(segment?.rules ?? { match: "all", conditions: [newEvent()] });
  const debounced = useDebounced(JSON.stringify({ obj, rules }));
  const preview = useQuery({
    queryKey: ["segment-preview", debounced],
    queryFn: async () => (await api.post<Preview>("/segments/preview", { object: obj, rules })).data,
    retry: false,
  });
  const save = useMutation({
    mutationFn: async () => (segment
      ? await api.patch<Segment>(`/segments/${segment.id}`, { name, description: description || null, object: obj, rules, active: segment.active })
      : await api.post<Segment>("/segments", { name, description: description || null, object: obj, rules })).data,
    onSuccess: (s) => { qc.invalidateQueries({ queryKey: ["segments"] }); toast.success(`${s.name}: ${s.member_count.toLocaleString()} ${s.object}s`); onClose(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const setCond = (i: number, c: Cond) => setRules({ ...rules, conditions: rules.conditions.map((x, j) => (j === i ? c : x)) });
  const fields = catalog.data?.fields[obj] ?? [];
  const eventOptions = useMemo(() => catalog.data?.events ?? [], [catalog.data]);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={segment ? "Edit segment" : "New segment"} className="max-w-3xl">
        <form className="space-y-4 p-5" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <h2 className="text-[15px] font-semibold">{segment ? `Edit ${segment.name}` : "New segment"}</h2>
          <div className="grid gap-3 sm:grid-cols-[2fr_1fr]">
            <div><Label htmlFor="seg-name">Name</Label><Input id="seg-name" required value={name} onChange={(e) => setName(e.target.value)} placeholder="Pricing-page visitors in manufacturing" /></div>
            <div><Label htmlFor="seg-object">People</Label>
              <Select id="seg-object" value={obj} disabled={!!segment} onChange={(e) => { const o = e.target.value as Obj; setObj(o); setRules({ ...rules, conditions: rules.conditions.map((c) => (c.type === "field" ? newField(o) : c)) }); }}>
                <option value="contact">Contacts</option><option value="lead">Leads</option>
              </Select></div>
          </div>
          <div><Label htmlFor="seg-desc">Description</Label><Textarea id="seg-desc" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} /></div>
          <div className="space-y-2 rounded-md border p-3">
            <div className="flex flex-wrap items-center gap-2 text-[13px]">
              <span>Include {obj}s matching</span>
              <Select aria-label="Match" className="h-8 w-24" value={rules.match} onChange={(e) => setRules({ ...rules, match: e.target.value as Rules["match"] })}>
                <option value="all">all</option><option value="any">any</option>
              </Select>
              <span>of these conditions:</span>
            </div>
            {rules.conditions.map((c, i) => (
              <div key={i} className="flex flex-wrap items-center gap-1.5 rounded bg-surface-2/60 p-2 text-[13px]">
                {c.type === "field" ? (
                  <>
                    <Select aria-label={`Condition ${i + 1} field`} className="h-8 w-48" value={c.field} onChange={(e) => setCond(i, { ...c, field: e.target.value })}>
                      {fields.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                    </Select>
                    <Select aria-label={`Condition ${i + 1} operator`} className="h-8 w-32" value={c.op} onChange={(e) => setCond(i, { ...c, op: e.target.value })}>
                      {(catalog.data?.ops ?? Object.keys(OP_LABEL)).map((o) => <option key={o} value={o}>{OP_LABEL[o] ?? o}</option>)}
                    </Select>
                    {!["is_set", "not_set"].includes(c.op) && (
                      <Input aria-label={`Condition ${i + 1} value`} className="h-8 min-w-0 flex-1" value={Array.isArray(c.value) ? c.value.join(", ") : (c.value ?? "")}
                        placeholder={["in", "not_in"].includes(c.op) ? "comma, separated, values" : "value"}
                        onChange={(e) => setCond(i, { ...c, value: e.target.value })} />
                    )}
                  </>
                ) : (
                  <>
                    <Select aria-label={`Condition ${i + 1} did`} className="h-8 w-28" value={c.negate ? "not" : "did"} onChange={(e) => setCond(i, { ...c, negate: e.target.value === "not" })}>
                      <option value="did">did</option><option value="not">did not</option>
                    </Select>
                    <Input aria-label={`Condition ${i + 1} event`} list="seg-events" className="h-8 w-40" value={c.event} onChange={(e) => setCond(i, { ...c, event: e.target.value.trim().toLowerCase() })} />
                    <datalist id="seg-events">{eventOptions.map((e) => <option key={e.key} value={e.key}>{e.label}</option>)}</datalist>
                    {c.event === "page_view" && <Input aria-label={`Condition ${i + 1} page contains`} className="h-8 w-36" placeholder="page contains…" value={c.url_contains ?? ""} onChange={(e) => setCond(i, { ...c, url_contains: e.target.value })} />}
                    <span>at least</span>
                    <Input aria-label={`Condition ${i + 1} times`} type="number" min={1} className="h-8 w-16" value={c.min_count} onChange={(e) => setCond(i, { ...c, min_count: Number(e.target.value) || 1 })} />
                    <span>times in</span>
                    <Input aria-label={`Condition ${i + 1} days`} type="number" min={1} max={730} className="h-8 w-16" value={c.within_days} onChange={(e) => setCond(i, { ...c, within_days: Number(e.target.value) || 30 })} />
                    <span>days</span>
                  </>
                )}
                <Button type="button" size="icon" variant="ghost" className="ml-auto" aria-label={`Remove condition ${i + 1}`} disabled={rules.conditions.length === 1}
                  onClick={() => setRules({ ...rules, conditions: rules.conditions.filter((_, j) => j !== i) })}><Trash2 className="h-3.5 w-3.5" /></Button>
              </div>
            ))}
            <div className="flex gap-2">
              <Button type="button" size="sm" variant="outline" onClick={() => setRules({ ...rules, conditions: [...rules.conditions, newField(obj)] })}><Plus className="h-3.5 w-3.5" />Attribute</Button>
              <Button type="button" size="sm" variant="outline" onClick={() => setRules({ ...rules, conditions: [...rules.conditions, newEvent()] })}><Plus className="h-3.5 w-3.5" />Behaviour</Button>
            </div>
          </div>
          <div className="rounded-md border p-3" aria-live="polite">
            {preview.isError ? <p className="text-[13px] text-destructive">{errorMessage(preview.error)}</p>
              : !preview.data ? <Skeleton className="h-12 w-full" />
                : (
                  <>
                    <p className="text-[13px]"><span className="text-lg font-semibold">{preview.data.count.toLocaleString()}</span> {obj}s match right now</p>
                    <ul className="mt-1.5 grid gap-x-4 gap-y-0.5 text-[12.5px] sm:grid-cols-2">
                      {preview.data.sample.map((p) => <li key={p.id} className="truncate"><Link href={p.href} className="hover:underline">{p.name}</Link><span className="text-muted-foreground">{p.detail ? ` · ${p.detail}` : ""}</span></li>)}
                    </ul>
                  </>
                )}
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
            <Button type="submit" size="sm" loading={save.isPending} disabled={preview.isError}>{segment ? "Save segment" : "Create segment"}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function AddToCampaign({ segment, onClose }: { segment: Segment; onClose: () => void }) {
  const campaigns = useQuery({ queryKey: ["campaigns", "list"], queryFn: () => get<{ campaigns: { id: string; name: string; status: string }[] }>("/campaigns") });
  const [id, setId] = useState("");
  const add = useMutation({
    mutationFn: async () => (await api.post<{ added: number; skipped: number }>(`/segments/${segment.id}/add-to-campaign`, { campaign_id: id })).data,
    onSuccess: (r) => { toast.success(`${r.added} added to the campaign${r.skipped ? ` (${r.skipped} were already members)` : ""}`); onClose(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title="Add to a campaign" className="max-w-md">
        <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
          <h2 className="text-[15px] font-semibold">Add {segment.name} to a campaign</h2>
          <p className="text-[13px] text-muted-foreground">Everyone in the segment right now joins as a member. Consent is still checked when the campaign sends email.</p>
          <div><Label htmlFor="seg-camp">Campaign</Label>
            <Select id="seg-camp" required value={id} onChange={(e) => setId(e.target.value)}>
              <option value="">Choose…</option>
              {campaigns.data?.campaigns.filter((c) => c.status !== "completed").map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </Select></div>
          <div className="flex justify-end gap-2"><Button type="button" size="sm" variant="ghost" onClick={onClose}>Cancel</Button><Button type="submit" size="sm" loading={add.isPending}>Add members</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function SegmentList() {
  const qc = useQueryClient();
  const { can } = useMe();
  const list = useQuery({ queryKey: ["segments"], queryFn: () => get<Segment[]>("/segments") });
  const [editing, setEditing] = useState<Segment | "new" | null>(null);
  const [adding, setAdding] = useState<Segment | null>(null);
  const refresh = useMutation({
    mutationFn: async (id: string) => (await api.post<Segment & { entered: number; left: number }>(`/segments/${id}/refresh`)).data,
    onSuccess: (s) => { qc.invalidateQueries({ queryKey: ["segments"] }); toast.success(`${s.member_count} members · ${s.entered} joined, ${s.left} left`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: async (id: string) => api.delete(`/segments/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["segments"] }),
  });
  const describe = (r: Rules) => r.conditions.map((c) => c.type === "event"
    ? `${c.negate ? "didn't" : "did"} ${c.event.replace(/_/g, " ")}${c.url_contains ? ` (${c.url_contains})` : ""} ${c.min_count > 1 ? `${c.min_count}× ` : ""}in ${c.within_days}d`
    : `${c.field.split(".")[1].replace(/_/g, " ")} ${OP_LABEL[c.op] ?? c.op}${c.value !== undefined && c.value !== "" ? ` ${Array.isArray(c.value) ? c.value.join(", ") : c.value}` : ""}`).join(r.match === "all" ? " and " : " or ");
  return (
    <Card>
      <CardHeader title="Segments" icon={<Filter className="h-4 w-4 text-muted-foreground" />}
        description="Dynamic audiences from attributes and behaviour. They refresh every 15 minutes; who joins can trigger webhooks, Slack posts and campaigns."
        action={can("campaigns", "create") && <Button size="sm" onClick={() => setEditing("new")}><Plus className="h-3.5 w-3.5" />New segment</Button>} />
      <CardBody>
        {list.isLoading && <Skeleton className="h-24 w-full" />}
        {list.data && !list.data.length && <EmptyState icon={<Users className="h-4 w-4" />} title="No segments yet" description="For example: contacts in manufacturing who viewed the pricing page twice this month." />}
        <ul className="divide-y">
          {list.data?.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="text-[13.5px] font-medium">{s.name} <Badge tone="outline">{s.object}s</Badge></p>
                <p className="truncate text-[12.5px] text-muted-foreground">{describe(s.rules)}</p>
                <p className="text-[11.5px] text-subtle">{s.refreshed_at ? `Updated ${relativeDays(s.refreshed_at)}` : "Not computed yet"}</p>
              </div>
              <span className="tabular text-lg font-semibold">{s.member_count.toLocaleString()}</span>
              {can("campaigns", "update") && (
                <div className="flex gap-1">
                  <Button size="sm" variant="outline" onClick={() => setEditing(s)}>Edit</Button>
                  <Button size="icon" variant="ghost" aria-label={`Refresh ${s.name}`} onClick={() => refresh.mutate(s.id)}><RefreshCw className="h-3.5 w-3.5" /></Button>
                  <Button size="icon" variant="ghost" aria-label={`Add ${s.name} to a campaign`} onClick={() => setAdding(s)}><Send className="h-3.5 w-3.5" /></Button>
                  {can("campaigns", "delete") && <Button size="icon" variant="ghost" aria-label={`Delete ${s.name}`} onClick={() => remove.mutate(s.id)}><Trash2 className="h-3.5 w-3.5" /></Button>}
                </div>
              )}
            </li>
          ))}
        </ul>
      </CardBody>
      {editing && <SegmentBuilder segment={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} />}
      {adding && <AddToCampaign segment={adding} onClose={() => setAdding(null)} />}
    </Card>
  );
}

interface Tracking { enabled: boolean; site_key: string; domains: string[]; snippet: string }

export function TrackingCard() {
  const qc = useQueryClient();
  const { can } = useMe();
  const cfg = useQuery({ queryKey: ["segments-tracking"], queryFn: () => get<Tracking>("/segments-tracking") });
  const [domains, setDomains] = useState("");
  useEffect(() => { if (cfg.data) setDomains(cfg.data.domains.join(", ")); }, [cfg.data]);
  const save = useMutation({
    mutationFn: async (enabled: boolean) => (await api.put<Tracking>("/segments-tracking", { enabled, domains: domains.split(",").map((d) => d.trim()).filter(Boolean) })).data,
    onSuccess: (d) => { qc.setQueryData(["segments-tracking"], d); toast.success(d.enabled ? "Website tracking is on" : "Website tracking is off"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const c = cfg.data;
  return (
    <Card>
      <CardHeader title="Website tracking" icon={<Globe className="h-4 w-4 text-muted-foreground" />}
        description="Page views and custom events from your website, tied to a person once they fill in a form or sign in. Visitors with Do Not Track are skipped; set window.cirraConsent = false before the snippet until they accept cookies." />
      <CardBody className="space-y-3 text-[13px]">
        {!c ? <Skeleton className="h-20 w-full" /> : (
          <>
            <div><Label htmlFor="trk-domains">Allowed sites</Label>
              <Input id="trk-domains" value={domains} disabled={!can("campaigns", "update")} placeholder="www.example.com, shop.example.com" onChange={(e) => setDomains(e.target.value)} />
              <p className="mt-1 text-[12px] text-muted-foreground">Events from other sites are refused. Leave empty to accept any site that has the key.</p></div>
            {can("campaigns", "update") && (
              <div className="flex gap-2">
                <Button size="sm" variant={c.enabled ? "outline" : "primary"} loading={save.isPending} onClick={() => save.mutate(!c.enabled)}>{c.enabled ? "Turn off" : "Turn on"}</Button>
                {c.enabled && <Button size="sm" variant="ghost" onClick={() => save.mutate(true)}>Save sites</Button>}
              </div>
            )}
            {c.enabled && (
              <div>
                <p className="mb-1 flex items-center gap-1.5 font-medium"><Code2 className="h-3.5 w-3.5" />Add this before &lt;/head&gt; on every page</p>
                <div className="flex items-start gap-2">
                  <pre className="min-w-0 flex-1 overflow-x-auto rounded bg-muted p-2 font-mono text-[11.5px]">{c.snippet}</pre>
                  <Button size="icon" variant="outline" aria-label="Copy snippet" onClick={() => navigator.clipboard?.writeText(c.snippet).then(() => toast.success("Snippet copied"))}><Copy className="h-3.5 w-3.5" /></Button>
                </div>
                <p className="mt-1.5 text-[12px] text-muted-foreground">Then call <code>cirra.track(&quot;demo_requested&quot;)</code> for custom events and <code>cirra.identify(email)</code> after sign-in. Forms posting to Cirra link the visitor automatically. Products send events with <code>POST /api/v1/events</code>.</p>
              </div>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}
