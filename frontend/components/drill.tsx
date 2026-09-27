"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { type Definition, fmtValue, type RResult } from "@/components/reportviz";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Table, Td } from "@/components/ui/extra";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/utils";

export interface DrillRequest { reportId?: string; definition?: Definition; values: unknown[]; label: string; period?: string; owner?: string }
interface DrillResult extends RResult { ids?: string[]; link?: string | null }

/** The records behind one bar, point, segment, matrix cell or summary row, each linked to its record page. */
export function DrillDialog({ request, onClose }: { request: DrillRequest | null; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["analytics", "drill", request], enabled: !!request, retry: false,
    queryFn: async () => {
      const body = { values: request!.values, period: request!.period || null, owner: request!.owner || null, definition: request!.definition ?? null };
      const url = request!.reportId ? `/analytics/reports/${request!.reportId}/drill` : "/analytics/drill";
      return (await api.post<DrillResult>(url, body)).data;
    },
  });
  const r = q.data;
  const numeric = (t: string) => t === "money" || t === "number";
  return (
    <Dialog open={!!request} onOpenChange={(o) => !o && onClose()}>
      <DialogContent title="Records behind this figure" className="max-w-4xl">
        <div className="space-y-3 p-5">
          <div>
            <h2 className="text-[15px] font-semibold">{request?.label || "Records"}</h2>
            <p className="text-[12.5px] text-muted-foreground">
              {r ? `${r.row_count.toLocaleString()} ${r.source_label.toLowerCase()}${r.truncated ? " (first 500)" : ""}` : "Loading…"}
              {r?.link ? " · open any row to see the record" : ""}
            </p>
          </div>
          {q.isError ? <p className="text-sm text-destructive">{errorMessage(q.error)}</p> : !r ? <Skeleton className="h-48" /> : !r.rows.length ? (
            <p className="py-6 text-center text-sm text-muted-foreground">No records.</p>
          ) : (
            <div className="max-h-[60vh] overflow-y-auto">
              <Table head={r.columns.map((c) => <span key={c.key} className={cn(numeric(c.type) && "block text-right")}>{c.label}</span>)} minWidth={Math.max(520, r.columns.length * 130)}>
                {r.rows.map((row, k) => {
                  const href = r.link && r.ids ? r.link.replace("{id}", r.ids[k]) : null;
                  return (
                    <tr key={k} className="hover:bg-muted/50">
                      {r.columns.map((c, j) => (
                        <Td key={c.key} className={cn("text-[13px]", numeric(c.type) && "tabular text-right")}>
                          {j === 0 && href ? <Link href={href} className="font-medium hover:text-primary hover:underline" onClick={onClose}>{fmtValue(row[j], c)}</Link> : fmtValue(row[j], c)}
                        </Td>
                      ))}
                    </tr>
                  );
                })}
              </Table>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
