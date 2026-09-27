"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, Clock, Mail, Pause, Play, Plus, Send, Trash2, Workflow } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { type EmailStats, pctText } from "@/components/campaigns";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Table, Td } from "@/components/ui/extra";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { cn, relativeDays } from "@/lib/utils";

/* Nurture journeys: emails and waits for a campaign's members, with steps that only send to people who opened,
   didn't open, clicked or didn't click the previous email. */

export type SendIf = "always" | "opened" | "not_opened" | "clicked" | "not_clicked";
export type Step = { type: "email"; subject: string; body: string; send_if: SendIf } | { type: "wait"; days: number };
export interface JourneyRow {
  id: string; name: string; description: string | null; campaign_id: string; campaign: string | null; status: "draft" | "active" | "paused" | "archived";
  steps: Step[]; sender: string | null; activated_at: string | null; updated_at: string;
  enrolled?: number; by_status?: Record<"active" | "completed" | "exited", number>; email?: EmailStats;
}
interface StepStat { index: number; type: string; waiting_here: number; sent?: number; opened?: number; clicked?: number; open_rate?: number | null; click_rate?: number | null }
interface JourneyDetail extends JourneyRow {
  stats: { enrolled: number; by_status: Record<"active" | "completed" | "exited", number>; exit_reasons: Record<string, number>; steps: StepStat[] };
}

export const SEND_IF_LABEL: Record<SendIf, string> = {
  always: "Everyone", opened: "Only if they opened the previous email", not_opened: "Only if they didn't open the previous email",
  clicked: "Only if they clicked in the previous email", not_clicked: "Only if they didn't click in the previous email",
};
const STATUS_TONE = { draft: "neutral", active: "good", paused: "warning", archived: "outline" } as const;

export function JourneyStatus({ s }: { s: JourneyRow["status"] }) {
  return <Badge tone={STATUS_TONE[s]}>{s[0].toUpperCase() + s.slice(1)}</Badge>;
}

