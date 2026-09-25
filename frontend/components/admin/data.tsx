"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { CheckCircle2, Download, FileUp, RotateCcw, Upload } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Table, Td } from "@/components/ui/extra";
import { Input, Label, Select } from "@/components/ui/input";
import { api, downloadFile, errorMessage } from "@/lib/api";

type Entity = "accounts" | "contacts" | "deals" | "products";
interface Preview {
  entity: Entity; headers: string[]; mapping: Record<string, string | null>; rows_total: number; rows_valid: number;
  fields: Record<string, { required: boolean; type: string; options: string[] | null }>;
  errors: { row: number | null; errors: string[] }[]; sample: Record<string, string>[];
}

export function DataPanel() {
  const qc = useQueryClient();
  const [entity, setEntity] = useState<Entity>("accounts");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [result, setResult] = useState<{ created: number; updated: number; rows: number } | null>(null);
  const [failure, setFailure] = useState<Preview["errors"] | null>(null);
  const form = () => { const fd = new FormData(); fd.append("file", file!); if (Object.keys(mapping).length) fd.append("mapping", JSON.stringify(mapping)); return fd; };
  const doPreview = useMutation({
    mutationFn: async () => (await api.post<Preview>(`/admin/import/${entity}/preview`, form())).data,
    onSuccess: (p) => { setPreview(p); setMapping(p.mapping); setResult(null); setFailure(null); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const commit = useMutation({
    mutationFn: async () => (await api.post(`/admin/import/${entity}/commit`, form())).data,
    onSuccess: (r) => { setResult(r); setFailure(null); qc.invalidateQueries(); toast.success(`Imported ${r.rows} ${entity}`); },
    onError: (e) => {
      const detail = isAxiosError(e) ? (e.response?.data as { detail?: { errors?: Preview["errors"] } })?.detail : undefined;
      if (detail && typeof detail === "object" && detail.errors) { setFailure(detail.errors); toast.error("Import rolled back: nothing was written"); } else toast.error(errorMessage(e));
    },
  });
  const fields = preview ? Object.entries(preview.fields) : [];
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader icon={<Upload className="h-4 w-4" />} title="Import" description="CSV or JSON. Columns are auto-mapped by name and synonyms; every row is validated first and the import is all-or-nothing, so a failure rolls back completely." />
        <div className="flex flex-wrap items-end gap-2 px-5 pb-5">
          <div><Label htmlFor="imp-e">Record type</Label>
            <Select id="imp-e" value={entity} onChange={(e) => { setEntity(e.target.value as Entity); setPreview(null); setMapping({}); }}>{["accounts", "contacts", "deals", "products"].map((x) => <option key={x}>{x}</option>)}</Select>
          </div>
          <div><Label htmlFor="imp-f">File</Label><Input id="imp-f" type="file" accept=".csv,.json" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPreview(null); setMapping({}); }} /></div>
          <Button size="sm" variant="outline" disabled={!file} loading={doPreview.isPending} onClick={() => doPreview.mutate()}><FileUp className="h-3.5 w-3.5" />Validate & map</Button>
        </div>
        {preview && (
          <div className="border-t px-5 py-4">
            <p className="mb-3 text-[13px]"><span className="font-medium">{preview.rows_valid} of {preview.rows_total}</span> rows valid{preview.errors.length ? `, ${preview.errors.length} with errors` : ""}.</p>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {fields.map(([k, meta]) => (
                <div key={k} className="flex items-center gap-2 text-[13px]">
                  <span className="w-36 shrink-0 truncate">{k}{meta.required && <span className="text-[color:var(--status-critical)]"> *</span>}</span>
                  <Select aria-label={`Column for ${k}`} className="h-8" value={mapping[k] ?? ""} onChange={(e) => setMapping({ ...mapping, [k]: e.target.value || null })}>
                    <option value="">(not mapped)</option>{preview.headers.map((h) => <option key={h}>{h}</option>)}
                  </Select>
                </div>
              ))}
            </div>
            {preview.errors.length > 0 && (
              <ul className="mt-3 max-h-40 overflow-auto rounded-md border p-2 text-[12px]">
                {preview.errors.map((e, i) => <li key={i}><span className="font-medium">Row {e.row}:</span> {e.errors.join("; ")}</li>)}
              </ul>
            )}
            <div className="mt-3 flex gap-2">
              <Button size="sm" variant="outline" loading={doPreview.isPending} onClick={() => doPreview.mutate()}>Re-validate with mapping</Button>
              <Button size="sm" disabled={preview.errors.length > 0} loading={commit.isPending} onClick={() => commit.mutate()}>Import {preview.rows_valid} rows</Button>
            </div>
            {result && <p className="mt-3 flex items-center gap-1.5 text-[13px] text-[color:var(--status-good)]"><CheckCircle2 className="h-4 w-4" />{result.created} created, {result.updated} updated</p>}
            {failure && (
              <div className="mt-3 rounded-md border border-[color:var(--status-critical)] p-2 text-[12px]">
                <p className="flex items-center gap-1.5 font-medium"><RotateCcw className="h-3.5 w-3.5" />Rolled back. No rows were written.</p>
                <ul>{failure.map((e, i) => <li key={i}>{e.row !== null && `Row ${e.row}: `}{e.errors.join("; ")}</li>)}</ul>
              </div>
            )}
          </div>
        )}
      </Card>
      <Card>
        <CardHeader icon={<Download className="h-4 w-4" />} title="Export" description="Exports respect your role's Export permission and row-level scope, and are written to the audit trail." />
        <Table head={["Record type", "CSV", "JSON"]} minWidth={400}>
          {(["accounts", "contacts", "deals", "products"] as Entity[]).map((e) => (
            <tr key={e}>
              <Td className="capitalize">{e}</Td>
              {(["csv", "json"] as const).map((fmt) => (
                <Td key={fmt}><Button size="sm" variant="ghost" onClick={() => downloadFile(`/admin/export/${e}`, `${e}.${fmt}`, { format: fmt }).catch((err) => toast.error(errorMessage(err)))}><Download className="h-3.5 w-3.5" />{fmt.toUpperCase()}</Button></Td>
              ))}
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
