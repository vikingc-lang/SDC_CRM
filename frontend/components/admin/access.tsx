"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { StatusPill, Table, Td } from "@/components/ui/extra";
import { Input, Label, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { ROLE_LABELS } from "@/lib/me";
import type { Action, Partner } from "@/lib/types";
import { relativeDays } from "@/lib/utils";

interface AdminUser { id: string; email: string; full_name: string; role: string; manager_id: string | null; partner_id: string | null; is_active: boolean; created_at: string }
type Cell = Record<Action, boolean> & { scope: "all" | "own" };
interface Matrix { roles: { key: string; label: string }[]; resources: string[]; actions: Action[]; matrix: Record<string, Record<string, Cell>> }

export function UsersPanel() {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: () => get<AdminUser[]>("/admin/users") });
  const partners = useQuery({ queryKey: ["prm", "partners"], queryFn: () => get<Partner[]>("/partners") });
  const [f, setF] = useState<{ email: string; full_name: string; role: string; manager_id: string; partner_id: string } | null>(null);
  const names = new Map(users.data?.map((u) => [u.id, u.full_name]));
  const patch = useMutation({
    mutationFn: async (v: { id: string; body: Partial<AdminUser> }) => (await api.patch(`/admin/users/${v.id}`, v.body)).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["admin", "users"] }); toast.success("User updated"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const create = useMutation({
    mutationFn: async () => (await api.post<{ temporary_password: string | null }>("/admin/users", { ...f!, manager_id: f!.manager_id || null, partner_id: f!.partner_id || null })).data,
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["admin", "users"] }); setF(null); toast.success(`User invited. Temporary password: ${r.temporary_password}`, { duration: 20000 }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader title="Users" description="Role determines CRUD + export rights; manager lines determine which team records a Sales Manager sees."
        action={<Button size="sm" onClick={() => setF({ email: "", full_name: "", role: "account_executive", manager_id: "", partner_id: "" })}><Plus className="h-3.5 w-3.5" />Invite user</Button>} />
      {!users.data ? <Skeleton className="m-5 h-40" /> : (
        <Table head={["Name", "Role", "Reports to", "Status", "Added", ""]} minWidth={820}>
          {users.data.map((u) => (
            <tr key={u.id}>
              <Td><span className="font-medium">{u.full_name}</span><span className="block text-[12px] text-muted-foreground">{u.email}</span></Td>
              <Td>
                <Select aria-label={`Role for ${u.full_name}`} className="h-8 w-44 text-[13px]" value={u.role} disabled={u.role === "partner"}
                  onChange={(e) => patch.mutate({ id: u.id, body: { role: e.target.value } })}>
                  {Object.entries(ROLE_LABELS).filter(([k]) => k !== "partner" || u.role === "partner").map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </Select>
              </Td>
              <Td className="text-[13px]">{u.partner_id ? partners.data?.find((p) => p.id === u.partner_id)?.name ?? "Partner" : u.manager_id ? names.get(u.manager_id) : "—"}</Td>
              <Td><StatusPill status={u.is_active ? "active" : "inactive"} /></Td>
              <Td className="text-[12.5px] text-muted-foreground">{relativeDays(u.created_at)}</Td>
              <Td><Button size="sm" variant="ghost" onClick={() => patch.mutate({ id: u.id, body: { is_active: !u.is_active } })}>{u.is_active ? "Deactivate" : "Reactivate"}</Button></Td>
            </tr>
          ))}
        </Table>
      )}
      <Dialog open={!!f} onOpenChange={(o) => !o && setF(null)}>
        <DialogContent title="Invite user">
          {f && (
            <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
              <h2 className="text-[15px] font-semibold">Invite user</h2>
              <div><Label htmlFor="u-name">Full name</Label><Input id="u-name" required value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></div>
              <div><Label htmlFor="u-email">Email</Label><Input id="u-email" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></div>
              <div className="grid grid-cols-2 gap-3">
                <div><Label htmlFor="u-role">Role</Label><Select id="u-role" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{Object.entries(ROLE_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></div>
                {f.role === "partner" ? (
                  <div><Label htmlFor="u-partner">Partner organisation</Label><Select id="u-partner" required value={f.partner_id} onChange={(e) => setF({ ...f, partner_id: e.target.value })}><option value="">Select…</option>{partners.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select></div>
                ) : (
                  <div><Label htmlFor="u-mgr">Reports to</Label><Select id="u-mgr" value={f.manager_id} onChange={(e) => setF({ ...f, manager_id: e.target.value })}><option value="">—</option>{users.data?.filter((u) => ["sales_manager", "super_admin"].includes(u.role)).map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}</Select></div>
                )}
              </div>
              <div className="flex justify-end"><Button type="submit" size="sm" loading={create.isPending}>Create user</Button></div>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </Card>
  );
}

export function RbacPanel() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["admin", "permissions"], queryFn: () => get<Matrix>("/admin/permissions") });
  const [role, setRole] = useState("account_executive");
  const [draft, setDraft] = useState<Record<string, Cell> | null>(null);
  useEffect(() => { if (data) setDraft(structuredClone(data.matrix[role])); }, [data, role]);
  const save = useMutation({
    mutationFn: async () => (await api.put("/admin/permissions", Object.entries(draft!).map(([resource, c]) => ({
      role, resource, can_create: c.create, can_read: c.read, can_update: c.update, can_delete: c.delete, can_export: c.export, scope: c.scope,
    })))).data,
    onSuccess: () => { qc.invalidateQueries(); toast.success(`${ROLE_LABELS[role]} permissions saved`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (!data || !draft) return <Skeleton className="h-80 w-full" />;
  const dirty = JSON.stringify(draft) !== JSON.stringify(data.matrix[role]);
  return (
    <Card>
      <CardHeader title="Roles & permissions" description="CRUD + Export per resource. Scope “own” limits rows to records the user owns (or their team's, for managers)."
        action={<div className="flex items-center gap-2">
          <Select aria-label="Role" className="h-8 w-auto" value={role} onChange={(e) => setRole(e.target.value)}>{data.roles.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}</Select>
          <Button size="sm" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate()}><Save className="h-3.5 w-3.5" />Save</Button>
        </div>} />
      <Table head={["Resource", ...data.actions.map((a) => a[0].toUpperCase() + a.slice(1)), "Row scope"]} minWidth={720}>
        {data.resources.map((res) => (
          <tr key={res}>
            <Td className="font-medium capitalize">{res}</Td>
            {data.actions.map((a) => (
              <Td key={a}>
                <input type="checkbox" aria-label={`${res} ${a}`} className="h-4 w-4 accent-[hsl(var(--primary))]" checked={draft[res][a]}
                  onChange={(e) => setDraft({ ...draft, [res]: { ...draft[res], [a]: e.target.checked } })} />
              </Td>
            ))}
            <Td>
              <Select aria-label={`${res} scope`} className="h-7 w-24 text-[12.5px]" value={draft[res].scope} onChange={(e) => setDraft({ ...draft, [res]: { ...draft[res], scope: e.target.value as "all" | "own" } })}>
                <option value="all">All</option><option value="own">Own</option>
              </Select>
            </Td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}
