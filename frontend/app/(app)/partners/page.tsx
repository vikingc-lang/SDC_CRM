"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FileText, Handshake, Plus, Upload, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/AppShell";
import { StatTile } from "@/components/charts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { StatusPill, Table, Tabs, Td, bytes, fmtMoney } from "@/components/ui/extra";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import { ConflictList, TierBadge, type CollateralItem, type CommissionReport } from "@/components/partners";
import type { Partner, Registration } from "@/lib/types";
import { relativeDays, shortDate } from "@/lib/utils";

const TIERS = ["registered", "silver", "gold", "platinum"];

export default function PartnersPage() {
  const [tab, setTab] = useState<"registrations" | "partners" | "commissions" | "collateral">("registrations");
  const regs = useQuery({ queryKey: ["prm", "registrations"], queryFn: () => get<Registration[]>("/partners/registrations") });
  const comm = useQuery({ queryKey: ["prm", "commissions"], queryFn: () => get<CommissionReport>("/partners/commissions") });
  const pending = regs.data?.filter((r) => r.status === "submitted") ?? [];
  const sum = (k: "attributed_pipeline" | "attributed_won" | "commission_earned") => comm.data?.partners.reduce((a, p) => a + p[k], 0) ?? 0;
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Partners" description="Deal registration with territory exclusivity, co-sell attribution and a governed collateral library for distributors and agencies." />
      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Registrations to review" value={String(pending.length)} icon={<Handshake className="h-4 w-4" />} sub={`${pending.filter((r) => r.conflicts.length).length} with conflicts`} />
        <StatTile label="Partner-sourced pipeline" value={fmtMoney(sum("attributed_pipeline"), "USD", true)} />
        <StatTile label="Partner-attributed wins" value={fmtMoney(sum("attributed_won"), "USD", true)} />
        <StatTile label="Commission earned" value={fmtMoney(sum("commission_earned"), "USD", true)} />
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[
        { value: "registrations", label: "Deal registrations", count: pending.length },
        { value: "partners", label: "Partner directory" },
        { value: "commissions", label: "Co-sell & commissions" },
        { value: "collateral", label: "Collateral" },
      ]} />
      {tab === "registrations" && <Registrations rows={regs.data} loading={regs.isLoading} />}
      {tab === "partners" && <Directory />}
      {tab === "commissions" && <Commissions data={comm.data} loading={comm.isLoading} />}
      {tab === "collateral" && <CollateralTab />}
    </div>
  );
}

