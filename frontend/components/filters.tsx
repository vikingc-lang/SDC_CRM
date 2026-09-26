"use client";

import { X } from "lucide-react";
import type { Filter } from "@/components/reportviz";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";

/* One condition row (field · operator · value) over a typed field catalogue. Shared by the report
   builder's filters and workflow conditions, which use the same server-side filter syntax. */

export interface CatalogueField { key: string; label: string; type: string; options: string[] | null; ops: string[] }

export const OP_LABELS: Record<string, string> = {
  eq: "is", neq: "is not", in: "is any of", not_in: "is none of", contains: "contains", is_empty: "is empty", not_empty: "is not empty",
  gt: ">", gte: "≥", lt: "<", lte: "≤", between: "between", on: "on", before: "before", after: "after", within: "in period",
  is_true: "is yes", is_false: "is no",
};
export const nice = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
export const NO_VALUE = ["is_empty", "not_empty", "is_true", "is_false"];

export function IconX({ label, onClick }: { label: string; onClick: () => void }) {
  return <Button type="button" variant="ghost" size="icon" className="h-9 w-9 shrink-0" aria-label={label} onClick={onClick}><X className="h-3.5 w-3.5" /></Button>;
}

export function FilterRow({ f, src, periods, onChange, onRemove }: { f: Filter; src: { fields: CatalogueField[] }; periods: string[]; onChange: (f: Filter) => void; onRemove: () => void }) {
  const fd = src.fields.find((x) => x.key === f.field)!;
  const val = (v: unknown) => onChange({ ...f, value: v });
  const pair = Array.isArray(f.value) ? (f.value as string[]) : ["", ""];
  let input: React.ReactNode = null;
  if (!NO_VALUE.includes(f.op)) {
    if (f.op === "within") {
      input = <Select aria-label="Period" value={String(f.value ?? "")} onChange={(e) => val(e.target.value)}>{periods.map((p) => <option key={p} value={p}>{nice(p)}</option>)}</Select>;
    } else if (f.op === "between") {
      const t = fd.type === "date" ? "date" : "number";
      input = <div className="flex gap-1.5"><Input aria-label="From" type={t} value={pair[0]} onChange={(e) => val([e.target.value, pair[1]])} /><Input aria-label="To" type={t} value={pair[1]} onChange={(e) => val([pair[0], e.target.value])} /></div>;
    } else if (fd.options && (f.op === "eq" || f.op === "neq")) {
      input = <Select aria-label="Value" value={String(f.value ?? "")} onChange={(e) => val(e.target.value)}><option value="">Choose…</option>{fd.options.map((o) => <option key={o} value={o}>{nice(o)}</option>)}</Select>;
    } else {
      const t = fd.type === "date" ? "date" : fd.type === "number" || fd.type === "money" ? "number" : "text";
      input = <Input aria-label="Value" type={t} placeholder={f.op === "in" || f.op === "not_in" ? "Comma-separated" : ""} value={String(f.value ?? "")} onChange={(e) => val(e.target.value)} />;
    }
  }
  return (
    <div className="space-y-1.5 rounded-md border p-2">
      <div className="flex gap-1.5">
        <Select aria-label="Filter field" className="min-w-0 flex-1" value={f.field} onChange={(e) => { const nf = src.fields.find((x) => x.key === e.target.value)!; onChange({ field: nf.key, op: nf.ops[0], value: nf.ops[0] === "within" ? periods[0] : "" }); }}>
          {src.fields.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
        </Select>
        <IconX label="Remove filter" onClick={onRemove} />
      </div>
      <Select aria-label="Condition" value={f.op} onChange={(e) => onChange({ ...f, op: e.target.value, value: e.target.value === "within" ? periods[0] : e.target.value === "between" ? ["", ""] : "" })}>
        {fd.ops.map((o) => <option key={o} value={o}>{OP_LABELS[o] ?? o}</option>)}
      </Select>
      {input}
    </div>
  );
}

