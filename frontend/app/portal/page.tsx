"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileText, Handshake, LogOut, Send } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Logo } from "@/components/AppShell";
import { StatTile } from "@/components/charts";
import { ConflictList, TierBadge, type CollateralItem } from "@/components/partners";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { StatusPill, Table, Tabs, Td, bytes, fmtMoney } from "@/components/ui/extra";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, downloadFile, errorMessage, get, getToken, setToken } from "@/lib/api";
import type { Partner, Registration } from "@/lib/types";
import { relativeDays, shortDate } from "@/lib/utils";

interface PortalMe { user: { id: string; full_name: string; email: string }; partner: Partner }
interface PortalCommissions {
  summary: { attributed_pipeline: number; attributed_won: number; commission_earned: number; deals: number } | null;
  lines: { deal: string; account: string; stage: string; role: string; split_pct: number; rate_pct: number; attributed_usd: number; commission_usd: number; status: string }[];
}

const EMPTY = { company_name: "", domain: "", contact_name: "", contact_email: "", estimated_amount: "", currency: "USD", territory: "", product_interest: "", notes: "" };

export default function PartnerPortal() {
  const router = useRouter();
  const qc = useQueryClient();
  const [tab, setTab] = useState<"register" | "registrations" | "commissions" | "collateral">("registrations");
  useEffect(() => { if (!getToken()) router.replace("/login"); }, [router]);
  const me = useQuery({ queryKey: ["portal", "me"], queryFn: () => get<PortalMe>("/portal/me"), retry: false });
  useEffect(() => { if (me.isError) router.replace("/"); }, [me.isError, router]);
  const regs = useQuery({ queryKey: ["portal", "regs"], queryFn: () => get<Registration[]>("/portal/registrations"), enabled: me.isSuccess });
  const comm = useQuery({ queryKey: ["portal", "comm"], queryFn: () => get<PortalCommissions>("/portal/commissions"), enabled: me.isSuccess });
  const coll = useQuery({ queryKey: ["portal", "coll"], queryFn: () => get<CollateralItem[]>("/portal/collateral"), enabled: me.isSuccess });

  const [f, setF] = useState(EMPTY);
  const submit = useMutation({
    mutationFn: async () => (await api.post<{ status: string; conflict_detected: boolean }>("/portal/registrations", {
      ...f, estimated_amount: Number(f.estimated_amount || 0), contact_email: f.contact_email || null, contact_name: f.contact_name || null,
      territory: f.territory || null, product_interest: f.product_interest || null, notes: f.notes || null,
    })).data,
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["portal"] }); setF(EMPTY); setTab("registrations");
      r.conflict_detected ? toast.warning("Registration submitted. A potential conflict was flagged for channel review.") : toast.success("Registration submitted for approval");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (!me.data) return <div className="mx-auto max-w-5xl p-6"><Skeleton className="h-10 w-48" /><Skeleton className="mt-6 h-64 w-full" /></div>;
  const p = me.data.partner;
  return (
    <div className="min-h-screen bg-background">
      <header className="border-b bg-surface">
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-4 py-3">
          <Logo />
          <span className="rounded-md bg-muted px-2 py-0.5 text-[12px] font-medium text-muted-foreground">Partner portal</span>
          <div className="ml-auto flex items-center gap-3 text-[13px]">
            <span className="hidden sm:inline">{me.data.user.full_name} · {p.name}</span>
            <Button size="sm" variant="ghost" onClick={() => { setToken(null); router.replace("/login"); }}><LogOut className="h-3.5 w-3.5" />Sign out</Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">
        <div className="mb-5 flex flex-wrap items-center gap-2">
          <h1 className="text-xl font-semibold tracking-tight">{p.name}</h1>
          <TierBadge tier={p.tier} />
          <span className="text-[13px] text-muted-foreground">· {p.territories.join(", ") || "all territories"} · {p.commission_rate}% resell / {p.referral_fee_rate}% referral</span>
        </div>
        <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatTile label="Registrations" value={String(regs.data?.length ?? 0)} sub={`${regs.data?.filter((r) => r.status === "approved").length ?? 0} approved`} />
          <StatTile label="Registered pipeline" value={fmtMoney(comm.data?.summary?.attributed_pipeline, "USD", true)} />
          <StatTile label="Won with us" value={fmtMoney(comm.data?.summary?.attributed_won, "USD", true)} />
          <StatTile label="Commission earned" value={fmtMoney(comm.data?.summary?.commission_earned, "USD", true)} />
        </div>
        <Tabs value={tab} onChange={setTab} tabs={[
          { value: "registrations", label: "My registrations" }, { value: "register", label: "Register a deal" },
          { value: "commissions", label: "Commissions" }, { value: "collateral", label: "Collateral", count: coll.data?.length },
        ]} />

        {tab === "register" && (
          <Card className="p-5">
            <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); submit.mutate(); }}>
              <p className="text-[13px] text-muted-foreground">Approved registrations carry territory exclusivity for 90 days. We check for existing customers and competing registrations automatically.</p>
              <div className="grid gap-3 sm:grid-cols-2">
                <div><Label htmlFor="r-co">Company</Label><Input id="r-co" required value={f.company_name} onChange={(e) => setF({ ...f, company_name: e.target.value })} /></div>
                <div><Label htmlFor="r-dom">Company domain</Label><Input id="r-dom" required placeholder="company.com" value={f.domain} onChange={(e) => setF({ ...f, domain: e.target.value })} /></div>
                <div><Label htmlFor="r-cn">Contact name</Label><Input id="r-cn" value={f.contact_name} onChange={(e) => setF({ ...f, contact_name: e.target.value })} /></div>
                <div><Label htmlFor="r-ce">Contact email</Label><Input id="r-ce" type="email" value={f.contact_email} onChange={(e) => setF({ ...f, contact_email: e.target.value })} /></div>
                <div className="grid grid-cols-[1fr_90px] gap-2">
                  <div><Label htmlFor="r-amt">Estimated value</Label><Input id="r-amt" type="number" min={0} value={f.estimated_amount} onChange={(e) => setF({ ...f, estimated_amount: e.target.value })} /></div>
                  <div><Label htmlFor="r-cur">Currency</Label><Select id="r-cur" value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value })}>{["USD", "EUR", "GBP", "CAD", "AUD"].map((c) => <option key={c}>{c}</option>)}</Select></div>
                </div>
                <div><Label htmlFor="r-ter">Territory</Label>
                  <Select id="r-ter" value={f.territory} onChange={(e) => setF({ ...f, territory: e.target.value })}>
                    <option value="">Select…</option>{p.territories.map((t) => <option key={t}>{t}</option>)}
                  </Select>
                </div>
              </div>
              <div><Label htmlFor="r-prod">Product interest</Label><Input id="r-prod" value={f.product_interest} onChange={(e) => setF({ ...f, product_interest: e.target.value })} /></div>
              <div><Label htmlFor="r-notes">Opportunity notes</Label><Textarea id="r-notes" rows={3} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></div>
              <div className="flex justify-end"><Button type="submit" size="sm" loading={submit.isPending}><Send className="h-3.5 w-3.5" />Submit registration</Button></div>
            </form>
          </Card>
        )}

        {tab === "registrations" && (
          <Card>
            {!regs.data?.length ? <EmptyState icon={<Handshake className="h-4 w-4" />} title="No registrations yet" action={<Button size="sm" onClick={() => setTab("register")}>Register a deal</Button>} /> : (
              <ul className="divide-y">
                {regs.data.map((r) => (
                  <li key={r.id} className="px-5 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{r.company_name}</span><span className="text-[12.5px] text-muted-foreground">{r.domain}</span><StatusPill status={r.status} />
                      <span className="ml-auto text-[12.5px] tabular-nums">{fmtMoney(r.estimated_amount, r.currency)}</span>
                    </div>
                    <p className="mt-0.5 text-[12px] text-muted-foreground">Submitted {relativeDays(r.created_at)}{r.exclusivity_expires_at && r.status === "approved" && ` · exclusive until ${shortDate(r.exclusivity_expires_at, true)}`}</p>
                    {r.status === "submitted" && <ConflictList conflicts={r.conflicts} />}
                    {r.decision_note && <p className="mt-1 text-[12.5px]">Channel team: {r.decision_note}</p>}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}

        {tab === "commissions" && (
          <Card>
            <CardHeader title="Commission statement" description="Commission becomes earned when an attributed deal closes won." />
            {!comm.data?.lines.length ? <EmptyState icon={<Handshake className="h-4 w-4" />} title="No attributed deals yet" /> : (
              <Table head={["Deal", "Stage", "Role", "Split", "Rate", "Attributed", "Commission", "Status"]} minWidth={820}>
                {comm.data.lines.map((l, i) => (
                  <tr key={i}>
                    <Td><span className="font-medium">{l.deal}</span><span className="block text-[12px] text-muted-foreground">{l.account}</span></Td>
                    <Td>{l.stage}</Td><Td className="capitalize">{l.role}</Td><Td>{l.split_pct}%</Td><Td>{l.rate_pct}%</Td>
                    <Td className="tabular-nums">{fmtMoney(l.attributed_usd)}</Td><Td className="tabular-nums">{fmtMoney(l.commission_usd)}</Td><Td><StatusPill status={l.status} /></Td>
                  </tr>
                ))}
              </Table>
            )}
          </Card>
        )}

        {tab === "collateral" && (
          <div className="grid gap-3 sm:grid-cols-2">
            {!coll.data?.length && <Card className="sm:col-span-2"><EmptyState icon={<FileText className="h-4 w-4" />} title="No collateral available for your tier yet" /></Card>}
            {coll.data?.map((c) => (
              <Card key={c.id} className="flex items-start gap-3 p-4">
                <FileText className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
                <div className="min-w-0 flex-1">
                  <p className="font-medium">{c.title}</p>
                  {c.description && <p className="text-[12.5px] text-muted-foreground">{c.description}</p>}
                  <p className="mt-1 text-[12px] capitalize text-subtle">{c.category.replace("_", " ")} · {bytes(c.file.size_bytes)}</p>
                </div>
                <Button size="sm" variant="outline" onClick={() => downloadFile(`/portal/collateral/${c.id}/download`, c.file.filename).catch((e) => toast.error(errorMessage(e)))}>
                  <Download className="h-3.5 w-3.5" />Download
                </Button>
              </Card>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
