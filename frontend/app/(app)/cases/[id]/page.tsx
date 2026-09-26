"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, BookOpen, Building2, Copy, Lock, MessageSquare, Star, User } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { type CaseRow, type Clock, duration, PriorityPill, type ServiceMeta, SlaBadge } from "@/components/service";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StatusPill } from "@/components/ui/extra";
import { Label, Select, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { cn, relativeDays, shortDate } from "@/lib/utils";

interface CaseDetail extends CaseRow {
  description: string | null; resolved_at: string | null; first_responded_at: string | null;
  account_detail: { id: string; name: string; tier: string; health_score: number };
  contact: { id: string; name: string; email: string | null; phone: string | null; job_title: string | null } | null;
  comments: { id: string; author: string | null; body: string; internal: boolean; created_at: string }[];
  suggested_articles: { id: string; title: string; category: string | null }[];
  other_cases: { id: string; case_number: string; subject: string; status: string }[];
  csat: { score: number | null; comment: string | null; at: string | null; survey_url: string | null };
}

export default function CasePage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const c = useQuery({ queryKey: ["cases", "detail", id], queryFn: () => get<CaseDetail>(`/cases/${id}`) });
  const meta = useQuery({ queryKey: ["cases", "meta"], queryFn: () => get<ServiceMeta>("/cases/meta") });
  const [reply, setReply] = useState("");
  const [internal, setInternal] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["cases"] });
  const patch = useMutation({
    mutationFn: async (body: Record<string, unknown>) => (await api.patch(`/cases/${id}`, body)).data,
    onSuccess: refresh, onError: (e) => toast.error(errorMessage(e)),
  });
  const post = useMutation({
    mutationFn: async () => (await api.post(`/cases/${id}/comments`, { body: reply, internal })).data,
    onSuccess: () => { setReply(""); refresh(); toast.success(internal ? "Note added" : "Reply sent"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (c.isError) return <p className="text-sm text-muted-foreground">Case not found or outside your scope. <Link href="/cases" className="text-primary hover:underline">Back to cases</Link></p>;
  if (!c.data || !meta.data) return <Skeleton className="h-[600px]" />;
  const d = c.data, m = meta.data, editable = m.can_edit;
  const closed = d.status === "resolved" || d.status === "closed";
  const copy = async (text: string) => { try { await navigator.clipboard.writeText(text); toast.success("Link copied"); } catch { toast.error("Copy failed. Select the link and copy it by hand."); } };

  return (
    <div className="mx-auto max-w-6xl">
      <Link href="/cases" className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />Service</Link>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[12.5px] text-muted-foreground tabular">{d.case_number} · opened {relativeDays(d.opened_at)} via {d.channel}</p>
          <h1 className="text-xl font-semibold tracking-tight">{d.subject}</h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-2"><PriorityPill p={d.priority} /><StatusPill status={d.status} /><SlaBadge clocks={d.clocks} status={d.status} /></div>
        </div>
        {editable && (
          <div className="flex gap-2">
            {closed ? <Button size="sm" variant="outline" loading={patch.isPending} onClick={() => patch.mutate({ status: "open" })}>Reopen</Button>
              : <Button size="sm" loading={patch.isPending} onClick={() => patch.mutate({ status: "resolved" })}>Resolve</Button>}
          </div>
        )}
      </div>

      <div className="grid gap-5 lg:grid-cols-[1fr_300px]">
        <div className="min-w-0 space-y-4">
          <Card>
            <CardBody className="space-y-4">
              {d.description && (
                <div className="rounded-lg bg-muted/60 p-3.5">
                  <p className="mb-1 text-[12px] font-medium text-muted-foreground">{d.contact?.name ?? d.account} wrote</p>
                  <p className="whitespace-pre-wrap text-[13.5px]">{d.description}</p>
                </div>
              )}
              {d.comments.map((x) => (
                <div key={x.id} className={cn("rounded-lg border p-3.5", x.internal && "border-dashed bg-[color-mix(in_srgb,var(--status-warning)_6%,transparent)]")}>
                  <p className="mb-1 flex items-center gap-1.5 text-[12px] text-muted-foreground">
                    {x.internal ? <Lock className="h-3 w-3" /> : <MessageSquare className="h-3 w-3" />}
                    <span className="font-medium text-foreground">{x.author ?? "Unknown"}</span>{x.internal ? " · internal note" : " · reply"} · {relativeDays(x.created_at)}
                  </p>
                  <p className="whitespace-pre-wrap text-[13.5px]">{x.body}</p>
                </div>
              ))}
              {!d.description && !d.comments.length && <p className="text-[13px] text-muted-foreground">No conversation yet.</p>}
            </CardBody>
          </Card>
          {editable && (
            <Card>
              <CardBody>
                <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); post.mutate(); }}>
                  <div role="radiogroup" aria-label="Message type" className="inline-grid grid-cols-2 rounded-md bg-muted p-0.5 text-[13px]">
                    {[[false, "Reply to customer"], [true, "Internal note"]].map(([v, l]) => (
                      <button key={String(v)} type="button" role="radio" aria-checked={internal === v} onClick={() => setInternal(v as boolean)}
                        className={cn("rounded px-3 py-1", internal === v ? "bg-surface font-medium shadow-sm" : "text-muted-foreground")}>{l as string}</button>
                    ))}
                  </div>
                  <Textarea id="case-reply" aria-label={internal ? "Internal note" : "Reply"} rows={4} required value={reply} onChange={(e) => setReply(e.target.value)}
                    placeholder={internal ? "Only your team sees this" : "Write your reply"} />
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-[12px] text-subtle">{internal ? "Notes don't stop the response clock." : d.first_responded_at ? "Sets the case to Pending (waiting on the customer)." : "The first reply stops the first-response clock."}</p>
                    <Button type="submit" size="sm" loading={post.isPending}>{internal ? "Add note" : "Send reply"}</Button>
                  </div>
                </form>
              </CardBody>
            </Card>
          )}
        </div>

        <div className="space-y-4">
          <Card>
            <CardBody className="space-y-3">
              {([["Status", "status", m.statuses], ["Priority", "severity", m.priorities]] as const).map(([label, key, opts]) => (
                <div key={key}><Label htmlFor={`case-${key}`}>{label}</Label>
                  <Select id={`case-${key}`} disabled={!editable} value={key === "status" ? d.status : d.priority} onChange={(e) => patch.mutate({ [key]: e.target.value })}>
                    {opts.map((o) => <option key={o} value={o}>{o[0].toUpperCase() + o.slice(1)}</option>)}</Select></div>
              ))}
              <div><Label htmlFor="case-owner">Owner</Label>
                <Select id="case-owner" disabled={!editable} value={d.owner_id ?? ""} onChange={(e) => patch.mutate({ owner_id: e.target.value || null })}>
                  <option value="">Unassigned</option>{m.agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</Select></div>
              <div><Label htmlFor="case-queue">Queue</Label>
                <Select id="case-queue" disabled={!editable} value={d.queue_id ?? ""} onChange={(e) => patch.mutate({ queue_id: e.target.value || null })}>
                  <option value="">No queue</option>{m.queues.map((q) => <option key={q.id} value={q.id}>{q.name}</option>)}</Select></div>
              <Clocks d={d} />
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Customer" icon={<Building2 className="h-4 w-4" />} />
            <CardBody className="space-y-2 pt-0 text-[13px]">
              <Link href={`/accounts/${d.account_detail.id}`} className="font-medium hover:text-primary hover:underline">{d.account_detail.name}</Link>
              <p className="text-muted-foreground">{d.account_detail.tier} · health {d.account_detail.health_score}</p>
              {d.contact && <div className="border-t pt-2"><p className="flex items-center gap-1.5 font-medium"><User className="h-3.5 w-3.5" />{d.contact.name}</p>
                {d.contact.job_title && <p className="text-muted-foreground">{d.contact.job_title}</p>}
                {d.contact.email && <p className="select-all">{d.contact.email}</p>}{d.contact.phone && <p className="select-all">{d.contact.phone}</p>}</div>}
              {d.other_cases.length > 0 && <div className="border-t pt-2"><p className="mb-1 text-[12px] font-medium text-muted-foreground">Other cases</p>
                {d.other_cases.map((o) => <Link key={o.id} href={`/cases/${o.id}`} className="block truncate hover:text-primary">{o.case_number} · {o.subject}</Link>)}</div>}
            </CardBody>
          </Card>
          {d.suggested_articles.length > 0 && (
            <Card>
              <CardHeader title="Suggested articles" icon={<BookOpen className="h-4 w-4" />} />
              <CardBody className="space-y-1.5 pt-0">
                {d.suggested_articles.map((a) => (
                  <div key={a.id} className="flex items-start justify-between gap-2 text-[13px]">
                    <Link href={`/knowledge?article=${a.id}`} className="hover:text-primary hover:underline">{a.title}</Link>
                    {editable && <button type="button" className="shrink-0 text-[12px] text-primary hover:underline"
                      onClick={() => { setInternal(false); setReply((r) => `${r}${r ? "\n\n" : ""}This article should help: ${a.title}`); }}>Use in reply</button>}
                  </div>
                ))}
              </CardBody>
            </Card>
          )}
          {closed && (
            <Card>
              <CardHeader title="Customer satisfaction" icon={<Star className="h-4 w-4" />} />
              <CardBody className="pt-0 text-[13px]">
                {d.csat.score ? (
                  <><p className="text-lg font-semibold">{d.csat.score} / 5</p>{d.csat.comment && <p className="text-muted-foreground">“{d.csat.comment}”</p>}
                    <p className="text-[12px] text-subtle">{d.csat.at && relativeDays(d.csat.at)}</p></>
                ) : d.csat.survey_url ? (
                  <><p className="mb-1.5 text-muted-foreground">Send the customer this one-question survey:</p>
                    <code className="block break-all rounded bg-muted px-2 py-1 font-mono text-[11.5px] select-all">{d.csat.survey_url}</code>
                    <Button size="sm" variant="outline" className="mt-2" onClick={() => copy(d.csat.survey_url!)}><Copy className="h-3.5 w-3.5" />Copy link</Button></>
                ) : <p className="text-muted-foreground">No survey for this case.</p>}
              </CardBody>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function Clocks({ d }: { d: CaseDetail }) {
  const row = (label: string, c: Clock) => {
    const text = c.state === "met" ? `Met ${c.done_at ? shortDate(c.done_at) : ""}` : c.state === "missed" ? "Missed"
      : c.state === "breached" ? `Overdue ${duration(c.minutes_left ?? 0)}` : c.state === "none" ? "—" : `Due in ${duration(c.minutes_left ?? 0)}`;
    const tone = c.state === "met" ? "good" : c.state === "missed" || c.state === "breached" ? "critical" : c.state === "due_soon" ? "warning" : "neutral";
    return <div className="flex items-center justify-between text-[13px]"><span className="text-muted-foreground">{label}</span><Badge tone={tone}>{text}</Badge></div>;
  };
  return <div className="space-y-1.5 border-t pt-3">{row("First response", d.clocks.first_response)}{row("Resolution", d.clocks.resolution)}</div>;
}
