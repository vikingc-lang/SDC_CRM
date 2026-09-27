"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Inbox, Mail, Radio } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { AccountPicker } from "@/components/objects";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Table, Tabs, Td } from "@/components/ui/extra";
import { Input, Label, Select } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import { cn, relativeDays } from "@/lib/utils";

/* Service operations: agent presence (for presence-routed queues), the supervisor's routing console, and the
   support-email inbox (what email-to-case did with each message, and the unmatched mail to file). */

type Presence = "available" | "busy" | "away" | "offline";
const PRESENCE: Record<Presence, { label: string; dot: string }> = {
  available: { label: "Available", dot: "bg-[var(--status-good)]" }, busy: { label: "Busy", dot: "bg-[var(--status-warning)]" },
  away: { label: "Away", dot: "bg-[var(--status-warning)] opacity-60" }, offline: { label: "Offline", dot: "bg-muted-foreground/50" },
};

export function PresenceControl() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["cases", "presence"], queryFn: () => get<{ status: Presence; capacity: number }>("/cases/presence/me") });
  const save = useMutation({
    mutationFn: async (body: { status?: Presence; capacity?: number }) => (await api.put("/cases/presence/me", body)).data,
    onSuccess: (_, v) => { qc.invalidateQueries({ queryKey: ["cases"] }); if (v.status) toast.success(`You're ${PRESENCE[v.status].label.toLowerCase()}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (!me.data) return null;
  return (
    <div className="flex items-center gap-2 rounded-md border px-2 py-1 text-[13px]">
      <span className={cn("h-2 w-2 rounded-full", PRESENCE[me.data.status].dot)} aria-hidden />
      <Select aria-label="My status" className="h-7 w-32 border-0 text-[13px] shadow-none" value={me.data.status} onChange={(e) => save.mutate({ status: e.target.value as Presence })}>
        {(Object.keys(PRESENCE) as Presence[]).map((k) => <option key={k} value={k}>{PRESENCE[k].label}</option>)}
      </Select>
      <label className="flex items-center gap-1 text-muted-foreground">Capacity
        <Input aria-label="My capacity" type="number" min={1} max={50} className="h-7 w-14 text-[13px]" defaultValue={me.data.capacity} key={me.data.capacity}
          onBlur={(e) => { const n = Number(e.target.value); if (n >= 1 && n <= 50 && n !== me.data!.capacity) save.mutate({ capacity: n }); }} />
      </label>
    </div>
  );
}

interface Console {
  agents: { id: string; name: string; active: boolean; status: Presence; capacity: number; open_cases: number; last_assigned_at: string | null; queues: string[] }[];
  queues: { id: string; name: string; routing: "least_loaded" | "presence"; email_address: string | null; waiting: number; oldest_waiting_at: string | null }[];
}

export function RoutingConsole() {
  const { can } = useMe();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["cases", "routing"], queryFn: () => get<Console>("/cases/routing"), refetchInterval: 30_000 });
  const set = useMutation({
    mutationFn: async ({ id, status }: { id: string; status: Presence }) => api.put(`/cases/presence/${id}`, { status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cases"] }), onError: (e) => toast.error(errorMessage(e)),
  });
  if (!q.data) return <Skeleton className="h-64" />;
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {q.data.queues.map((x) => (
          <Card key={x.id} className="p-4">
            <p className="flex items-center justify-between text-[13.5px] font-medium">{x.name}
              <Badge tone={x.routing === "presence" ? "primary" : "neutral"}>{x.routing === "presence" ? "Presence" : "Least loaded"}</Badge></p>
            <p className="mt-1 text-[12px] text-muted-foreground">{x.email_address ? <><Mail className="mr-1 inline h-3 w-3" />{x.email_address}</> : "No support address"}</p>
            <p className="mt-2 text-[22px] font-semibold tabular">{x.waiting}<span className="ml-1 text-[12.5px] font-normal text-muted-foreground">waiting unassigned</span></p>
            {x.oldest_waiting_at && <p className="text-[12px] text-muted-foreground">oldest opened {relativeDays(x.oldest_waiting_at)}</p>}
          </Card>
        ))}
      </div>
      <Card>
        <CardHeader title="Agents" description="Presence and load. In presence-routed queues only Available agents under capacity receive new work; the rest waits and is pushed as agents free up." />
        {!q.data.agents.length ? <p className="px-5 pb-5 text-[13px] text-muted-foreground">No agents are in a queue yet (Admin → Service).</p> : (
          <Table head={["Agent", "Status", "Load", "Queues", "Last assigned", ...(can("admin", "update") ? [""] : [])]}>
            {q.data.agents.map((a) => {
              const pct = Math.min(100, Math.round((a.open_cases / a.capacity) * 100));
              return (
                <tr key={a.id}>
                  <Td className="font-medium">{a.name}{!a.active && <span className="ml-1 text-[12px] text-subtle">(inactive)</span>}</Td>
                  <Td><span className="inline-flex items-center gap-1.5 text-[13px]"><span className={cn("h-2 w-2 rounded-full", PRESENCE[a.status].dot)} />{PRESENCE[a.status].label}</span></Td>
                  <Td className="w-48"><div className="flex items-center gap-2 text-[12.5px] tabular">{a.open_cases}/{a.capacity}
                    <div className="h-1.5 flex-1 rounded-full bg-muted"><div className={cn("h-1.5 rounded-full", pct >= 100 ? "bg-[var(--status-warning)]" : "bg-primary")} style={{ width: `${pct}%` }} /></div></div></Td>
                  <Td className="text-[12.5px]">{a.queues.join(", ")}</Td>
                  <Td className="text-[12.5px] text-muted-foreground">{a.last_assigned_at ? relativeDays(a.last_assigned_at) : "—"}</Td>
                  {can("admin", "update") && <Td>
                    <Select aria-label={`Set ${a.name}'s status`} className="h-7 w-32 text-[12px]" value={a.status} onChange={(e) => set.mutate({ id: a.id, status: e.target.value as Presence })}>
                      {(Object.keys(PRESENCE) as Presence[]).map((k) => <option key={k} value={k}>{PRESENCE[k].label}</option>)}
                    </Select></Td>}
                </tr>
              );
            })}
          </Table>
        )}
      </Card>
    </div>
  );
}

