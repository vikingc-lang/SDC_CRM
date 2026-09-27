"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckSquare, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/input";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";

export type BulkEntity = "leads" | "accounts" | "contacts" | "cases";

/** Row selection that forgets rows which disappear from the list (after a filter change or a bulk edit). */
export function useSelection(ids: string[]) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const key = ids.join(",");
  useEffect(() => { setSelected((s) => new Set([...s].filter((id) => ids.includes(id)))); }, [key]);  // eslint-disable-line react-hooks/exhaustive-deps
  return useMemo(() => ({
    selected, count: selected.size, all: ids.length > 0 && ids.every((id) => selected.has(id)),
    has: (id: string) => selected.has(id),
    toggle: (id: string) => setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; }),
    toggleAll: () => setSelected((s) => (ids.every((id) => s.has(id)) ? new Set() : new Set(ids))),
    clear: () => setSelected(new Set()),
  }), [selected, key]);  // eslint-disable-line react-hooks/exhaustive-deps
}
export type Selection = ReturnType<typeof useSelection>;

export function SelectAllBox({ sel, label = "Select all" }: { sel: Selection; label?: string }) {
  return <input type="checkbox" aria-label={label} checked={sel.all} onChange={sel.toggleAll} className="h-4 w-4 align-middle" />;
}

export function RowBox({ sel, id, label }: { sel: Selection; id: string; label: string }) {
  return <input type="checkbox" aria-label={`Select ${label}`} checked={sel.has(id)} onChange={() => sel.toggle(id)} onClick={(e) => e.stopPropagation()} className="h-4 w-4 align-middle" />;
}

type Opt = { value: string; label: string };
interface ActionDef { key: string; label: string; resource?: string; options: () => Opt[] | undefined; note?: string }

/** Action bar shown while rows are selected: pick an action and a value, apply to every selected record. */
export function BulkBar({ entity, sel }: { entity: BulkEntity; sel: Selection }) {
  const qc = useQueryClient();
  const { can } = useMe();
  const [action, setAction] = useState("");
  const [value, setValue] = useState("");
  const active = sel.count > 0;
  const users = useQuery({ queryKey: ["users"], queryFn: () => get<{ id: string; full_name: string; role: string }[]>("/users"), enabled: active });
  const camps = useQuery({ queryKey: ["campaigns", "list"], queryFn: () => get<{ campaigns: { id: string; name: string; status: string }[] }>("/campaigns"),
    enabled: active && can("campaigns", "update") && (entity === "leads" || entity === "contacts") });
  const meta = useQuery({ queryKey: ["cases", "meta"], queryFn: () => get<{ queues: { id: string; name: string }[] }>("/cases/meta"), enabled: active && entity === "cases" });
  const people = () => users.data?.filter((u) => !["partner", "auditor"].includes(u.role)).map((u) => ({ value: u.id, label: u.full_name }));
  const campaigns = () => camps.data?.campaigns.filter((c) => c.status !== "completed" && c.status !== "aborted").map((c) => ({ value: c.id, label: c.name }));
  const list = (xs: string[]) => () => xs.map((x) => ({ value: x, label: x[0].toUpperCase() + x.slice(1).replace("_", " ") }));
  const ACTIONS: Record<BulkEntity, ActionDef[]> = {
    leads: [{ key: "assign_owner", label: "Assign owner", options: people }, { key: "set_status", label: "Change status", options: list(["working", "recycled", "disqualified"]) },
      { key: "add_to_campaign", label: "Add to campaign", resource: "campaigns", options: campaigns }],
    accounts: [{ key: "assign_owner", label: "Assign owner", options: people }, { key: "set_tier", label: "Change tier", options: () => ["SMB", "Mid-Market", "Enterprise"].map((t) => ({ value: t, label: t })) }],
    contacts: [{ key: "add_to_campaign", label: "Add to campaign", resource: "campaigns", options: campaigns }],
    cases: [{ key: "assign_owner", label: "Assign owner", options: people }, { key: "set_status", label: "Change status", options: list(["open", "pending", "resolved", "closed"]) },
      { key: "set_priority", label: "Change priority", options: list(["critical", "high", "medium", "low"]) },
      { key: "set_queue", label: "Move to queue", options: () => meta.data?.queues.map((q) => ({ value: q.id, label: q.name })) }],
  };
  const available = ACTIONS[entity].filter((a) => can(a.resource ?? entity, "update"));
  const current = available.find((a) => a.key === action);
  const apply = useMutation({
    mutationFn: async () => (await api.post<{ matched: number; changed: number; skipped: number; already?: number }>(`/bulk/${entity}`,
      { ids: [...sel.selected], action, value, note: action === "set_status" && value === "disqualified" ? "other" : null })).data,
    onSuccess: (r) => {
      const parts = [`${r.changed} updated`];
      if (r.already) parts.push(`${r.already} already there`);
      else if (r.matched > r.changed) parts.push(`${r.matched - r.changed} already set`);
      if (r.skipped) parts.push(`${r.skipped} skipped (not yours to change)`);
      toast.success(`${current?.label}: ${parts.join(", ")}`);
      qc.invalidateQueries({ queryKey: [entity] });
      if (action === "add_to_campaign") qc.invalidateQueries({ queryKey: ["campaigns"] });
      sel.clear(); setAction(""); setValue("");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (!active || !available.length) return null;
  return (
    <div role="toolbar" aria-label="Bulk actions" className="sticky top-2 z-20 mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-primary/30 bg-primary-soft px-3 py-2 shadow-card">
      <CheckSquare className="h-4 w-4 text-primary" />
      <span className="text-[13px] font-medium">{sel.count} selected</span>
      <Select aria-label="Bulk action" className="h-8 w-44 text-[13px]" value={action} onChange={(e) => { setAction(e.target.value); setValue(""); }}>
        <option value="">Choose an action…</option>
        {available.map((a) => <option key={a.key} value={a.key}>{a.label}</option>)}
      </Select>
      {current && (
        <Select aria-label="Value" className="h-8 w-48 text-[13px]" value={value} onChange={(e) => setValue(e.target.value)}>
          <option value="">Choose…</option>
          {(current.options() ?? []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </Select>
      )}
      <Button size="sm" disabled={!action || !value} loading={apply.isPending} onClick={() => apply.mutate()}>Apply to {sel.count}</Button>
      <Button size="sm" variant="ghost" onClick={sel.clear}><X className="h-3.5 w-3.5" />Clear</Button>
    </div>
  );
}
