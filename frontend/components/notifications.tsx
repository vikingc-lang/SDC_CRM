"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, BellRing, Check, Clock, Mail, MailOpen, MessageSquare, Monitor, Moon, Send, Smartphone, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Tabs } from "@/components/ui/extra";
import { Input, Label, Select } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { cn, relativeDays } from "@/lib/utils";

export interface InboxItem {
  id: string; kind: string; kind_label: string; title: string; body: string | null; link: string | null; priority: "normal" | "high";
  read: boolean; archived: boolean; snoozed_until: string | null; created_at: string;
}
export interface Inbox { unread: number; unread_by_kind: Record<string, number>; items: InboxItem[]; kinds: { key: string; label: string }[] }
type Channel = "in_app" | "email" | "push" | "chat";
interface Prefs {
  email_mode: "instant" | "digest" | "off"; digest_hour: number; quiet: { enabled: boolean; start: string; end: string };
  kinds: Record<string, Record<Channel, boolean>>;
  catalog: { key: string; label: string; description: string; high_priority: boolean }[];
  channels: Record<Channel, boolean>;
  devices: { id: string; user_agent: string | null; created_at: string; last_used_at: string | null }[];
  timezone: string;
}

type View = "inbox" | "unread" | "snoozed" | "archived";

function snoozeUntil(choice: "1h" | "3h" | "tomorrow" | "monday"): string {
  const d = new Date();
  if (choice === "1h") d.setHours(d.getHours() + 1);
  else if (choice === "3h") d.setHours(d.getHours() + 3);
  else {
    const days = choice === "tomorrow" ? 1 : ((8 - d.getDay()) % 7 || 7);
    d.setDate(d.getDate() + days);
    d.setHours(9, 0, 0, 0);
  }
  return d.toISOString();
}

