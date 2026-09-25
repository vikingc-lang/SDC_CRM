"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api";
import type { Contact } from "@/lib/types";

type Kind = "call" | "meeting" | "email" | "note";

/** Structured logging for call & meeting intelligence (disposition, duration, agenda, attendance). */
export function LogActivityDialog({ open, onOpenChange, accountId, dealId, contacts }: {
  open: boolean; onOpenChange: (o: boolean) => void; accountId: string; dealId?: string; contacts: Contact[];
}) {
  const qc = useQueryClient();
  const [f, setF] = useState({ activity_type: "call" as Kind, contact_id: "", subject: "", summary: "", direction: "outbound", disposition: "connected",
    duration_min: "15", attendance: "attended", agenda: "", sentiment: "neutral", occurred_at: "" });
  const m = useMutation({
    mutationFn: async () => (await api.post("/activities", {
      account_id: accountId, deal_id: dealId ?? null, contact_id: f.contact_id || null, activity_type: f.activity_type, subject: f.subject || null,
      summary: f.summary, sentiment: f.sentiment, direction: ["call", "email"].includes(f.activity_type) ? f.direction : null,
      disposition: f.activity_type === "call" ? f.disposition : null,
      duration_seconds: ["call", "meeting"].includes(f.activity_type) && f.duration_min ? Math.round(Number(f.duration_min) * 60) : null,
      attendance: f.activity_type === "meeting" ? f.attendance : null, agenda: f.activity_type === "meeting" ? f.agenda || null : null,
      occurred_at: f.occurred_at ? new Date(f.occurred_at).toISOString() : null,
    })).data,
    onSuccess: () => { qc.invalidateQueries(); onOpenChange(false); toast.success("Logged to the activity ledger"); setF({ ...f, summary: "", subject: "", agenda: "" }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const t = f.activity_type;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Log activity" className="max-w-lg">
        <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <h2 className="text-[15px] font-semibold">Log activity</h2>
          <div className="flex rounded-md border p-0.5 text-[13px]">
            {(["call", "meeting", "email", "note"] as Kind[]).map((k) => (
              <button type="button" key={k} onClick={() => setF({ ...f, activity_type: k })}
                className={`flex-1 rounded px-2 py-1 capitalize ${t === k ? "bg-muted font-medium" : "text-muted-foreground"}`}>{k}</button>
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div><Label>Contact</Label>
              <Select value={f.contact_id} onChange={(e) => setF({ ...f, contact_id: e.target.value })}>
                <option value="">—</option>
                {contacts.filter((c) => c.status !== "erased").map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </Select>
            </div>
            <div><Label>When</Label><Input type="datetime-local" value={f.occurred_at} onChange={(e) => setF({ ...f, occurred_at: e.target.value })} /></div>
            {(t === "call" || t === "email") && (
              <div><Label>Direction</Label><Select value={f.direction} onChange={(e) => setF({ ...f, direction: e.target.value })}><option value="outbound">Outbound</option><option value="inbound">Inbound</option></Select></div>
            )}
            {t === "call" && (
              <div><Label>Outcome</Label>
                <Select value={f.disposition} onChange={(e) => setF({ ...f, disposition: e.target.value })}>
                  <option value="connected">Connected</option><option value="left_voicemail">Left voicemail</option><option value="gatekeeper">Gatekeeper</option>
                  <option value="no_answer">No answer</option><option value="busy">Busy</option><option value="wrong_number">Wrong number</option>
                </Select>
              </div>
            )}
            {t === "meeting" && (
              <div><Label>Attendance</Label><Select value={f.attendance} onChange={(e) => setF({ ...f, attendance: e.target.value })}><option value="attended">Attended</option><option value="no_show">No-show</option><option value="cancelled">Cancelled</option><option value="scheduled">Scheduled</option></Select></div>
            )}
            {(t === "call" || t === "meeting") && (
              <div><Label>Duration (minutes)</Label><Input type="number" min={0} value={f.duration_min} onChange={(e) => setF({ ...f, duration_min: e.target.value })} /></div>
            )}
            <div><Label>Sentiment</Label><Select value={f.sentiment} onChange={(e) => setF({ ...f, sentiment: e.target.value })}><option value="positive">Positive</option><option value="neutral">Neutral</option><option value="negative">Negative</option></Select></div>
          </div>
          {(t === "email" || t === "meeting") && <div><Label>Subject</Label><Input value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })} /></div>}
          {t === "meeting" && <div><Label>Agenda</Label><Textarea value={f.agenda} onChange={(e) => setF({ ...f, agenda: e.target.value })} className="min-h-[60px]" placeholder={"1. Goals\n2. Demo\n3. Next steps"} /></div>}
          <div><Label>Notes</Label><Textarea required value={f.summary} onChange={(e) => setF({ ...f, summary: e.target.value })} className="min-h-[80px]" /></div>
          <div className="flex justify-end"><Button type="submit" size="sm" loading={m.isPending}>Log {t}</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
