"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, ArrowLeft, Building2, ExternalLink, FileUp, GitMerge, Landmark, LifeBuoy, MapPin, Paperclip, PhoneCall, Plus, Sparkles, UserPlus,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { ActivityTimeline } from "@/components/ActivityTimeline";
import { AgingBars, HealthBreakdown, UsageTrend } from "@/components/charts";
import { NewContactDialog, NewDealDialog, NewTaskDialog } from "@/components/forms";
import { HealthMeter, HealthRing, RiskBadge, RoleBadge } from "@/components/indicators";
import { LogActivityDialog } from "@/components/LogActivityDialog";
import { AlertsBanner, ContractList, CustomFieldsEditor } from "@/components/panels";
import { TaskRow } from "@/components/TaskList";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Field, StatusPill, Table, Tabs, Td, bytes, fmtMoney } from "@/components/ui/extra";
import { Input, Label, Select } from "@/components/ui/input";
import { Avatar, Skeleton } from "@/components/ui/misc";
import { API_URL, api, errorMessage, get, getToken } from "@/lib/api";
import { useMe } from "@/lib/me";
import { ui } from "@/lib/store";
import type { Account360 } from "@/lib/types";
import { money, relativeDays, shortDate } from "@/lib/utils";

type Tab = "overview" | "details" | "hierarchy" | "commercial" | "success" | "files";

interface TreeNode { id: string; name: string; domain: string; health_score: number; lifecycle_stage: string; depth: number; is_current: boolean;
  open_pipeline: number; won_revenue: number; contract_spend: number; rollup: Record<string, number>; descendants: number; children: TreeNode[] }

