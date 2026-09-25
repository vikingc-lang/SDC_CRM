"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Clock, HeartPulse, Link2, Mail, Phone, ShieldAlert, ShieldCheck, Smartphone, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ActivityTimeline } from "@/components/ActivityTimeline";
import { RoleBadge } from "@/components/indicators";
import { CustomFieldsEditor } from "@/components/panels";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, StatusPill } from "@/components/ui/extra";
import { Input, Label, Select } from "@/components/ui/input";
import { Avatar, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import type { Activity, Contact, CustomFieldDef } from "@/lib/types";
import { relativeDays, shortDate } from "@/lib/utils";

interface Profile extends Contact {
  activities: Activity[];
  consent_events: { id: number; event_type: string; channel: string | null; regulation: string | null; source: string | null; created_at: string }[];
  erasure: { subject_hash: string; fields_erased: string[]; regulation: string | null; created_at: string } | null;
  channel_permissions: Record<string, { allowed: boolean; reason: string | null }>;
  custom_field_definitions: CustomFieldDef[];
}

function LocalTime({ tz }: { tz: string }) {
  const [now, setNow] = useState<string>("");
  useEffect(() => {
    const f = () => {
      try { setNow(new Intl.DateTimeFormat("en-US", { timeZone: tz, hour: "numeric", minute: "2-digit", weekday: "short" }).format(new Date())); } catch { setNow(""); }
    };
    f();
    const t = setInterval(f, 30_000);
    return () => clearInterval(t);
  }, [tz]);
  return <>{now ? `${now} local (${tz})` : tz}</>;
}

export default function ContactPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const { can } = useMe();
  const { data: c, isLoading } = useQuery({ queryKey: ["contact", id], queryFn: () => get<Profile>(`/contacts/${id}`) });
  const [erase, setErase] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["contact", id] });
  const consent = useMutation({
    mutationFn: async (body: Record<string, unknown>) => (await api.post(`/contacts/${id}/consent`, body)).data,
    onSuccess: () => { refresh(); toast.success("Consent record updated"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const patch = useMutation({
    mutationFn: async (body: Record<string, unknown>) => (await api.patch(`/contacts/${id}`, body)).data,
    onSuccess: () => { refresh(); toast.success("Contact updated"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const doErase = useMutation({
    mutationFn: async () => (await api.post(`/contacts/${id}/erase`, { regulation: c?.consent?.regime || null, confirm: true })).data,
    onSuccess: (r: { subject_hash: string }) => { setErase(false); refresh(); toast.success("Personal data erased", { description: `Evidence hash ${r.subject_hash.slice(0, 16)}…` }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (isLoading || !c) return <div className="mx-auto max-w-6xl space-y-4"><Skeleton className="h-20 w-full" /><Skeleton className="h-64 w-full" /></div>;
  const erased = c.status === "erased";
  const rsi = c.rsi_factors ?? {};

  return (
    <div className="mx-auto max-w-6xl">
      <Link href={`/accounts/${c.account_id}`} className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />{c.account_name}</Link>
      <div className="mb-6 flex flex-wrap items-center gap-4">
        <Avatar name={c.name} size={56} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-[22px] font-semibold tracking-tight">{c.name}</h1>
            <RoleBadge role={c.buying_role} />
            {c.status !== "active" && <StatusPill status={c.status ?? "active"} />}
          </div>
          <p className="mt-1 text-[13.5px] text-muted-foreground">{[c.job_title, c.department].filter(Boolean).join(" · ")}{c.account_name && ` at ${c.account_name}`}</p>
        </div>
        {!erased && can("contacts", "update") && (
          <Select className="w-auto" value={c.status} onChange={(e) => patch.mutate({ status: e.target.value })} aria-label="Contact status">
            <option value="active">Active</option><option value="departed">Departed company</option>
          </Select>
        )}
      </div>

      {erased && c.erasure && (
        <div className="mb-6 rounded-lg border p-4 text-[13px]" style={{ borderColor: "color-mix(in srgb, var(--status-critical) 40%, transparent)" }}>
          <p className="font-medium">Personal data erased {shortDate(c.erasure.created_at, true)} ({c.erasure.regulation ?? "privacy request"})</p>
          <p className="mt-1 text-muted-foreground">Fields: {c.erasure.fields_erased.join(", ")}. The encryption key was destroyed, so historical values in the audit trail are unrecoverable.</p>
          <p className="mt-1 break-all font-mono text-[11px] text-subtle">Evidence SHA-256: {c.erasure.subject_hash}</p>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card>
            <CardHeader title="Profile" />
            <CardBody>
              <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
                <Field label="Email">{c.email ? <a className="inline-flex items-center gap-1.5 hover:underline" href={`mailto:${c.email}`}><Mail className="h-3.5 w-3.5 text-muted-foreground" />{c.email}</a> : null}</Field>
                <Field label="Direct phone">{c.phone ? <span className="inline-flex items-center gap-1.5"><Phone className="h-3.5 w-3.5 text-muted-foreground" />{c.phone}</span> : null}</Field>
                <Field label="Mobile">{c.mobile ? <span className="inline-flex items-center gap-1.5"><Smartphone className="h-3.5 w-3.5 text-muted-foreground" />{c.mobile}</span> : null}</Field>
                <Field label="LinkedIn">{c.linkedin_url ? <a className="inline-flex items-center gap-1.5 text-primary hover:underline" href={c.linkedin_url} target="_blank" rel="noreferrer"><Link2 className="h-3.5 w-3.5" />Profile</a> : null}</Field>
                <Field label="Time zone">{c.timezone ? <span className="inline-flex items-center gap-1.5"><Clock className="h-3.5 w-3.5 text-muted-foreground" /><LocalTime tz={c.timezone} /></span> : null}</Field>
                <Field label="Department">{c.department}</Field>
              </dl>
            </CardBody>
          </Card>
          {!!c.custom_field_definitions.length && (
            <Card>
              <CardHeader title="Custom fields" />
              <CardBody><CustomFieldsEditor defs={c.custom_field_definitions} values={c.custom_fields ?? {}} canEdit={!erased && can("contacts", "update")} onSave={(v) => patch.mutate({ custom_fields: v })} /></CardBody>
            </Card>
          )}
          <Card>
            <CardHeader title="Engagement" />
            <CardBody><ActivityTimeline activities={c.activities} empty={<p className="text-sm text-muted-foreground">No activity linked to this person yet.</p>} /></CardBody>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Relationship strength" icon={<HeartPulse className="h-4 w-4 text-muted-foreground" />}
              description="40% reply latency · 30% inbound frequency · 30% meeting attendance" />
            <CardBody>
              <p className="text-3xl font-semibold">{c.relationship_strength ?? "—"}<span className="text-base font-normal text-muted-foreground">/100</span></p>
              <dl className="mt-3 space-y-1.5 text-[12.5px]">
                <div className="flex justify-between"><dt className="text-muted-foreground">Median reply time</dt><dd>{rsi.median_reply_hours != null ? `${rsi.median_reply_hours} h` : "no two-way email yet"}</dd></div>
                <div className="flex justify-between"><dt className="text-muted-foreground">Inbound touches (30 days)</dt><dd>{rsi.inbound_30d ?? 0}</dd></div>
                <div className="flex justify-between"><dt className="text-muted-foreground">Meetings attended / missed</dt><dd>{rsi.meetings_attended ?? 0} / {rsi.meetings_missed ?? 0}</dd></div>
              </dl>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Privacy & consent" icon={<ShieldCheck className="h-4 w-4 text-muted-foreground" />}
              description={c.consent?.updated_at ? `Updated ${relativeDays(c.consent.updated_at)}` : "No consent recorded"} />
            <CardBody className="space-y-3 text-[13px]">
              <div className="grid grid-cols-2 gap-2">
                <div><Label>Regime</Label>
                  <Select disabled={erased} value={c.consent?.regime ?? ""} onChange={(e) => consent.mutate({ regime: e.target.value })}>
                    <option value="">—</option><option value="GDPR">GDPR</option><option value="CCPA">CCPA</option><option value="OTHER">Other</option>
                  </Select>
                </div>
                <div><Label>Email consent</Label>
                  <Select disabled={erased} value={c.consent?.email ?? "unknown"} onChange={(e) => consent.mutate({ consent_email: e.target.value })}>
                    <option value="granted">Granted</option><option value="denied">Denied</option><option value="unknown">Unknown</option>
                  </Select>
                </div>
                <div className="col-span-2"><Label>Lawful basis</Label>
                  <Select disabled={erased} value={c.consent?.basis ?? ""} onChange={(e) => consent.mutate({ basis: e.target.value })}>
                    <option value="">—</option><option value="consent">Consent</option><option value="legitimate_interest">Legitimate interest</option>
                    <option value="contract">Contract</option><option value="legal_obligation">Legal obligation</option>
                  </Select>
                </div>
              </div>
              <div className="space-y-1.5">
                <p className="text-[12px] font-medium text-muted-foreground">Channel opt-outs</p>
                {(["email", "phone", "sms"] as const).map((ch) => (
                  <label key={ch} className="flex items-center gap-2">
                    <input type="checkbox" disabled={erased} checked={!!c.consent?.opt_out[ch]} onChange={(e) => consent.mutate({ [`opt_out_${ch}`]: e.target.checked })} />
                    <span className="capitalize">{ch}</span>
                    <span className="ml-auto text-[12px]" style={{ color: c.channel_permissions[ch]?.allowed ? "var(--status-good)" : "var(--status-critical)" }}>
                      {c.channel_permissions[ch]?.allowed ? "Can contact" : "Blocked"}
                    </span>
                  </label>
                ))}
                <label className="flex items-center gap-2">
                  <input type="checkbox" disabled={erased} checked={!!c.consent?.do_not_sell} onChange={(e) => consent.mutate({ do_not_sell: e.target.checked })} />
                  Do not sell / share (CCPA)
                </label>
                {!c.channel_permissions.email?.allowed && c.channel_permissions.email?.reason && <p className="text-[12px] text-muted-foreground">{c.channel_permissions.email.reason}</p>}
              </div>
              {!erased && can("contacts", "delete") && (
                <Button variant="outline" size="sm" className="w-full" onClick={() => setErase(true)}><Trash2 className="h-3.5 w-3.5" />Erase personal data</Button>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Consent history" description="Append-only" />
            <CardBody className="space-y-1.5">
              {!c.consent_events.length && <p className="text-[13px] text-muted-foreground">No events.</p>}
              {c.consent_events.map((e) => (
                <div key={e.id} className="flex items-center gap-2 text-[12.5px]">
                  <span className="font-medium">{e.event_type.replace(/_/g, " ")}</span>
                  {e.channel && <span className="text-muted-foreground">· {e.channel}</span>}
                  {e.regulation && <span className="text-muted-foreground">· {e.regulation}</span>}
                  <span className="ml-auto text-subtle">{relativeDays(e.created_at)}</span>
                </div>
              ))}
            </CardBody>
          </Card>
        </div>
      </div>

      <Dialog open={erase} onOpenChange={setErase}>
        <DialogContent title="Erase personal data" className="max-w-md">
          <div className="space-y-3 p-5 text-[13.5px]">
            <h2 className="flex items-center gap-2 text-[15px] font-semibold"><ShieldAlert className="h-4 w-4" style={{ color: "var(--status-critical)" }} />Erase {c.name}?</h2>
            <p>This permanently anonymises the name, email, phones and LinkedIn, redacts mentions in notes, and destroys the person&apos;s encryption key so every historical value in the audit trail becomes unreadable. It cannot be undone.</p>
            <p className="text-muted-foreground">A SHA-256 fingerprint is kept as evidence that the request was honoured.</p>
            <div><Label>Type ERASE to confirm</Label><Input value={confirmText} onChange={(e) => setConfirmText(e.target.value)} /></div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setErase(false)}>Cancel</Button>
              <Button variant="destructive" size="sm" disabled={confirmText !== "ERASE"} loading={doErase.isPending} onClick={() => doErase.mutate()}>Erase permanently</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
