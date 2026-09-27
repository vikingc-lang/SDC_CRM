"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, Send } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Label, Select } from "@/components/ui/input";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import { relativeDays } from "@/lib/utils";

/* Scheduled delivery of a saved report: daily, weekly or monthly at an hour (UTC), to me and, for a shared
   report, colleagues. Each person gets the report as they would see it themselves - in-app, and by email with
   a CSV when system email is configured. */

interface Subscription {
  id: string; frequency: "daily" | "weekly" | "monthly"; weekday: number; day_of_month: number; hour: number;
  recipient_ids: string[]; active: boolean; last_sent_at: string | null; last_status: string | null;
}
interface SubState { subscription: Subscription | null; email_enabled: boolean }

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const hourLabel = (h: number) => `${String(h).padStart(2, "0")}:00 UTC`;

export function SubscribeButton({ reportId, shared }: { reportId: string; shared: boolean }) {
  const [open, setOpen] = useState(false);
  const q = useQuery({ queryKey: ["analytics", "subscription", reportId], queryFn: () => get<SubState>(`/analytics/reports/${reportId}/subscription`) });
  const on = !!q.data?.subscription?.active;
  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)} aria-pressed={on}>
        <BellRing className="h-3.5 w-3.5" />{on ? "Subscribed" : "Subscribe"}
      </Button>
      {open && q.data && <SubscribeDialog reportId={reportId} shared={shared} state={q.data} onClose={() => setOpen(false)} />}
    </>
  );
}

function SubscribeDialog({ reportId, shared, state, onClose }: { reportId: string; shared: boolean; state: SubState; onClose: () => void }) {
  const qc = useQueryClient();
  const { me } = useMe();
  const sub = state.subscription;
  const [f, setF] = useState({ frequency: sub?.frequency ?? "weekly", weekday: sub?.weekday ?? 0, day_of_month: sub?.day_of_month ?? 1, hour: sub?.hour ?? 7,
    recipient_ids: sub?.recipient_ids ?? [], active: sub?.active ?? true });
  useEffect(() => { if (!shared) setF((x) => ({ ...x, recipient_ids: [] })); }, [shared]);
  const users = useQuery({ queryKey: ["users"], queryFn: () => get<{ id: string; full_name: string; role: string; is_active?: boolean }[]>("/users"), enabled: shared });
  const people = (users.data ?? []).filter((u) => u.id !== me?.id && !["partner"].includes(u.role) && u.is_active !== false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["analytics", "subscription", reportId] });
  const save = useMutation({
    mutationFn: async () => (await api.put<SubState>(`/analytics/reports/${reportId}/subscription`, f)).data,
    onSuccess: () => { refresh(); toast.success(f.active ? "Subscription saved" : "Subscription paused"); onClose(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: async () => api.delete(`/analytics/reports/${reportId}/subscription`),
    onSuccess: () => { refresh(); toast.success("Unsubscribed"); onClose(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const sendNow = useMutation({
    mutationFn: async () => (await api.post<{ status: string }>(`/analytics/reports/${reportId}/subscription/send`)).data,
    onSuccess: (r) => { refresh(); qc.invalidateQueries({ queryKey: ["notifications"] }); toast.success(r.status); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const toggle = (id: string) => setF({ ...f, recipient_ids: f.recipient_ids.includes(id) ? f.recipient_ids.filter((x) => x !== id) : [...f.recipient_ids, id] });

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title="Subscribe to report" className="max-w-lg">
        <form className="space-y-4 p-5" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <div>
            <h2 className="text-[15px] font-semibold">Deliver this report on a schedule</h2>
            <p className="mt-0.5 text-[13px] text-muted-foreground">
              Everyone gets the report as they would see it themselves, as a notification{state.email_enabled ? " and an email with the data attached as CSV" : ""}.
              {!state.email_enabled && " Email delivery starts once an administrator configures system email."}
            </p>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <Label htmlFor="sub-freq">How often</Label>
              <Select id="sub-freq" value={f.frequency} onChange={(e) => setF({ ...f, frequency: e.target.value as Subscription["frequency"] })}>
                <option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option>
              </Select>
            </div>
            {f.frequency === "weekly" && (
              <div><Label htmlFor="sub-day">On</Label>
                <Select id="sub-day" value={f.weekday} onChange={(e) => setF({ ...f, weekday: Number(e.target.value) })}>{DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}</Select></div>
            )}
            {f.frequency === "monthly" && (
              <div><Label htmlFor="sub-dom">Day of month</Label>
                <Select id="sub-dom" value={f.day_of_month} onChange={(e) => setF({ ...f, day_of_month: Number(e.target.value) })}>
                  {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>{d}</option>)}</Select></div>
            )}
            <div><Label htmlFor="sub-hour">At</Label>
              <Select id="sub-hour" value={f.hour} onChange={(e) => setF({ ...f, hour: Number(e.target.value) })}>
                {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{hourLabel(h)}</option>)}</Select></div>
          </div>
          <div>
            <p className="mb-1 text-[13px] font-medium">Also send to</p>
            {!shared ? <p className="text-[12.5px] text-muted-foreground">Share the report with the team to send it to colleagues. Until then it goes only to you.</p> : (
              <div className="max-h-40 space-y-1 overflow-y-auto rounded-md border p-2">
                {people.map((u) => (
                  <label key={u.id} className="flex items-center gap-2 text-[13px]">
                    <input type="checkbox" className="h-4 w-4" checked={f.recipient_ids.includes(u.id)} onChange={() => toggle(u.id)} />{u.full_name}
                  </label>
                ))}
                {!people.length && <p className="text-[12.5px] text-muted-foreground">{users.isLoading ? "Loading…" : "No colleagues to add."}</p>}
              </div>
            )}
          </div>
          {sub && (
            <label className="flex items-center gap-2 text-[13px]">
              <input type="checkbox" className="h-4 w-4" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} />Active (untick to pause)
            </label>
          )}
          {sub?.last_sent_at && <p className="text-[12px] text-subtle">Last delivered {relativeDays(sub.last_sent_at)}: {sub.last_status}</p>}
          <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-4">
            <div className="flex gap-2">
              {sub && <Button type="button" size="sm" variant="ghost" loading={remove.isPending} onClick={() => remove.mutate()}>Unsubscribe</Button>}
              {sub && <Button type="button" size="sm" variant="outline" loading={sendNow.isPending} onClick={() => sendNow.mutate()}><Send className="h-3.5 w-3.5" />Send now</Button>}
            </div>
            <Button type="submit" size="sm" loading={save.isPending}>{sub ? "Save" : "Subscribe"}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
