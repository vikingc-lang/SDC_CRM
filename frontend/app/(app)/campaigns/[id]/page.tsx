"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Filter as FilterIcon, Mail, Pencil, Plus, Send, Trash2, Users } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/AppShell";
import { type Campaign, CampaignDialog, CampaignStatusBadge, type MemberStatus, TYPE_LABELS, pctText } from "@/components/campaigns";
import { JourneysTab } from "@/components/journeys";
import { StatTile } from "@/components/charts";
import { type CatalogueField, FilterRow, nice } from "@/components/filters";
import type { Filter } from "@/components/reportviz";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Table, Tabs, Td } from "@/components/ui/extra";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import { money, relativeDays, shortDate } from "@/lib/utils";

interface Member { id: string; status: MemberStatus; source: string; added_at: string; sent_at: string | null; responded_at: string | null;
  person: { kind: "lead" | "contact"; id: string; name: string; email: string | null; company: string | null; link: string } }
interface Preview { eligible: number; blocked: Record<string, number>; sample: { to: string; subject: string; body: string } | null }
interface Sources { sources: { key: string; label: string; fields: CatalogueField[] }[]; periods: string[] }

const MEMBER_STATUSES: MemberStatus[] = ["targeted", "sent", "responded", "registered", "attended", "unsubscribed", "bounced"];
const FUNNEL: MemberStatus[] = ["targeted", "sent", "responded", "registered", "attended"];
type TabKey = "overview" | "members" | "email" | "journeys";

export default function CampaignPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const { can } = useMe();
  const [tab, setTab] = useState<TabKey>("overview");
  const [editing, setEditing] = useState(false);
  const c = useQuery({ queryKey: ["campaigns", "detail", id], queryFn: () => get<Campaign>(`/campaigns/${id}`) });
  const del = useMutation({
    mutationFn: async () => (await api.delete(`/campaigns/${id}`)).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["campaigns"] }); router.push("/campaigns"); },
  });
  if (c.isError) return <p className="text-sm text-muted-foreground">{errorMessage(c.error, "Campaign not found")}</p>;
  if (!c.data) return <Skeleton className="h-96" />;
  const cp = c.data;
  const editable = can("campaigns", "update");
  return (
    <div className="mx-auto max-w-6xl">
      <Link href="/campaigns" className="mb-3 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />Campaigns</Link>
      <PageHeader title={<span className="flex flex-wrap items-center gap-2">{cp.name}<CampaignStatusBadge status={cp.status} /></span>}
        description={<>{TYPE_LABELS[cp.type] ?? cp.type} · code <code className="font-mono">{cp.code}</code>{cp.start_date ? ` · ${shortDate(cp.start_date, true)}` : ""}{cp.end_date ? ` – ${shortDate(cp.end_date, true)}` : ""}{cp.owner ? ` · ${cp.owner}` : ""}</>}
        actions={<div className="flex gap-2">
          {editable && <Button size="sm" variant="outline" onClick={() => setEditing(true)}><Pencil className="h-3.5 w-3.5" />Edit</Button>}
          {can("campaigns", "delete") && <Button size="sm" variant="ghost" aria-label="Delete campaign" onClick={() => confirm(`Delete ${cp.name} and its member list?`) && del.mutate()}><Trash2 className="h-3.5 w-3.5" /></Button>}
        </div>} />
      {cp.description && <p className="-mt-3 mb-4 max-w-3xl text-[13.5px] text-muted-foreground">{cp.description}</p>}
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "overview", label: "Overview" }, { value: "members", label: `Members (${cp.metrics.members})` }, { value: "email", label: "Email" }, { value: "journeys", label: "Nurture journeys" }]} />
      {tab === "overview" && <Overview cp={cp} />}
      {tab === "members" && <Members cp={cp} editable={editable} />}
      {tab === "email" && <EmailTab cp={cp} editable={editable} />}
      {tab === "journeys" && <JourneysTab campaignId={cp.id} editable={editable} />}
      {editing && <CampaignDialog campaign={cp} onClose={() => setEditing(false)} />}
    </div>
  );
}

