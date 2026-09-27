"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Boxes, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Label, Select } from "@/components/ui/input";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import type { CustomFieldDef } from "@/lib/types";
import { relativeDays } from "@/lib/utils";

/* Custom objects: admin-defined record types. Fields are custom field definitions (typed, validated, with
   field-level security); records have a name, an optional account and an owner. */

export interface CustomObjectDef { id: string; key: string; label: string; plural_label: string; description: string | null; source: string; fields: CustomFieldDef[] }
export interface CustomRecordRow {
  id: string; object: string; name: string; account: { id: string; name: string | null } | null; owner: { id: string; full_name: string | null } | null;
  data: Record<string, unknown>; created_at: string; updated_at: string;
}

export function useObjects() {
  const { can } = useMe();
  return useQuery({ queryKey: ["objects"], queryFn: () => get<CustomObjectDef[]>("/objects"), enabled: can("custom_objects", "read"), staleTime: 60_000 });
}

/** One input per field type; read-only fields are shown but locked. */
export function FieldInput({ d, value, onChange }: { d: CustomFieldDef; value: unknown; onChange: (v: unknown) => void }) {
  const id = `cf-${d.key}`;
  if (d.field_type === "select") {
    return <Select id={id} disabled={d.read_only} value={String(value ?? "")} onChange={(e) => onChange(e.target.value || null)}>
      <option value="">—</option>{d.options.map((o) => <option key={o}>{o}</option>)}</Select>;
  }
  if (d.field_type === "boolean") {
    return <Select id={id} disabled={d.read_only} value={value === true ? "true" : value === false ? "false" : ""} onChange={(e) => onChange(e.target.value === "" ? null : e.target.value === "true")}>
      <option value="">—</option><option value="true">Yes</option><option value="false">No</option></Select>;
  }
  const type = d.field_type === "number" ? "number" : d.field_type === "date" ? "date" : d.field_type === "url" ? "url" : "text";
  return <Input id={id} type={type} disabled={d.read_only} value={String(value ?? "")} onChange={(e) => onChange(e.target.value || null)} />;
}

/** Search-as-you-type account picker (only accounts the user can see come back). */
export function AccountPicker({ value, onChange, initialName }: { value: string; onChange: (id: string) => void; initialName?: string | null }) {
  const [q, setQ] = useState("");
  const accounts = useQuery({ queryKey: ["accounts", "pick", q], placeholderData: (p) => p,
    queryFn: () => get<{ id: string; name: string }[]>("/accounts", { search: q || undefined, limit: 25 }) });
  const opts = accounts.data ?? [];
  return (
    <div className="space-y-1.5">
      <Input aria-label="Search accounts" placeholder="Search accounts…" value={q} onChange={(e) => setQ(e.target.value)} />
      <Select id="rec-account" aria-label="Account" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">No account</option>
        {value && !opts.some((a) => a.id === value) && <option value={value}>{initialName ?? "Current account"}</option>}
        {opts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
      </Select>
    </div>
  );
}

export function NewRecordDialog({ obj, open, onOpenChange, accountId, accountName }: {
  obj: CustomObjectDef; open: boolean; onOpenChange: (o: boolean) => void; accountId?: string; accountName?: string;
}) {
  const qc = useQueryClient();
  const router = useRouter();
  const [name, setName] = useState("");
  const [account, setAccount] = useState(accountId ?? "");
  const [data, setData] = useState<Record<string, unknown>>({});
  const editable = obj.fields.filter((f) => !f.read_only);
  const create = useMutation({
    mutationFn: async () => (await api.post<CustomRecordRow>(`/objects/${obj.key}/records`, { name, account_id: account || null,
      data: Object.fromEntries(Object.entries(data).filter(([, v]) => v !== null && v !== "")) })).data,
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: [obj.source] }); qc.invalidateQueries({ queryKey: ["objects", "related"] });
      toast.success(`${obj.label} created`); onOpenChange(false); setName(""); setData({});
      router.push(`/objects/${obj.key}/${r.id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={`New ${obj.label.toLowerCase()}`} className="max-w-lg">
        <form className="max-h-[85vh] space-y-3 overflow-y-auto p-5" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <h2 className="text-[15px] font-semibold">New {obj.label.toLowerCase()}</h2>
          <div><Label htmlFor="rec-name">Name</Label><Input id="rec-name" required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} /></div>
          <div><Label htmlFor="rec-account">Account</Label>
            {accountId ? <p className="text-[13px]">{accountName}</p> : <AccountPicker value={account} onChange={setAccount} />}</div>
          <div className="grid gap-3 sm:grid-cols-2">
            {editable.map((d) => (
              <div key={d.key}><Label htmlFor={`cf-${d.key}`}>{d.label}{d.required && " *"}</Label>
                <FieldInput d={d} value={data[d.key]} onChange={(v) => setData({ ...data, [d.key]: v })} /></div>
            ))}
          </div>
          <div className="flex justify-end pt-2"><Button type="submit" size="sm" loading={create.isPending}>Create {obj.label.toLowerCase()}</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** On an account page: the custom-object records linked to it, per object, with a quick "new". */
export function RelatedObjectRecords({ accountId, accountName }: { accountId: string; accountName: string }) {
  const { can } = useMe();
  const objects = useObjects();
  const [creating, setCreating] = useState<CustomObjectDef | null>(null);
  const related = useQuery({
    queryKey: ["objects", "related", accountId, objects.data?.map((o) => o.key)], enabled: !!objects.data?.length,
    queryFn: async () => Promise.all(objects.data!.map(async (o) => ({ obj: o,
      records: (await get<{ records: CustomRecordRow[] }>(`/objects/${o.key}/records`, { account_id: accountId, limit: 10 })).records }))),
  });
  if (!objects.data?.length) return null;
  return (
    <Card>
      <CardHeader title="Related records" description="Custom objects on this account" icon={<Boxes className="h-4 w-4 text-muted-foreground" />} />
      <CardBody className="space-y-4">
        {(related.data ?? []).map(({ obj, records }) => (
          <div key={obj.key}>
            <div className="mb-1 flex items-center justify-between">
              <p className="text-[12px] font-medium uppercase tracking-wide text-subtle">{obj.plural_label} · {records.length}</p>
              {can("custom_objects", "create") && <Button variant="ghost" size="sm" aria-label={`New ${obj.label}`} onClick={() => setCreating(obj)}><Plus className="h-3.5 w-3.5" /></Button>}
            </div>
            {records.length === 0 ? <p className="text-[13px] text-muted-foreground">None yet.</p> : records.map((r) => (
              <Link key={r.id} href={`/objects/${obj.key}/${r.id}`} className="flex items-center justify-between rounded-md px-2 py-1.5 text-[13px] hover:bg-muted">
                <span className="truncate font-medium">{r.name}</span><span className="shrink-0 text-[12px] text-muted-foreground">{relativeDays(r.updated_at)}</span>
              </Link>
            ))}
          </div>
        ))}
      </CardBody>
      {creating && <NewRecordDialog obj={creating} open onOpenChange={(o) => !o && setCreating(null)} accountId={accountId} accountName={accountName} />}
    </Card>
  );
}
