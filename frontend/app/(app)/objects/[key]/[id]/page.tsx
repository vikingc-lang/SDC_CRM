"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Boxes, Pencil, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { AccountPicker, type CustomObjectDef, type CustomRecordRow } from "@/components/objects";
import { CustomFieldsEditor } from "@/components/panels";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { relativeDays } from "@/lib/utils";

interface RecordPage { object: CustomObjectDef; record: CustomRecordRow; can_edit: boolean; can_delete: boolean }

export default function CustomRecordPage() {
  const { key, id } = useParams<{ key: string; id: string }>();
  const qc = useQueryClient();
  const router = useRouter();
  const q = useQuery({ queryKey: [`obj_${key}`, "record", id], queryFn: () => get<RecordPage>(`/objects/${key}/records/${id}`), retry: false });
  const [editing, setEditing] = useState(false);
  const [f, setF] = useState({ name: "", account_id: "" });
  const [confirmDelete, setConfirmDelete] = useState(false);
  const save = useMutation({
    mutationFn: async (body: Record<string, unknown>) => (await api.patch<CustomRecordRow>(`/objects/${key}/records/${id}`, body)).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: [`obj_${key}`] }); qc.invalidateQueries({ queryKey: ["objects", "related"] }); setEditing(false); toast.success("Saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const del = useMutation({
    mutationFn: async () => api.delete(`/objects/${key}/records/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: [`obj_${key}`] }); toast.success("Deleted"); router.push(`/objects/${key}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (q.isLoading) return <Skeleton className="h-96" />;
  if (!q.data) return <EmptyState icon={<Boxes className="h-4 w-4" />} title="Record not found" description={q.error ? errorMessage(q.error) : undefined} />;
  const { object: obj, record: r } = q.data;
  return (
    <div className="mx-auto max-w-5xl">
      <Link href={`/objects/${key}`} className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />{obj.plural_label}</Link>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[12px] font-medium uppercase tracking-wide text-subtle">{obj.label}</p>
          <h1 className="text-xl font-semibold tracking-tight">{r.name}</h1>
          <p className="mt-0.5 text-[13px] text-muted-foreground">
            {r.account ? <Link href={`/accounts/${r.account.id}`} className="hover:underline">{r.account.name}</Link> : "No account"}
            {" · "}Owner {r.owner?.full_name ?? "—"} · updated {relativeDays(r.updated_at)}
          </p>
        </div>
        <div className="flex gap-2">
          {q.data.can_edit && !editing && <Button size="sm" variant="outline" onClick={() => { setF({ name: r.name, account_id: r.account?.id ?? "" }); setEditing(true); }}><Pencil className="h-3.5 w-3.5" />Edit details</Button>}
          {q.data.can_delete && (confirmDelete
            ? <Button size="sm" variant="destructive" loading={del.isPending} onClick={() => del.mutate()}>Confirm delete</Button>
            : <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(true)}><Trash2 className="h-3.5 w-3.5" />Delete</Button>)}
        </div>
      </div>
      {editing && (
        <Card className="mb-5">
          <CardBody>
            <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate({ name: f.name, account_id: f.account_id || null }); }}>
              <div><Label htmlFor="rec-edit-name">Name</Label><Input id="rec-edit-name" required maxLength={200} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
              <div><Label htmlFor="rec-account">Account</Label><AccountPicker value={f.account_id} onChange={(v) => setF({ ...f, account_id: v })} initialName={r.account?.name} /></div>
              <div className="flex gap-2 sm:col-span-2">
                <Button type="submit" size="sm" loading={save.isPending}>Save</Button>
                <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
              </div>
            </form>
          </CardBody>
        </Card>
      )}
      <Card>
        <CardHeader title="Details" description={obj.description ?? undefined} />
        <CardBody>
          {obj.fields.length === 0 ? <p className="text-[13px] text-muted-foreground">This object has no fields yet. Admins add them in Admin → Custom fields.</p>
            : <CustomFieldsEditor defs={obj.fields} values={r.data} canEdit={q.data.can_edit} saving={save.isPending} onSave={(v) => save.mutate({ data: v })} />}
        </CardBody>
      </Card>
    </div>
  );
}
