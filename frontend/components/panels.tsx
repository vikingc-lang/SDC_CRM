"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ExternalLink, FilePlus2, FileSignature, Handshake, OctagonAlert, Pencil, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { StatusPill, fmtMoney } from "@/components/ui/extra";
import { Input, Label, Select } from "@/components/ui/input";
import { api, errorMessage, get } from "@/lib/api";
import type { Alert, CustomFieldDef, DocumentSummary, Partner, Quote } from "@/lib/types";
import { relativeDays, shortDate } from "@/lib/utils";

const SEVERITY = { high: "var(--status-critical)", medium: "var(--status-serious)", low: "var(--status-warning)" } as const;

export function AlertsBanner({ alerts }: { alerts: Alert[] }) {
  if (!alerts.length) return null;
  return (
    <div className="mb-6 space-y-2" role="status">
      {alerts.map((a) => (
        <div key={a.id} className="flex items-start gap-2.5 rounded-lg border bg-surface px-3.5 py-2.5 text-[13px] shadow-card"
          style={{ borderColor: `color-mix(in srgb, ${SEVERITY[a.severity]} 45%, transparent)` }}>
          {a.severity === "high" ? <OctagonAlert className="mt-0.5 h-4 w-4 shrink-0" style={{ color: SEVERITY.high }} /> : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" style={{ color: SEVERITY[a.severity] }} />}
          <span className="flex-1"><span className="font-medium capitalize">{a.kind.replace(/_/g, " ")}:</span> {a.message}</span>
          <span className="shrink-0 text-[11.5px] uppercase tracking-wide text-muted-foreground">{a.severity}</span>
        </div>
      ))}
    </div>
  );
}

export function QuotesCard({ dealId, quotes, canCreate }: { dealId: string; quotes: Quote[]; canCreate: boolean }) {
  const router = useRouter();
  const create = useMutation({
    mutationFn: async () => (await api.post<Quote>(`/deals/${dealId}/quotes`, { lines: [] })).data,
    onSuccess: (q) => router.push(`/quotes/${q.id}`),
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader title="Quotes" icon={<FileSignature className="h-4 w-4 text-muted-foreground" />}
        action={canCreate && <Button size="sm" variant="outline" loading={create.isPending} onClick={() => create.mutate()}><Plus className="h-3.5 w-3.5" />New quote</Button>} />
      <CardBody className="space-y-2">
        {!quotes.length && <p className="text-[13px] text-muted-foreground">No quotes yet. Build one from the product catalog with tiered pricing and approval routing.</p>}
        {quotes.map((q) => (
          <Link key={q.id} href={`/quotes/${q.id}`} className="flex items-center gap-3 rounded-md border px-3 py-2.5 transition-colors hover:border-primary/40">
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13.5px] font-medium">{q.quote_number} · {q.name}</p>
              <p className="text-[12px] text-muted-foreground">{q.lines.length} lines · {q.term_months} months · {q.payment_terms}{q.max_discount_pct ? ` · up to ${q.max_discount_pct}% off` : ""}</p>
            </div>
            <div className="text-right">
              <p className="tabular text-[13.5px] font-semibold">{fmtMoney(q.tcv, q.currency)}</p>
              <p className="text-[11px] text-muted-foreground">TCV</p>
            </div>
            <StatusPill status={q.status} />
          </Link>
        ))}
      </CardBody>
    </Card>
  );
}

const DOC_LABEL = { nda: "NDA", sow: "Statement of Work", order_form: "Order Form" } as const;

export function DocumentsCard({ dealId, documents, canCreate, hasApprovedQuote }: { dealId: string; documents: DocumentSummary[]; canCreate: boolean; hasApprovedQuote: boolean }) {
  const router = useRouter();
  const gen = useMutation({
    mutationFn: async (doc_type: keyof typeof DOC_LABEL) => (await api.post<DocumentSummary>("/documents", { doc_type, deal_id: dealId })).data,
    onSuccess: (d) => router.push(`/documents/${d.id}`),
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader title="Documents & e-signature" icon={<FilePlus2 className="h-4 w-4 text-muted-foreground" />} description="Generated from CRM fields, signed in Cirra" />
      <CardBody className="space-y-3">
        {canCreate && (
          <div className="flex flex-wrap gap-2">
            {(Object.keys(DOC_LABEL) as (keyof typeof DOC_LABEL)[]).map((t) => (
              <Button key={t} size="sm" variant="outline" disabled={gen.isPending || (t === "order_form" && !hasApprovedQuote)}
                title={t === "order_form" && !hasApprovedQuote ? "Needs an approved quote" : undefined} onClick={() => gen.mutate(t)}>
                <Plus className="h-3.5 w-3.5" />{DOC_LABEL[t]}
              </Button>
            ))}
          </div>
        )}
        {documents.map((d) => (
          <Link key={d.id} href={`/documents/${d.id}`} className="flex items-center gap-3 rounded-md border px-3 py-2.5 transition-colors hover:border-primary/40">
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13.5px] font-medium">{d.title}</p>
              <p className="text-[12px] text-muted-foreground">
                {d.signers.length ? d.signers.map((s) => `${s.name} (${s.status})`).join(" · ") : `Created ${relativeDays(d.created_at)}`}
              </p>
            </div>
            <StatusPill status={d.status} />
          </Link>
        ))}
        {!documents.length && <p className="text-[13px] text-muted-foreground">No documents yet.</p>}
      </CardBody>
    </Card>
  );
}

export function PartnersCard({ dealId, partners, canEdit }: {
  dealId: string; partners: { id: string; partner: { id: string; name: string; tier: string }; role: string; split_pct: number; commission_rate: number | null }[]; canEdit: boolean;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ partner_id: "", role: "co_sell", split_pct: "30" });
  const { data: all } = useQuery({ queryKey: ["partners"], queryFn: () => get<Partner[]>("/partners"), enabled: open });
  const add = useMutation({
    mutationFn: async () => (await api.post(`/deals/${dealId}/partners`, { ...f, split_pct: Number(f.split_pct) })).data,
    onSuccess: () => { qc.invalidateQueries(); setOpen(false); toast.success("Partner attribution saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: async (id: string) => api.delete(`/deals/${dealId}/partners/${id}`),
    onSuccess: () => qc.invalidateQueries(),
  });
  return (
    <Card>
      <CardHeader title="Partner attribution" icon={<Handshake className="h-4 w-4 text-muted-foreground" />}
        action={canEdit && <Button variant="ghost" size="sm" onClick={() => setOpen(true)}><Plus className="h-3.5 w-3.5" /></Button>} />
      <CardBody className="space-y-2">
        {!partners.length && <p className="text-[13px] text-muted-foreground">Direct deal: no partner involved.</p>}
        {partners.map((p) => (
          <div key={p.id} className="flex items-center gap-2 text-[13px]">
            <span className="flex-1"><span className="font-medium">{p.partner.name}</span> <span className="capitalize text-muted-foreground">· {p.role.replace("_", "-")} · {p.partner.tier}</span></span>
            <span className="tabular font-medium">{p.split_pct}%</span>
            {canEdit && <button aria-label="Remove partner" onClick={() => remove.mutate(p.id)} className="rounded p-1 text-subtle hover:bg-muted hover:text-foreground"><Trash2 className="h-3.5 w-3.5" /></button>}
          </div>
        ))}
      </CardBody>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title="Add partner" className="max-w-sm">
          <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
            <h2 className="text-[15px] font-semibold">Attribute a partner</h2>
            <div><Label>Partner</Label>
              <Select required value={f.partner_id} onChange={(e) => setF({ ...f, partner_id: e.target.value })}>
                <option value="" disabled>Select…</option>
                {all?.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.tier})</option>)}
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div><Label>Role</Label><Select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}><option value="co_sell">Co-sell</option><option value="resell">Resell</option><option value="referral">Referral</option></Select></div>
              <div><Label>Split %</Label><Input type="number" min={0} max={100} value={f.split_pct} onChange={(e) => setF({ ...f, split_pct: e.target.value })} /></div>
            </div>
            <div className="flex justify-end"><Button size="sm" type="submit" loading={add.isPending}>Save</Button></div>
          </form>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

