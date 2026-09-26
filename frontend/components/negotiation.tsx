"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, GitCompare, MessageSquare, PencilLine } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { api, errorMessage, get } from "@/lib/api";
import type { DocumentSummary } from "@/lib/types";
import { cn, relativeDays } from "@/lib/utils";

interface Negotiation {
  current_version: number; status: string; open_comments: number;
  versions: { id: string; version: number; note: string | null; source: "internal" | "customer"; author_name: string | null; created_at: string }[];
  comments: { id: string; version: number; clause: string | null; body: string; party: "customer" | "company"; author_name: string; resolved: boolean; created_at: string }[];
}
interface Diff { from: number; to: number; stats: { inserted: number; deleted: number }; lines: { op: "equal" | "insert" | "delete"; text: string }[] }

/** Redlining workspace: numbered versions, line-level diff, clause comments from both parties. */
export function NegotiationPanel({ doc, canEdit }: { doc: DocumentSummary; canEdit: boolean }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["negotiation", doc.id], queryFn: () => get<Negotiation>(`/documents/${doc.id}/negotiation`) });
  const [from, setFrom] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState("");
  const [note, setNote] = useState("");
  const [comment, setComment] = useState({ clause: "", body: "" });
  const base = from ?? (data && data.current_version > 1 ? data.current_version - 1 : null);
  const { data: diff } = useQuery({ queryKey: ["diff", doc.id, base, data?.current_version], enabled: !!base,
    queryFn: () => get<Diff>(`/documents/${doc.id}/diff`, { from_version: base }) });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["negotiation", doc.id] }); qc.invalidateQueries({ queryKey: ["document", doc.id] }); };
  const revise = useMutation({
    mutationFn: async () => (await api.post(`/documents/${doc.id}/versions`, { body, note: note || null })).data,
    onSuccess: () => { refresh(); setEditing(false); setNote(""); toast.success("New version saved; outstanding signatures were voided"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const add = useMutation({
    mutationFn: async () => (await api.post(`/documents/${doc.id}/comments`, { clause: comment.clause || null, body: comment.body })).data,
    onSuccess: () => { refresh(); setComment({ clause: "", body: "" }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const resolve = useMutation({
    mutationFn: async (cid: string) => (await api.post(`/documents/${doc.id}/comments/${cid}/resolve`)).data,
    onSuccess: refresh,
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (!data) return null;
  const open = !["completed", "voided"].includes(doc.status);

  return (
    <Card>
      <CardHeader title="Negotiation" icon={<GitCompare className="h-4 w-4 text-muted-foreground" />}
        description={`Version ${data.current_version} · ${data.open_comments} open comment${data.open_comments === 1 ? "" : "s"}`}
        action={canEdit && open && !editing ? <Button variant="outline" size="sm" onClick={() => { setBody(doc.body ?? ""); setEditing(true); }}><PencilLine className="h-3.5 w-3.5" />Revise</Button> : undefined} />
      <CardBody className="space-y-4">
        {editing && (
          <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); revise.mutate(); }}>
            <Textarea className="min-h-[320px] font-mono text-[12px]" value={body} onChange={(e) => setBody(e.target.value)} />
            <Input placeholder="What changed and why (shown in the audit trail)" value={note} onChange={(e) => setNote(e.target.value)} />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(false)}>Cancel</Button>
              <Button type="submit" size="sm" loading={revise.isPending}>Save version {data.current_version + 1}</Button>
            </div>
          </form>
        )}

        <ol className="space-y-1.5 text-[13px]">
          {[...data.versions].reverse().map((v) => (
            <li key={v.id} className="flex flex-wrap items-center gap-2">
              <span className="font-medium">v{v.version}</span>
              <Badge tone={v.source === "customer" ? "warning" : "outline"}>{v.source === "customer" ? "customer redline" : "internal"}</Badge>
              <span className="min-w-0 flex-1 truncate text-muted-foreground">{v.note ?? (v.version === 1 ? "Generated from template" : "")}</span>
              <span className="text-[12px] text-subtle">{v.author_name} · {relativeDays(v.created_at)}</span>
            </li>
          ))}
        </ol>

        {data.current_version > 1 && (
          <div>
            <div className="mb-2 flex items-center gap-2 text-[12.5px]">
              <Label className="mb-0">Compare</Label>
              <Select className="h-8 w-24" value={base ?? ""} onChange={(e) => setFrom(Number(e.target.value))}>
                {data.versions.filter((v) => v.version < data.current_version).map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}
              </Select>
              <span className="text-muted-foreground">→ v{data.current_version}</span>
              {diff && <span className="ml-auto text-muted-foreground">+{diff.stats.inserted} / −{diff.stats.deleted} lines</span>}
            </div>
            <div className="max-h-72 overflow-auto rounded-md border font-mono text-[11.5px] leading-5">
              {diff?.lines.filter((l, i, all) => l.op !== "equal" || all[i - 1]?.op !== "equal" || all[i + 1]?.op !== "equal").map((l, i) => (
                <div key={i} className={cn("whitespace-pre-wrap px-2", l.op === "insert" && "bg-[color-mix(in_srgb,var(--status-good)_14%,transparent)]",
                  l.op === "delete" && "bg-[color-mix(in_srgb,var(--status-critical)_12%,transparent)] line-through")}>
                  {l.op === "insert" ? "+ " : l.op === "delete" ? "− " : "  "}{l.text || " "}
                </div>
              ))}
            </div>
          </div>
        )}

        <div>
          <p className="mb-2 flex items-center gap-1.5 text-[12px] font-medium text-muted-foreground"><MessageSquare className="h-3.5 w-3.5" />Clause comments</p>
          {data.comments.length === 0 && <p className="text-[13px] text-subtle">No comments. Customers can also comment from their signing link.</p>}
          <ul className="space-y-2">
            {data.comments.map((c) => (
              <li key={c.id} className={cn("rounded-md border p-2.5 text-[13px]", c.resolved && "opacity-60")}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{c.author_name}</span>
                  <Badge tone={c.party === "customer" ? "warning" : "outline"}>{c.party}</Badge>
                  {c.clause && <span className="text-[12px] text-muted-foreground">{c.clause}</span>}
                  <span className="ml-auto text-[11.5px] text-subtle">v{c.version} · {relativeDays(c.created_at)}</span>
                </div>
                <p className="mt-1">{c.body}</p>
                {!c.resolved && canEdit && open && <Button variant="ghost" size="sm" className="mt-1" onClick={() => resolve.mutate(c.id)}><Check className="h-3.5 w-3.5" />Resolve</Button>}
              </li>
            ))}
          </ul>
          {canEdit && open && (
            <form className="mt-3 space-y-2" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
              <Input placeholder="Clause (optional), e.g. 6. Limitation of liability" value={comment.clause} onChange={(e) => setComment({ ...comment, clause: e.target.value })} />
              <Textarea className="min-h-[60px]" placeholder="Internal comment for legal / deal desk" value={comment.body} onChange={(e) => setComment({ ...comment, body: e.target.value })} />
              <div className="flex justify-end"><Button type="submit" variant="outline" size="sm" disabled={comment.body.trim().length < 3} loading={add.isPending}>Comment</Button></div>
            </form>
          )}
        </div>
      </CardBody>
    </Card>
  );
}
