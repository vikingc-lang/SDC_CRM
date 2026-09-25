"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { BUYING_ROLES } from "@/components/indicators";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Label, Select } from "@/components/ui/input";
import { api, errorMessage, get } from "@/lib/api";
import type { AccountListItem, Pipeline } from "@/lib/types";

type Opener = { open: boolean; onOpenChange: (o: boolean) => void };

function FormShell({ title, subtitle, children, onSubmit, busy, submitLabel }: {
  title: string; subtitle?: string; children: React.ReactNode; onSubmit: () => void; busy: boolean; submitLabel: string;
}) {
  return (
    <form onSubmit={(e) => { e.preventDefault(); onSubmit(); }} className="p-5">
      <h2 className="text-[15px] font-semibold">{title}</h2>
      {subtitle && <p className="mt-0.5 text-[13px] text-muted-foreground">{subtitle}</p>}
      <div className="mt-4 space-y-3">{children}</div>
      <div className="mt-5 flex justify-end gap-2">
        <Button type="submit" size="sm" loading={busy}>{submitLabel}</Button>
      </div>
    </form>
  );
}

function useAccounts(enabled: boolean) {
  return useQuery({ queryKey: ["accounts", "all"], queryFn: () => get<AccountListItem[]>("/accounts", { limit: 200 }), enabled });
}

