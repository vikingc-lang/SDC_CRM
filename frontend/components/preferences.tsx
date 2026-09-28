"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarSync, Globe2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StatusPill } from "@/components/ui/extra";
import { Label, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { LOCALES, supported, useT } from "@/lib/i18n";
import { useMe } from "@/lib/me";
import { relativeDays } from "@/lib/utils";

const ZONES = ["UTC", "America/Los_Angeles", "America/Denver", "America/Chicago", "America/New_York", "America/Sao_Paulo", "Europe/London",
  "Europe/Dublin", "Europe/Paris", "Europe/Berlin", "Europe/Madrid", "Europe/Amsterdam", "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore",
  "Asia/Tokyo", "Australia/Sydney", "Pacific/Auckland"];

/** Language, number / date formats and time zone for this user (stored on the profile, applied everywhere). */
export function RegionCard() {
  const qc = useQueryClient();
  const t = useT();
  const { me } = useMe();
  const [locale, setLocale] = useState<string>("");
  const [zone, setZone] = useState<string>("");
  useEffect(() => { setLocale(me?.preferences?.locale ?? ""); setZone(me?.preferences?.timezone ?? ""); }, [me?.preferences?.locale, me?.preferences?.timezone]);
  const save = useMutation({
    mutationFn: async (body: { locale?: string | null; timezone?: string | null }) => (await api.patch("/users/me/preferences", body)).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["me"] }); toast.success(t("settings.saved")); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const preview = supported(locale || (typeof navigator !== "undefined" ? navigator.language : "en-US"));
  const sample = `${new Intl.NumberFormat(preview, { style: "currency", currency: "EUR" }).format(1234567.89)} · ${new Date(Date.UTC(2026, 2, 31, 15, 30)).toLocaleString(preview, {
    dateStyle: "medium", timeStyle: "short", timeZone: zone || undefined })}`;
  return (
    <Card>
      <CardHeader icon={<Globe2 className="h-4 w-4 text-muted-foreground" />} title={t("settings.region")} description={t("settings.region_hint")} />
      <CardBody className="grid gap-3 sm:grid-cols-2">
        <div><Label htmlFor="pref-locale">{t("settings.language")}</Label>
          <Select id="pref-locale" value={locale} onChange={(e) => { setLocale(e.target.value); save.mutate({ locale: e.target.value || null }); }}>
            <option value="">{t("common.browser_default")}</option>
            {LOCALES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
          </Select></div>
        <div><Label htmlFor="pref-zone">{t("settings.timezone")}</Label>
          <Select id="pref-zone" value={zone} onChange={(e) => { setZone(e.target.value); save.mutate({ timezone: e.target.value || null }); }}>
            <option value="">{t("common.browser_default")}</option>
            {ZONES.map((z) => <option key={z} value={z}>{z.replace(/_/g, " ")}</option>)}
          </Select></div>
        <p className="text-[12.5px] text-muted-foreground sm:col-span-2">{t("settings.example")}: <span className="tabular text-foreground">{sample}</span></p>
      </CardBody>
    </Card>
  );
}

interface Connection { id: string; provider: string; provider_label: string; account_email: string | null; status: string; last_synced_at: string | null; last_error: string | null }

/** Two-way Google / Microsoft 365 calendar sync (OAuth). */
export function CalendarSyncCard() {
  const qc = useQueryClient();
  const t = useT();
  const data = useQuery({ queryKey: ["calendar", "connections"], queryFn: () => get<{ providers: { key: string; label: string; configured: boolean }[]; connections: Connection[] }>("/calendar/connections") });
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    if (q.get("calendar") === "connected") toast.success("Calendar connected; the first sync has run");
    if (q.get("calendar") === "error") toast.error(q.get("reason") || "The calendar couldn't be connected");
    if (q.get("calendar")) window.history.replaceState(null, "", window.location.pathname);
  }, []);
  const connect = useMutation({
    mutationFn: async (provider: string) => (await api.post<{ url: string }>(`/calendar/connect/${provider}`)).data,
    onSuccess: (r) => { window.location.href = r.url; },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const sync = useMutation({
    mutationFn: async (id: string) => (await api.post<{ stats: Record<string, number> }>(`/calendar/connections/${id}/sync`)).data,
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["calendar"] });
      const parts = Object.entries(r.stats).filter(([, v]) => v).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`);
      toast.success(parts.length ? `Synced: ${parts.join(", ")}` : "Synced: no changes");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const disconnect = useMutation({
    mutationFn: async (id: string) => api.delete(`/calendar/connections/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["calendar"] }); toast.success("Calendar disconnected"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader icon={<CalendarSync className="h-4 w-4 text-muted-foreground" />} title={t("settings.calendar")} description={t("settings.calendar_hint")} />
      <CardBody className="space-y-2.5">
        {!data.data ? <Skeleton className="h-16" /> : data.data.providers.map((p) => {
          const c = data.data!.connections.find((x) => x.provider === p.key);
          return (
            <div key={p.key} className="flex flex-wrap items-center gap-3 rounded-md border px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="text-[13.5px] font-medium">{p.label}</p>
                <p className="text-[12px] text-muted-foreground">
                  {c ? <>{c.account_email ?? "Connected"} · {c.last_synced_at ? t("settings.last_sync", { when: relativeDays(c.last_synced_at) }) : t("settings.never_synced")}</>
                    : p.configured ? "Not connected" : t("settings.not_configured")}
                </p>
                {c?.last_error && <p className="text-[12px] text-destructive">{c.last_error}</p>}
              </div>
              {c && <StatusPill status={c.status === "active" ? "active" : "failed"} />}
              {c ? (
                <>
                  <Button size="sm" variant="outline" loading={sync.isPending && sync.variables === c.id} onClick={() => sync.mutate(c.id)}><RefreshCw className="h-3.5 w-3.5" />{t("settings.sync_now")}</Button>
                  <Button size="sm" variant="ghost" loading={disconnect.isPending} onClick={() => disconnect.mutate(c.id)}>{t("settings.disconnect")}</Button>
                </>
              ) : p.configured && (
                <Button size="sm" loading={connect.isPending && connect.variables === p.key} onClick={() => connect.mutate(p.key)}>{t("settings.connect", { provider: p.label })}</Button>
              )}
            </div>
          );
        })}
        <p className="text-[12px] text-subtle">Syncs every ten minutes. Only meetings with at least one CRM contact are brought in; invitations aren&apos;t emailed when Cirra adds events.</p>
      </CardBody>
    </Card>
  );
}
