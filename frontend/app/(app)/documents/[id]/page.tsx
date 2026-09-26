"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, Copy, Download, Fingerprint, Send } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { NegotiationPanel } from "@/components/negotiation";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StatusPill } from "@/components/ui/extra";
import { Input, Label, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { API_URL, api, errorMessage, get, getToken } from "@/lib/api";
import { useMe } from "@/lib/me";
import type { Contact, DocumentSummary } from "@/lib/types";
import { shortDate } from "@/lib/utils";

async function openPdf(id: string, title: string) {
  const res = await fetch(`${API_URL}/api/v1/documents/${id}/pdf`, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!res.ok) return toast.error("Could not load the PDF");
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = `${title}.pdf`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const { me, can } = useMe();
  const { data: doc, isLoading } = useQuery({ queryKey: ["document", id], queryFn: () => get<DocumentSummary>(`/documents/${id}`) });
  const { data: contacts } = useQuery({ queryKey: ["contacts", doc?.account.id], queryFn: () => get<Contact[]>("/contacts", { account_id: doc?.account.id }), enabled: !!doc });
  const [customer, setCustomer] = useState({ name: "", email: "" });
  const [company, setCompany] = useState<{ name: string; email: string } | null>(null);
  const [provider, setProvider] = useState("builtin");
  const send = useMutation({
    mutationFn: async () => (await api.post(`/documents/${id}/send`, { signers: [
      { ...customer, party: "customer" }, { ...(company ?? { name: me?.full_name ?? "", email: me?.email ?? "" }), party: "company" }], provider })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["document", id] }); toast.success(provider === "builtin" ? "Sent for signature. Share the signing links." : `Envelope created in ${provider === "docusign" ? "DocuSign" : "Adobe Sign"}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (isLoading || !doc) return <div className="mx-auto max-w-6xl space-y-4"><Skeleton className="h-16 w-full" /><Skeleton className="h-96 w-full" /></div>;
  const counter = company ?? { name: me?.full_name ?? "", email: me?.email ?? "" };

  return (
    <div className="mx-auto max-w-6xl">
      <Link href={doc.deal_id ? `/deals/${doc.deal_id}` : `/accounts/${doc.account.id}`} className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-3.5 w-3.5" />{doc.account.name}
      </Link>
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <h1 className="min-w-0 flex-1 text-[22px] font-semibold tracking-tight">{doc.title}</h1>
        <StatusPill status={doc.status} />
        {!!doc.current_version && <span className="text-[12.5px] text-muted-foreground">v{doc.current_version}</span>}
        {doc.esign_provider && doc.esign_provider !== "builtin" && <span className="text-[12.5px] text-muted-foreground">via {doc.esign_provider === "docusign" ? "DocuSign" : "Adobe Sign"} · {doc.envelope_id}</span>}
        <Button variant="outline" size="sm" onClick={() => openPdf(doc.id, doc.title)}><Download className="h-4 w-4" />{doc.status === "completed" ? "Signed PDF" : "Preview PDF"}</Button>
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardBody className="pt-5">
            <article className="doc-body max-w-none text-[14px] leading-relaxed" dangerouslySetInnerHTML={{ __html: doc.body_html ?? "" }} />
          </CardBody>
        </Card>
        <div className="space-y-6">
          <Card>
            <CardHeader title="Signatures" description="Customer signs first, then the company countersigns" />
            <CardBody className="space-y-3">
              {doc.signers.map((s) => (
                <div key={s.id} className="rounded-md border p-2.5 text-[13px]">
                  <div className="flex items-center gap-2">
                    {s.status === "signed" ? <CheckCircle2 className="h-4 w-4" style={{ color: "var(--status-good)" }} /> : <span className="flex h-4 w-4 items-center justify-center rounded-full border text-[10px]">{s.order}</span>}
                    <span className="font-medium">{s.name}</span>
                    <span className="text-muted-foreground">· {s.party === "customer" ? "Customer" : "SDC Solutions"}</span>
                    <span className="ml-auto"><StatusPill status={s.status} /></span>
                  </div>
                  <p className="mt-1 text-[12px] text-muted-foreground">{s.email}{s.signed_at && ` · signed ${shortDate(s.signed_at, true)} from ${s.signed_ip}`}</p>
                  {s.sign_url && (
                    <div className="mt-2 flex gap-1.5">
                      <Input readOnly value={s.sign_url} className="h-8 text-[12px]" aria-label="Signing link" />
                      <Button size="icon" variant="outline" aria-label="Copy signing link" onClick={() => { navigator.clipboard?.writeText(s.sign_url ?? ""); toast.success("Signing link copied"); }}><Copy className="h-3.5 w-3.5" /></Button>
                      <a className="inline-flex h-8 items-center rounded-md border px-2 text-[12px] hover:bg-muted" href={s.sign_url.replace(/^https?:\/\/[^/]+/, "")} target="_blank" rel="noreferrer">Open</a>
                    </div>
                  )}
                </div>
              ))}
              {["draft", "in_negotiation"].includes(doc.status) && can("documents", "update") && (
                <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); send.mutate(); }}>
                  <div>
                    <Label>Customer signer</Label>
                    <select className="mb-2 h-9 w-full rounded-md border border-input bg-surface px-2 text-sm" value="" onChange={(e) => {
                      const c = contacts?.find((x) => x.id === e.target.value);
                      if (c) setCustomer({ name: c.name, email: c.email ?? "" });
                    }}>
                      <option value="">Pick from buying committee…</option>
                      {contacts?.filter((c) => c.email).map((c) => <option key={c.id} value={c.id}>{c.name} · {c.buying_role}</option>)}
                    </select>
                    <div className="grid grid-cols-2 gap-2">
                      <Input required placeholder="Name" value={customer.name} onChange={(e) => setCustomer({ ...customer, name: e.target.value })} />
                      <Input required type="email" placeholder="Email" value={customer.email} onChange={(e) => setCustomer({ ...customer, email: e.target.value })} />
                    </div>
                  </div>
                  <div>
                    <Label>Company countersigner</Label>
                    <div className="grid grid-cols-2 gap-2">
                      <Input required value={counter.name} onChange={(e) => setCompany({ ...counter, name: e.target.value })} />
                      <Input required type="email" value={counter.email} onChange={(e) => setCompany({ ...counter, email: e.target.value })} />
                    </div>
                  </div>
                  <div>
                    <Label>E-signature</Label>
                    <Select value={provider} onChange={(e) => setProvider(e.target.value)}>
                      <option value="builtin">Cirra e-sign (built in)</option>
                      <option value="docusign">DocuSign</option>
                      <option value="adobe_sign">Adobe Sign</option>
                    </Select>
                  </div>
                  <Button size="sm" type="submit" loading={send.isPending} className="w-full"><Send className="h-4 w-4" />Send for e-signature</Button>
                </form>
              )}
            </CardBody>
          </Card>
          <NegotiationPanel doc={doc} canEdit={can("documents", "update")} />
          <Card>
            <CardHeader title="Integrity" icon={<Fingerprint className="h-4 w-4 text-muted-foreground" />} />
            <CardBody className="space-y-1 text-[12px] text-muted-foreground">
              <p>Content SHA-256</p>
              <p className="break-all font-mono text-[11px] text-foreground">{doc.content_sha256}</p>
              <p className="pt-2">The signed PDF carries a certificate with each signer&apos;s timestamp, IP and user agent, and is attached to the account timeline.</p>
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}
