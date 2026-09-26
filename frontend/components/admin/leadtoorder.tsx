"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, KeyRound, Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { CopyButton, SOURCES } from "@/components/leads";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Table, Td, Tabs } from "@/components/ui/extra";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import type { PipelineFull, UserBrief } from "@/lib/types";
import { relativeDays } from "@/lib/utils";

type U = UserBrief & { role: string; email: string };
const useUsers = () => useQuery({ queryKey: ["users"], queryFn: () => get<U[]>("/users") });
const csv = (v: string) => v.split(",").map((x) => x.trim()).filter(Boolean);

// ---- lead management -------------------------------------------------------------------------------------------
interface LeadSettings {
  icp: { industries: string[]; min_employees: number; max_employees: number; min_revenue: number; regions: string[]; countries: string[] };
  fit_weights: Record<string, number>; event_points: Record<string, number>; engagement_half_life_days: number;
  score_weights: { fit: number; engagement: number }; mql_threshold: number; conversion_min_criteria: { bant: number; meddpicc: number };
}

export function LeadManagementPanel() {
  const [tab, setTab] = useState<"scoring" | "routing" | "intake">("scoring");
  return (
    <div>
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "scoring", label: "Scoring & ICP" }, { value: "routing", label: "Assignment rules" }, { value: "intake", label: "Web forms & webhooks" }]} />
      {tab === "scoring" && <ScoringPanel />}
      {tab === "routing" && <RoutingPanel />}
      {tab === "intake" && <IntakePanel />}
    </div>
  );
}

