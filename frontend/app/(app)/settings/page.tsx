"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AudioLines, Brain, CalendarDays, Copy, Cpu, Database, Inbox, Landmark, Mail, RefreshCw, Server, ShieldCheck, Trash2, Upload } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StatusPill } from "@/components/ui/extra";
import { Input, Label } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { API_URL, api, errorMessage, get } from "@/lib/api";
import { ROLE_LABELS, useMe } from "@/lib/me";
import { relativeDays } from "@/lib/utils";

interface AIStatus { llm_provider: string; model: string; embedding_provider: string; embedding_dim: number; background: string; transcription_provider: string; erp_connector: string }
interface Mailbox { id: string; provider: string; email_address: string; imap_host: string; smtp_host: string | null; status: string; last_synced_at: string | null; last_error: string | null }

const PROVIDER_LABEL: Record<string, string> = {
  ollama: "Ollama (local, on-prem)",
  aws_bedrock: "Claude on AWS Bedrock (your VPC)",
  anthropic: "Claude API",
  heuristic: "relate deterministic engine (offline)",
};
const ASR_LABEL: Record<string, string> = { disabled: "Disabled (type or paste notes)", whisper_asr: "Local Whisper ASR service", faster_whisper: "faster-whisper in the API container" };

export default function SettingsPage() {
  const qc = useQueryClient();
  const { me, can } = useMe();
  const status = useQuery({ queryKey: ["ai-status"], queryFn: () => get<AIStatus>("/ai/status") });
  const rescore = useMutation({
    mutationFn: async () => (await api.post<{ accounts_rescored: number }>("/admin/jobs/rescore")).data,
    onSuccess: (d) => { qc.invalidateQueries(); toast.success(`Re-scored ${d.accounts_rescored} accounts`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const s = status.data;
  const rows = s ? [
    { icon: Brain, label: "Language model", value: PROVIDER_LABEL[s.llm_provider] ?? s.llm_provider, sub: s.model },
    { icon: Database, label: "Retrieval", value: `Hybrid: Postgres full-text + pgvector (${s.embedding_dim}-d)`, sub: `Embeddings: ${s.embedding_provider === "hash" ? "offline feature hashing" : s.embedding_provider}; fused with reciprocal-rank fusion` },
    { icon: AudioLines, label: "Voice transcription", value: ASR_LABEL[s.transcription_provider] ?? s.transcription_provider, sub: "Audio is transcribed on your infrastructure and discarded after transcription" },
    { icon: Landmark, label: "ERP connector", value: s.erp_connector, sub: "Customer master, A/R aging and credit holds" },
    { icon: Cpu, label: "Background jobs", value: s.background === "celery" ? "Celery + Redis workers" : "In-process", sub: "Embedding, scoring, SLA escalation, renewals, ERP sync" },
    { icon: ShieldCheck, label: "Data residency", value: "Single-tenant, self-hosted, zero egress by default", sub: "No CRM data leaves your infrastructure unless you choose a cloud model or configure webhooks" },
  ] : [];

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="Settings" description="Your mail and calendar connections, plus the workspace intelligence layer" />
      <div className="space-y-6">
        <MailCard />
        <CalendarCard />
        <Card>
          <CardHeader title="Intelligence layer" icon={<Server className="h-4 w-4 text-muted-foreground" />} description="Configured with environment variables at deploy time." />
          <CardBody className="divide-y">
            {status.isLoading && <Skeleton className="h-32 w-full" />}
            {rows.map(({ icon: Icon, label, value, sub }) => (
              <div key={label} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
                <Icon className="mt-0.5 h-4 w-4 text-muted-foreground" />
                <div className="flex-1">
                  <p className="text-[12.5px] text-muted-foreground">{label}</p>
                  <p className="text-sm font-medium">{value}</p>
                  <p className="text-[12.5px] text-muted-foreground">{sub}</p>
                </div>
              </div>
            ))}
          </CardBody>
        </Card>
        {can("admin", "update") && (
          <Card>
            <CardHeader title="Scoring engine" description="Health, relationship strength and churn risk are re-computed on every activity. Recency decays daily, so run a full re-score after imports." />
            <CardBody>
              <Button variant="outline" size="sm" loading={rescore.isPending} onClick={() => rescore.mutate()}><RefreshCw className="h-3.5 w-3.5" />Re-score all accounts</Button>
            </CardBody>
          </Card>
        )}
        <Card>
          <CardHeader title="Your profile" />
          <CardBody>
            {me ? (
              <div className="flex items-center gap-3 text-sm">
                <div className="flex-1"><p className="font-medium">{me.full_name}</p><p className="text-muted-foreground">{me.email}</p></div>
                <Badge tone="primary">{ROLE_LABELS[me.role] ?? me.role}</Badge>
              </div>
            ) : <Skeleton className="h-10 w-full" />}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function MailCard() {
  const qc = useQueryClient();
  const boxes = useQuery({ queryKey: ["mailboxes"], queryFn: () => get<Mailbox[]>("/email/mailboxes") });
  const [f, setF] = useState({ email_address: "", imap_host: "", imap_port: 993, smtp_host: "", smtp_port: 587, username: "", password: "" });
  const [open, setOpen] = useState(false);
  const connect = useMutation({
    mutationFn: async () => (await api.post("/email/mailboxes", { ...f, smtp_host: f.smtp_host || null, username: f.username || null })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["mailboxes"] }); setOpen(false); setF({ ...f, password: "" }); toast.success("Mailbox connected. Credentials are encrypted at rest."); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const sync = useMutation({
    mutationFn: async (id: string) => (await api.post<{ fetched?: number; logged?: number; error?: string }>(`/email/mailboxes/${id}/sync`)).data,
    onSuccess: (r) => { qc.invalidateQueries(); r.error ? toast.error(r.error) : toast.success(`Synced: ${r.fetched ?? 0} fetched, ${r.logged ?? 0} logged to timelines`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({ mutationFn: async (id: string) => api.delete(`/email/mailboxes/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["mailboxes"] }) });
  const ingest = useMutation({
    mutationFn: async (file: File) => { const fd = new FormData(); fd.append("file", file); return (await api.post("/email/ingest", fd)).data; },
    onSuccess: () => { qc.invalidateQueries(); toast.success("Email logged to the matching contact's timeline"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader icon={<Mail className="h-4 w-4 text-muted-foreground" />} title="Email sync"
        description="Connect any IMAP/SMTP mailbox (Exchange, Microsoft 365, Google Workspace, Dovecot). Emails with known contacts are logged automatically, and consent and opt-outs are enforced on send."
        action={<Button size="sm" variant="outline" onClick={() => setOpen(!open)}>{open ? "Cancel" : "Connect mailbox"}</Button>} />
      <CardBody className="space-y-3">
        {open && (
          <form className="grid gap-3 rounded-md border p-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); connect.mutate(); }}>
            <div className="sm:col-span-2"><Label htmlFor="mb-email">Email address</Label><Input id="mb-email" type="email" required value={f.email_address} onChange={(e) => setF({ ...f, email_address: e.target.value })} /></div>
            <div><Label htmlFor="mb-imap">IMAP host</Label><Input id="mb-imap" required placeholder="outlook.office365.com" value={f.imap_host} onChange={(e) => setF({ ...f, imap_host: e.target.value })} /></div>
            <div><Label htmlFor="mb-imap-port">IMAP port</Label><Input id="mb-imap-port" type="number" value={f.imap_port} onChange={(e) => setF({ ...f, imap_port: Number(e.target.value) })} /></div>
            <div><Label htmlFor="mb-smtp">SMTP host</Label><Input id="mb-smtp" placeholder="smtp.office365.com" value={f.smtp_host} onChange={(e) => setF({ ...f, smtp_host: e.target.value })} /></div>
            <div><Label htmlFor="mb-smtp-port">SMTP port</Label><Input id="mb-smtp-port" type="number" value={f.smtp_port} onChange={(e) => setF({ ...f, smtp_port: Number(e.target.value) })} /></div>
            <div><Label htmlFor="mb-user">Username (if different)</Label><Input id="mb-user" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></div>
            <div><Label htmlFor="mb-pass">Password / app password</Label><Input id="mb-pass" type="password" required value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></div>
            <div className="flex justify-end sm:col-span-2"><Button type="submit" size="sm" loading={connect.isPending}>Connect</Button></div>
          </form>
        )}
        {boxes.data?.map((m) => (
          <div key={m.id} className="flex flex-wrap items-center gap-3 rounded-md border px-3 py-2">
            <Inbox className="h-4 w-4 text-muted-foreground" />
            <div className="min-w-0 flex-1"><p className="text-[13.5px] font-medium">{m.email_address}</p>
              <p className="text-[12px] text-muted-foreground">{m.imap_host} · {m.last_synced_at ? `synced ${relativeDays(m.last_synced_at)}` : "never synced"}{m.last_error && ` · ${m.last_error}`}</p></div>
            <StatusPill status={m.status} />
            <Button size="sm" variant="outline" loading={sync.isPending && sync.variables === m.id} onClick={() => sync.mutate(m.id)}><RefreshCw className="h-3.5 w-3.5" />Sync</Button>
            <Button size="sm" variant="ghost" aria-label="Disconnect" onClick={() => remove.mutate(m.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
          </div>
        ))}
        {boxes.data && !boxes.data.length && !open && <p className="text-[13px] text-muted-foreground">No mailbox connected.</p>}
        <label className="inline-flex cursor-pointer items-center gap-2 text-[13px] text-primary hover:underline">
          <Upload className="h-3.5 w-3.5" />Log a saved email (.eml)
          <input type="file" accept=".eml,message/rfc822" className="sr-only" onChange={(e) => { const file = e.target.files?.[0]; if (file) ingest.mutate(file); e.target.value = ""; }} />
        </label>
      </CardBody>
    </Card>
  );
}

function CalendarCard() {
  const qc = useQueryClient();
  const [feed, setFeed] = useState<string | null>(null);
  const token = useMutation({
    mutationFn: async () => (await api.post<{ feed_path: string }>("/calendar/token")).data,
    onSuccess: (r) => setFeed(`${API_URL}${r.feed_path}`),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const imp = useMutation({
    mutationFn: async (file: File) => { const fd = new FormData(); fd.append("file", file); return (await api.post<Record<string, number>>("/calendar/import", fd)).data; },
    onSuccess: (r) => { qc.invalidateQueries(); toast.success(`Calendar import: ${Object.entries(r).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ")}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader icon={<CalendarDays className="h-4 w-4 text-muted-foreground" />} title="Calendar"
        description="Subscribe to your tasks and milestones from Outlook, Google Calendar, Apple Calendar or any CalDAV client. Import .ics invites to log meetings with attendees matched to contacts." />
      <CardBody className="space-y-3">
        {feed ? (
          <div className="flex items-center gap-2">
            <Input readOnly value={feed} aria-label="iCal feed URL" className="font-mono text-[12px]" />
            <Button size="sm" variant="outline" onClick={() => navigator.clipboard.writeText(feed).then(() => toast.success("Feed URL copied"))}><Copy className="h-3.5 w-3.5" />Copy</Button>
          </div>
        ) : <Button size="sm" variant="outline" loading={token.isPending} onClick={() => token.mutate()}>Get my iCal feed URL</Button>}
        <label className="inline-flex cursor-pointer items-center gap-2 text-[13px] text-primary hover:underline">
          <Upload className="h-3.5 w-3.5" />Import meetings from .ics
          <input type="file" accept=".ics,text/calendar" className="sr-only" onChange={(e) => { const file = e.target.files?.[0]; if (file) imp.mutate(file); e.target.value = ""; }} />
        </label>
      </CardBody>
    </Card>
  );
}