export function CustomFieldsEditor({ defs, values, onSave, canEdit, saving }: {
  defs: CustomFieldDef[]; values: Record<string, unknown>; onSave: (v: Record<string, unknown>) => void; canEdit: boolean; saving?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const known = new Set(defs.map((d) => d.key));
  const extra = Object.entries(values).filter(([k]) => !known.has(k) && !["health_breakdown", "domain_unverified", "source"].includes(k));
  const show = (v: unknown) => (v === true ? "Yes" : v === false ? "No" : v === undefined || v === null || v === "" ? "—" : String(v));
  return (
    <div>
      {!editing ? (
        <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
          {defs.map((d) => (
            <div key={d.key} className="min-w-0">
              <dt className="text-[12px] text-muted-foreground">{d.label}</dt>
              <dd className="mt-0.5 truncate text-[13.5px]">
                {d.field_type === "url" && values[d.key] ? <a className="inline-flex items-center gap-1 text-primary hover:underline" href={String(values[d.key])} target="_blank" rel="noreferrer">Open<ExternalLink className="h-3 w-3" /></a> : show(values[d.key])}
              </dd>
            </div>
          ))}
          {extra.map(([k, v]) => (
            <div key={k} className="min-w-0"><dt className="text-[12px] text-muted-foreground">{k}</dt><dd className="mt-0.5 truncate text-[13.5px]">{show(typeof v === "object" ? JSON.stringify(v) : v)}</dd></div>
          ))}
          {!defs.length && !extra.length && <p className="text-[13px] text-muted-foreground">No custom fields defined. Admins can add them in Admin → Custom fields.</p>}
        </dl>
      ) : (
        <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); onSave(draft); setEditing(false); }}>
          {defs.map((d) => (
            <div key={d.key}>
              <Label>{d.label}{d.required && " *"}</Label>
              {d.field_type === "select" ? (
                <Select value={String(draft[d.key] ?? "")} onChange={(e) => setDraft({ ...draft, [d.key]: e.target.value || null })}>
                  <option value="">—</option>
                  {d.options.map((o) => <option key={o}>{o}</option>)}
                </Select>
              ) : d.field_type === "boolean" ? (
                <Select value={draft[d.key] === true ? "true" : draft[d.key] === false ? "false" : ""} onChange={(e) => setDraft({ ...draft, [d.key]: e.target.value === "" ? null : e.target.value === "true" })}>
                  <option value="">—</option><option value="true">Yes</option><option value="false">No</option>
                </Select>
              ) : (
                <Input type={d.field_type === "number" ? "number" : d.field_type === "date" ? "date" : d.field_type === "url" ? "url" : "text"}
                  value={String(draft[d.key] ?? "")} onChange={(e) => setDraft({ ...draft, [d.key]: e.target.value || null })} />
              )}
            </div>
          ))}
          <div className="flex gap-2 sm:col-span-2">
            <Button type="submit" size="sm" loading={saving}>Save</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </form>
      )}
      {canEdit && !editing && defs.length > 0 && (
        <Button size="sm" variant="ghost" className="mt-3" onClick={() => { setDraft({ ...values }); setEditing(true); }}><Pencil className="h-3.5 w-3.5" />Edit fields</Button>
      )}
    </div>
  );
}