function ScoringPanel() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["lead-settings"], queryFn: () => get<LeadSettings>("/admin/lead-settings") });
  const [s, setS] = useState<LeadSettings | null>(null);
  useEffect(() => { if (data) setS(data); }, [data]);
  const save = useMutation({
    mutationFn: async () => (await api.put<LeadSettings & { rescored: number }>("/admin/lead-settings", s)).data,
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["lead-settings"] }); qc.invalidateQueries({ queryKey: ["leads"] }); toast.success(`Saved; ${r.rescored} open leads rescored`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (!s) return <Skeleton className="h-80 w-full" />;
  const icp = s.icp;
  const num = (v: string) => Number(v) || 0;
  return (
    <Card>
      <CardHeader title="Lead scoring" description="Score = fit × weight + engagement × weight. Engagement points halve every half-life. Leads at or above the threshold become MQLs and route to SDRs."
        action={<Button size="sm" loading={save.isPending} onClick={() => save.mutate()}><Save className="h-3.5 w-3.5" />Save & rescore</Button>} />
      <CardBody className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-3">
          <p className="text-[12px] font-medium text-muted-foreground">Ideal customer profile (fit)</p>
          <div><Label>Industries</Label><Textarea className="min-h-[60px]" value={icp.industries.join(", ")} onChange={(e) => setS({ ...s, icp: { ...icp, industries: csv(e.target.value) } })} /></div>
          <div className="grid grid-cols-3 gap-2">
            <div><Label>Min employees</Label><Input type="number" value={icp.min_employees} onChange={(e) => setS({ ...s, icp: { ...icp, min_employees: num(e.target.value) } })} /></div>
            <div><Label>Max employees</Label><Input type="number" value={icp.max_employees} onChange={(e) => setS({ ...s, icp: { ...icp, max_employees: num(e.target.value) } })} /></div>
            <div><Label>Min revenue</Label><Input type="number" value={icp.min_revenue} onChange={(e) => setS({ ...s, icp: { ...icp, min_revenue: num(e.target.value) } })} /></div>
          </div>
          <div><Label>Target regions</Label><Input value={icp.regions.join(", ")} onChange={(e) => setS({ ...s, icp: { ...icp, regions: csv(e.target.value) } })} /></div>
          <p className="pt-2 text-[12px] font-medium text-muted-foreground">Fit weights (points)</p>
          <div className="grid grid-cols-5 gap-2">
            {Object.entries(s.fit_weights).map(([k, v]) => (
              <div key={k}><Label className="capitalize">{k}</Label><Input type="number" value={v} onChange={(e) => setS({ ...s, fit_weights: { ...s.fit_weights, [k]: num(e.target.value) } })} /></div>
            ))}
          </div>
        </div>
        <div className="space-y-3">
          <p className="text-[12px] font-medium text-muted-foreground">Engagement (intent)</p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {Object.entries(s.event_points).map(([k, v]) => (
              <div key={k}><Label className="capitalize">{k.replace(/_/g, " ")}</Label><Input type="number" value={v} onChange={(e) => setS({ ...s, event_points: { ...s.event_points, [k]: num(e.target.value) } })} /></div>
            ))}
          </div>
          <div className="grid grid-cols-3 gap-2 pt-2">
            <div><Label>Half-life (days)</Label><Input type="number" value={s.engagement_half_life_days} onChange={(e) => setS({ ...s, engagement_half_life_days: num(e.target.value) })} /></div>
            <div><Label>Fit weight</Label><Input type="number" step={0.05} min={0} max={1} value={s.score_weights.fit}
              onChange={(e) => { const fit = Number(e.target.value); setS({ ...s, score_weights: { fit, engagement: Math.round((1 - fit) * 100) / 100 } }); }} /></div>
            <div><Label>MQL threshold</Label><Input type="number" min={1} max={100} value={s.mql_threshold} onChange={(e) => setS({ ...s, mql_threshold: num(e.target.value) })} /></div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div><Label>BANT minimum to convert</Label><Input type="number" min={0} max={4} value={s.conversion_min_criteria.bant}
              onChange={(e) => setS({ ...s, conversion_min_criteria: { ...s.conversion_min_criteria, bant: num(e.target.value) } })} /></div>
            <div><Label>MEDDPICC minimum</Label><Input type="number" min={0} max={8} value={s.conversion_min_criteria.meddpicc}
              onChange={(e) => setS({ ...s, conversion_min_criteria: { ...s.conversion_min_criteria, meddpicc: num(e.target.value) } })} /></div>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

interface Rule { id: string; name: string; priority: number; active: boolean; criteria: Record<string, unknown>; method: string; assignees: { id: string; full_name: string }[] }

function RoutingPanel() {
  const qc = useQueryClient();
  const { data: users } = useUsers();
  const { data } = useQuery({ queryKey: ["assignment-rules"], queryFn: () => get<Rule[]>("/admin/assignment-rules") });
  const [f, setF] = useState({ name: "", priority: 50, method: "round_robin", regions: "", sources: "", industries: "", min_employees: "", existing_account: false, assignee_ids: [] as string[] });
  const create = useMutation({
    mutationFn: async () => {
      const criteria: Record<string, unknown> = {};
      if (f.regions) criteria.regions = csv(f.regions);
      if (f.sources) criteria.sources = csv(f.sources);
      if (f.industries) criteria.industries = csv(f.industries);
      if (f.min_employees) criteria.min_employees = Number(f.min_employees);
      if (f.existing_account) criteria.existing_account = true;
      return (await api.post("/admin/assignment-rules", { name: f.name, priority: f.priority, method: f.method, criteria, assignee_ids: f.assignee_ids })).data;
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["assignment-rules"] }); toast.success("Rule added"); setF({ ...f, name: "" }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/admin/assignment-rules/${id}`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["assignment-rules"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const sellers = users?.filter((u) => ["sdr", "account_executive", "sales_manager"].includes(u.role)) ?? [];
  return (
    <div className="space-y-6">
      <Card className="overflow-hidden">
        <CardHeader title="Assignment rules" description="Evaluated by priority (lowest first); the first match assigns the lead. No match: round robin across SDRs." />
        <Table head={["Priority", "Rule", "When", "Assign", ""]} minWidth={720}>
          {data?.map((r) => (
            <tr key={r.id}>
              <Td className="tabular">{r.priority}</Td>
              <Td className="font-medium">{r.name}{!r.active && <Badge className="ml-2">inactive</Badge>}</Td>
              <Td className="text-[12.5px] text-muted-foreground">{Object.entries(r.criteria).map(([k, v]) => `${k.replace(/_/g, " ")}: ${Array.isArray(v) ? v.join(", ") : String(v)}`).join(" · ") || "any lead"}</Td>
              <Td className="text-[12.5px]">{r.method === "account_owner" ? "Account owner" : `${r.method === "round_robin" ? "Round robin" : "Specific"}: ${r.assignees.map((a) => a.full_name).join(", ")}`}</Td>
              <Td><Button variant="ghost" size="icon" aria-label="Delete rule" onClick={() => remove.mutate(r.id)}><Trash2 className="h-3.5 w-3.5" /></Button></Td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card>
        <CardHeader title="Add a rule" />
        <CardBody>
          <form className="grid gap-3 md:grid-cols-4" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
            <div className="md:col-span-2"><Label>Name</Label><Input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
            <div><Label>Priority</Label><Input type="number" value={f.priority} onChange={(e) => setF({ ...f, priority: Number(e.target.value) })} /></div>
            <div><Label>Method</Label><Select value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>
              <option value="round_robin">Round robin</option><option value="specific">Specific owner</option><option value="account_owner">Existing account owner</option></Select></div>
            <div><Label>Regions</Label><Input placeholder="EMEA, APAC" value={f.regions} onChange={(e) => setF({ ...f, regions: e.target.value })} /></div>
            <div><Label>Sources</Label><Input placeholder="web_form, partner" value={f.sources} onChange={(e) => setF({ ...f, sources: e.target.value })} /></div>
            <div><Label>Industries</Label><Input value={f.industries} onChange={(e) => setF({ ...f, industries: e.target.value })} /></div>
            <div><Label>Min employees</Label><Input type="number" value={f.min_employees} onChange={(e) => setF({ ...f, min_employees: e.target.value })} /></div>
            <label className="flex items-center gap-2 text-[13px] md:col-span-4"><input type="checkbox" checked={f.existing_account} onChange={(e) => setF({ ...f, existing_account: e.target.checked })} />Only leads that match an existing account</label>
            {f.method !== "account_owner" && (
              <div className="md:col-span-4">
                <Label>Assignees</Label>
                <div className="flex flex-wrap gap-2">
                  {sellers.map((u) => (
                    <label key={u.id} className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-[12.5px]">
                      <input type="checkbox" checked={f.assignee_ids.includes(u.id)}
                        onChange={(e) => setF({ ...f, assignee_ids: e.target.checked ? [...f.assignee_ids, u.id] : f.assignee_ids.filter((x) => x !== u.id) })} />
                      {u.full_name}
                    </label>
                  ))}
                </div>
              </div>
            )}
            <div className="md:col-span-4 flex justify-end"><Button type="submit" size="sm" loading={create.isPending}><Plus className="h-3.5 w-3.5" />Add rule</Button></div>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}

interface IntakeKey { id: string; name: string; kind: "web_form" | "webhook"; key_prefix: string; source: string; campaign: string | null; active: boolean; last_used_at: string | null; created_at: string }

function IntakePanel() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["intake-keys"], queryFn: () => get<IntakeKey[]>("/admin/intake-keys") });
  const [f, setF] = useState({ name: "", kind: "web_form", source: "web_form", campaign: "" });
  const [issued, setIssued] = useState<{ key: string; kind: string } | null>(null);
  const create = useMutation({
    mutationFn: async () => (await api.post<{ key: string }>("/admin/intake-keys", { ...f, campaign: f.campaign || null })).data,
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["intake-keys"] }); setIssued({ key: r.key, kind: f.kind }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const revoke = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/admin/intake-keys/${id}`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["intake-keys"] }),
  });
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const api_ = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000") + "/api/v1";
  return (
    <div className="space-y-6">
      {issued && (
        <Card className="border-primary/40">
          <CardHeader title="Key issued" description="Copy it now: only a hash is stored, so it cannot be shown again." icon={<KeyRound className="h-4 w-4 text-primary" />} />
          <CardBody className="space-y-3 text-[12.5px]">
            <div className="flex gap-2"><Input readOnly value={issued.key} className="font-mono" /><CopyButton text={issued.key} /></div>
            {issued.kind === "web_form" ? (<>
              <p className="text-muted-foreground">Hosted form (share or embed in an iframe):</p>
              <div className="flex gap-2"><Input readOnly value={`${origin}/forms/${issued.key}`} className="font-mono" /><CopyButton text={`${origin}/forms/${issued.key}`} /></div>
              <p className="text-muted-foreground">Or post from your own site&apos;s HTML form:</p>
              <pre className="overflow-auto rounded bg-muted p-3">{`<form method="post" action="${api_}/intake/leads?key=${issued.key}">
  <input name="email" type="email" required> <input name="company">
  <input name="website_url_confirm" style="display:none"> <!-- spam honeypot -->
  <label><input type="checkbox" name="consent" value="true"> Email me updates</label>
</form>`}</pre>
            </>) : (<>
              <p className="text-muted-foreground">Marketing automation webhook (engagement events):</p>
              <pre className="overflow-auto rounded bg-muted p-3">{`POST ${api_}/intake/events
X-Cirra-Key: ${issued.key}
[{"email": "buyer@acme.com", "event_type": "webinar_attended", "detail": "Q4 webinar"}]`}</pre>
            </>)}
          </CardBody>
        </Card>
      )}
      <Card className="overflow-hidden">
        <CardHeader title="Intake keys" description="Web-to-lead forms and inbound webhooks. Rate limited to 120 requests per minute per key." />
        <Table head={["Name", "Kind", "Key", "Source / campaign", "Last used", ""]} minWidth={720}>
          {data?.map((k) => (
            <tr key={k.id} className={k.active ? "" : "opacity-50"}>
              <Td className="font-medium">{k.name}</Td>
              <Td>{k.kind === "web_form" ? "Web form" : "Webhook"}</Td>
              <Td className="font-mono text-[12px]">{k.key_prefix}…</Td>
              <Td className="text-muted-foreground">{k.source.replace("_", " ")}{k.campaign && ` · ${k.campaign}`}</Td>
              <Td className="text-muted-foreground">{k.last_used_at ? relativeDays(k.last_used_at) : "never"}</Td>
              <Td>{k.active && <Button variant="ghost" size="sm" onClick={() => revoke.mutate(k.id)}>Revoke</Button>}</Td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card>
        <CardHeader title="Issue a key" />
        <CardBody>
          <form className="grid gap-3 md:grid-cols-4" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
            <div><Label>Name</Label><Input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
            <div><Label>Kind</Label><Select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value, source: e.target.value === "web_form" ? "web_form" : "campaign" })}>
              <option value="web_form">Web form</option><option value="webhook">Webhook</option></Select></div>
            <div><Label>Lead source</Label><Select value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })}>{SOURCES.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}</Select></div>
            <div><Label>Campaign</Label><Input value={f.campaign} onChange={(e) => setF({ ...f, campaign: e.target.value })} /></div>
            <div className="md:col-span-4 flex justify-end"><Button type="submit" size="sm" loading={create.isPending}><KeyRound className="h-3.5 w-3.5" />Issue key</Button></div>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}