/** The full notification center: inbox, unread, snoozed and archived, filtered by kind. */
export function NotificationCenter() {
  const qc = useQueryClient();
  const router = useRouter();
  const [view, setView] = useState<View>("inbox");
  const [kind, setKind] = useState<string>("");
  const inbox = useQuery({
    queryKey: ["notifications", view, kind],
    queryFn: () => get<Inbox>("/notifications", { view, ...(kind ? { kind } : {}), limit: 100 }),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["notifications"] });
  const act = useMutation({
    mutationFn: async ({ path, body }: { path: string; body?: unknown }) => (await api.post(`/notifications/${path}`, body)).data,
    onSuccess: refresh,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const data = inbox.data;
  const kinds = (data?.kinds ?? []).filter((k) => view !== "inbox" || data?.unread_by_kind[k.key] || k.key === kind);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Tabs<View> className="mb-0 flex-1" value={view} onChange={setView} tabs={[
          { value: "inbox", label: "Inbox", count: data && view === "inbox" ? data.unread : undefined },
          { value: "unread", label: "Unread" },
          { value: "snoozed", label: "Snoozed" },
          { value: "archived", label: "Archived" },
        ]} />
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => act.mutate({ path: "read" })} disabled={!data?.unread}><Check className="h-3.5 w-3.5" />Mark all read</Button>
          <Button size="sm" variant="outline" onClick={() => act.mutate({ path: "archive", body: { all_read: true } })}><Archive className="h-3.5 w-3.5" />Archive read</Button>
          <Link href="/settings?tab=notifications" className="inline-flex h-8 items-center rounded-md px-2.5 text-[13px] text-primary hover:underline">Settings</Link>
        </div>
      </div>
      {!!kinds.length && (
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by kind">
          <button onClick={() => setKind("")} className={cn("rounded-full border px-2.5 py-0.5 text-[12px]", !kind ? "border-primary bg-primary-soft text-primary" : "text-muted-foreground")}>All</button>
          {kinds.map((k) => (
            <button key={k.key} onClick={() => setKind(k.key === kind ? "" : k.key)} aria-pressed={k.key === kind}
              className={cn("rounded-full border px-2.5 py-0.5 text-[12px]", k.key === kind ? "border-primary bg-primary-soft text-primary" : "text-muted-foreground")}>
              {k.label}{data?.unread_by_kind[k.key] ? ` · ${data.unread_by_kind[k.key]}` : ""}
            </button>
          ))}
        </div>
      )}
      <Card>
        {inbox.isLoading && <div className="p-5"><Skeleton className="h-40 w-full" /></div>}
        {data && !data.items.length && (
          <div className="p-5"><EmptyState icon={<BellRing className="h-5 w-5" />} title={view === "inbox" ? "You're all caught up" : `Nothing ${view}`}
            description={view === "snoozed" ? "Snoozed notifications come back to your inbox at the time you chose." : undefined} /></div>
        )}
        <ul className="divide-y">
          {data?.items.map((n) => (
            <li key={n.id} className={cn("flex flex-wrap items-start gap-3 px-4 py-3 sm:flex-nowrap", !n.read && "bg-primary-soft/40")}>
              <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", n.read ? "bg-transparent" : n.priority === "high" ? "bg-destructive" : "bg-primary")} aria-hidden />
              <button className="min-w-0 flex-1 text-left" onClick={() => { if (!n.read) act.mutate({ path: "read", body: [n.id] }); if (n.link) router.push(n.link); }}>
                <span className="block text-[13.5px] font-medium leading-snug">{n.title}</span>
                {n.body && <span className="block text-[12.5px] text-muted-foreground">{n.body}</span>}
                <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11.5px] text-subtle">
                  <Badge tone={n.priority === "high" ? "critical" : "neutral"}>{n.kind_label}</Badge>
                  {relativeDays(n.created_at)}
                  {n.snoozed_until && view === "snoozed" && <span>· back {relativeDays(n.snoozed_until)}</span>}
                </span>
              </button>
              <div className="flex shrink-0 items-center gap-1">
                {n.read
                  ? <Button size="icon" variant="ghost" aria-label={`Mark unread: ${n.title}`} title="Mark unread" onClick={() => act.mutate({ path: "unread", body: { ids: [n.id] } })}><Mail className="h-3.5 w-3.5" /></Button>
                  : <Button size="icon" variant="ghost" aria-label={`Mark read: ${n.title}`} title="Mark read" onClick={() => act.mutate({ path: "read", body: [n.id] })}><MailOpen className="h-3.5 w-3.5" /></Button>}
                {view !== "archived" && (
                  <Select aria-label={`Snooze: ${n.title}`} className="h-8 w-[136px] text-[12px]" value="" onChange={(e) => {
                    const v = e.target.value as "1h" | "3h" | "tomorrow" | "monday";
                    if (v) act.mutate({ path: "snooze", body: { ids: [n.id], until: snoozeUntil(v) } }, { onSuccess: () => toast.success("Snoozed") });
                  }}>
                    <option value="">Snooze…</option><option value="1h">1 hour</option><option value="3h">3 hours</option>
                    <option value="tomorrow">Tomorrow 9:00</option><option value="monday">Next week</option>
                  </Select>
                )}
                {n.archived
                  ? <Button size="icon" variant="ghost" aria-label={`Restore: ${n.title}`} title="Restore" onClick={() => act.mutate({ path: "archive", body: { ids: [n.id], restore: true } })}><ArchiveRestore className="h-3.5 w-3.5" /></Button>
                  : <Button size="icon" variant="ghost" aria-label={`Archive: ${n.title}`} title="Archive" onClick={() => act.mutate({ path: "archive", body: { ids: [n.id] } })}><Archive className="h-3.5 w-3.5" /></Button>}
              </div>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

// ---- Web Push on this device ------------------------------------------------------------------------------------
function b64ToBytes(b64: string): Uint8Array {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

export function deviceLabel(ua: string | null | undefined): string {
  if (!ua) return "Unknown device";
  const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Windows/.test(ua) ? "Windows" : /Mac OS/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} on ${os}` : browser;
}

export function pushSupported(): boolean {
  return typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

export async function enablePush(): Promise<void> {
  if (!pushSupported()) throw new Error("This browser can't receive push notifications. On iPhone, add Cirra to your Home Screen first.");
  const permission = await window.Notification.requestPermission();
  if (permission !== "granted") throw new Error("Notifications are blocked for this site in your browser settings");
  const reg = (await navigator.serviceWorker.getRegistration()) ?? (await navigator.serviceWorker.register("/sw.js"));
  await navigator.serviceWorker.ready;
  const { public_key } = await get<{ public_key: string }>("/notifications/push/key");
  const sub = (await reg.pushManager.getSubscription()) ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(public_key) }));
  const json = sub.toJSON();
  await api.post("/notifications/push/subscribe", { endpoint: json.endpoint, keys: json.keys, user_agent: deviceLabel(navigator.userAgent) });
}

const CHANNELS: { key: Channel; label: string; icon: typeof Mail }[] = [
  { key: "in_app", label: "In the app", icon: Monitor },
  { key: "email", label: "Email", icon: Mail },
  { key: "push", label: "Push", icon: Smartphone },
  { key: "chat", label: "Slack", icon: MessageSquare },
];

/** Settings → Notifications: what reaches you where, email digests, quiet hours and devices. */
export function NotificationSettingsCard() {
  const qc = useQueryClient();
  const prefs = useQuery({ queryKey: ["notification-prefs"], queryFn: () => get<Prefs>("/notifications/preferences") });
  const save = useMutation({
    mutationFn: async (body: Partial<Prefs>) => (await api.put<Prefs>("/notifications/preferences", body)).data,
    onMutate: (body) => {  // show the change at once; roll back if the server refuses it
      const prev = qc.getQueryData<Prefs>(["notification-prefs"]);
      if (prev) qc.setQueryData<Prefs>(["notification-prefs"], { ...prev, ...body, kinds: body.kinds ?? prev.kinds, quiet: body.quiet ?? prev.quiet });
      return { prev };
    },
    onSuccess: (d) => { qc.setQueryData(["notification-prefs"], d); qc.invalidateQueries({ queryKey: ["notifications"] }); toast.success("Notification settings saved"); },
    onError: (e, _body, ctx) => { if (ctx?.prev) qc.setQueryData(["notification-prefs"], ctx.prev); toast.error(errorMessage(e)); },
  });
  const push = useMutation({
    mutationFn: enablePush,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["notification-prefs"] }); toast.success("Push notifications are on for this device"); },
    onError: (e) => toast.error(e instanceof Error && !("response" in e) ? e.message : errorMessage(e)),
  });
  const removeDevice = useMutation({
    mutationFn: async (id: string) => (await api.post("/notifications/push/unsubscribe", { id })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notification-prefs"] }),
  });
  const test = useMutation({
    mutationFn: async () => (await api.post<{ delivery: Record<string, number> }>("/notifications/test")).data,
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
      const where = Object.keys(d.delivery).filter((k) => k.endsWith(":sent")).map((k) => k.split(":")[0]);
      toast.success(`Test notification sent${where.length ? ` (also by ${where.join(", ")})` : " in the app"}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const p = prefs.data;
  if (!p) return <Card><CardHeader title="Notifications" /><CardBody><Skeleton className="h-40 w-full" /></CardBody></Card>;
  const toggle = (kind: string, ch: Channel, value: boolean) => save.mutate({ kinds: { ...p.kinds, [kind]: { ...p.kinds[kind], [ch]: value } } });
  const hint: Record<Channel, string> = { in_app: "", email: "System email isn't set up on this server", push: "", chat: "Connect Slack in Admin → Connectors" };
  return (
    <Card id="notifications">
      <CardHeader icon={<BellRing className="h-4 w-4 text-muted-foreground" />} title="Notifications"
        description="Choose what reaches you in the app, by email, as a push notification on your devices, or in Slack."
        action={<Button size="sm" variant="outline" loading={test.isPending} onClick={() => test.mutate()}><Send className="h-3.5 w-3.5" />Send a test</Button>} />
      <CardBody className="space-y-5">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-[13px]">
            <thead>
              <tr className="text-left text-[12px] text-muted-foreground">
                <th className="py-1.5 pr-2 font-medium">Notify me about</th>
                {CHANNELS.map((c) => (
                  <th key={c.key} className="px-2 py-1.5 text-center font-medium" title={p.channels[c.key] ? undefined : hint[c.key]}>
                    <span className={cn("inline-flex items-center gap-1", !p.channels[c.key] && "opacity-50")}><c.icon className="h-3.5 w-3.5" />{c.label}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y">
              {p.catalog.map((k) => (
                <tr key={k.key}>
                  <td className="py-2 pr-2">
                    <span className="font-medium">{k.label}</span>{k.high_priority && <Badge tone="critical" className="ml-1.5">urgent</Badge>}
                    <span className="block text-[12px] text-muted-foreground">{k.description}</span>
                  </td>
                  {CHANNELS.map((c) => (
                    <td key={c.key} className="px-2 py-2 text-center">
                      <input type="checkbox" className="h-4 w-4 accent-[var(--primary)]" aria-label={`${k.label}: ${c.label}`}
                        disabled={!p.channels[c.key]} checked={p.kinds[k.key]?.[c.key] ?? false} onChange={(e) => toggle(k.key, c.key, e.target.checked)} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="n-email-mode">Email</Label>
            <div className="flex gap-2">
              <Select id="n-email-mode" value={p.email_mode} onChange={(e) => save.mutate({ email_mode: e.target.value as Prefs["email_mode"] })}>
                <option value="instant">Send each one as it happens</option>
                <option value="digest">One daily summary</option>
                <option value="off">Never email me</option>
              </Select>
              {p.email_mode === "digest" && (
                <Select aria-label="Summary time" className="w-28" value={p.digest_hour} onChange={(e) => save.mutate({ digest_hour: Number(e.target.value) })}>
                  {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
                </Select>
              )}
            </div>
            <p className="mt-1 text-[12px] text-muted-foreground">Urgent notifications are always emailed right away. Times are in {p.timezone}.</p>
          </div>
          <div>
            <Label htmlFor="n-quiet"><span className="inline-flex items-center gap-1"><Moon className="h-3.5 w-3.5" />Quiet hours</span></Label>
            <div className="flex items-center gap-2">
              <input id="n-quiet" type="checkbox" className="h-4 w-4 accent-[var(--primary)]" checked={p.quiet.enabled}
                onChange={(e) => save.mutate({ quiet: { ...p.quiet, enabled: e.target.checked } })} />
              <Input aria-label="Quiet from" type="time" className="w-28" value={p.quiet.start} disabled={!p.quiet.enabled}
                onChange={(e) => save.mutate({ quiet: { ...p.quiet, start: e.target.value } })} />
              <span className="text-muted-foreground">to</span>
              <Input aria-label="Quiet until" type="time" className="w-28" value={p.quiet.end} disabled={!p.quiet.enabled}
                onChange={(e) => save.mutate({ quiet: { ...p.quiet, end: e.target.value } })} />
            </div>
            <p className="mt-1 text-[12px] text-muted-foreground">Email, push and Slack wait until quiet hours end (urgent ones still come through).</p>
          </div>
        </div>
        <div>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <p className="text-[13px] font-medium">Devices with push notifications</p>
            <Button size="sm" variant="outline" loading={push.isPending} onClick={() => push.mutate()} disabled={!pushSupported()}>
              <Smartphone className="h-3.5 w-3.5" />Turn on for this device
            </Button>
          </div>
          {!p.devices.length && <p className="text-[12.5px] text-muted-foreground">No devices yet. On a phone, install Cirra from the browser menu (Add to Home Screen) first.</p>}
          <ul className="space-y-1.5">
            {p.devices.map((d) => (
              <li key={d.id} className="flex items-center gap-2 rounded-md border px-3 py-1.5 text-[13px]">
                <Smartphone className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="flex-1">{d.user_agent ?? "Unknown device"}</span>
                <span className="text-[12px] text-muted-foreground"><Clock className="mr-1 inline h-3 w-3" />{d.last_used_at ? `last push ${relativeDays(d.last_used_at)}` : `added ${relativeDays(d.created_at)}`}</span>
                <Button size="icon" variant="ghost" aria-label={`Remove ${d.user_agent ?? "device"}`} onClick={() => removeDevice.mutate(d.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
              </li>
            ))}
          </ul>
        </div>
      </CardBody>
    </Card>
  );
}
