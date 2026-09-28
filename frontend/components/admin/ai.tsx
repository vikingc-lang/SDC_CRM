"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Coins, ScrollText, ShieldCheck, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { StatTile } from "@/components/charts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StatusPill, Table, Td } from "@/components/ui/extra";
import { Input, Label, Select } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { fmtNumber, relativeDays } from "@/lib/utils";

interface Policy {
  enabled: boolean; monthly_budget_usd: number; user_monthly_budget_usd: number; user_daily_calls: number; warn_at_pct: number;
  mask_pii: boolean; block_injection: boolean; log_prompts: boolean; retention_days: number;
  prices: Record<string, { input: number; output: number }>; feature_enabled: Record<string, boolean>;
}
interface Row { key: string | null; calls: number; cost_usd: number; tokens: number; label?: string; name?: string }
interface AgentPolicy { enabled: boolean; mode: "auto" | "approve"; actions: string[] }
interface Governance {
  policy: Policy; features: Record<string, string>; can_edit: boolean; period_days: number;
  month: { cost_usd: number; budget_usd: number; used_pct: number | null; by_user: Row[] };
  by_feature: Row[]; by_status: Row[]; daily: { date: string; calls: number; cost_usd: number }[];
  trust: { calls_flagged: number; pii_values_masked: number };
  agents: { policy: Record<string, AgentPolicy>; catalog: Record<string, { label: string; description: string; may: string[]; default_mode: string }>;
    actions: Record<string, string> };
}
interface LogRow {
  id: number; created_at: string; user: string; feature: string; provider: string; model: string | null; status: string; input_tokens: number;
  output_tokens: number; cost_usd: number; latency_ms: number | null; pii_masked: number; injection_flags: string[]; detail: string | null;
  prompt_excerpt: string | null; response_excerpt: string | null;
}

const usd = (v: number) => (v >= 100 ? `$${fmtNumber(v, 0)}` : `$${fmtNumber(v, v < 1 ? 4 : 2)}`);
const STATUS_TONE: Record<string, string> = { ok: "completed", blocked: "blocked", error: "failed", refused: "rejected", fallback: "pending" };

export function AiGovernancePanel() {
  const gov = useQuery({ queryKey: ["ai", "governance"], queryFn: () => get<Governance>("/ai/governance") });
  if (!gov.data) return <Skeleton className="h-[480px]" />;
  const g = gov.data;
  const flagged = g.trust.calls_flagged;
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="AI spend this month" value={usd(g.month.cost_usd)} icon={<Coins className="h-4 w-4" />}
          sub={g.month.budget_usd ? `${g.month.used_pct ?? 0}% of ${usd(g.month.budget_usd)} budget` : "No monthly budget"} />
        <StatTile label={`Model calls (${g.period_days} days)`} value={fmtNumber(g.by_status.reduce((n, s) => n + s.calls, 0))}
          sub={`${g.by_status.find((s) => s.key === "blocked")?.calls ?? 0} blocked by policy`} />
        <StatTile label="Personal data masked" value={fmtNumber(g.trust.pii_values_masked)} icon={<ShieldCheck className="h-4 w-4" />} sub="values replaced before the model saw them" />
        <StatTile label="Prompt-injection flags" value={fmtNumber(flagged)} sub={g.policy.block_injection ? "flagged calls are blocked" : "flagged and fenced; not blocked"} />
      </div>
      {g.month.budget_usd > 0 && (
        <div className="h-2 overflow-hidden rounded-full bg-muted" role="meter" aria-label="Budget used" aria-valuenow={g.month.used_pct ?? 0} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-full rounded-full" style={{ width: `${Math.min(100, g.month.used_pct ?? 0)}%`, background: (g.month.used_pct ?? 0) >= g.policy.warn_at_pct ? "var(--status-warning)" : "var(--series-1)" }} />
        </div>
      )}
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="By feature" description={`Last ${g.period_days} days`} icon={<Sparkles className="h-4 w-4" />} />
          {!g.by_feature.length ? <p className="px-5 pb-5 text-[13px] text-muted-foreground">No model calls yet. With the rule-based engine (LLM_PROVIDER=heuristic) nothing is metered.</p> : (
            <Table head={["Feature", "Calls", "Tokens", "Cost"]} minWidth={360}>
              {g.by_feature.sort((a, b) => b.cost_usd - a.cost_usd).map((f) => (
                <tr key={f.key}><Td className="text-[13px]">{f.label}</Td><Td className="tabular text-[13px]">{fmtNumber(f.calls)}</Td>
                  <Td className="tabular text-[13px]">{fmtNumber(f.tokens)}</Td><Td className="tabular text-[13px]">{usd(f.cost_usd)}</Td></tr>
              ))}
            </Table>
          )}
        </Card>
        <Card>
          <CardHeader title="By person" description="This month" />
          {!g.month.by_user.length ? <p className="px-5 pb-5 text-[13px] text-muted-foreground">Nobody has used AI features this month.</p> : (
            <Table head={["Person", "Calls", "Cost"]} minWidth={320}>
              {g.month.by_user.map((u) => (
                <tr key={u.key ?? "system"}><Td className="text-[13px]">{u.name}</Td><Td className="tabular text-[13px]">{fmtNumber(u.calls)}</Td>
                  <Td className="tabular text-[13px]">{usd(u.cost_usd)}{g.policy.user_monthly_budget_usd > 0 && u.key && u.cost_usd >= g.policy.user_monthly_budget_usd && <Badge tone="critical" className="ml-2">at limit</Badge>}</Td></tr>
              ))}
            </Table>
          )}
        </Card>
      </div>
      <PolicyCard g={g} />
      <AgentsCard g={g} />
      <AiLogCard features={g.features} />
    </div>
  );
}