export function ContractList({ contracts }: { contracts: { id: string; contract_number: string; name: string; start_date: string; end_date: string; days_to_expiry: number;
  currency: string; acv: number; tcv: number; status: string; renewal_deal_id: string | null }[] }) {
  if (!contracts.length) return <p className="text-[13px] text-muted-foreground">No contracts yet. A countersigned Order Form creates one automatically.</p>;
  return (
    <div className="space-y-2">
      {contracts.map((c) => (
        <div key={c.id} className="rounded-md border px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[13.5px] font-medium">{c.contract_number}</span>
            <StatusPill status={c.status} />
            <span className="ml-auto tabular text-[13.5px] font-semibold">{fmtMoney(c.acv, c.currency)}<span className="text-[11px] font-normal text-muted-foreground"> ACV</span></span>
          </div>
          <p className="mt-0.5 text-[12px] text-muted-foreground">
            {shortDate(c.start_date, true)} → {shortDate(c.end_date, true)} · TCV {fmtMoney(c.tcv, c.currency)}
            {c.status === "active" && ` · ${c.days_to_expiry} days left`}
            {c.renewal_deal_id && <> · <Link className="text-primary hover:underline" href={`/deals/${c.renewal_deal_id}`}>renewal opportunity</Link></>}
          </p>
        </div>
      ))}
    </div>
  );
}