export default function Account360Page() {
  const { id } = useParams<{ id: string }>();
  const [tab, setTab] = useState<Tab>("overview");
  const [dialog, setDialog] = useState<"deal" | "contact" | "task" | "log" | null>(null);
  const { can } = useMe();
  const { data, isLoading, isError } = useQuery({ queryKey: ["account360", id], queryFn: () => get<Account360>(`/accounts/${id}/360`) });
  const brief = useQuery({ queryKey: ["account-brief", id], queryFn: () => get<{ brief: string; engine: string }>(`/ai/accounts/${id}/brief`) });

  if (isError) return <p className="text-sm text-muted-foreground">Account not found or outside your scope. <Link href="/accounts" className="text-primary hover:underline">Back to accounts</Link></p>;
  if (isLoading || !data) return <div className="mx-auto max-w-7xl space-y-4"><Skeleton className="h-20 w-full" /><Skeleton className="h-64 w-full" /></div>;
  const { account, contacts, summary } = data;

  return (
    <div className="mx-auto max-w-7xl">
      <Link href="/accounts" className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />Accounts</Link>

      <div className="mb-5 flex flex-wrap items-center gap-5">
        <HealthRing score={account.health_score} size={72} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-[24px] font-semibold tracking-tight">{account.name}</h1>
            <Badge tone={account.lifecycle_stage === "customer" ? "good" : account.lifecycle_stage === "churned" ? "critical" : "neutral"} className="capitalize">{account.lifecycle_stage}</Badge>
            {account.customer_master.credit_hold && <Badge tone="critical">Credit hold</Badge>}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-muted-foreground">
            <a href={`https://${account.domain}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:text-foreground">{account.domain}<ExternalLink className="h-3 w-3" /></a>
            <Badge tone="outline">{account.tier}</Badge>
            {account.industry && <span>{account.industry}</span>}
            {account.parent && <span>part of <Link className="text-foreground hover:underline" href={`/accounts/${account.parent.id}`}>{account.parent.name}</Link></span>}
            {account.owner && <span className="flex items-center gap-1.5"><Avatar name={account.owner.full_name} size={18} />{account.owner.full_name}</span>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {can("contacts", "create") && <Button variant="outline" size="sm" onClick={() => setDialog("contact")}><UserPlus className="h-4 w-4" />Contact</Button>}
          {can("deals", "create") && <Button variant="outline" size="sm" onClick={() => setDialog("deal")}><Plus className="h-4 w-4" />Deal</Button>}
          {can("activities", "create") && <Button variant="outline" size="sm" onClick={() => setDialog("log")}><PhoneCall className="h-4 w-4" />Log call/meeting</Button>}
          {can("activities", "create") && <Button variant="ai" size="sm" onClick={() => ui.openQuickLog({ accountId: account.id })}><Sparkles className="h-4 w-4" />Quick-Log</Button>}
        </div>
      </div>

      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-5">
        {[
          { label: "Open pipeline", value: money(summary.open_pipeline, { compact: true }) },
          { label: "Weighted", value: money(summary.weighted_pipeline, { compact: true }) },
          { label: "Active contracts (ACV)", value: money(summary.active_contract_value, { compact: true }) },
          { label: "Credit available", value: data.finance.credit_available == null ? "—" : money(data.finance.credit_available, { compact: true }) },
          { label: "Relationship strength", value: account.relationship_strength == null ? "—" : `${account.relationship_strength}/100` },
        ].map((k) => (
          <div key={k.label} className="rounded-lg border bg-surface px-4 py-3 shadow-card">
            <p className="text-[12px] text-muted-foreground">{k.label}</p>
            <p className="mt-1 text-lg font-semibold">{k.value}</p>
          </div>
        ))}
      </div>

      <AlertsBanner alerts={data.alerts} />

      <Tabs value={tab} onChange={setTab} tabs={[
        { value: "overview", label: "Overview" },
        { value: "details", label: "Details" },
        { value: "hierarchy", label: "Hierarchy", count: account.subsidiaries.length || undefined },
        { value: "commercial", label: "Contracts & finance", count: data.contracts.length || undefined },
        { value: "success", label: "Success", count: data.support_tickets.filter((t) => t.status === "open").length || undefined },
        { value: "files", label: "Files", count: data.files.length || undefined },
      ]} />

      {tab === "overview" && <Overview data={data} brief={brief.data?.brief} onDialog={setDialog} />}
      {tab === "details" && <Details data={data} />}
      {tab === "hierarchy" && <Hierarchy id={account.id} />}
      {tab === "commercial" && <Commercial data={data} />}
      {tab === "success" && <Success data={data} />}
      {tab === "files" && <Files data={data} />}

      <NewDealDialog open={dialog === "deal"} onOpenChange={(o) => setDialog(o ? "deal" : null)} accountId={account.id} />
      <NewContactDialog open={dialog === "contact"} onOpenChange={(o) => setDialog(o ? "contact" : null)} accountId={account.id} />
      <NewTaskDialog open={dialog === "task"} onOpenChange={(o) => setDialog(o ? "task" : null)} accountId={account.id} />
      <LogActivityDialog open={dialog === "log"} onOpenChange={(o) => setDialog(o ? "log" : null)} accountId={account.id} contacts={contacts} />
    </div>
  );
}

function Overview({ data, brief, onDialog }: { data: Account360; brief?: string; onDialog: (d: "task" | "contact" | "log") => void }) {
  const { account, contacts, deals, recent_activities, tasks, summary } = data;
  const [filter, setFilter] = useState("all");
  const acts = filter === "all" ? recent_activities : recent_activities.filter((a) => a.type === filter);
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
      <div className="space-y-6 lg:col-span-2">
        <div className="ai-border rounded-xl p-4 shadow-card">
          <p className="flex items-center gap-1.5 text-[11.5px] font-medium uppercase tracking-wide text-ai"><Sparkles className="h-3.5 w-3.5" />Account brief</p>
          {brief ? <p className="mt-2 text-[14px] leading-relaxed">{brief}</p> : <div className="mt-2 space-y-2"><Skeleton className="w-full" /><Skeleton className="w-3/4" /></div>}
        </div>
        <Card>
          <CardHeader title="Deals" description={`${deals.length} total`} />
          <CardBody className="space-y-3">
            {deals.length === 0 && <p className="text-sm text-muted-foreground">No deals yet.</p>}
            {deals.map((d) => (
              <Link key={d.id} href={`/deals/${d.id}`} className="block rounded-lg border p-3.5 transition-colors hover:border-primary/40">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{d.title}</span>
                  {!d.is_won && !d.is_lost && <RiskBadge score={d.risk_score} />}
                  {d.is_won && <Badge tone="good">Won</Badge>}
                  {d.is_lost && <Badge tone="critical">Lost · {d.loss_reason?.replace(/_/g, " ")}</Badge>}
                  <span className="ml-auto text-[15px] font-semibold">{fmtMoney(d.amount, d.currency)}</span>
                </div>
                <p className="mt-1 text-[12px] text-muted-foreground">
                  {d.stage} · {d.probability}% · weighted {money(d.weighted_value, { compact: true })}{d.target_close_date && ` · closes ${shortDate(d.target_close_date, true)}`}
                </p>
              </Link>
            ))}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Activity ledger" description="Emails, calls, meetings, notes, files, documents and stage changes"
            action={<Select className="h-8 w-36 text-[13px]" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter activity">
              {["all", "email", "call", "meeting", "note", "file", "document", "system"].map((t) => <option key={t} value={t}>{t === "all" ? "All types" : t === "system" ? "Stage changes" : t[0].toUpperCase() + t.slice(1) + "s"}</option>)}
            </Select>} />
          <CardBody><ActivityTimeline activities={acts} /></CardBody>
        </Card>
      </div>
      <div className="space-y-6">
        {account.health_breakdown && (
          <Card>
            <CardHeader title="Health drivers" description="0.30 recency · 0.25 sentiment · 0.15 velocity · 0.15 support · 0.15 milestones" />
            <CardBody>
              <HealthBreakdown breakdown={account.health_breakdown} />
              {!!account.health_breakdown.sentiment_drift && account.health_breakdown.sentiment_drift < 0 && (
                <p className="mt-3 text-[12px] text-muted-foreground">Sentiment drifting {account.health_breakdown.sentiment_drift} points versus earlier notes.</p>
              )}
            </CardBody>
          </Card>
        )}
        <Card>
          <CardHeader title="Buying committee" action={<Button variant="ghost" size="sm" onClick={() => onDialog("contact")}><Plus className="h-3.5 w-3.5" /></Button>} />
          <CardBody className="space-y-3">
            {!summary.has_champion && (
              <div className="flex gap-2 rounded-md border p-2.5 text-[12.5px]" style={{ borderColor: "color-mix(in srgb, var(--status-warning) 50%, transparent)" }}>
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color: "var(--status-serious)" }} />
                <span>No active Champion or Decision Maker. This adds 40 points to every deal&apos;s risk.</span>
              </div>
            )}
            {contacts.map((c) => (
              <Link key={c.id} href={`/contacts/${c.id}`} className={`flex items-center gap-3 rounded-md p-1 hover:bg-muted/60 ${c.status !== "active" ? "opacity-60" : ""}`}>
                <Avatar name={c.name} size={32} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13.5px] font-medium">{c.name}{c.status === "departed" && <span className="ml-1.5 text-[11px] font-normal text-muted-foreground">departed</span>}</p>
                  <p className="truncate text-[12px] text-muted-foreground">{c.job_title ?? c.email ?? "—"}{c.relationship_strength != null && c.status === "active" && ` · RSI ${c.relationship_strength}`}</p>
                </div>
                <RoleBadge role={c.buying_role} />
              </Link>
            ))}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Tasks" description={`${tasks.filter((t) => !t.completed).length} open`} action={<Button variant="ghost" size="sm" onClick={() => onDialog("task")}><Plus className="h-3.5 w-3.5" /></Button>} />
          <CardBody className="px-3">
            {tasks.length === 0 && <p className="px-2 text-sm text-muted-foreground">No tasks.</p>}
            {tasks.slice(0, 8).map((t) => <TaskRow key={t.id} task={t} showContext={false} />)}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function Details({ data }: { data: Account360 }) {
  const qc = useQueryClient();
  const { can } = useMe();
  const { account } = data;
  const cm = account.customer_master;
  const [edit, setEdit] = useState(false);
  const [f, setF] = useState({ annual_revenue: account.annual_revenue ?? "", employee_count: account.employee_count ?? "", industry_code: account.industry_code ?? "",
    lifecycle_stage: account.lifecycle_stage, alt_domains: account.alt_domains.join(", "), city: "", country: "" });
  const save = useMutation({
    mutationFn: async (body: Record<string, unknown>) => (await api.patch(`/accounts/${account.id}`, body)).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["account360", account.id] }); setEdit(false); toast.success("Account updated"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const addr = Object.values(cm.billing_address || {}).filter(Boolean).join(", ");
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader title="Firmographics" icon={<Building2 className="h-4 w-4 text-muted-foreground" />}
          action={can("accounts", "update") && !edit && <Button variant="ghost" size="sm" onClick={() => setEdit(true)}>Edit</Button>} />
        <CardBody>
          {!edit ? (
            <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
              <Field label="Industry">{account.industry}</Field>
              <Field label="Industry code (NAICS)">{account.industry_code}</Field>
              <Field label="Annual revenue">{account.annual_revenue != null ? money(account.annual_revenue, { compact: true }) : null}</Field>
              <Field label="Employees">{account.employee_count?.toLocaleString()}</Field>
              <Field label="Tier">{account.tier}</Field>
              <Field label="Lifecycle"><span className="capitalize">{account.lifecycle_stage}</span></Field>
              <Field label="Domains" className="sm:col-span-2">{[account.domain, ...account.alt_domains].join(", ")}</Field>
              <Field label="Locations" className="sm:col-span-2">
                {account.locations.length ? (
                  <span className="flex flex-wrap gap-1.5">{account.locations.map((l, i) => <Badge key={i} tone="outline"><MapPin className="h-3 w-3" />{l.type}: {l.city}{l.region ? `, ${l.region}` : ""}, {l.country}</Badge>)}</span>
                ) : null}
              </Field>
            </dl>
          ) : (
            <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => {
              e.preventDefault();
              const locations = f.city && f.country ? [...account.locations, { type: "office", city: f.city, country: f.country }] : undefined;
              save.mutate({ annual_revenue: f.annual_revenue === "" ? null : Number(f.annual_revenue), employee_count: f.employee_count === "" ? null : Number(f.employee_count),
                industry_code: f.industry_code || null, lifecycle_stage: f.lifecycle_stage, alt_domains: f.alt_domains.split(",").map((d) => d.trim()).filter(Boolean),
                ...(locations ? { locations } : {}) });
            }}>
              <div><Label>Annual revenue (USD)</Label><Input type="number" value={f.annual_revenue} onChange={(e) => setF({ ...f, annual_revenue: e.target.value })} /></div>
              <div><Label>Employees</Label><Input type="number" value={f.employee_count} onChange={(e) => setF({ ...f, employee_count: e.target.value })} /></div>
              <div><Label>Industry code</Label><Input value={f.industry_code} onChange={(e) => setF({ ...f, industry_code: e.target.value })} /></div>
              <div><Label>Lifecycle</Label><Select value={f.lifecycle_stage} onChange={(e) => setF({ ...f, lifecycle_stage: e.target.value })}>{["prospect", "customer", "churned", "partner"].map((s) => <option key={s}>{s}</option>)}</Select></div>
              <div className="sm:col-span-2"><Label>Alternate domains (comma separated)</Label><Input value={f.alt_domains} onChange={(e) => setF({ ...f, alt_domains: e.target.value })} /></div>
              <div><Label>Add location: city</Label><Input value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} /></div>
              <div><Label>Country</Label><Input value={f.country} onChange={(e) => setF({ ...f, country: e.target.value })} placeholder="US" /></div>
              <div className="flex gap-2 sm:col-span-2"><Button size="sm" type="submit" loading={save.isPending}>Save</Button><Button size="sm" variant="ghost" type="button" onClick={() => setEdit(false)}>Cancel</Button></div>
            </form>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Customer master (ERP)" icon={<Landmark className="h-4 w-4 text-muted-foreground" />}
          description={cm.erp_synced_at ? `Synced from ERP ${relativeDays(cm.erp_synced_at)}` : "Not yet linked to the ERP"} />
        <CardBody>
          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
            <Field label="Legal entity">{cm.legal_name}</Field>
            <Field label="Tax ID">{cm.tax_id}</Field>
            <Field label="ERP customer ID">{cm.erp_customer_id}</Field>
            <Field label="Payment terms">{cm.payment_terms}</Field>
            <Field label="Credit limit">{cm.credit_limit != null ? money(cm.credit_limit) : null}</Field>
            <Field label="Credit status">{cm.credit_hold ? <span className="font-medium" style={{ color: "var(--status-critical)" }}>On hold</span> : "Good standing"}</Field>
            <Field label="Billing address" className="sm:col-span-2">{addr || null}</Field>
          </dl>
        </CardBody>
      </Card>
      <Card className="lg:col-span-2">
        <CardHeader title="Custom attributes" description="Tenant-defined fields stored in schemaless JSONB, validated against their definitions" />
        <CardBody>
          <CustomFieldsEditor defs={data.custom_field_definitions} values={account.custom_fields} canEdit={can("accounts", "update")} saving={save.isPending}
            onSave={(v) => save.mutate({ custom_fields: v })} />
        </CardBody>
      </Card>
    </div>
  );
}

function Hierarchy({ id }: { id: string }) {
  const { data } = useQuery({ queryKey: ["hierarchy", id], queryFn: () => get<{ root: TreeNode | null }>(`/accounts/${id}/hierarchy`) });
  if (!data?.root) return <Skeleton className="h-40 w-full" />;
  const rows: TreeNode[] = [];
  const walk = (n: TreeNode) => { rows.push(n); n.children.forEach(walk); };
  walk(data.root);
  return (
    <Card className="overflow-hidden">
      <CardHeader title="Corporate family" description="Pipeline, won revenue and contract spend roll up from subsidiaries to parents" />
      <Table head={["Entity", "Health", "Open pipeline (own)", "Roll-up pipeline", "Roll-up won", "Roll-up contract spend"]} minWidth={760}>
        {rows.map((n) => (
          <tr key={n.id} className={n.is_current ? "bg-primary-soft/40" : ""}>
            <Td>
              <div className="flex items-center gap-1.5" style={{ paddingLeft: n.depth * 20 }}>
                {n.depth > 0 && <span className="text-subtle">└</span>}
                <Link href={`/accounts/${n.id}`} className="font-medium hover:underline">{n.name}</Link>
                {n.descendants > 0 && <span className="text-[11px] text-muted-foreground">({n.descendants} below)</span>}
              </div>
              <p className="text-[12px] text-muted-foreground" style={{ paddingLeft: n.depth * 20 + (n.depth ? 14 : 0) }}>{n.domain} · <span className="capitalize">{n.lifecycle_stage}</span></p>
            </Td>
            <Td><HealthMeter score={n.health_score} /></Td>
            <Td className="tabular">{money(n.open_pipeline, { compact: true })}</Td>
            <Td className="tabular font-medium">{money(n.rollup.open_pipeline, { compact: true })}</Td>
            <Td className="tabular">{money(n.rollup.won_revenue, { compact: true })}</Td>
            <Td className="tabular">{money(n.rollup.contract_spend, { compact: true })}</Td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

function Commercial({ data }: { data: Account360 }) {
  const f = data.finance;
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader title="Contracts" />
        <CardBody><ContractList contracts={data.contracts} /></CardBody>
      </Card>
      <Card>
        <CardHeader title="Accounts receivable" icon={<Landmark className="h-4 w-4 text-muted-foreground" />}
          description={`Open ${money(f.open_balance)} · overdue ${money(f.overdue_balance)}${f.credit_limit != null ? ` · limit ${money(f.credit_limit)}` : ""}`} />
        <CardBody>
          {f.credit_hold && <p className="mb-3 rounded-md border px-3 py-2 text-[12.5px]" style={{ borderColor: "color-mix(in srgb, var(--status-critical) 45%, transparent)" }}><span className="font-medium">Credit hold</span>: new quotes require finance approval.</p>}
          <AgingBars buckets={f.buckets} />
          {!!f.invoices?.length && (
            <div className="mt-4 space-y-1.5">
              {f.invoices.map((i) => (
                <div key={i.id} className="flex items-center gap-2 text-[12.5px]">
                  <span className="w-28 font-mono">{i.invoice_number}</span>
                  <span className="text-muted-foreground">due {shortDate(i.due_date)}</span>
                  <span className="ml-auto tabular">{fmtMoney(i.balance, i.currency)}</span>
                  <StatusPill status={i.status === "open" && i.days_overdue > 0 ? "overdue" : i.status} />
                </div>
              ))}
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function Success({ data }: { data: Account360 }) {
  const churn = data.account.churn_factors as Record<string, unknown>;
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader title="Churn early warning" icon={<LifeBuoy className="h-4 w-4 text-muted-foreground" />} description={`Churn risk ${data.account.churn_risk}/100`} />
        <CardBody className="space-y-2 text-[13px]">
          {Object.keys(churn).length === 0 && <p className="text-muted-foreground">No churn signals.</p>}
          {churn.utilization_pct !== undefined && <p>Seat utilisation <span className="font-medium">{String(churn.utilization_pct)}%</span>{churn.active_user_trend_pct !== undefined && ` (trend ${String(churn.active_user_trend_pct)}% over 60 days)`}</p>}
          {!!churn.champions_departed && <p>Champion turnover: <span className="font-medium">{(churn.champions_departed as string[]).join(", ")}</span> left{churn.no_remaining_champion ? "; no champion remains" : ""}</p>}
          {(churn.open_critical_tickets !== undefined) && <p>Open tickets: {String(churn.open_critical_tickets)} critical, {String(churn.open_high_tickets)} high</p>}
          {churn.low_health !== undefined && <p>Low account health ({String(churn.low_health)})</p>}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Product adoption" />
        <CardBody><UsageTrend points={data.usage} /></CardBody>
      </Card>
      <Card>
        <CardHeader title="Support tickets" />
        <CardBody className="space-y-2">
          {!data.support_tickets.length && <p className="text-[13px] text-muted-foreground">No tickets.</p>}
          {data.support_tickets.map((t) => (
            <div key={t.id} className="flex items-center gap-2 text-[13px]">
              <Badge tone={t.severity === "critical" || t.severity === "high" ? "critical" : "neutral"} className="capitalize">{t.severity}</Badge>
              <span className="flex-1 truncate">{t.subject}</span>
              <span className="text-[12px] text-muted-foreground">{relativeDays(t.opened_at)}</span>
              <StatusPill status={t.status} />
            </div>
          ))}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Onboarding" />
        <CardBody className="space-y-3">
          {!data.onboarding.length && <p className="text-[13px] text-muted-foreground">Provisioned automatically when a deal is Closed-Won.</p>}
          {data.onboarding.map((p) => (
            <div key={p.id}>
              <div className="flex items-center gap-2 text-[13.5px]"><span className="flex-1 font-medium">{p.name}</span><StatusPill status={p.status} /></div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-series-track"><div className="h-full rounded-full bg-series-1" style={{ width: `${Math.max(2, p.progress)}%` }} /></div>
              <p className="mt-1 text-[12px] text-muted-foreground">{p.progress}% complete · go-live {shortDate(p.target_go_live, true)}{p.overdue ? ` · ${p.overdue} milestone(s) overdue` : ""}</p>
              <Link href="/success" className="text-[12.5px] font-medium text-primary hover:underline">Open workspace</Link>
            </div>
          ))}
        </CardBody>
      </Card>
    </div>
  );
}

function Files({ data }: { data: Account360 }) {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const upload = useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return (await api.post(`/accounts/${data.account.id}/files`, fd)).data;
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["account360", data.account.id] }); toast.success("File attached to the timeline"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const download = async (id: string, name: string) => {
    const res = await fetch(`${API_URL}/api/v1/files/${id}`, { headers: { Authorization: `Bearer ${getToken()}` } });
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
  };
  return (
    <Card>
      <CardHeader title="Files" icon={<Paperclip className="h-4 w-4 text-muted-foreground" />} description="Attachments, signed contracts and generated documents"
        action={<><input ref={input} type="file" className="hidden" onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} />
          <Button size="sm" variant="outline" loading={upload.isPending} onClick={() => input.current?.click()}><FileUp className="h-4 w-4" />Upload</Button></>} />
      <CardBody className="space-y-1.5">
        {!data.files.length && <p className="text-[13px] text-muted-foreground">No files yet.</p>}
        {data.files.map((f) => (
          <button key={f.id} onClick={() => download(f.id, f.filename)} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] hover:bg-muted">
            <Paperclip className="h-3.5 w-3.5 text-muted-foreground" /><span className="flex-1 truncate">{f.filename}</span>
            <span className="text-[12px] text-muted-foreground">{bytes(f.size_bytes)} · {relativeDays(f.created_at)}</span>
          </button>
        ))}
        <p className="pt-2 text-[12px] text-subtle"><GitMerge className="mr-1 inline h-3 w-3" />Files follow the account through merges.</p>
      </CardBody>
    </Card>
  );
}
