"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api";

export type CampaignStatus = "planned" | "active" | "completed" | "aborted";
export type MemberStatus = "targeted" | "sent" | "responded" | "registered" | "attended" | "unsubscribed" | "bounced";
export interface Metrics {
  members: number; by_status: Record<MemberStatus, number>; responses: number; response_rate: number | null; leads: number; converted_leads: number;
  sourced_pipeline: number; sourced_won: number; sourced_deals: number; influenced_pipeline: number; influenced_won: number; influenced_deals: number;
  cost: number; budget: number; budget_used_pct: number | null; cost_per_lead: number | null; cost_per_response: number | null; roi_pct: number | null;
  deals?: { id: string; title: string; amount_usd: number; status: "Open" | "Won" | "Lost"; attribution: "sourced" | "influenced" }[];
}
export interface Campaign {
  id: string; name: string; code: string; type: string; status: CampaignStatus; description: string | null; owner_id: string | null; owner: string | null;
  start_date: string | null; end_date: string | null; budget: number; actual_cost: number; expected_revenue: number;
  email_subject: string | null; email_body: string | null; last_sent_at: string | null; metrics: Metrics;
}

export const TYPE_LABELS: Record<string, string> = {
  email: "Email", webinar: "Webinar", event: "Event", trade_show: "Trade show", paid_ads: "Paid ads", content: "Content", partner: "Partner", other: "Other",
};
const STATUS_TONE = { planned: "neutral", active: "primary", completed: "good", aborted: "warning" } as const;

export function CampaignStatusBadge({ status }: { status: CampaignStatus }) {
  return <Badge tone={STATUS_TONE[status]}>{status[0].toUpperCase() + status.slice(1)}</Badge>;
}

export const pctText = (v: number | null | undefined) => (v == null ? "—" : `${v.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`);

export function CampaignDialog({ campaign, onClose }: { campaign?: Campaign; onClose: () => void }) {
  const qc = useQueryClient();
  const router = useRouter();
  const [f, setF] = useState({
    name: campaign?.name ?? "", code: campaign?.code ?? "", campaign_type: campaign?.type ?? "email", status: campaign?.status ?? "planned",
    description: campaign?.description ?? "", start_date: campaign?.start_date ?? "", end_date: campaign?.end_date ?? "",
    budget: campaign?.budget ?? 0, actual_cost: campaign?.actual_cost ?? 0, expected_revenue: campaign?.expected_revenue ?? 0,
  });
  const save = useMutation({
    mutationFn: async () => {
      const body = { ...f, code: f.code || null, description: f.description || null, start_date: f.start_date || null, end_date: f.end_date || null,
        email_subject: campaign?.email_subject ?? null, email_body: campaign?.email_body ?? null };
      return campaign ? (await api.put(`/campaigns/${campaign.id}`, body)).data : (await api.post<{ id: string; attached_leads: number }>("/campaigns", body)).data;
    },
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      onClose();
      if (!campaign) router.push(`/campaigns/${(r as { id: string }).id}`);
    },
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={campaign ? "Edit campaign" : "New campaign"} className="max-w-lg">
        <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <h2 className="text-[15px] font-semibold">{campaign ? "Edit campaign" : "New campaign"}</h2>
          <div><Label htmlFor="cp-name">Name</Label><Input id="cp-name" required minLength={2} maxLength={150} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
          <div><Label htmlFor="cp-code">Tracking code</Label><Input id="cp-code" maxLength={80} placeholder="Made from the name if left empty" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} />
            <p className="mt-1 text-[12px] text-subtle">Leads captured with this code (or the campaign name) as their campaign or utm_campaign join automatically.</p></div>
          <div className="grid grid-cols-2 gap-2">
            <div><Label htmlFor="cp-type">Type</Label><Select id="cp-type" value={f.campaign_type} onChange={(e) => setF({ ...f, campaign_type: e.target.value })}>
              {Object.entries(TYPE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select></div>
            <div><Label htmlFor="cp-status">Status</Label><Select id="cp-status" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value as Campaign["status"] })}>
              {["planned", "active", "completed", "aborted"].map((s) => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}</Select></div>
            <div><Label htmlFor="cp-start">Start</Label><Input id="cp-start" type="date" value={f.start_date} onChange={(e) => setF({ ...f, start_date: e.target.value })} /></div>
            <div><Label htmlFor="cp-end">End</Label><Input id="cp-end" type="date" value={f.end_date} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></div>
            <div><Label htmlFor="cp-budget">Budget (USD)</Label><Input id="cp-budget" type="number" min={0} value={f.budget} onChange={(e) => setF({ ...f, budget: Number(e.target.value) })} /></div>
            <div><Label htmlFor="cp-cost">Actual cost (USD)</Label><Input id="cp-cost" type="number" min={0} value={f.actual_cost} onChange={(e) => setF({ ...f, actual_cost: Number(e.target.value) })} /></div>
            <div className="col-span-2"><Label htmlFor="cp-expected">Expected revenue (USD)</Label><Input id="cp-expected" type="number" min={0} value={f.expected_revenue} onChange={(e) => setF({ ...f, expected_revenue: Number(e.target.value) })} /></div>
          </div>
          <div><Label htmlFor="cp-desc">Description</Label><Textarea id="cp-desc" rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></div>
          {save.isError && <p className="text-sm text-destructive">{errorMessage(save.error)}</p>}
          <div className="flex justify-end gap-2"><Button type="button" variant="ghost" size="sm" onClick={onClose}>Cancel</Button><Button type="submit" size="sm" loading={save.isPending}>{campaign ? "Save" : "Create campaign"}</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