export function NewAccountDialog({ open, onOpenChange }: Opener) {
  const [f, setF] = useState({ name: "", domain: "", industry: "", tier: "Mid-Market" });
  const qc = useQueryClient();
  const router = useRouter();
  const m = useMutation({
    mutationFn: async () => (await api.post<{ id: string }>("/accounts", { ...f, industry: f.industry || null })).data,
    onSuccess: (a) => {
      qc.invalidateQueries();
      onOpenChange(false);
      toast.success(`${f.name} created`);
      router.push(`/accounts/${a.id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="New account">
        <FormShell title="New account" onSubmit={() => m.mutate()} busy={m.isPending} submitLabel="Create account">
          <div><Label htmlFor="acc-name">Company name</Label><Input id="acc-name" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} autoFocus /></div>
          <div><Label htmlFor="acc-domain">Domain</Label><Input id="acc-domain" required placeholder="company.com" value={f.domain} onChange={(e) => setF({ ...f, domain: e.target.value })} /></div>
          <div className="grid grid-cols-2 gap-3">
            <div><Label htmlFor="acc-ind">Industry</Label><Input id="acc-ind" value={f.industry} onChange={(e) => setF({ ...f, industry: e.target.value })} /></div>
            <div><Label htmlFor="acc-tier">Tier</Label><Select id="acc-tier" value={f.tier} onChange={(e) => setF({ ...f, tier: e.target.value })}>{["SMB", "Mid-Market", "Enterprise"].map((t) => <option key={t}>{t}</option>)}</Select></div>
          </div>
        </FormShell>
      </DialogContent>
    </Dialog>
  );
}

export function NewDealDialog({ open, onOpenChange, accountId }: Opener & { accountId?: string }) {
  const [f, setF] = useState({ title: "", account_id: accountId ?? "", amount: "", stage_id: "", target_close_date: "" });
  const qc = useQueryClient();
  const { data: accounts } = useAccounts(open && !accountId);
  const { data: pipelines } = useQuery({ queryKey: ["pipelines"], queryFn: () => get<Pipeline[]>("/pipelines"), enabled: open });
  const stages = pipelines?.[0]?.stages.filter((s) => !s.is_closed_won && !s.is_closed_lost) ?? [];
  const m = useMutation({
    mutationFn: async () =>
      (await api.post("/deals", {
        title: f.title, account_id: accountId ?? f.account_id, amount: Number(f.amount || 0),
        stage_id: f.stage_id || null, target_close_date: f.target_close_date || null,
      })).data,
    onSuccess: () => { qc.invalidateQueries(); onOpenChange(false); toast.success("Deal created"); setF({ ...f, title: "", amount: "" }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="New deal">
        <FormShell title="New deal" subtitle="Tip: ⌘K can create deals straight from meeting notes." onSubmit={() => m.mutate()} busy={m.isPending} submitLabel="Create deal">
          <div><Label htmlFor="deal-title">Deal title</Label><Input id="deal-title" required value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} autoFocus /></div>
          {!accountId && (
            <div><Label htmlFor="deal-acc">Account</Label>
              <Select id="deal-acc" required value={f.account_id} onChange={(e) => setF({ ...f, account_id: e.target.value })}>
                <option value="" disabled>Select account…</option>
                {accounts?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div><Label htmlFor="deal-amt">Amount (USD)</Label><Input id="deal-amt" type="number" min={0} value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></div>
            <div><Label htmlFor="deal-close">Target close</Label><Input id="deal-close" type="date" value={f.target_close_date} onChange={(e) => setF({ ...f, target_close_date: e.target.value })} /></div>
          </div>
          <div><Label htmlFor="deal-stage">Stage</Label>
            <Select id="deal-stage" value={f.stage_id} onChange={(e) => setF({ ...f, stage_id: e.target.value })}>
              <option value="">Discovery (default)</option>
              {stages.map((s) => <option key={s.id} value={s.id}>{s.name} · {s.default_probability}%</option>)}
            </Select>
          </div>
        </FormShell>
      </DialogContent>
    </Dialog>
  );
}

export function NewContactDialog({ open, onOpenChange, accountId }: Opener & { accountId?: string }) {
  const [f, setF] = useState({ first_name: "", last_name: "", email: "", job_title: "", phone: "", buying_role: "Evaluator", account_id: accountId ?? "" });
  const qc = useQueryClient();
  const { data: accounts } = useAccounts(open && !accountId);
  const m = useMutation({
    mutationFn: async () =>
      (await api.post("/contacts", { ...f, account_id: accountId ?? f.account_id, email: f.email || null, job_title: f.job_title || null, phone: f.phone || null })).data,
    onSuccess: () => { qc.invalidateQueries(); onOpenChange(false); toast.success("Contact added"); setF({ ...f, first_name: "", last_name: "", email: "", job_title: "", phone: "" }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="New contact">
        <FormShell title="New contact" onSubmit={() => m.mutate()} busy={m.isPending} submitLabel="Add contact">
          <div className="grid grid-cols-2 gap-3">
            <div><Label htmlFor="c-first">First name</Label><Input id="c-first" required value={f.first_name} onChange={(e) => setF({ ...f, first_name: e.target.value })} autoFocus /></div>
            <div><Label htmlFor="c-last">Last name</Label><Input id="c-last" value={f.last_name} onChange={(e) => setF({ ...f, last_name: e.target.value })} /></div>
          </div>
          {!accountId && (
            <div><Label htmlFor="c-acc">Account</Label>
              <Select id="c-acc" required value={f.account_id} onChange={(e) => setF({ ...f, account_id: e.target.value })}>
                <option value="" disabled>Select account…</option>
                {accounts?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div><Label htmlFor="c-title">Job title</Label><Input id="c-title" value={f.job_title} onChange={(e) => setF({ ...f, job_title: e.target.value })} /></div>
            <div><Label htmlFor="c-role">Buying role</Label><Select id="c-role" value={f.buying_role} onChange={(e) => setF({ ...f, buying_role: e.target.value })}>{BUYING_ROLES.map((r) => <option key={r}>{r}</option>)}</Select></div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div><Label htmlFor="c-email">Email</Label><Input id="c-email" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></div>
            <div><Label htmlFor="c-phone">Phone</Label><Input id="c-phone" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></div>
          </div>
        </FormShell>
      </DialogContent>
    </Dialog>
  );
}

export function NewTaskDialog({ open, onOpenChange, accountId, dealId }: Opener & { accountId?: string; dealId?: string }) {
  const [f, setF] = useState({ title: "", due_date: "" });
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: async () => (await api.post("/tasks", { title: f.title, due_date: f.due_date || null, account_id: accountId ?? null, deal_id: dealId ?? null })).data,
    onSuccess: () => { qc.invalidateQueries(); onOpenChange(false); setF({ title: "", due_date: "" }); toast.success("Task added"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="New task">
        <FormShell title="New task" onSubmit={() => m.mutate()} busy={m.isPending} submitLabel="Add task">
          <div><Label htmlFor="t-title">What needs to happen?</Label><Input id="t-title" required value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} autoFocus /></div>
          <div><Label htmlFor="t-due">Due date</Label><Input id="t-due" type="date" value={f.due_date} onChange={(e) => setF({ ...f, due_date: e.target.value })} /></div>
        </FormShell>
      </DialogContent>
    </Dialog>
  );
}