function Toggle({ id, label, hint, checked, disabled, onChange }: { id: string; label: string; hint?: string; checked: boolean; disabled?: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-start gap-2 text-[13px]">
      <input id={id} type="checkbox" className="mt-0.5" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span><span className="font-medium">{label}</span>{hint && <span className="block text-[12px] text-muted-foreground">{hint}</span>}</span>
    </label>
  );
}

function PolicyCard({ g }: { g: Governance }) {
  const qc = useQueryClient();
  const [p, setP] = useState<Policy>(g.policy);
  useEffect(() => setP(g.policy), [g.policy]);
  const save = useMutation({
    mutationFn: async () => (await api.put("/ai/governance", { policy: p })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["ai"] }); toast.success("AI policy saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const ro = !g.can_edit;
  const num = (k: keyof Policy, label: string, props: { min: number; max: number; step?: number }, hint?: string) => (
    <div><Label htmlFor={`ai-${k}`}>{label}</Label>
      <Input id={`ai-${k}`} type="number" disabled={ro} {...props} value={p[k] as number} onChange={(e) => setP({ ...p, [k]: Number(e.target.value) })} />
      {hint && <p className="mt-1 text-[11.5px] text-subtle">{hint}</p>}</div>
  );
  return (
    <Card>
      <CardHeader title="Budgets and trust" icon={<ShieldCheck className="h-4 w-4" />}
        description="Limits stop further model calls (features fall back to the rule-based engine, so nothing breaks). The trust layer runs on every call."
        action={g.can_edit && <Button size="sm" loading={save.isPending} onClick={() => save.mutate()}>Save</Button>} />
      <CardBody className="space-y-5">
        <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {num("monthly_budget_usd", "Monthly budget, organisation (USD)", { min: 0, max: 1000000, step: 10 }, "0 = no limit")}
          {num("user_monthly_budget_usd", "Monthly budget per person (USD)", { min: 0, max: 1000000, step: 1 }, "0 = no limit")}
          {num("user_daily_calls", "Calls per person per day", { min: 0, max: 100000 }, "0 = no limit")}
          {num("warn_at_pct", "Warn admins at (% of budget)", { min: 0, max: 100 })}
          {num("retention_days", "Keep prompt excerpts (days)", { min: 1, max: 3650 })}
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Toggle id="ai-enabled" label="AI features on" hint="Off: every feature uses the rule-based engine." checked={p.enabled} disabled={ro} onChange={(v) => setP({ ...p, enabled: v })} />
          <Toggle id="ai-mask" label="Mask personal data before it reaches the model" hint="Emails, phone numbers, card numbers, IBANs and national ids become placeholders and are restored in the answer."
            checked={p.mask_pii} disabled={ro} onChange={(v) => setP({ ...p, mask_pii: v })} />
          <Toggle id="ai-block" label="Block suspected prompt injection" hint="Off: suspicious text is flagged and fenced as untrusted data, and the call still runs."
            checked={p.block_injection} disabled={ro} onChange={(v) => setP({ ...p, block_injection: v })} />
          <Toggle id="ai-log" label="Keep masked prompt and response excerpts" hint="For review in the AI log below; cleared after the retention period."
            checked={p.log_prompts} disabled={ro} onChange={(v) => setP({ ...p, log_prompts: v })} />
        </div>
        <div>
          <p className="mb-2 text-[12px] font-medium uppercase tracking-wide text-muted-foreground">Features</p>
          <div className="flex flex-wrap gap-x-5 gap-y-2">
            {Object.entries(g.features).filter(([k]) => k !== "other").map(([k, label]) => (
              <Toggle key={k} id={`ai-f-${k}`} label={label} checked={p.feature_enabled[k] ?? true} disabled={ro}
                onChange={(v) => setP({ ...p, feature_enabled: { ...p.feature_enabled, [k]: v } })} />
            ))}
          </div>
        </div>
        <div>
          <p className="mb-2 text-[12px] font-medium uppercase tracking-wide text-muted-foreground">Token prices (USD per million tokens, matched by model name)</p>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {Object.entries(p.prices).map(([name, pr]) => (
              <div key={name} className="rounded-md border p-2.5">
                <p className="mb-1.5 text-[12.5px] font-medium">{name === "default" ? "Any other model" : `Models with “${name}”`}</p>
                <div className="grid grid-cols-2 gap-2">
                  <Input aria-label={`${name} input price`} id={`ai-price-${name}-in`} type="number" min={0} step={0.01} disabled={ro} value={pr.input}
                    onChange={(e) => setP({ ...p, prices: { ...p.prices, [name]: { ...pr, input: Number(e.target.value) } } })} />
                  <Input aria-label={`${name} output price`} id={`ai-price-${name}-out`} type="number" min={0} step={0.01} disabled={ro} value={pr.output}
                    onChange={(e) => setP({ ...p, prices: { ...p.prices, [name]: { ...pr, output: Number(e.target.value) } } })} />
                </div>
                <p className="mt-1 text-[11px] text-subtle">input · output</p>
              </div>
            ))}
          </div>
          <p className="mt-1.5 text-[12px] text-subtle">Local models (Ollama) cost nothing unless you add a price for them.</p>
        </div>
      </CardBody>
    </Card>
  );
}

function AgentsCard({ g }: { g: Governance }) {
  const qc = useQueryClient();
  const [pol, setPol] = useState(g.agents.policy);
  useEffect(() => setPol(g.agents.policy), [g.agents.policy]);
  const save = useMutation({
    mutationFn: async () => (await api.put("/ai/agents/policy", { policy: pol })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["ai"] }); toast.success("Agent permissions saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader title="Agents and approvals" icon={<Bot className="h-4 w-4" />}
        description="What each AI agent may change, and whether a person must approve first. Every change is recorded either way; approvals go to the deal owner (Approvals → AI suggestions)."
        action={g.can_edit && <Button size="sm" loading={save.isPending} onClick={() => save.mutate()}>Save</Button>} />
      <CardBody className="grid gap-3 lg:grid-cols-2">
        {Object.entries(g.agents.catalog).map(([key, agent]) => {
          const a = pol[key];
          return (
            <div key={key} className="rounded-lg border p-3.5">
              <div className="flex flex-wrap items-center gap-3">
                <Toggle id={`agent-${key}-on`} label={agent.label} checked={a.enabled} disabled={!g.can_edit} onChange={(v) => setPol({ ...pol, [key]: { ...a, enabled: v } })} />
                <span className="flex-1" />
                <Select aria-label={`${agent.label} mode`} id={`agent-${key}-mode`} className="h-8 w-auto text-[13px]" disabled={!g.can_edit || !a.enabled} value={a.mode}
                  onChange={(e) => setPol({ ...pol, [key]: { ...a, mode: e.target.value as AgentPolicy["mode"] } })}>
                  <option value="approve">Needs approval</option><option value="auto">Acts automatically</option>
                </Select>
              </div>
              <p className="mt-1.5 text-[12.5px] text-muted-foreground">{agent.description}</p>
              <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1.5">
                {agent.may.map((act) => (
                  <label key={act} className="flex items-center gap-1.5 text-[12.5px]">
                    <input id={`agent-${key}-${act}`} type="checkbox" disabled={!g.can_edit || !a.enabled} checked={a.actions.includes(act)}
                      onChange={(e) => setPol({ ...pol, [key]: { ...a, actions: e.target.checked ? [...a.actions, act] : a.actions.filter((x) => x !== act) } })} />
                    {g.agents.actions[act] ?? act}
                  </label>
                ))}
              </div>
            </div>
          );
        })}
      </CardBody>
    </Card>
  );
}

function AiLogCard({ features }: { features: Record<string, string> }) {
  const [status, setStatus] = useState("");
  const [feature, setFeature] = useState("");
  const [flagged, setFlagged] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const log = useQuery({ queryKey: ["ai", "log", status, feature, flagged], queryFn: () => get<LogRow[]>("/ai/usage/log", { status: status || undefined, feature: feature || undefined, flagged }) });
  return (
    <Card>
      <CardHeader title="AI log" icon={<ScrollText className="h-4 w-4" />} description="Every model call and every call the policy stopped, newest first."
        action={<div className="flex flex-wrap items-center gap-2">
          <Select aria-label="Status" id="ai-log-status" className="h-8 w-auto text-[13px]" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All outcomes</option>{["ok", "blocked", "error", "refused"].map((s) => <option key={s} value={s}>{s}</option>)}</Select>
          <Select aria-label="Feature" id="ai-log-feature" className="h-8 w-auto text-[13px]" value={feature} onChange={(e) => setFeature(e.target.value)}>
            <option value="">All features</option>{Object.entries(features).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select>
          <label className="flex items-center gap-1.5 text-[13px]"><input id="ai-log-flagged" type="checkbox" checked={flagged} onChange={(e) => setFlagged(e.target.checked)} />Flagged only</label>
        </div>} />
      {!log.data ? <Skeleton className="m-5 h-24" /> : !log.data.length ? (
        <EmptyState icon={<ScrollText className="h-4 w-4" />} title="Nothing logged" description="Calls appear here once an AI provider is configured and people use AI features." />
      ) : (
        <Table head={["When", "Person", "Feature", "Outcome", "Tokens", "Cost", "Trust"]} minWidth={820}>
          {log.data.map((r) => (
            <tr key={r.id} className="align-top">
              <Td className="whitespace-nowrap text-[12.5px] text-muted-foreground">{relativeDays(r.created_at)}</Td>
              <Td className="text-[13px]">{r.user}</Td>
              <Td className="text-[13px]">{r.feature}<span className="block text-[11.5px] text-subtle">{r.model ?? r.provider}{r.latency_ms != null && ` · ${fmtNumber(r.latency_ms)} ms`}</span></Td>
              <Td><StatusPill status={STATUS_TONE[r.status] ?? r.status} />{r.detail && <span className="mt-0.5 block text-[11.5px] text-muted-foreground">{r.detail}</span>}</Td>
              <Td className="tabular text-[12.5px]">{fmtNumber(r.input_tokens)} / {fmtNumber(r.output_tokens)}</Td>
              <Td className="tabular text-[12.5px]">{usd(r.cost_usd)}</Td>
              <Td className="text-[12px]">
                {r.pii_masked > 0 && <Badge tone="outline" className="mr-1">{r.pii_masked} masked</Badge>}
                {r.injection_flags.map((f) => <Badge key={f} tone="critical" className="mr-1">{f.replace(/_/g, " ")}</Badge>)}
                {(r.prompt_excerpt || r.response_excerpt) && (
                  <button type="button" className="text-primary hover:underline" onClick={() => setOpen(open === r.id ? null : r.id)}>{open === r.id ? "Hide" : "Show"} text</button>)}
                {open === r.id && (
                  <div className="mt-2 max-w-md space-y-2 whitespace-pre-wrap break-words rounded-md bg-muted p-2 text-[12px]">
                    {r.prompt_excerpt && <p><span className="font-medium">Sent (masked): </span>{r.prompt_excerpt}</p>}
                    {r.response_excerpt && <p><span className="font-medium">Returned: </span>{r.response_excerpt}</p>}
                  </div>
                )}
              </Td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}