interface InboundMsg {
  id: string; from_email: string; from_name: string | null; to_email: string | null; subject: string; body: string; status: string; detail: string | null;
  case_id: string | null; case_number: string | null; received_at: string;
}
const STATUS_LABEL: Record<string, string> = { unmatched: "Needs filing", case_created: "Case opened", appended: "Added to case", converted: "Filed",
  ignored: "Ignored", dismissed: "Dismissed" };

export function SupportInbox() {
  const [tab, setTab] = useState<"unmatched" | "all">("unmatched");
  const [filing, setFiling] = useState<InboundMsg | null>(null);
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["cases", "inbound", tab], queryFn: () => get<{ email_enabled: boolean; inbound_enabled: boolean; messages: InboundMsg[] }>("/cases/inbound", { status: tab }) });
  const dismiss = useMutation({
    mutationFn: async (id: string) => api.post(`/cases/inbound/${id}/dismiss`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["cases", "inbound"] }); toast.success("Dismissed"); }, onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <div>
      {q.data && !q.data.inbound_enabled && (
        <p className="mb-3 rounded-md border border-dashed px-3 py-2 text-[12.5px] text-muted-foreground">
          Email-to-case isn&apos;t connected yet: set INBOUND_EMAIL_SECRET for your mail provider&apos;s inbound webhook, or SUPPORT_IMAP_* for a support mailbox.
          {!q.data.email_enabled && " Replies and acknowledgements go out by email once system email (SMTP_*) is configured."}
        </p>
      )}
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "unmatched", label: "Needs filing" }, { value: "all", label: "All support email" }]} />
      <Card>
        {!q.data ? <Skeleton className="m-4 h-40" /> : !q.data.messages.length ? (
          <EmptyState icon={<Inbox className="h-4 w-4" />} title={tab === "unmatched" ? "Nothing to file" : "No support email yet"}
            description={tab === "unmatched" ? "Mail from senders that match no contact or account lands here." : undefined} />
        ) : (
          <Table head={["From", "Subject", "Received", "Result", ""]} minWidth={860}>
            {q.data.messages.map((m) => (
              <tr key={m.id}>
                <Td><span className="font-medium">{m.from_name ?? m.from_email}</span>{m.from_name && <p className="text-[12px] text-muted-foreground">{m.from_email}</p>}</Td>
                <Td className="max-w-md"><p className="truncate text-[13px] font-medium">{m.subject || "(no subject)"}</p><p className="line-clamp-2 text-[12px] text-muted-foreground">{m.body}</p></Td>
                <Td className="whitespace-nowrap text-[12.5px] text-muted-foreground">{relativeDays(m.received_at)}</Td>
                <Td className="text-[12.5px]"><Badge tone={m.status === "unmatched" ? "warning" : m.status === "ignored" || m.status === "dismissed" ? "neutral" : "good"}>{STATUS_LABEL[m.status] ?? m.status}</Badge>
                  {m.case_id ? <Link href={`/cases/${m.case_id}`} className="ml-1.5 text-primary hover:underline">{m.case_number}</Link> : m.detail && <p className="mt-0.5 text-[12px] text-muted-foreground">{m.detail}</p>}</Td>
                <Td className="whitespace-nowrap text-right">{m.status === "unmatched" && <>
                  <Button size="sm" variant="outline" onClick={() => setFiling(m)}>File as case</Button>
                  <Button size="sm" variant="ghost" loading={dismiss.isPending} onClick={() => dismiss.mutate(m.id)}>Dismiss</Button></>}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      {filing && <FileDialog msg={filing} onClose={() => setFiling(null)} />}
    </div>
  );
}

function FileDialog({ msg, onClose }: { msg: InboundMsg; onClose: () => void }) {
  const qc = useQueryClient();
  const [account, setAccount] = useState("");
  const [contact, setContact] = useState("");
  const contacts = useQuery({ queryKey: ["contacts", "of", account], enabled: !!account,
    queryFn: () => get<{ id: string; first_name: string; last_name: string; email: string | null }[]>("/contacts", { account_id: account }) });
  const file = useMutation({
    mutationFn: async () => (await api.post<{ case_id: string; case_number: string }>(`/cases/inbound/${msg.id}/file`, { account_id: account, contact_id: contact || null })).data,
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["cases"] }); toast.success(`Filed as ${r.case_number}`); onClose(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title="File as case" className="max-w-lg">
        <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); file.mutate(); }}>
          <div><h2 className="text-[15px] font-semibold">File “{msg.subject || "(no subject)"}”</h2><p className="text-[12.5px] text-muted-foreground">From {msg.from_email}</p></div>
          <div><Label htmlFor="rec-account">Account</Label><AccountPicker value={account} onChange={(v) => { setAccount(v); setContact(""); }} /></div>
          {account && <div><Label htmlFor="file-contact">Contact (optional)</Label>
            <Select id="file-contact" value={contact} onChange={(e) => setContact(e.target.value)}>
              <option value="">No contact: reply to {msg.from_email}</option>
              {(contacts.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.first_name} {c.last_name}{c.email ? ` · ${c.email}` : ""}</option>)}
            </Select></div>}
          <div className="flex justify-end gap-2"><Button type="button" size="sm" variant="ghost" onClick={onClose}>Cancel</Button>
            <Button type="submit" size="sm" disabled={!account} loading={file.isPending}>File as case</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function ServiceOpsLinks() {
  const { can } = useMe();
  const inbox = useQuery({ queryKey: ["cases", "inbound", "unmatched"], queryFn: () => get<{ messages: InboundMsg[] }>("/cases/inbound", { status: "unmatched" }),
    enabled: can("cases", "update") });
  const n = inbox.data?.messages.length ?? 0;
  return (
    <>
      <Link href="/cases/routing" className="inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-[13px] hover:bg-muted"><Radio className="h-3.5 w-3.5" />Routing</Link>
      {can("cases", "update") && <Link href="/cases/inbox" className="inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-[13px] hover:bg-muted">
        <Inbox className="h-3.5 w-3.5" />Email inbox{n > 0 && <Badge tone="warning">{n}</Badge>}</Link>}
    </>
  );
}