export function JourneysTab({ campaignId, editable }: { campaignId: string; editable: boolean }) {
  const router = useRouter();
  const q = useQuery({ queryKey: ["journeys", campaignId], queryFn: () => get<JourneyRow[]>("/journeys", { campaign_id: campaignId }) });
  const create = useMutation({
    mutationFn: async () => (await api.post<JourneyRow>("/journeys", { name: "New journey", campaign_id: campaignId, steps: [
      { type: "email", subject: "Welcome, {{first_name}}", body: "Hi {{first_name}},\n\nThanks for your interest.", send_if: "always" },
      { type: "wait", days: 3 },
      { type: "email", subject: "In case you missed it", body: "Hi {{first_name}},\n\nA quick follow-up.", send_if: "not_opened" }] })).data,
    onSuccess: (j) => router.push(`/journeys/${j.id}`), onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader title="Nurture journeys" description="Automated email sequences for this campaign's members. Emails are tracked; people leave when they unsubscribe, bounce or convert."
        action={editable && <Button size="sm" loading={create.isPending} onClick={() => create.mutate()}><Plus className="h-3.5 w-3.5" />New journey</Button>} />
      {!q.data ? <Skeleton className="m-5 h-24" /> : !q.data.length ? (
        <EmptyState icon={<Workflow className="h-4 w-4" />} title="No journeys yet" description="Start with a welcome email, a wait, and a follow-up for people who didn't open." />
      ) : (
        <Table head={["Journey", "Status", "Steps", "People", "Opened", "Clicked"]} minWidth={720}>
          {q.data.map((j) => (
            <tr key={j.id}>
              <Td><Link href={`/journeys/${j.id}`} className="font-medium hover:text-primary hover:underline">{j.name}</Link>
                <p className="text-[12px] text-muted-foreground">{j.sender ? `from ${j.sender}` : ""}{j.activated_at ? ` · started ${relativeDays(j.activated_at)}` : ""}</p></Td>
              <Td><JourneyStatus s={j.status} /></Td>
              <Td className="text-[13px] tabular">{j.steps.filter((s) => s.type === "email").length} emails · {j.steps.filter((s) => s.type === "wait").length} waits</Td>
              <Td className="text-[13px] tabular">{j.enrolled ?? 0}{j.by_status ? <span className="text-muted-foreground"> ({j.by_status.active} active)</span> : null}</Td>
              <Td className="text-[13px] tabular">{pctText(j.email?.open_rate ?? null)}</Td>
              <Td className="text-[13px] tabular">{pctText(j.email?.click_rate ?? null)}</Td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}

export function JourneyBuilder({ id }: { id: string }) {
  const qc = useQueryClient();
  const router = useRouter();
  const q = useQuery({ queryKey: ["journeys", "detail", id], queryFn: () => get<JourneyDetail>(`/journeys/${id}`) });
  const [name, setName] = useState("");
  const [steps, setSteps] = useState<Step[]>([]);
  const [dirty, setDirty] = useState(false);
  useEffect(() => { if (q.data && !dirty) { setName(q.data.name); setSteps(q.data.steps); } }, [q.data, dirty]);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["journeys"] }); qc.invalidateQueries({ queryKey: ["campaigns"] }); };
  const save = useMutation({
    mutationFn: async () => (await api.put(`/journeys/${id}`, { name, campaign_id: q.data!.campaign_id, description: q.data!.description, steps })).data,
    onSuccess: () => { setDirty(false); refresh(); toast.success("Journey saved"); }, onError: (e) => toast.error(errorMessage(e)),
  });
  const status = useMutation({
    mutationFn: async (s: "active" | "paused" | "archived") => (await api.post<{ status: string; enrolled?: number }>(`/journeys/${id}/status`, null, { params: { status: s } })).data,
    onSuccess: (r) => { refresh(); toast.success(r.status === "active" ? `Journey live: ${r.enrolled ?? 0} people enrolled` : `Journey ${r.status}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const runNow = useMutation({
    mutationFn: async () => (await api.post<{ enrolled: number; advanced: number; failed: number }>("/journeys/run")).data,
    onSuccess: (r) => { refresh(); toast.success(`Processed ${r.advanced} people${r.failed ? `, ${r.failed} to retry` : ""}`); }, onError: (e) => toast.error(errorMessage(e)),
  });
  const test = useMutation({
    mutationFn: async (step: number) => (await api.post<{ sent_to: string; delivered: boolean }>(`/journeys/${id}/test`, { step })).data,
    onSuccess: (r) => toast.success(r.delivered ? `Test sent to ${r.sent_to}` : `Test recorded for ${r.sent_to} (connect your mailbox to deliver it)`),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const del = useMutation({
    mutationFn: async () => api.delete(`/journeys/${id}`),
    onSuccess: () => { refresh(); router.push(`/campaigns/${q.data!.campaign_id}`); }, onError: (e) => toast.error(errorMessage(e)),
  });
  if (q.isError) return <p className="text-sm text-muted-foreground">{errorMessage(q.error, "Journey not found")}</p>;
  if (!q.data) return <Skeleton className="h-96" />;
  const j = q.data, locked = j.status === "active" || j.status === "archived";
  const stat = (i: number) => j.stats.steps.find((s) => s.index === i);
  const update = (i: number, s: Step) => { const n = [...steps]; n[i] = s; setSteps(n); setDirty(true); };
  const move = (i: number, d: -1 | 1) => { const n = [...steps]; [n[i], n[i + d]] = [n[i + d], n[i]]; setSteps(n); setDirty(true); };
  const add = (s: Step) => { setSteps([...steps, s]); setDirty(true); };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link href={`/campaigns/${j.campaign_id}`} className="text-[12.5px] text-muted-foreground hover:text-foreground hover:underline">{j.campaign}</Link>
          {locked ? <h1 className="text-xl font-semibold tracking-tight">{j.name}</h1>
            : <Input aria-label="Journey name" className="mt-1 h-9 w-80 text-[15px] font-semibold" value={name} onChange={(e) => { setName(e.target.value); setDirty(true); }} />}
          <p className="mt-1 flex items-center gap-2 text-[13px] text-muted-foreground"><JourneyStatus s={j.status} />{j.sender ? `Sends from ${j.sender}'s mailbox` : ""}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {!locked && dirty && <Button size="sm" variant="outline" loading={save.isPending} onClick={() => save.mutate()}>Save changes</Button>}
          {j.status !== "active" && j.status !== "archived" && <Button size="sm" disabled={dirty} title={dirty ? "Save your changes first" : undefined} loading={status.isPending}
            onClick={() => status.mutate("active")}><Play className="h-3.5 w-3.5" />{j.activated_at ? "Resume" : "Activate"}</Button>}
          {j.status === "active" && <>
            <Button size="sm" variant="outline" loading={runNow.isPending} onClick={() => runNow.mutate()}><Send className="h-3.5 w-3.5" />Process due steps now</Button>
            <Button size="sm" variant="outline" loading={status.isPending} onClick={() => status.mutate("paused")}><Pause className="h-3.5 w-3.5" />Pause</Button></>}
          {j.status !== "archived" && j.activated_at && <Button size="sm" variant="ghost" onClick={() => status.mutate("archived")}>Archive</Button>}
          {!j.activated_at && <Button size="sm" variant="ghost" aria-label="Delete journey" onClick={() => del.mutate()}><Trash2 className="h-3.5 w-3.5" /></Button>}
        </div>
      </div>
      {j.status === "active" && <p className="rounded-md border border-dashed px-3 py-2 text-[12.5px] text-muted-foreground">Live: new campaign members are enrolled automatically and due steps run every five minutes. Pause to edit.</p>}

      <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
        <div className="space-y-2">
          {steps.map((s, i) => {
            const st = stat(i);
            return (
              <Card key={i} className={cn("p-4", s.type === "wait" && "border-dashed")}>
                <div className="mb-2 flex items-center justify-between gap-2">
                  <p className="flex items-center gap-2 text-[13px] font-medium">
                    {s.type === "email" ? <Mail className="h-4 w-4 text-primary" /> : <Clock className="h-4 w-4 text-muted-foreground" />}
                    Step {i + 1}: {s.type === "email" ? "Email" : "Wait"}
                    {st && st.waiting_here > 0 && <Badge tone="outline">{st.waiting_here} here now</Badge>}
                  </p>
                  {!locked && <div className="flex gap-0.5">
                    <Button size="icon" variant="ghost" className="h-7 w-7" aria-label={`Move step ${i + 1} up`} disabled={i === 0} onClick={() => move(i, -1)}><ChevronUp className="h-3.5 w-3.5" /></Button>
                    <Button size="icon" variant="ghost" className="h-7 w-7" aria-label={`Move step ${i + 1} down`} disabled={i === steps.length - 1} onClick={() => move(i, 1)}><ChevronDown className="h-3.5 w-3.5" /></Button>
                    <Button size="icon" variant="ghost" className="h-7 w-7" aria-label={`Remove step ${i + 1}`} onClick={() => { setSteps(steps.filter((_, k) => k !== i)); setDirty(true); }}><Trash2 className="h-3.5 w-3.5" /></Button>
                  </div>}
                </div>
                {s.type === "wait" ? (
                  <label className="flex items-center gap-2 text-[13px]">Wait
                    <Input aria-label={`Days to wait at step ${i + 1}`} type="number" min={0.1} max={180} step={0.5} disabled={locked} className="h-8 w-20"
                      value={s.days} onChange={(e) => update(i, { type: "wait", days: Number(e.target.value) })} />days, then continue</label>
                ) : (
                  <div className="space-y-2">
                    <div><Label htmlFor={`step-${i}-if`}>Send to</Label>
                      <Select id={`step-${i}-if`} disabled={locked} value={s.send_if} onChange={(e) => update(i, { ...s, send_if: e.target.value as SendIf })}>
                        {(Object.keys(SEND_IF_LABEL) as SendIf[]).map((k) => <option key={k} value={k}>{SEND_IF_LABEL[k]}</option>)}</Select></div>
                    <div><Label htmlFor={`step-${i}-subject`}>Subject</Label>
                      <Input id={`step-${i}-subject`} disabled={locked} maxLength={200} value={s.subject} onChange={(e) => update(i, { ...s, subject: e.target.value })} /></div>
                    <div><Label htmlFor={`step-${i}-body`}>Body</Label>
                      <Textarea id={`step-${i}-body`} disabled={locked} rows={5} value={s.body} onChange={(e) => update(i, { ...s, body: e.target.value })} />
                      <p className="mt-1 text-[12px] text-subtle">{"{{first_name}}"}, {"{{last_name}}"}, {"{{company}}"} are filled in; links are tracked and an unsubscribe link is added.</p></div>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      {st && (st.sent ?? 0) > 0 ? <p className="text-[12.5px] tabular text-muted-foreground">{st.sent} sent · {pctText(st.open_rate ?? null)} opened · {pctText(st.click_rate ?? null)} clicked</p> : <span />}
                      <Button type="button" size="sm" variant="ghost" disabled={dirty} loading={test.isPending && test.variables === i} onClick={() => test.mutate(i)}>Send me a test</Button>
                    </div>
                  </div>
                )}
              </Card>
            );
          })}
          {!locked && (
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={() => add({ type: "email", subject: "", body: "", send_if: "always" })}><Mail className="h-3.5 w-3.5" />Add email</Button>
              <Button size="sm" variant="outline" onClick={() => add({ type: "wait", days: 2 })}><Clock className="h-3.5 w-3.5" />Add wait</Button>
            </div>
          )}
        </div>
        <div className="space-y-4">
          <Card>
            <CardHeader title="People" />
            <CardBody className="space-y-1.5 text-[13px]">
              <p className="flex justify-between"><span className="text-muted-foreground">Enrolled</span><span className="tabular font-medium">{j.stats.enrolled}</span></p>
              {(["active", "completed", "exited"] as const).map((k) => <p key={k} className="flex justify-between"><span className="text-muted-foreground">{k[0].toUpperCase() + k.slice(1)}</span><span className="tabular">{j.stats.by_status[k]}</span></p>)}
              {Object.keys(j.stats.exit_reasons).length > 0 && <div className="border-t pt-2">
                <p className="mb-1 text-[12px] font-medium text-muted-foreground">Why people left</p>
                {Object.entries(j.stats.exit_reasons).map(([r, n]) => <p key={r} className="flex justify-between text-[12.5px]"><span>{r}</span><span className="tabular">{n}</span></p>)}
              </div>}
            </CardBody>
          </Card>
          {j.email && j.email.sent > 0 && <Card>
            <CardHeader title="Email results" />
            <CardBody className="space-y-1.5 text-[13px]">
              <p className="flex justify-between"><span className="text-muted-foreground">Sent</span><span className="tabular">{j.email.sent}</span></p>
              <p className="flex justify-between"><span className="text-muted-foreground">Opened</span><span className="tabular">{j.email.opened} ({pctText(j.email.open_rate)})</span></p>
              <p className="flex justify-between"><span className="text-muted-foreground">Clicked</span><span className="tabular">{j.email.clicked} ({pctText(j.email.click_rate)})</span></p>
              <p className="pt-1 text-[12px] text-subtle">Opens are indicative: some mail apps load images automatically.</p>
            </CardBody>
          </Card>}
        </div>
      </div>
    </div>
  );
}
