"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ChevronDown, ChevronUp, Columns3, Pencil, Plus, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { type BulkEntity, BulkBar, RowBox, SelectAllBox, useSelection } from "@/components/bulk";
import { type CatalogueField, FilterRow, nice, NO_VALUE } from "@/components/filters";
import { type Filter, fmtValue, type RResult } from "@/components/reportviz";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { StatusPill, Table, Td } from "@/components/ui/extra";
import { Input, Label, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { cn } from "@/lib/utils";

/* Saved list views: a named set of columns, filters and sort over a list, private or shared with the team.
   Views run on the report engine, so every report field (custom fields included) can be a column or filter,
   and row-level access always applies. Columns the server marks as inline-editable (owner, status, priority,
   tier) can be changed in place; the change goes through the same bulk endpoint as a one-record selection. */

export type ViewSource = BulkEntity | "deals" | `obj_${string}`;
const BULK: string[] = ["leads", "accounts", "contacts", "cases"];
export interface ListView {
  id: string; source: ViewSource; name: string; visibility: "private" | "shared"; columns: string[]; filters: Filter[];
  sort: { by?: string | null; dir: "asc" | "desc" }; owner: string | null; can_edit: boolean;
}
interface InlineSpec { action: string; kind?: "user"; options?: string[] }
interface ViewsMeta {
  views: ListView[]; default_columns: string[]; inline: Record<string, InlineSpec>; can_share: boolean; link: string | null;
  fields: CatalogueField[]; periods: string[];
}
interface ViewResult extends RResult { ids?: string[]; link?: string | null }
type Sort = { by: string; dir: "asc" | "desc" } | null;

const MAX_COLUMNS = 12;
const ROW_LIMIT = 500;
const storeKey = (s: string) => `cirra.listview.${s}`;
const readStored = (s: string) => { try { return window.localStorage.getItem(storeKey(s)) ?? ""; } catch { return ""; } };
const writeStored = (s: string, v: string) => { try { if (v) window.localStorage.setItem(storeKey(s), v); else window.localStorage.removeItem(storeKey(s)); } catch { /* private mode */ } };

/** Drop filters the user hasn't finished filling in, so saving never fails on them. */
const finished = (fs: Filter[]) => fs.filter((f) => NO_VALUE.includes(f.op) || (Array.isArray(f.value) ? f.value.every((v) => v !== "") : f.value !== "" && f.value != null));

/** The views of one list and which one is showing (remembered per browser; "" is the standard list). Lists
    without a hand-built page (custom objects) pass ``standard`` to get a default view of the source's columns. */
export function useListView(source: ViewSource, standard = false) {
  const meta = useQuery({ queryKey: ["views", source], queryFn: () => get<ViewsMeta>("/views", { source }) });
  const [viewId, setViewId] = useState("");
  useEffect(() => { setViewId(readStored(source)); }, [source]);
  const view = meta.data?.views.find((v) => v.id === viewId)
    ?? (standard && meta.data ? { id: "", source, name: "Standard list", visibility: "shared" as const, columns: meta.data.default_columns, filters: [],
      sort: { by: null, dir: "asc" as const }, owner: null, can_edit: false } : null);
  const select = (id: string) => { setViewId(id); writeStored(source, id); };
  return { source, meta: meta.data, view, select };
}
export type ListViewState = ReturnType<typeof useListView>;

/** View switcher with New / Edit; sits in a list page's toolbar. */
export function ListViewPicker({ lv, className }: { lv: ListViewState; className?: string }) {
  const [editing, setEditing] = useState<null | "new" | "edit">(null);
  if (!lv.meta) return null;
  const mine = lv.meta.views.filter((v) => v.can_edit || v.visibility === "private");
  const team = lv.meta.views.filter((v) => !mine.includes(v));
  return (
    <div className={cn("flex items-center gap-1.5", className)}>
      <Columns3 className="h-3.5 w-3.5 text-subtle" aria-hidden />
      <Select aria-label="List view" className="h-8 w-52 text-[13px]" value={lv.view?.id ?? ""} onChange={(e) => lv.select(e.target.value)}>
        <option value="">Standard list</option>
        {mine.length > 0 && <optgroup label="My views">{mine.map((v) => <option key={v.id} value={v.id}>{v.name}{v.visibility === "shared" ? " (shared)" : ""}</option>)}</optgroup>}
        {team.length > 0 && <optgroup label="Team views">{team.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</optgroup>}
      </Select>
      {lv.view?.can_edit && lv.view.id && <Button size="sm" variant="ghost" onClick={() => setEditing("edit")}><Pencil className="h-3.5 w-3.5" />Edit view</Button>}
      <Button size="sm" variant="outline" onClick={() => setEditing("new")}><Plus className="h-3.5 w-3.5" />New view</Button>
      {editing && <ViewEditor lv={lv} view={editing === "edit" ? lv.view : null} onClose={() => setEditing(null)} />}
    </div>
  );
}

/** The selected view as a table: sortable headers, selection + bulk actions, in-place edits. */
export function ViewGrid({ lv }: { lv: ListViewState }) {
  const view = lv.view!;
  const meta = lv.meta!;
  const [sort, setSort] = useState<Sort>(null);
  useEffect(() => { setSort(null); }, [view.id]);
  const active: Sort = sort ?? (view.sort?.by ? { by: view.sort.by, dir: view.sort.dir } : null);
  const run = useQuery({
    // keyed under the source so bulk actions and in-place edits refresh it with the standard list
    queryKey: [lv.source, "view", view.id, view.columns, view.filters, active],
    placeholderData: keepPreviousData, retry: false,
    queryFn: async () => (await api.post<ViewResult>("/views/run", { source: lv.source, columns: view.columns, filters: view.filters,
      sort: active ?? { by: null, dir: "asc" }, limit: ROW_LIMIT })).data,
  });
  const r = run.data;
  const ids = r?.ids ?? [];
  const sel = useSelection(ids);
  const bulkEntity = BULK.includes(lv.source) ? (lv.source as BulkEntity) : null;
  const selectable = !!bulkEntity;  // the bulk bar itself hides when the role has no bulk action here
  const needUsers = Object.values(meta.inline).some((s) => s.kind === "user");
  const users = useQuery({ queryKey: ["users"], queryFn: () => get<{ id: string; full_name: string; role: string; is_active?: boolean }[]>("/users"), enabled: needUsers });
  const numeric = (t: string) => t === "money" || t === "number";
  const toggleSort = (key: string) => setSort(active?.by === key ? { by: key, dir: active.dir === "asc" ? "desc" : "asc" } : { by: key, dir: "asc" });

  return (
    <div>
      {bulkEntity && <BulkBar entity={bulkEntity} sel={sel} />}
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2 text-[12.5px] text-muted-foreground">
          <span>
            {r ? `${r.row_count.toLocaleString()} ${r.source_label.toLowerCase()}${r.truncated ? ` (first ${ROW_LIMIT}; add filters to narrow)` : ""}` : "Loading…"}
            {run.isFetching && r ? " · updating…" : ""}
          </span>
          <span className="flex flex-wrap items-center gap-1.5">
            {view.filters.length > 0 && <span>{view.filters.length} filter{view.filters.length > 1 ? "s" : ""}</span>}
            {view.id && <Badge tone={view.visibility === "shared" ? "primary" : "neutral"}>{view.visibility === "shared" ? "Shared with team" : "Only you"}</Badge>}
            {view.owner && !view.can_edit && <span>by {view.owner}</span>}
          </span>
        </div>
        {run.isError ? <p className="p-4 text-sm text-destructive">{errorMessage(run.error)}</p> : !r ? <Skeleton className="m-4 h-40" /> : !r.rows.length ? (
          <p className="py-10 text-center text-sm text-muted-foreground">No records match this view.</p>
        ) : (
          <Table minWidth={Math.max(640, r.columns.length * 140)} head={[
            ...(selectable ? [<SelectAllBox key="all" sel={sel} label={`Select all ${r.source_label.toLowerCase()}`} />] : []),
            ...r.columns.map((c) => (
              <button key={c.key} type="button" onClick={() => toggleSort(c.key)} aria-label={`Sort by ${c.label}`}
                className={cn("inline-flex items-center gap-1 hover:text-foreground", numeric(c.type) && "ml-auto")}>
                {c.label}
                {active?.by === c.key && (active.dir === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
              </button>
            ))]}>
            {r.rows.map((row, k) => {
              const id = ids[k];
              const href = r.link && id ? r.link.replace("{id}", id) : null;
              return (
                <tr key={id ?? k} className={id && sel.has(id) ? "bg-primary-soft/40" : "hover:bg-muted/50"}>
                  {selectable && <Td className="w-8">{id && <RowBox sel={sel} id={id} label={String(row[0] ?? "record")} />}</Td>}
                  {r.columns.map((c, j) => {
                    const spec = meta.inline[c.key];
                    const shown = c.key === "status" && row[j] ? <StatusPill status={String(row[j])} /> : fmtValue(row[j], c);
                    return (
                      <Td key={c.key} className={cn("text-[13px]", numeric(c.type) && "tabular text-right")}>
                        {j === 0 && href ? <Link href={href} className="font-medium hover:text-primary hover:underline">{fmtValue(row[j], c)}</Link>
                          : spec && id && bulkEntity ? <InlineCell entity={bulkEntity} id={id} label={c.label} spec={spec} value={row[j]} shown={shown}
                            users={users.data?.filter((u) => !["partner", "auditor"].includes(u.role) && u.is_active !== false)} />
                          : shown}
                      </Td>
                    );
                  })}
                </tr>
              );
            })}
          </Table>
        )}
      </Card>
    </div>
  );
}

function InlineCell({ entity, id, label, spec, value, shown, users }: {
  entity: BulkEntity; id: string; label: string; spec: InlineSpec; value: unknown; shown: React.ReactNode;
  users?: { id: string; full_name: string }[];
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const options = spec.kind === "user" ? (users ?? []).map((u) => ({ value: u.id, label: u.full_name })) : (spec.options ?? []).map((o) => ({ value: o, label: nice(o) }));
  const current = spec.kind === "user" ? options.find((o) => o.label === value)?.value ?? "" : String(value ?? "");
  const save = useMutation({
    mutationFn: async (v: string) => (await api.post<{ changed: number; matched: number }>(`/bulk/${entity}`,
      { ids: [id], action: spec.action, value: v, note: spec.action === "set_status" && v === "disqualified" ? "other" : null })).data,
    onSuccess: (res) => {
      if (res.changed) toast.success(`${label} updated`);
      else toast.message(res.matched ? `${label} can't change for this record` : "This record isn't yours to change");
      qc.invalidateQueries({ queryKey: [entity] });
      setEditing(false);
    },
    onError: (e) => { toast.error(errorMessage(e)); setEditing(false); },
  });
  if (editing) {
    return (
      <Select autoFocus aria-label={`New ${label.toLowerCase()}`} className="h-7 min-w-[8rem] text-[12.5px]" value={current} disabled={save.isPending}
        onChange={(e) => e.target.value && e.target.value !== current ? save.mutate(e.target.value) : setEditing(false)}
        onBlur={() => !save.isPending && setEditing(false)} onKeyDown={(e) => e.key === "Escape" && setEditing(false)}>
        {!current && <option value="">Choose…</option>}
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </Select>
    );
  }
  return (
    <span className="group inline-flex items-center gap-1">
      {shown || <span className="text-subtle">—</span>}
      <button type="button" aria-label={`Edit ${label.toLowerCase()}`} onClick={() => setEditing(true)}
        className="rounded p-0.5 text-subtle opacity-0 hover:bg-muted hover:text-foreground focus:opacity-100 group-hover:opacity-100">
        <Pencil className="h-3 w-3" />
      </button>
    </span>
  );
}

function ViewEditor({ lv, view, onClose }: { lv: ListViewState; view: ListView | null; onClose: () => void }) {
  const meta = lv.meta!;
  const qc = useQueryClient();
  const [name, setName] = useState(view?.name ?? "");
  const [visibility, setVisibility] = useState<"private" | "shared">(view?.visibility ?? "private");
  const [columns, setColumns] = useState<string[]>(view?.columns ?? meta.default_columns);
  const [filters, setFilters] = useState<Filter[]>(view?.filters ?? []);
  const [sortBy, setSortBy] = useState(view?.sort?.by ?? "");
  const [dir, setDir] = useState<"asc" | "desc">(view?.sort?.dir ?? "asc");
  const field = (k: string) => meta.fields.find((f) => f.key === k);
  const body = () => ({ source: lv.source, name: name.trim(), visibility, columns, filters: finished(filters), sort: { by: sortBy || null, dir } });
  const done = (v: ListView, msg: string) => { qc.invalidateQueries({ queryKey: ["views", lv.source] }); lv.select(v.id); toast.success(msg); onClose(); };
  const create = useMutation({ mutationFn: async () => (await api.post<ListView>("/views", body())).data, onSuccess: (v) => done(v, "View saved"), onError: (e) => toast.error(errorMessage(e)) });
  const update = useMutation({ mutationFn: async () => (await api.put<ListView>(`/views/${view!.id}`, body())).data, onSuccess: (v) => done(v, "View updated"), onError: (e) => toast.error(errorMessage(e)) });
  const del = useMutation({
    mutationFn: async () => api.delete(`/views/${view!.id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["views", lv.source] }); lv.select(""); toast.success("View deleted"); onClose(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const move = (i: number, d: -1 | 1) => { const c = [...columns]; [c[i], c[i + d]] = [c[i + d], c[i]]; setColumns(c); };
  const busy = create.isPending || update.isPending || del.isPending;

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={view ? "Edit view" : "New view"} className="max-w-2xl">
        <form className="max-h-[85vh] space-y-5 overflow-y-auto p-5" onSubmit={(e) => { e.preventDefault(); if (view) update.mutate(); else create.mutate(); }}>
          <div>
            <h2 className="text-[15px] font-semibold">{view ? `Edit “${view.name}”` : "New list view"}</h2>
            <p className="mt-0.5 text-[13px] text-muted-foreground">Pick the columns, filters and sort you want to see. Views only ever show records you can access.</p>
          </div>
          <div className="grid gap-3 sm:grid-cols-[1fr_12rem]">
            <div><Label htmlFor="lv-name">Name</Label><Input id="lv-name" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. My hot leads" /></div>
            <div>
              <Label htmlFor="lv-vis">Who can use it</Label>
              <Select id="lv-vis" value={visibility} onChange={(e) => setVisibility(e.target.value as "private" | "shared")} disabled={!meta.can_share && visibility !== "shared"}>
                <option value="private">Only me</option><option value="shared">Whole team</option>
              </Select>
            </div>
          </div>

          <section>
            <p className="mb-1.5 text-[13px] font-medium">Columns <span className="font-normal text-subtle">({columns.length}/{MAX_COLUMNS}; the first one links to the record)</span></p>
            <ol className="space-y-1">
              {columns.map((c, i) => (
                <li key={c} className="flex items-center gap-1 rounded-md border px-2 py-1 text-[13px]">
                  <span className="flex-1">{field(c)?.label ?? c}</span>
                  <Button type="button" size="icon" variant="ghost" className="h-7 w-7" aria-label={`Move ${field(c)?.label} up`} disabled={i === 0} onClick={() => move(i, -1)}><ChevronUp className="h-3.5 w-3.5" /></Button>
                  <Button type="button" size="icon" variant="ghost" className="h-7 w-7" aria-label={`Move ${field(c)?.label} down`} disabled={i === columns.length - 1} onClick={() => move(i, 1)}><ChevronDown className="h-3.5 w-3.5" /></Button>
                  <Button type="button" size="icon" variant="ghost" className="h-7 w-7" aria-label={`Remove ${field(c)?.label}`} disabled={columns.length === 1} onClick={() => setColumns(columns.filter((x) => x !== c))}><X className="h-3.5 w-3.5" /></Button>
                </li>
              ))}
            </ol>
            {columns.length < MAX_COLUMNS && (
              <Select aria-label="Add column" className="mt-1.5" value="" onChange={(e) => e.target.value && setColumns([...columns, e.target.value])}>
                <option value="">Add a column…</option>
                {meta.fields.filter((f) => !columns.includes(f.key)).map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
              </Select>
            )}
          </section>

          <section className="space-y-1.5">
            <p className="text-[13px] font-medium">Filters <span className="font-normal text-subtle">(all must match)</span></p>
            {filters.map((f, k) => <FilterRow key={k} f={f} src={meta} periods={meta.periods}
              onChange={(nf) => { const fs = [...filters]; fs[k] = nf; setFilters(fs); }} onRemove={() => setFilters(filters.filter((_, j) => j !== k))} />)}
            <Button type="button" size="sm" variant="ghost" onClick={() => { const f = meta.fields[0]; setFilters([...filters, { field: f.key, op: f.ops[0], value: "" }]); }}>
              <Plus className="h-3.5 w-3.5" />Add filter
            </Button>
          </section>

          <section>
            <p className="mb-1.5 text-[13px] font-medium">Sort</p>
            <div className="flex gap-1.5">
              <Select aria-label="Sort by" className="min-w-0 flex-1" value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
                <option value="">Default order</option>
                {meta.fields.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
              </Select>
              <Select aria-label="Sort direction" className="w-48" value={dir} disabled={!sortBy} onChange={(e) => setDir(e.target.value as "asc" | "desc")}>
                <option value="asc">A → Z / low → high</option><option value="desc">Z → A / high → low</option>
              </Select>
            </div>
          </section>

          <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-4">
            <div>{view && <Button type="button" size="sm" variant="ghost" loading={del.isPending} disabled={busy} onClick={() => del.mutate()}><Trash2 className="h-3.5 w-3.5" />Delete view</Button>}</div>
            <div className="flex gap-2">
              {view && <Button type="button" size="sm" variant="outline" disabled={busy || !name.trim()} onClick={() => create.mutate()}>Save as new view</Button>}
              <Button type="submit" size="sm" loading={create.isPending || update.isPending} disabled={busy || !name.trim() || !columns.length}>{view ? "Save changes" : "Save view"}</Button>
            </div>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
