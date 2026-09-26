"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRightLeft, Ban, CheckCircle2, CircleDashed, RefreshCcw, Route, Sparkles, Users } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { BUYING_ROLES } from "@/components/indicators";
import { DISQUALIFY_REASONS, EVENT_TYPES, ScoreBar } from "@/components/leads";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, StatusPill } from "@/components/ui/extra";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import type { LeadDetail, PipelineFull, UserBrief } from "@/lib/types";
import { cn, relativeDays, shortDate } from "@/lib/utils";

type Crit = Record<string, { met: boolean; note: string }>;

export default function LeadPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const { me, can } = useMe();
  const { data: lead, isLoading, isError } = useQuery({ queryKey: ["lead", id], queryFn: () => get<LeadDetail>(`/leads/${id}`) });
  const [framework, setFramework] = useState<"bant" | "meddpicc">("bant");
  const [crit, setCrit] = useState<Crit>({});
  const [event, setEvent] = useState({ event_type: "meeting_booked", detail: "" });
  const [convertOpen, setConvertOpen] = useState(false);
  const [dqOpen, setDqOpen] = useState(false);

  useEffect(() => {
    if (!lead) return;
    setFramework(lead.qualification.framework);
    setCrit(Object.fromEntries(lead.qualification.items.map((i) => [i.key, { met: i.met, note: i.note ?? "" }])));
  }, [lead]);

  const refresh = () => { qc.invalidateQueries({ queryKey: ["lead", id] }); qc.invalidateQueries({ queryKey: ["leads"] }); };
  const action = useMutation({
    mutationFn: async (v: { path: string; body?: unknown; ok: string }) => (await api.post(`/leads/${id}/${v.path}`, v.body ?? {})).data,
    onSuccess: (_, v) => { refresh(); toast.success(v.ok); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const saveQual = useMutation({
    mutationFn: async () => (await api.put<LeadDetail>(`/leads/${id}/qualification`, { framework, criteria: crit })).data,
    onSuccess: (l) => { refresh(); toast.success(l.status === "sql" ? "Sales qualified: ready to convert" : "Qualification saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (isError) return <p className="text-sm text-muted-foreground">Lead not found. <Link href="/leads" className="text-primary hover:underline">Back to leads</Link></p>;
  if (isLoading || !lead) return <div className="mx-auto max-w-6xl space-y-4"><Skeleton className="h-20 w-full" /><Skeleton className="h-64 w-full" /></div>;

  const threshold = lead.score_breakdown.mql_threshold ?? 60;
  const items = lead.frameworks[framework];
  const met = items.filter((i) => crit[i.key]?.met).length;
  const minimum = framework === "bant" ? 3 : 5;
  const closed = lead.status === "converted" || lead.status === "disqualified";
  const editable = can("leads", "update") && !closed;
  const fit = lead.score_breakdown.fit ?? {};
  const eng = lead.score_breakdown.engagement ?? {};
  const enriched = Object.entries(lead.enrichment ?? {}).filter(([, v]) => v !== null && v !== "" && !(Array.isArray(v) && v.length === 0));

  return (
    <div className="mx-auto max-w-6xl">
      <Link href="/leads" className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />Leads</Link>
      <div className="mb-5 flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-[24px] font-semibold tracking-tight">{lead.full_name || lead.email}</h1>
            <StatusPill status={lead.status} />
            {lead.account_match && <Badge tone="primary">Matches account {lead.account_match.name}</Badge>}
          </div>
          <p className="mt-1 text-[13.5px] text-muted-foreground">
            {[lead.job_title, lead.company_name].filter(Boolean).join(" at ")}{lead.email && <> · {lead.email}</>}
            {lead.owner && <> · owned by {lead.owner.full_name}</>} · {lead.source.replace("_", " ")}{lead.campaign && ` (${lead.campaign})`}
          </p>
        </div>
        {editable && (
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" onClick={() => action.mutate({ path: "enrich", ok: "Enriched" })}><Sparkles className="h-3.5 w-3.5" />Enrich</Button>
            <Button variant="outline" size="sm" onClick={() => action.mutate({ path: "route", body: { force: true }, ok: "Re-routed" })}><Route className="h-3.5 w-3.5" />Re-route</Button>
            <Button variant="outline" size="sm" onClick={() => setDqOpen(true)}><Ban className="h-3.5 w-3.5" />Disqualify</Button>
            <Button size="sm" onClick={() => setConvertOpen(true)}><ArrowRightLeft className="h-3.5 w-3.5" />Convert</Button>
          </div>
        )}
        {lead.status === "disqualified" && can("leads", "update") && (
          <Button variant="outline" size="sm" onClick={() => action.mutate({ path: "recycle", ok: "Recycled into nurture" })}><RefreshCcw className="h-3.5 w-3.5" />Recycle</Button>
        )}
      </div>

      {lead.status === "converted" && (
        <Card className="mb-5 p-4 text-[13.5px]">
          <p className="font-medium">Converted {relativeDays(lead.converted_at)}</p>
          <p className="mt-1 text-muted-foreground">
            {lead.converted.account_id && <Link className="text-primary hover:underline" href={`/accounts/${lead.converted.account_id}`}>Account</Link>}
            {lead.converted.contact_id && <> · <Link className="text-primary hover:underline" href={`/contacts/${lead.converted.contact_id}`}>Contact</Link></>}
            {lead.converted.deal_id && <> · <Link className="text-primary hover:underline" href={`/deals/${lead.converted.deal_id}`}>Opportunity</Link></>}
          </p>
        </Card>
      )}
      {lead.status === "disqualified" && (
        <Card className="mb-5 p-4 text-[13.5px]"><span className="font-medium">Disqualified:</span> {lead.disqualified_reason?.replace(/_/g, " ")}{lead.disqualify_note && `: ${lead.disqualify_note}`}</Card>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card>
            <CardHeader title="Lead score" description={`${Math.round((lead.score_breakdown.weights?.fit ?? 0.5) * 100)}% fit + ${Math.round((lead.score_breakdown.weights?.engagement ?? 0.5) * 100)}% engagement · MQL at ${threshold}`}
              action={<ScoreBar score={lead.score} threshold={threshold} />} />
            <CardBody className="grid grid-cols-1 gap-6 md:grid-cols-2">
              <div>
                <p className="mb-2 text-[12px] font-medium text-muted-foreground">Fit to ICP · {lead.fit_score}</p>
                {Object.entries(fit).map(([k, p]) => (
                  <div key={k} className="flex items-center gap-2 py-1 text-[13px]">
                    <span className="w-24 capitalize text-muted-foreground">{k}</span>
                    <span className="min-w-0 flex-1 truncate">{p.value == null ? <span className="text-subtle">unknown</span> : typeof p.value === "number" ? p.value.toLocaleString() : String(p.value)}</span>
                    <span className={cn("tabular font-medium", !p.points && "text-subtle")}>+{p.points}</span>
                  </div>
                ))}
              </div>
              <div>
                <p className="mb-2 text-[12px] font-medium text-muted-foreground">Engagement (30-day half-life) · {lead.engagement_score}</p>
                {Object.keys(eng).length === 0 && <p className="text-[13px] text-subtle">No engagement yet.</p>}
                {Object.entries(eng).map(([k, v]) => (
                  <div key={k} className="flex items-center gap-2 py-1 text-[13px]">
                    <span className="flex-1 capitalize">{k.replace(/_/g, " ")}</span><span className="tabular font-medium">+{v}</span>
                  </div>
                ))}
                {lead.mql_at && <p className="mt-2 text-[12px] text-muted-foreground">Became MQL {shortDate(lead.mql_at, true)}</p>}
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Qualification" description={`${met}/${items.length} confirmed · conversion needs ${minimum}`}
              action={
                <div className="flex rounded-md border p-0.5 text-[12px]">
                  {(["bant", "meddpicc"] as const).map((fw) => (
                    <button key={fw} disabled={!editable} onClick={() => { setFramework(fw); setCrit({}); }}
                      className={cn("rounded px-2 py-1 uppercase", framework === fw ? "bg-primary text-primary-foreground" : "text-muted-foreground")}>{fw}</button>
                  ))}
                </div>
              } />
            <CardBody className="space-y-2">
              {items.map((i) => {
                const c = crit[i.key] ?? { met: false, note: "" };
                return (
                  <div key={i.key} className="flex flex-wrap items-center gap-2">
                    <button disabled={!editable} onClick={() => setCrit({ ...crit, [i.key]: { ...c, met: !c.met } })}
                      className="flex w-44 items-center gap-2 text-left text-[13.5px]" aria-pressed={c.met}>
                      {c.met ? <CheckCircle2 className="h-4 w-4 text-primary" /> : <CircleDashed className="h-4 w-4 text-subtle" />}{i.label}
                    </button>
                    <Input className="h-8 min-w-0 flex-1" disabled={!editable} placeholder="Evidence (who said what, when)" value={c.note}
                      onChange={(e) => setCrit({ ...crit, [i.key]: { ...c, note: e.target.value } })} />
                  </div>
                );
              })}
              {editable && <div className="flex justify-end pt-1"><Button size="sm" loading={saveQual.isPending} onClick={() => saveQual.mutate()}>Save qualification</Button></div>}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Engagement timeline" />
            <CardBody>
              {editable && (
                <form className="mb-4 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); action.mutate({ path: "events", body: event, ok: "Engagement logged and rescored" }); }}>
                  <Select className="h-8 w-48" value={event.event_type} onChange={(e) => setEvent({ ...event, event_type: e.target.value })}>
                    {EVENT_TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
                  </Select>
                  <Input className="h-8 min-w-0 flex-1" placeholder="Detail" value={event.detail} onChange={(e) => setEvent({ ...event, detail: e.target.value })} />
                  <Button size="sm" variant="outline" type="submit">Log</Button>
                </form>
              )}
              <ol className="space-y-2.5">
                {lead.events.map((e) => (
                  <li key={e.id} className="flex items-start gap-3 text-[13px]">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                    <div className="min-w-0 flex-1">
                      <p><span className="font-medium capitalize">{e.event_type.replace(/_/g, " ")}</span>{e.detail && <span className="text-muted-foreground"> · {e.detail}</span>}</p>
                      <p className="text-[12px] text-subtle">{shortDate(e.occurred_at, true)}{e.source && ` · ${e.source.replace("_", " ")}`}</p>
                    </div>
                    <span className="tabular text-[12px] text-muted-foreground">{e.points} pts</span>
                  </li>
                ))}
              </ol>
            </CardBody>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Profile" />
            <CardBody className="grid grid-cols-2 gap-3">
              <Field label="Company">{lead.company_name}</Field>
              <Field label="Domain">{lead.domain}</Field>
              <Field label="Industry">{lead.industry}</Field>
              <Field label="Employees">{lead.employee_count?.toLocaleString()}</Field>
              <Field label="Country">{lead.country}</Field>
              <Field label="Region">{lead.region}</Field>
              <Field label="Email consent"><span className="capitalize">{lead.consent_email}</span>{lead.privacy_regime && ` · ${lead.privacy_regime}`}</Field>
              <Field label="Assigned">{lead.assigned_at ? relativeDays(lead.assigned_at) : null}</Field>
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Duplicate check" icon={<Users className="h-4 w-4 text-muted-foreground" />} />
            <CardBody>
              {lead.duplicate_matches.length === 0 ? <p className="text-[13px] text-muted-foreground">No matches in leads, contacts or accounts.</p> : (
                <ul className="space-y-2 text-[13px]">
                  {lead.duplicate_matches.map((m) => (
                    <li key={`${m.type}-${m.id}`} className="flex items-center gap-2">
                      <Badge tone={m.type === "account" ? "primary" : "warning"} className="capitalize">{m.type}</Badge>
                      <Link className="min-w-0 flex-1 truncate hover:underline" href={m.type === "lead" ? `/leads/${m.id}` : m.type === "contact" ? `/contacts/${m.id}` : `/accounts/${m.id}`}>{m.name}</Link>
                      {m.reason && <span className="text-[12px] text-subtle">{m.reason}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>
          {enriched.length > 0 && (
            <Card>
              <CardHeader title="Enrichment" description={lead.enriched_at ? `Updated ${relativeDays(lead.enriched_at)}` : undefined} />
              <CardBody className="space-y-1 text-[12.5px]">
                {enriched.map(([k, v]) => <p key={k}><span className="text-muted-foreground">{k.replace(/_/g, " ")}:</span> {typeof v === "object" ? JSON.stringify(v) : String(v)}</p>)}
              </CardBody>
            </Card>
          )}
        </div>
      </div>

      <ConvertDialog lead={lead} open={convertOpen} onOpenChange={setConvertOpen} canOverride={me?.role === "super_admin" || me?.role === "sales_manager"}
        qualified={met >= minimum} />
      <DisqualifyDialog id={lead.id} open={dqOpen} onOpenChange={setDqOpen} onDone={refresh} />
    </div>
  );
}

function ConvertDialog({ lead, open, onOpenChange, canOverride, qualified }: {
  lead: LeadDetail; open: boolean; onOpenChange: (o: boolean) => void; canOverride: boolean; qualified: boolean;
}) {
  const router = useRouter();
  const qc = useQueryClient();
  const { data: pipelines } = useQuery({ queryKey: ["pipelines"], queryFn: () => get<PipelineFull[]>("/pipelines"), enabled: open });
  const { data: users } = useQuery({ queryKey: ["users"], queryFn: () => get<(UserBrief & { role: string })[]>("/users"), enabled: open });
  const solution = pipelines?.find((p) => p.name === "Enterprise Solution Sale") ?? pipelines?.[0];
  const [f, setF] = useState({ create_deal: true, deal_title: "", amount: "", pipeline_id: "", owner_id: "", buying_role: "Champion", target_close_date: "", override: false });
  const m = useMutation({
    mutationFn: async () => (await api.post<{ account_id: string; deal_id: string | null; created: Record<string, boolean> }>(`/leads/${lead.id}/convert`, {
      create_deal: f.create_deal, deal_title: f.deal_title || `${lead.company_name ?? lead.full_name}: new opportunity`, amount: Number(f.amount || 0),
      pipeline_id: f.pipeline_id || solution?.id, owner_id: f.owner_id || null, buying_role: f.buying_role, target_close_date: f.target_close_date || null,
      override: f.override,
    })).data,
    onSuccess: (r) => {
      qc.invalidateQueries();
      onOpenChange(false);
      toast.success(`Converted: ${r.created.account ? "new" : "existing"} account, ${r.created.contact ? "new" : "existing"} contact${r.deal_id ? ", opportunity created" : ""}`);
      router.push(r.deal_id ? `/deals/${r.deal_id}` : `/accounts/${r.account_id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Convert lead" className="max-w-lg">
        <form className="p-5" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <h2 className="text-[15px] font-semibold">Convert to account, contact and opportunity</h2>
          <p className="mt-0.5 text-[13px] text-muted-foreground">
            {lead.account_match ? `Links to the existing account ${lead.account_match.name}.` : `Creates the account ${lead.company_name ?? lead.domain ?? ""}.`}
            {" "}Consent, engagement history and qualification carry over.
          </p>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <label className="col-span-2 flex items-center gap-2 text-[13px]"><input type="checkbox" checked={f.create_deal} onChange={(e) => setF({ ...f, create_deal: e.target.checked })} />Create an opportunity</label>
            {f.create_deal && <>
              <div className="col-span-2"><Label htmlFor="cv-title">Opportunity name</Label><Input id="cv-title" placeholder={`${lead.company_name ?? ""}: new opportunity`} value={f.deal_title} onChange={(e) => setF({ ...f, deal_title: e.target.value })} /></div>
              <div><Label htmlFor="cv-amt">Amount (USD)</Label><Input id="cv-amt" type="number" min={0} value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></div>
              <div><Label htmlFor="cv-close">Target close</Label><Input id="cv-close" type="date" value={f.target_close_date} onChange={(e) => setF({ ...f, target_close_date: e.target.value })} /></div>
              <div className="col-span-2"><Label htmlFor="cv-pipe">Pipeline</Label>
                <Select id="cv-pipe" value={f.pipeline_id || solution?.id || ""} onChange={(e) => setF({ ...f, pipeline_id: e.target.value })}>
                  {pipelines?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </Select></div>
              <div><Label htmlFor="cv-owner">Opportunity owner</Label>
                <Select id="cv-owner" value={f.owner_id} onChange={(e) => setF({ ...f, owner_id: e.target.value })}>
                  <option value="">{lead.owner ? `${lead.owner.full_name} (lead owner)` : "Me"}</option>
                  {users?.filter((u) => ["account_executive", "sales_manager"].includes(u.role)).map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
                </Select></div>
            </>}
            <div><Label htmlFor="cv-role">Contact buying role</Label>
              <Select id="cv-role" value={f.buying_role} onChange={(e) => setF({ ...f, buying_role: e.target.value })}>{BUYING_ROLES.filter((r) => r !== "Blocker").map((r) => <option key={r}>{r}</option>)}</Select></div>
          </div>
          {!qualified && (
            <p className="mt-3 rounded-md bg-muted p-2.5 text-[12.5px]">
              Qualification is below the conversion minimum. {canOverride ? "As a manager you can override it:" : "Confirm more criteria first, or ask a sales manager."}
              {canOverride && <label className="mt-1.5 flex items-center gap-2"><input type="checkbox" checked={f.override} onChange={(e) => setF({ ...f, override: e.target.checked })} />Override the qualification gate</label>}
            </p>
          )}
          <div className="mt-5 flex justify-end"><Button type="submit" size="sm" loading={m.isPending}>Convert</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DisqualifyDialog({ id, open, onOpenChange, onDone }: { id: string; open: boolean; onOpenChange: (o: boolean) => void; onDone: () => void }) {
  const [reason, setReason] = useState<string>("not_icp");
  const [note, setNote] = useState("");
  const m = useMutation({
    mutationFn: async () => (await api.post(`/leads/${id}/disqualify`, { reason, note: note || null })).data,
    onSuccess: () => { onDone(); onOpenChange(false); toast.success("Lead disqualified"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Disqualify lead">
        <form className="p-5" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <h2 className="text-[15px] font-semibold">Disqualify lead</h2>
          <div className="mt-4 space-y-3">
            <div><Label htmlFor="dq-reason">Reason</Label><Select id="dq-reason" value={reason} onChange={(e) => setReason(e.target.value)}>
              {DISQUALIFY_REASONS.map((r) => <option key={r} value={r}>{r.replace(/_/g, " ")}</option>)}</Select></div>
            <div><Label htmlFor="dq-note">Note</Label><Textarea id="dq-note" value={note} onChange={(e) => setNote(e.target.value)} /></div>
          </div>
          <div className="mt-5 flex justify-end"><Button type="submit" size="sm" loading={m.isPending}>Disqualify</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
