"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Globe, MousePointerClick, Network, Plus, Trash2, UserPlus, Users } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Label, Select } from "@/components/ui/input";
import { Avatar, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import type { Contact } from "@/lib/types";
import { cn, relativeDays } from "@/lib/utils";

export const STANCE_TONE: Record<string, string> = {
  champion: "var(--status-good)", supporter: "color-mix(in srgb, var(--status-good) 60%, var(--muted-foreground))",
  neutral: "var(--muted-foreground)", skeptic: "var(--status-warning)", blocker: "var(--status-critical)",
};
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function Stance({ stance }: { stance: string | null | undefined }) {
  if (!stance) return null;
  return (
    <span className="inline-flex items-center gap-1 text-[11.5px] text-muted-foreground">
      <span className="h-2 w-2 rounded-full" style={{ background: STANCE_TONE[stance] }} aria-hidden />{cap(stance)}
    </span>
  );
}

// ---- buying committee on a deal ----------------------------------------------------------------------------------
interface Member { contact_id: string; name: string; title: string | null; email: string | null; status: string; role: string; influence: string; stance: string; is_primary: boolean; notes: string | null }
interface Committee {
  members: Member[]; coverage: number; needed: string[]; roles: string[]; stances: string[]; influence: string[];
  gaps: { kind: string; role?: string; contact_id?: string; message: string }[];
  suggestions: { contact_id: string; name: string; title: string | null; suggested_role: string; why: string }[];
}

export function BuyingCommittee({ dealId, contacts, canEdit }: { dealId: string; contacts: Contact[]; canEdit: boolean }) {
  const qc = useQueryClient();
  const committee = useQuery({ queryKey: ["committee", dealId], queryFn: () => get<Committee>(`/deals/${dealId}/committee`) });
  const [adding, setAdding] = useState(false);
  const [pick, setPick] = useState({ contact_id: "", role: "Champion" });
  const save = useMutation({
    mutationFn: async (body: Partial<Member> & { contact_id: string }) => (await api.put<Committee>(`/deals/${dealId}/committee`, body)).data,
    onSuccess: (d) => { qc.setQueryData(["committee", dealId], d); qc.invalidateQueries({ queryKey: ["deal", dealId] }); setAdding(false); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: async (contactId: string) => (await api.delete<Committee>(`/deals/${dealId}/committee/${contactId}`)).data,
    onSuccess: (d) => { qc.setQueryData(["committee", dealId], d); qc.invalidateQueries({ queryKey: ["deal", dealId] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const c = committee.data;
  const on = new Set(c?.members.map((m) => m.contact_id));
  const available = contacts.filter((x) => !on.has(x.id) && x.status !== "erased");
  const update = (m: Member, change: Partial<Member>) => save.mutate({ contact_id: m.contact_id, role: m.role, influence: m.influence, stance: m.stance, is_primary: m.is_primary, ...change });
  return (
    <Card>
      <CardHeader title="Buying committee" icon={<Users className="h-4 w-4 text-muted-foreground" />}
        description={c ? `${c.coverage}% covered for this stage` : undefined}
        action={canEdit && available.length > 0 ? <Button variant="ghost" size="sm" onClick={() => setAdding(!adding)} aria-label="Add to buying committee"><Plus className="h-3.5 w-3.5" /></Button> : undefined} />
      <CardBody className="space-y-3">
        {!c && <Skeleton className="h-24 w-full" />}
        {c && (
          <>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted" role="meter" aria-label="Committee coverage" aria-valuenow={c.coverage} aria-valuemin={0} aria-valuemax={100}>
              <div className="h-full rounded-full" style={{ width: `${c.coverage}%`, background: c.coverage >= 70 ? "var(--status-good)" : c.coverage >= 40 ? "var(--status-warning)" : "var(--status-critical)" }} />
            </div>
            <div className="flex flex-wrap gap-1">
              {c.needed.map((r) => {
                const has = c.members.some((m) => m.role === r && m.status === "active");
                return <Badge key={r} tone={has ? "good" : "outline"}>{has ? "✓" : "○"} {r}</Badge>;
              })}
            </div>
            {adding && (
              <form className="space-y-2 rounded-md border p-2.5" onSubmit={(e) => { e.preventDefault(); if (pick.contact_id) save.mutate({ ...pick }); }}>
                <div><Label htmlFor="bc-contact">Person</Label>
                  <Select id="bc-contact" required value={pick.contact_id} onChange={(e) => setPick({ ...pick, contact_id: e.target.value })}>
                    <option value="">Choose a contact…</option>
                    {available.map((x) => <option key={x.id} value={x.id}>{x.name}{x.job_title ? ` · ${x.job_title}` : ""}</option>)}
                  </Select></div>
                <div><Label htmlFor="bc-role">Role in this decision</Label>
                  <Select id="bc-role" value={pick.role} onChange={(e) => setPick({ ...pick, role: e.target.value })}>
                    {c.roles.map((r) => <option key={r} value={r}>{r}</option>)}
                  </Select></div>
                <div className="flex justify-end gap-2">
                  <Button type="button" size="sm" variant="ghost" onClick={() => setAdding(false)}>Cancel</Button>
                  <Button type="submit" size="sm" loading={save.isPending}>Add</Button>
                </div>
              </form>
            )}
            {!c.members.length && !adding && (
              <p className="text-[13px] text-muted-foreground">Nobody mapped yet. Add the people involved in this decision and their roles; until then Cirra uses the contacts&apos; usual roles at the account.</p>
            )}
            <ul className="space-y-2.5">
              {c.members.map((m) => (
                <li key={m.contact_id} className={cn("rounded-md border p-2", m.status !== "active" && "opacity-60")}>
                  <div className="flex items-center gap-2.5">
                    <Avatar name={m.name} size={28} />
                    <div className="min-w-0 flex-1">
                      <Link href={`/contacts/${m.contact_id}`} className="block truncate text-[13.5px] font-medium hover:underline">
                        {m.name}{m.is_primary && <span className="ml-1.5 text-[11px] font-normal text-muted-foreground">primary</span>}
                        {m.status === "departed" && <span className="ml-1.5 text-[11px] font-normal text-destructive">left</span>}
                      </Link>
                      <p className="truncate text-[12px] text-muted-foreground">{m.title ?? "—"}</p>
                    </div>
                    {canEdit && <Button size="icon" variant="ghost" aria-label={`Remove ${m.name} from the committee`} onClick={() => remove.mutate(m.contact_id)}><Trash2 className="h-3.5 w-3.5" /></Button>}
                  </div>
                  {canEdit ? (
                    <div className="mt-2 grid grid-cols-2 gap-1.5">
                      <Select aria-label={`${m.name}: role`} className="col-span-2 h-8 text-[12px]" value={m.role} onChange={(e) => update(m, { role: e.target.value })}>
                        {c.roles.map((r) => <option key={r} value={r}>{r}</option>)}
                      </Select>
                      <Select aria-label={`${m.name}: influence`} className="h-8 text-[12px]" value={m.influence} onChange={(e) => update(m, { influence: e.target.value })}>
                        {c.influence.map((r) => <option key={r} value={r}>{cap(r)} influence</option>)}
                      </Select>
                      <Select aria-label={`${m.name}: stance`} className="h-8 text-[12px]" value={m.stance} onChange={(e) => update(m, { stance: e.target.value })}>
                        {c.stances.map((r) => <option key={r} value={r}>{cap(r)}</option>)}
                      </Select>
                    </div>
                  ) : (
                    <div className="mt-1.5 flex flex-wrap items-center gap-2"><Badge tone="primary">{m.role}</Badge><span className="text-[11.5px] text-muted-foreground">{cap(m.influence)} influence</span><Stance stance={m.stance} /></div>
                  )}
                </li>
              ))}
            </ul>
            {!!c.gaps.length && (
              <ul className="space-y-1">
                {c.gaps.map((g, i) => (
                  <li key={i} className="flex items-start gap-1.5 text-[12.5px]"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color: "var(--status-warning)" }} />{g.message}</li>
                ))}
              </ul>
            )}
            {canEdit && !!c.suggestions.length && (
              <div>
                <p className="mb-1 text-[12px] font-medium text-muted-foreground">Who to bring in</p>
                <ul className="space-y-1.5">
                  {c.suggestions.map((s) => (
                    <li key={s.contact_id} className="flex items-center gap-2 text-[12.5px]">
                      <span className="min-w-0 flex-1"><span className="font-medium">{s.name}</span> <span className="text-muted-foreground">· {s.why}</span></span>
                      <Button size="sm" variant="outline" aria-label={`Add ${s.name} as ${s.suggested_role}`}
                        onClick={() => save.mutate({ contact_id: s.contact_id, role: s.suggested_role })}><UserPlus className="h-3.5 w-3.5" />{s.suggested_role}</Button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}

// ---- org chart on an account ---------------------------------------------------------------------------------------
interface OrgNode {
  id: string; name: string; title: string | null; department: string | null; buying_role: string; influence: string | null; stance: string | null;
  status: string; reports_to_id: string | null; reports_outside: boolean; relationship_strength: number | null; days_since_touch: number | null;
  deal_roles: { deal_id: string; deal: string; role: string; stance: string }[];
}

export function OrgChart({ accountId, canEdit }: { accountId: string; canEdit: boolean }) {
  const qc = useQueryClient();
  const chart = useQuery({ queryKey: ["org-chart", accountId], queryFn: () => get<{ nodes: OrgNode[]; roots: string[]; unplaced: number }>(`/accounts/${accountId}/org-chart`) });
  const [editing, setEditing] = useState(false);
  const setManager = useMutation({
    mutationFn: async ({ id, manager }: { id: string; manager: string | null }) => (await api.patch(`/contacts/${id}`, { reports_to_id: manager })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["org-chart", accountId] }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const nodes = chart.data?.nodes ?? [];
  const children = (id: string) => nodes.filter((n) => n.reports_to_id === id);
  const renderNode = (n: OrgNode, depth: number): React.ReactNode => (
    <li key={n.id} className="relative">
      <div className={cn("flex flex-wrap items-center gap-2.5 rounded-md border bg-surface px-2.5 py-2", n.status === "departed" && "opacity-60")}
        style={{ borderLeftWidth: 3, borderLeftColor: n.stance ? STANCE_TONE[n.stance] : "var(--border)" }}>
        <Avatar name={n.name} size={28} />
        <div className="min-w-0 flex-1">
          <Link href={`/contacts/${n.id}`} className="block truncate text-[13px] font-medium hover:underline">{n.name}{n.status === "departed" && <span className="ml-1 text-[11px] font-normal text-destructive">left</span>}</Link>
          <p className="truncate text-[12px] text-muted-foreground">{n.title ?? "—"}{n.department ? ` · ${n.department}` : ""}</p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone="outline">{n.buying_role}</Badge>
          {n.influence && <span className="text-[11px] text-muted-foreground">{cap(n.influence)}</span>}
          <Stance stance={n.stance} />
          {n.deal_roles.map((r) => <Badge key={r.deal_id} tone="primary" title={r.deal}>{r.role}</Badge>)}
          {n.days_since_touch != null && <span className={cn("text-[11px]", n.days_since_touch > 30 ? "text-destructive" : "text-subtle")}>{n.days_since_touch}d</span>}
        </div>
        {editing && (
          <Select aria-label={`${n.name} reports to`} className="h-8 w-full text-[12px] sm:w-44" value={n.reports_to_id ?? ""}
            onChange={(e) => setManager.mutate({ id: n.id, manager: e.target.value || null })}>
            <option value="">Reports to nobody here</option>
            {nodes.filter((m) => m.id !== n.id).map((m) => <option key={m.id} value={m.id}>Reports to {m.name}</option>)}
          </Select>
        )}
      </div>
      {children(n.id).length > 0 && (
        <ul className="ml-4 mt-2 space-y-2 border-l pl-4" aria-label={`Reports to ${n.name}`}>{children(n.id).map((c) => renderNode(c, depth + 1))}</ul>
      )}
    </li>
  );
  return (
    <Card>
      <CardHeader title="Org chart" icon={<Network className="h-4 w-4 text-muted-foreground" />}
        description="Who reports to whom, their buying role, influence and stance, and the deals they're part of. Colour shows stance; days since we last spoke to them."
        action={canEdit && nodes.length > 1 ? <Button size="sm" variant={editing ? "primary" : "outline"} onClick={() => setEditing(!editing)}>{editing ? "Done" : "Edit reporting lines"}</Button> : undefined} />
      <CardBody>
        {chart.isLoading && <Skeleton className="h-32 w-full" />}
        {chart.data && !nodes.length && <p className="text-[13px] text-muted-foreground">No contacts at this account yet.</p>}
        <ul className="space-y-2">{(chart.data?.roots ?? []).map((id) => { const n = nodes.find((x) => x.id === id); return n ? renderNode(n, 0) : null; })}</ul>
        {!!chart.data?.unplaced && chart.data.unplaced > 1 && !editing && canEdit && (
          <p className="mt-3 text-[12px] text-muted-foreground">{chart.data.unplaced} people aren&apos;t placed yet. Choose <b>Edit reporting lines</b> to build the chart.</p>
        )}
      </CardBody>
    </Card>
  );
}

/** Reports to / influence / stance on the contact page. */
export function StakeholderFields({ contact, colleagues, canEdit, onSave }: {
  contact: Contact & { reports_to_id?: string | null; influence?: string | null; stance?: string | null }; colleagues: Contact[]; canEdit: boolean;
  onSave: (change: Record<string, unknown>) => void;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <div><Label htmlFor="sh-manager">Reports to</Label>
        <Select id="sh-manager" disabled={!canEdit} value={contact.reports_to_id ?? ""} onChange={(e) => onSave({ reports_to_id: e.target.value || null })}>
          <option value="">—</option>
          {colleagues.filter((c) => c.id !== contact.id && c.status !== "erased").map((c) => <option key={c.id} value={c.id}>{c.name}{c.job_title ? ` · ${c.job_title}` : ""}</option>)}
        </Select></div>
      <div><Label htmlFor="sh-influence">Influence</Label>
        <Select id="sh-influence" disabled={!canEdit} value={contact.influence ?? ""} onChange={(e) => onSave({ influence: e.target.value || null })}>
          <option value="">—</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option>
        </Select></div>
      <div><Label htmlFor="sh-stance">Stance toward us</Label>
        <Select id="sh-stance" disabled={!canEdit} value={contact.stance ?? ""} onChange={(e) => onSave({ stance: e.target.value || null })}>
          <option value="">—</option>
          {["champion", "supporter", "neutral", "skeptic", "blocker"].map((s) => <option key={s} value={s}>{cap(s)}</option>)}
        </Select></div>
    </div>
  );
}

// ---- web, email and product activity -------------------------------------------------------------------------------
interface Behavior {
  events: { id: number; event: string; label: string; url: string | null; properties: Record<string, unknown>; source: string; occurred_at: string }[];
  last_30_days: Record<string, number>; top_pages: { url: string; views: number }[]; last_seen: string | null;
}

export function BehaviorCard({ path }: { path: string }) {
  const q = useQuery({ queryKey: ["behavior", path], queryFn: () => get<Behavior>(path) });
  const b = q.data;
  const shortUrl = (u: string | null) => { if (!u) return ""; try { const x = new URL(u); return x.pathname + x.search; } catch { return u; } };
  return (
    <Card>
      <CardHeader title="Website, email and product activity" icon={<Globe className="h-4 w-4 text-muted-foreground" />}
        description={b?.last_seen ? `Last seen ${relativeDays(b.last_seen)}` : "What this person did on your website, in your emails and in your product"} />
      <CardBody className="space-y-3">
        {q.isLoading && <Skeleton className="h-20 w-full" />}
        {b && !b.events.length && <p className="text-[13px] text-muted-foreground">No tracked activity yet. Add the tracking snippet to your website (Campaigns → Segments) or send product events over the API.</p>}
        {b && !!b.events.length && (
          <>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(b.last_30_days).map(([k, v]) => <Badge key={k} tone="outline">{k.replace(/_/g, " ")} · {v}</Badge>)}
              <span className="text-[11.5px] text-subtle">last 30 days</span>
            </div>
            {!!b.top_pages.length && (
              <div>
                <p className="mb-1 text-[12px] font-medium text-muted-foreground">Most viewed pages</p>
                <ul className="space-y-0.5 text-[12.5px]">{b.top_pages.map((p) => <li key={p.url} className="flex gap-2"><span className="min-w-0 flex-1 truncate">{shortUrl(p.url)}</span><span className="text-muted-foreground">{p.views}×</span></li>)}</ul>
              </div>
            )}
            <ol className="space-y-1.5">
              {b.events.slice(0, 15).map((e) => (
                <li key={e.id} className="flex items-start gap-2 text-[12.5px]">
                  <MousePointerClick className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1"><span className="font-medium">{e.label}</span>{e.url && <span className="text-muted-foreground"> · {shortUrl(e.url)}</span>}
                    {Object.keys(e.properties).filter((k) => !["title", "referrer"].includes(k)).slice(0, 2).map((k) => <span key={k} className="text-muted-foreground"> · {k}: {String(e.properties[k])}</span>)}</span>
                  <span className="shrink-0 text-subtle">{relativeDays(e.occurred_at)}</span>
                </li>
              ))}
            </ol>
          </>
        )}
      </CardBody>
    </Card>
  );
}