// ---- deal desk: approval chain -------------------------------------------------------------------------------------
interface Group { key: string; label: string; level: number; members: { id: string; full_name: string }[]; implicit: { id: string; full_name: string }[] }
interface Policy { id: string; name: string; rule_type: string; threshold: number | null; approver_role: string; active: boolean }
const RULE_TYPES: Record<string, string> = { discount_pct: "Discount above %", payment_terms: "Payment terms beyond days", credit_hold: "Account on credit hold",
  tcv: "TCV above (USD)", custom_terms: "Non-standard terms present", credit_risk: "Credit risk score at or above" };

export function ApprovalChainPanel() {
  const qc = useQueryClient();
  const { data: users } = useUsers();
  const { data: groups } = useQuery({ queryKey: ["approval-groups"], queryFn: () => get<Group[]>("/approval-groups") });
  const { data: policies } = useQuery({ queryKey: ["approval-policies"], queryFn: () => get<Policy[]>("/approval-policies") });
  const [p, setP] = useState({ name: "", rule_type: "discount_pct", threshold: "", approver_role: "deal_desk" });
  const setGroup = useMutation({
    mutationFn: async (v: { key: string; member_ids: string[] }) => (await api.put(`/approval-groups/${v.key}`, { member_ids: v.member_ids })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["approval-groups"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const addPolicy = useMutation({
    mutationFn: async () => (await api.post("/approval-policies", { ...p, threshold: p.threshold === "" ? null : Number(p.threshold) })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["approval-policies"] }); setP({ ...p, name: "" }); toast.success("Policy added"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const delPolicy = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/approval-policies/${id}`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["approval-policies"] }),
  });
  if (!groups || !policies) return <Skeleton className="h-80 w-full" />;
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader title="Approval chain" description="Approvals run in this order. A level only sees a quote once every earlier level has approved; a rejection stops the chain." />
        <CardBody className="space-y-3">
          {groups.map((g) => (
            <div key={g.key} className="grid gap-2 rounded-md border p-3 md:grid-cols-[200px_1fr]">
              <div><p className="text-[13.5px] font-medium">{g.level}. {g.label}</p>
                <p className="text-[12px] text-muted-foreground">{policies.filter((x) => x.approver_role === g.key).length} policies route here</p></div>
              <div className="flex flex-wrap items-center gap-1.5">
                {g.members.map((m) => (
                  <Badge key={m.id} tone="primary">{m.full_name}
                    <button aria-label={`Remove ${m.full_name}`} className="ml-1" onClick={() => setGroup.mutate({ key: g.key, member_ids: g.members.filter((x) => x.id !== m.id).map((x) => x.id) })}>×</button>
                  </Badge>
                ))}
                {g.implicit.map((m) => <Badge key={m.id} tone="outline" title="By role">{m.full_name}</Badge>)}
                <Select className="h-7 w-44 text-[12px]" value="" onChange={(e) => e.target.value && setGroup.mutate({ key: g.key, member_ids: [...g.members.map((x) => x.id), e.target.value] })}>
                  <option value="">Add approver…</option>
                  {users?.filter((u) => !g.members.some((m) => m.id === u.id)).map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
                </Select>
              </div>
            </div>
          ))}
        </CardBody>
      </Card>
      <Card className="overflow-hidden">
        <CardHeader title="Approval policies" description="Any matching policy adds its level to the quote's chain on submit." />
        <Table head={["Policy", "Trigger", "Level", ""]} minWidth={640}>
          {policies.map((x) => (
            <tr key={x.id}>
              <Td className="font-medium">{x.name}</Td>
              <Td className="text-muted-foreground">{RULE_TYPES[x.rule_type] ?? x.rule_type}{x.threshold != null && ` ${x.threshold.toLocaleString()}`}</Td>
              <Td>{groups.find((g) => g.key === x.approver_role)?.label ?? x.approver_role}</Td>
              <Td><Button variant="ghost" size="icon" aria-label="Delete policy" onClick={() => delPolicy.mutate(x.id)}><Trash2 className="h-3.5 w-3.5" /></Button></Td>
            </tr>
          ))}
        </Table>
        <form className="grid gap-3 border-t p-4 md:grid-cols-5" onSubmit={(e) => { e.preventDefault(); addPolicy.mutate(); }}>
          <div className="md:col-span-2"><Label>Name</Label><Input required value={p.name} onChange={(e) => setP({ ...p, name: e.target.value })} /></div>
          <div><Label>Trigger</Label><Select value={p.rule_type} onChange={(e) => setP({ ...p, rule_type: e.target.value })}>{Object.entries(RULE_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select></div>
          <div><Label>Threshold</Label><Input type="number" disabled={["credit_hold", "custom_terms"].includes(p.rule_type)} value={p.threshold} onChange={(e) => setP({ ...p, threshold: e.target.value })} /></div>
          <div><Label>Level</Label><Select value={p.approver_role} onChange={(e) => setP({ ...p, approver_role: e.target.value })}>{groups.map((g) => <option key={g.key} value={g.key}>{g.label}</option>)}</Select></div>
          <div className="md:col-span-5 flex justify-end"><Button size="sm" type="submit" loading={addPolicy.isPending}><Plus className="h-3.5 w-3.5" />Add policy</Button></div>
        </form>
      </Card>
    </div>
  );
}

// ---- stage administration ---------------------------------------------------------------------------------------------
export function StagesPanel() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["pipelines"], queryFn: () => get<PipelineFull[]>("/pipelines") });
  const [pid, setPid] = useState("");
  const [names, setNames] = useState<Record<string, string>>({});
  const [n, setN] = useState({ name: "", default_probability: 30, after_stage_id: "" });
  const pipeline = data?.find((p) => p.id === pid) ?? data?.[0];
  const done = () => qc.invalidateQueries({ queryKey: ["pipelines"] });
  const patch = useMutation({
    mutationFn: async (v: { id: string; body: Record<string, unknown> }) => (await api.patch(`/pipelines/stages/${v.id}`, v.body)).data,
    onSuccess: done, onError: (e) => toast.error(errorMessage(e)),
  });
  const del = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/pipelines/stages/${id}`)).data,
    onSuccess: () => { done(); toast.success("Stage removed"); }, onError: (e) => toast.error(errorMessage(e)),
  });
  const add = useMutation({
    mutationFn: async () => (await api.post(`/pipelines/${pipeline!.id}/stages`, { ...n, after_stage_id: n.after_stage_id || null })).data,
    onSuccess: () => { done(); setN({ ...n, name: "" }); toast.success("Stage added"); }, onError: (e) => toast.error(errorMessage(e)),
  });
  if (!data || !pipeline) return <Skeleton className="h-80 w-full" />;
  return (
    <Card>
      <CardHeader title="Stages" description="Add, rename, reorder or remove stages. Closed stages stay last; stages with deals or history cannot be deleted." />
      <div className="px-5"><Tabs value={pipeline.id} onChange={setPid} tabs={data.map((p) => ({ value: p.id, label: p.name }))} className="mb-4" /></div>
      <CardBody className="space-y-2 pt-0">
        {pipeline.stages.map((s, i) => {
          const closed = s.is_closed_won || s.is_closed_lost;
          return (
            <div key={s.id} className="flex flex-wrap items-center gap-2 rounded-md border p-2">
              <span className="w-6 text-center tabular text-[12.5px] text-muted-foreground">{s.stage_order}</span>
              <Input className="h-8 min-w-0 flex-1" value={names[s.id] ?? s.name} disabled={closed} onChange={(e) => setNames({ ...names, [s.id]: e.target.value })}
                onBlur={() => names[s.id] && names[s.id] !== s.name && patch.mutate({ id: s.id, body: { name: names[s.id] } })} />
              <Input className="h-8 w-20" type="number" min={0} max={100} defaultValue={s.default_probability} disabled={closed} aria-label="Probability"
                onBlur={(e) => Number(e.target.value) !== s.default_probability && patch.mutate({ id: s.id, body: { default_probability: Number(e.target.value) } })} />
              <span className="text-[12px] text-muted-foreground">%</span>
              <Button variant="ghost" size="icon" aria-label="Move up" disabled={closed || i === 0} onClick={() => patch.mutate({ id: s.id, body: { move: "up" } })}><ArrowUp className="h-3.5 w-3.5" /></Button>
              <Button variant="ghost" size="icon" aria-label="Move down" disabled={closed || pipeline.stages[i + 1]?.is_closed_won || pipeline.stages[i + 1]?.is_closed_lost}
                onClick={() => patch.mutate({ id: s.id, body: { move: "down" } })}><ArrowDown className="h-3.5 w-3.5" /></Button>
              <Button variant="ghost" size="icon" aria-label="Delete stage" disabled={closed} onClick={() => confirm(`Delete ${s.name}?`) && del.mutate(s.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
            </div>
          );
        })}
        <form className="flex flex-wrap items-end gap-2 pt-2" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
          <div className="min-w-[200px] flex-1"><Label>New stage</Label><Input required value={n.name} onChange={(e) => setN({ ...n, name: e.target.value })} /></div>
          <div><Label>Probability</Label><Input className="w-24" type="number" min={0} max={100} value={n.default_probability} onChange={(e) => setN({ ...n, default_probability: Number(e.target.value) })} /></div>
          <div><Label>After</Label><Select className="w-52" value={n.after_stage_id} onChange={(e) => setN({ ...n, after_stage_id: e.target.value })}>
            <option value="">Before Closed-Won</option>
            {pipeline.stages.filter((s) => !s.is_closed_won && !s.is_closed_lost).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</Select></div>
          <Button size="sm" type="submit" loading={add.isPending}><Plus className="h-3.5 w-3.5" />Add stage</Button>
        </form>
      </CardBody>
    </Card>
  );
}