function Registrations({ rows, loading }: { rows?: Registration[]; loading: boolean }) {
  const qc = useQueryClient();
  const { can } = useMe();
  const [notes, setNotes] = useState<Record<string, string>>({});
  const decide = useMutation({
    mutationFn: async (v: { id: string; approve: boolean }) => (await api.post<Registration>(`/partners/registrations/${v.id}/decide`, { approve: v.approve, note: notes[v.id] || null })).data,
    onSuccess: (r, v) => { qc.invalidateQueries(); toast.success(v.approve ? `Approved: exclusivity until ${shortDate(r.exclusivity_expires_at, true)}, deal created in Partner pipeline` : "Registration rejected"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (loading) return <Skeleton className="h-48 w-full" />;
  if (!rows?.length) return <Card><EmptyState icon={<Handshake className="h-4 w-4" />} title="No registrations yet" description="Partners submit opportunities from the partner portal." /></Card>;
  return (
    <div className="space-y-3">
      {rows.map((r) => (
        <Card key={r.id} className="p-4">
          <div className="flex flex-wrap items-start gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{r.company_name}</span>
                <span className="text-[12.5px] text-muted-foreground">{r.domain}</span>
                <StatusPill status={r.status} />
              </div>
              <p className="mt-1 text-[12.5px] text-muted-foreground">
                {r.partner.name} <TierBadge tier={r.partner.tier} /> · {fmtMoney(r.estimated_amount, r.currency)} · {r.territory ?? "no territory"} · {r.product_interest ?? "—"} · submitted {relativeDays(r.created_at)}
              </p>
              {r.notes && <p className="mt-1 text-[13px]">{r.notes}</p>}
              <ConflictList conflicts={r.conflicts} />
              {r.decision_note && <p className="mt-1 text-[12.5px] text-muted-foreground">Decision note: {r.decision_note}</p>}
              {r.deal_id && <Link href={`/deals/${r.deal_id}`} className="mt-1 inline-block text-[12.5px] text-primary hover:underline">Open registered deal →</Link>}
              {r.exclusivity_expires_at && r.status === "approved" && <p className="text-[12px] text-muted-foreground">Exclusive until {shortDate(r.exclusivity_expires_at, true)}</p>}
            </div>
            {r.status === "submitted" && can("partners", "update") && (
              <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
                <Input className="h-8 w-full sm:w-48" placeholder="Decision note" value={notes[r.id] ?? ""} onChange={(e) => setNotes({ ...notes, [r.id]: e.target.value })} />
                <Button size="sm" variant="outline" disabled={decide.isPending} onClick={() => decide.mutate({ id: r.id, approve: false })}><X className="h-3.5 w-3.5" />Reject</Button>
                <Button size="sm" disabled={decide.isPending} onClick={() => decide.mutate({ id: r.id, approve: true })}><Check className="h-3.5 w-3.5" />Approve</Button>
              </div>
            )}
          </div>
        </Card>
      ))}
    </div>
  );
}

const EMPTY_PARTNER = { name: "", partner_type: "reseller", tier: "registered", domains: "", territories: "", commission_rate: 10, referral_fee_rate: 5, status: "active" };

function Directory() {
  const qc = useQueryClient();
  const { can } = useMe();
  const { data, isLoading } = useQuery({ queryKey: ["prm", "partners"], queryFn: () => get<Partner[]>("/partners") });
  const [edit, setEdit] = useState<(typeof EMPTY_PARTNER & { id?: string }) | null>(null);
  const save = useMutation({
    mutationFn: async () => {
      const body = { ...edit!, domains: edit!.domains.split(",").map((s) => s.trim()).filter(Boolean), territories: edit!.territories.split(",").map((s) => s.trim()).filter(Boolean) };
      return edit!.id ? (await api.put(`/partners/${edit!.id}`, body)).data : (await api.post("/partners", body)).data;
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["prm"] }); setEdit(null); toast.success("Partner saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader title="Partner directory" description="Tier drives collateral access; domains let partner users sign in to the portal; territories back exclusivity checks."
        action={can("partners", "create") && <Button size="sm" onClick={() => setEdit({ ...EMPTY_PARTNER })}><Plus className="h-3.5 w-3.5" />Add partner</Button>} />
      {isLoading ? <Skeleton className="m-5 h-24" /> : (
        <Table head={["Partner", "Type", "Tier", "Domains", "Territories", "Commission", "Status", ""]} minWidth={900}>
          {data?.map((p) => (
            <tr key={p.id}>
              <Td className="font-medium">{p.name}</Td>
              <Td className="capitalize">{p.partner_type}</Td>
              <Td><TierBadge tier={p.tier} /></Td>
              <Td className="text-[12.5px]">{p.domains.join(", ") || "—"}</Td>
              <Td className="text-[12.5px]">{p.territories.join(", ") || "—"}</Td>
              <Td className="text-[12.5px] tabular-nums">{p.commission_rate}% resell · {p.referral_fee_rate}% referral</Td>
              <Td><StatusPill status={p.status} /></Td>
              <Td>{can("partners", "update") && <Button size="sm" variant="ghost" onClick={() => setEdit({ ...p, domains: p.domains.join(", "), territories: p.territories.join(", ") })}>Edit</Button>}</Td>
            </tr>
          ))}
        </Table>
      )}
      <Dialog open={!!edit} onOpenChange={(o) => !o && setEdit(null)}>
        <DialogContent title={edit?.id ? "Edit partner" : "Add partner"}>
          {edit && (
            <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
              <h2 className="text-[15px] font-semibold">{edit.id ? "Edit partner" : "Add partner"}</h2>
              <div><Label htmlFor="p-name">Name</Label><Input id="p-name" required value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></div>
              <div className="grid grid-cols-3 gap-3">
                <div><Label htmlFor="p-type">Type</Label><Select id="p-type" value={edit.partner_type} onChange={(e) => setEdit({ ...edit, partner_type: e.target.value })}>{["distributor", "agency", "reseller", "referral", "technology"].map((t) => <option key={t}>{t}</option>)}</Select></div>
                <div><Label htmlFor="p-tier">Tier</Label><Select id="p-tier" value={edit.tier} onChange={(e) => setEdit({ ...edit, tier: e.target.value })}>{TIERS.map((t) => <option key={t}>{t}</option>)}</Select></div>
                <div><Label htmlFor="p-status">Status</Label><Select id="p-status" value={edit.status} onChange={(e) => setEdit({ ...edit, status: e.target.value })}><option>active</option><option>inactive</option></Select></div>
              </div>
              <div><Label htmlFor="p-dom">Email domains (comma separated)</Label><Input id="p-dom" value={edit.domains} onChange={(e) => setEdit({ ...edit, domains: e.target.value })} placeholder="partner.com" /></div>
              <div><Label htmlFor="p-ter">Territories</Label><Input id="p-ter" value={edit.territories} onChange={(e) => setEdit({ ...edit, territories: e.target.value })} placeholder="US-West, DACH" /></div>
              <div className="grid grid-cols-2 gap-3">
                <div><Label htmlFor="p-cr">Resell commission %</Label><Input id="p-cr" type="number" min={0} max={100} value={edit.commission_rate} onChange={(e) => setEdit({ ...edit, commission_rate: Number(e.target.value) })} /></div>
                <div><Label htmlFor="p-rf">Referral fee %</Label><Input id="p-rf" type="number" min={0} max={100} value={edit.referral_fee_rate} onChange={(e) => setEdit({ ...edit, referral_fee_rate: Number(e.target.value) })} /></div>
              </div>
              <div className="flex justify-end"><Button type="submit" size="sm" loading={save.isPending}>Save partner</Button></div>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function Commissions({ data, loading }: { data?: CommissionReport; loading: boolean }) {
  if (loading) return <Skeleton className="h-48 w-full" />;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="By partner" description="Attribution follows each deal's partner split; commission is earned on Closed-Won." />
        <Table head={["Partner", "Tier", "Deals", "Pipeline", "Won", "Commission"]}>
          {data?.partners.map((p) => (
            <tr key={p.partner_id}>
              <Td className="font-medium">{p.partner}</Td><Td><TierBadge tier={p.tier} /></Td><Td>{p.deals}</Td>
              <Td className="tabular-nums">{fmtMoney(p.attributed_pipeline)}</Td><Td className="tabular-nums">{fmtMoney(p.attributed_won)}</Td>
              <Td className="tabular-nums font-medium">{fmtMoney(p.commission_earned)}</Td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card>
        <CardHeader title="Deal lines" />
        <Table head={["Deal", "Partner", "Role", "Split", "Rate", "Attributed", "Commission", "Status"]} minWidth={900}>
          {data?.lines.map((l, i) => (
            <tr key={i}>
              <Td>{l.deal_id ? <Link href={`/deals/${l.deal_id}`} className="font-medium hover:underline">{l.deal}</Link> : l.deal}<span className="block text-[12px] text-muted-foreground">{l.account} · {l.stage}</span></Td>
              <Td>{l.partner}</Td><Td className="capitalize">{l.role.replace("_", "-")}</Td><Td>{l.split_pct}%</Td><Td>{l.rate_pct}%</Td>
              <Td className="tabular-nums">{fmtMoney(l.attributed_usd)}</Td><Td className="tabular-nums">{fmtMoney(l.commission_usd)}</Td><Td><StatusPill status={l.status} /></Td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}

function CollateralTab() {
  const qc = useQueryClient();
  const { can } = useMe();
  const { data, isLoading } = useQuery({ queryKey: ["prm", "collateral"], queryFn: () => get<CollateralItem[]>("/partners/collateral") });
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ title: "", category: "deck", description: "", min_tier: "registered", allowed_domains: "" });
  const [file, setFile] = useState<File | null>(null);
  const upload = useMutation({
    mutationFn: async () => {
      const form = new FormData();
      Object.entries(f).forEach(([k, v]) => form.append(k, v));
      form.append("file", file!);
      return (await api.post("/partners/collateral", form)).data;
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["prm", "collateral"] }); setOpen(false); setFile(null); setF({ ...f, title: "", description: "" }); toast.success("Collateral published"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader title="Collateral repository" description="Access is filtered by partner tier and, optionally, by partner email domain. Every download is logged."
        action={can("partners", "create") && <Button size="sm" onClick={() => setOpen(true)}><Upload className="h-3.5 w-3.5" />Upload</Button>} />
      {isLoading ? <Skeleton className="m-5 h-24" /> : !data?.length ? <EmptyState icon={<FileText className="h-4 w-4" />} title="No collateral yet" /> : (
        <Table head={["Title", "Category", "Minimum tier", "Restricted to", "File", "Downloads"]} minWidth={820}>
          {data.map((c) => (
            <tr key={c.id}>
              <Td><span className="font-medium">{c.title}</span>{c.description && <span className="block text-[12px] text-muted-foreground">{c.description}</span>}</Td>
              <Td className="capitalize">{c.category.replace("_", " ")}</Td>
              <Td><TierBadge tier={c.min_tier} /></Td>
              <Td className="text-[12.5px]">{c.allowed_domains.length ? c.allowed_domains.join(", ") : "All partners at tier"}</Td>
              <Td className="text-[12px] text-muted-foreground">{c.file.filename} · {bytes(c.file.size_bytes)}</Td>
              <Td className="tabular-nums">{c.downloads}</Td>
            </tr>
          ))}
        </Table>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title="Upload collateral">
          <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); if (file) upload.mutate(); }}>
            <h2 className="text-[15px] font-semibold">Upload collateral</h2>
            <div><Label htmlFor="c-title">Title</Label><Input id="c-title" required value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
            <div className="grid grid-cols-2 gap-3">
              <div><Label htmlFor="c-cat">Category</Label><Select id="c-cat" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{["deck", "whitepaper", "battlecard", "price_list", "case_study"].map((c) => <option key={c} value={c}>{c.replace("_", " ")}</option>)}</Select></div>
              <div><Label htmlFor="c-tier">Minimum tier</Label><Select id="c-tier" value={f.min_tier} onChange={(e) => setF({ ...f, min_tier: e.target.value })}>{TIERS.map((t) => <option key={t}>{t}</option>)}</Select></div>
            </div>
            <div><Label htmlFor="c-desc">Description</Label><Textarea id="c-desc" rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></div>
            <div><Label htmlFor="c-dom">Restrict to partner domains (optional)</Label><Input id="c-dom" placeholder="northstar-partners.com" value={f.allowed_domains} onChange={(e) => setF({ ...f, allowed_domains: e.target.value })} /></div>
            <div><Label htmlFor="c-file">File</Label><Input id="c-file" type="file" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></div>
            <div className="flex justify-end"><Button type="submit" size="sm" loading={upload.isPending}>Publish</Button></div>
          </form>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