function Overview({ cp }: { cp: Campaign }) {
  const m = cp.metrics;
  const top = Math.max(1, ...FUNNEL.map((s) => m.by_status[s] ?? 0));
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Responses" value={m.responses.toLocaleString()} sub={`${pctText(m.response_rate)} of people reached`} />
        <StatTile label="Sourced pipeline" value={money(m.sourced_pipeline, { compact: true })} emphasis sub={`${m.sourced_deals} deal${m.sourced_deals === 1 ? "" : "s"} from ${m.leads} lead${m.leads === 1 ? "" : "s"}`} />
        <StatTile label="Won (influenced)" value={money(m.influenced_won, { compact: true })} sub={`${m.influenced_deals} influenced deal${m.influenced_deals === 1 ? "" : "s"}`} />
        <StatTile label="ROI" value={pctText(m.roi_pct)} sub={m.cost ? `on ${money(m.cost)} spent` : "Add the actual cost to see ROI"} />
      </div>
      {m.email && m.email.sent > 0 && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatTile label="Emails sent" value={m.email.sent.toLocaleString()} sub="campaign sends and journey steps" />
          <StatTile label="Open rate" value={pctText(m.email.open_rate)} sub={`${m.email.opened} opened (indicative)`} />
          <StatTile label="Click rate" value={pctText(m.email.click_rate)} sub={`${m.email.clicked} clicked`} />
          <StatTile label="Click-to-open" value={pctText(m.email.click_to_open)} sub="clicks among openers" />
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
        <Card className="min-w-0">
          <CardHeader title="Attributed deals" description="Sourced: created from a member lead. Influenced: opened on a member contact's account after they joined." />
          {!m.deals?.length ? <EmptyState icon={<Users className="h-4 w-4" />} title="No attributed deals yet" /> : (
            <Table head={["Deal", "Attribution", "Status", "Amount (USD)"]} minWidth={560}>
              {m.deals.map((d) => (
                <tr key={d.id}>
                  <Td><Link href={`/deals/${d.id}`} className="font-medium hover:text-primary hover:underline">{d.title}</Link></Td>
                  <Td><Badge tone={d.attribution === "sourced" ? "primary" : "outline"}>{nice(d.attribution)}</Badge></Td>
                  <Td className="text-[13px]">{d.status}</Td>
                  <Td className="tabular text-[13px]">{money(d.amount_usd)}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader title="Member funnel" />
            <CardBody className="space-y-2">
              {FUNNEL.map((s) => (
                <div key={s} className="grid grid-cols-[88px_1fr_40px] items-center gap-2 text-[12.5px]">
                  <span className="text-muted-foreground">{nice(s)}</span>
                  <div className="h-2 rounded-full bg-muted"><div className="h-2 rounded-full bg-primary" style={{ width: `${((m.by_status[s] ?? 0) / top) * 100}%` }} /></div>
                  <span className="text-right tabular">{m.by_status[s] ?? 0}</span>
                </div>
              ))}
              <p className="pt-1 text-[12px] text-subtle">{m.by_status.unsubscribed ?? 0} unsubscribed · {m.by_status.bounced ?? 0} bounced</p>
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Budget" />
            <CardBody className="space-y-1.5 text-[13px]">
              <p className="flex justify-between"><span className="text-muted-foreground">Budget</span><span className="tabular">{money(m.budget)}</span></p>
              <p className="flex justify-between"><span className="text-muted-foreground">Spent</span><span className="tabular">{money(m.cost)}{m.budget_used_pct != null ? ` (${pctText(m.budget_used_pct)})` : ""}</span></p>
              <p className="flex justify-between"><span className="text-muted-foreground">Expected revenue</span><span className="tabular">{money(cp.expected_revenue)}</span></p>
              <p className="flex justify-between"><span className="text-muted-foreground">Cost per lead</span><span className="tabular">{m.cost_per_lead != null ? money(m.cost_per_lead) : "—"}</span></p>
              <p className="flex justify-between"><span className="text-muted-foreground">Cost per response</span><span className="tabular">{m.cost_per_response != null ? money(m.cost_per_response) : "—"}</span></p>
              <p className="flex justify-between"><span className="text-muted-foreground">Leads converted</span><span className="tabular">{m.converted_leads} of {m.leads}</span></p>
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Members({ cp, editable }: { cp: Campaign; editable: boolean }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [building, setBuilding] = useState(false);
  useEffect(() => { const t = setTimeout(() => setSearch(q), 300); return () => clearTimeout(t); }, [q]);
  const list = useQuery({ queryKey: ["campaigns", "members", cp.id, status, search], placeholderData: (p) => p,
    queryFn: () => get<{ total: number; members: Member[] }>(`/campaigns/${cp.id}/members`, { status: status || undefined, q: search || undefined }) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["campaigns"] });
  const patch = useMutation({
    mutationFn: async (v: { id: string; status: string }) => (await api.patch(`/campaigns/${cp.id}/members/${v.id}`, { status: v.status })).data,
    onSuccess: refresh, onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({ mutationFn: async (mid: string) => (await api.delete(`/campaigns/${cp.id}/members/${mid}`)).data, onSuccess: refresh });
  return (
    <Card className="overflow-hidden">
      <CardHeader title="Members" description="Leads and contacts in this campaign. Marking someone as responded, registered or attended logs engagement on the lead and feeds its score."
        action={editable && <Button size="sm" onClick={() => setBuilding(true)}><FilterIcon className="h-3.5 w-3.5" />Add from a filter</Button>} />
      <div className="flex flex-wrap gap-2 border-b px-5 pb-3">
        <Input aria-label="Search members" className="h-8 w-56" placeholder="Search name, email or company" value={q} onChange={(e) => setQ(e.target.value)} />
        <Select aria-label="Member status" className="h-8 w-40" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>{MEMBER_STATUSES.map((s) => <option key={s} value={s}>{nice(s)}</option>)}</Select>
        {list.data && <span className="self-center text-[12.5px] text-muted-foreground">{list.data.total.toLocaleString()} shown to you</span>}
      </div>
      {!list.data ? <Skeleton className="m-5 h-40" /> : !list.data.members.length ? <EmptyState icon={<Users className="h-4 w-4" />} title="No members match" description={editable ? "Add people from a leads or contacts filter." : undefined} /> : (
        <Table head={["Person", "Type", "Status", "Joined", "Sent", "Responded", ""]} minWidth={880}>
          {list.data.members.map((m) => (
            <tr key={m.id}>
              <Td><Link href={m.person.link} className="font-medium hover:text-primary hover:underline">{m.person.name}</Link>
                <span className="block text-[12px] text-muted-foreground">{[m.person.email, m.person.company].filter(Boolean).join(" · ")}</span></Td>
              <Td><Badge tone="outline">{m.person.kind === "lead" ? "Lead" : "Contact"}</Badge>{m.source !== "manual" && <span className="block text-[11.5px] text-subtle">via {m.source}</span>}</Td>
              <Td>{editable && m.status !== "unsubscribed" ? (
                <Select aria-label={`Status for ${m.person.name}`} className="h-8 w-36 text-[13px]" value={m.status} disabled={patch.isPending} onChange={(e) => patch.mutate({ id: m.id, status: e.target.value })}>
                  {MEMBER_STATUSES.map((s) => <option key={s} value={s}>{nice(s)}</option>)}</Select>
              ) : <Badge tone={m.status === "unsubscribed" ? "warning" : "neutral"}>{nice(m.status)}</Badge>}</Td>
              <Td className="text-[12.5px] text-muted-foreground">{relativeDays(m.added_at)}</Td>
              <Td className="text-[12.5px] text-muted-foreground">{m.sent_at ? relativeDays(m.sent_at) : "—"}</Td>
              <Td className="text-[12.5px] text-muted-foreground">{m.responded_at ? relativeDays(m.responded_at) : "—"}</Td>
              <Td>{editable && <Button variant="ghost" size="icon" aria-label={`Remove ${m.person.name}`} onClick={() => remove.mutate(m.id)}><Trash2 className="h-3.5 w-3.5" /></Button>}</Td>
            </tr>
          ))}
        </Table>
      )}
      {building && <BuildList cp={cp} onClose={() => setBuilding(false)} />}
    </Card>
  );
}

function BuildList({ cp, onClose }: { cp: Campaign; onClose: () => void }) {
  const qc = useQueryClient();
  const meta = useQuery({ queryKey: ["analytics", "sources"], queryFn: () => get<Sources>("/analytics/sources") });
  const [source, setSource] = useState<"leads" | "contacts">("leads");
  const [filters, setFilters] = useState<Filter[]>([]);
  const src = meta.data?.sources.find((s) => s.key === source);
  const add = useMutation({
    mutationFn: async () => (await api.post<{ added: number; skipped: number; matched: number }>(`/campaigns/${cp.id}/members/from-filter`, { source, filters })).data,
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["campaigns"] }); toast.success(`Added ${r.added} of ${r.matched} matching ${source}${r.skipped ? ` (${r.skipped} already in)` : ""}`); onClose(); },
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title="Add members from a filter" className="max-w-lg">
        <div className="space-y-3 p-5">
          <h2 className="text-[15px] font-semibold">Add members from a filter</h2>
          <div className="w-48"><Label htmlFor="bl-source">From</Label><Select id="bl-source" value={source} onChange={(e) => { setSource(e.target.value as "leads" | "contacts"); setFilters([]); }}>
            <option value="leads">Leads</option><option value="contacts">Contacts</option></Select></div>
          {!src ? <Skeleton className="h-20" /> : <>
            <div className="space-y-2">{filters.map((f, i) => (
              <FilterRow key={i} f={f} src={src} periods={meta.data!.periods} onChange={(nf) => setFilters(filters.map((x, j) => (j === i ? nf : x)))} onRemove={() => setFilters(filters.filter((_, j) => j !== i))} />
            ))}</div>
            <Button type="button" variant="outline" size="sm" onClick={() => { const fd = src.fields[0]; setFilters([...filters, { field: fd.key, op: fd.ops[0], value: fd.ops[0] === "within" ? meta.data!.periods[0] : "" }]); }}>
              <Plus className="h-3.5 w-3.5" />Add condition</Button>
            <p className="text-[12px] text-subtle">{filters.length ? "People matching every condition are added." : `With no conditions, every ${source === "leads" ? "lead" : "contact"} is added (up to 5,000).`} People already in the campaign are skipped.</p>
          </>}
          {add.isError && <p className="text-sm text-destructive">{errorMessage(add.error)}</p>}
          <div className="flex justify-end gap-2"><Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button><Button size="sm" loading={add.isPending} onClick={() => add.mutate()}>Add members</Button></div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function EmailTab({ cp, editable }: { cp: Campaign; editable: boolean }) {
  const qc = useQueryClient();
  const [subject, setSubject] = useState(cp.email_subject ?? "");
  const [body, setBody] = useState(cp.email_body ?? "");
  const dirty = subject !== (cp.email_subject ?? "") || body !== (cp.email_body ?? "");
  const preview = useQuery({ queryKey: ["campaigns", "preview", cp.id], queryFn: () => get<Preview>(`/campaigns/${cp.id}/email/preview`) });
  const save = useMutation({
    mutationFn: async () => (await api.put(`/campaigns/${cp.id}`, { name: cp.name, code: cp.code, campaign_type: cp.type, status: cp.status, description: cp.description,
      start_date: cp.start_date, end_date: cp.end_date, budget: cp.budget, actual_cost: cp.actual_cost, expected_revenue: cp.expected_revenue,
      email_subject: subject || null, email_body: body || null })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["campaigns"] }); toast.success("Email saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const send = useMutation({
    mutationFn: async () => (await api.post<{ sent: number; delivered_via_smtp: number; skipped: Record<string, number> }>(`/campaigns/${cp.id}/email/send`)).data,
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      const skipped = Object.values(r.skipped).reduce((a, b) => a + b, 0);
      toast.success(`Sent to ${r.sent}${r.delivered_via_smtp < r.sent ? ` (${r.sent - r.delivered_via_smtp} logged only: no SMTP mailbox connected)` : ""}${skipped ? `; ${skipped} skipped` : ""}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const p = preview.data;
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
      <Card>
        <CardHeader title="Campaign email" icon={<Mail className="h-4 w-4" />}
          description="Placeholders: {{first_name}}, {{last_name}}, {{company}}. An unsubscribe link is added automatically (or place {{unsubscribe_url}} yourself)." />
        <CardBody className="space-y-3">
          <div><Label htmlFor="em-subject">Subject</Label><Input id="em-subject" maxLength={200} disabled={!editable} value={subject} onChange={(e) => setSubject(e.target.value)} /></div>
          <div><Label htmlFor="em-body">Body</Label><Textarea id="em-body" rows={12} disabled={!editable} value={body} onChange={(e) => setBody(e.target.value)} /></div>
          {editable && <div className="flex justify-end"><Button size="sm" variant="outline" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate()}>Save email</Button></div>}
        </CardBody>
      </Card>
      <div className="space-y-4">
        <Card>
          <CardHeader title="Recipients" description="Members still 'targeted' who have an address and allow email." />
          <CardBody className="space-y-2 text-[13px]">
            {!p ? <Skeleton className="h-16" /> : <>
              <p><span className="text-2xl font-semibold tabular">{p.eligible}</span> <span className="text-muted-foreground">will receive it</span></p>
              {Object.entries(p.blocked).map(([why, n]) => <p key={why} className="flex justify-between text-muted-foreground"><span>{why}</span><span className="tabular">{n}</span></p>)}
              {cp.last_sent_at && <p className="text-[12px] text-subtle">Last sent {relativeDays(cp.last_sent_at)}. People are only emailed once.</p>}
              {editable && <Button className="w-full" size="sm" disabled={!p.eligible || dirty || !subject || !body} loading={send.isPending}
                onClick={() => confirm(`Send to ${p.eligible} ${p.eligible === 1 ? "person" : "people"}?`) && send.mutate()}><Send className="h-3.5 w-3.5" />Send to {p.eligible}</Button>}
              {dirty && <p className="text-[12px] text-subtle">Save the email before sending.</p>}
            </>}
          </CardBody>
        </Card>
        {p?.sample && (
          <Card>
            <CardHeader title="Preview" description={`As ${p.sample.to} will see it`} />
            <CardBody className="space-y-2 text-[13px]">
              <p className="font-medium">{p.sample.subject}</p>
              <pre className="whitespace-pre-wrap break-words font-sans text-[12.5px] text-muted-foreground">{p.sample.body}</pre>
            </CardBody>
          </Card>
        )}
      </div>
    </div>
  );
}
