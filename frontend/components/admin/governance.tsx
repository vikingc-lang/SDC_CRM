"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitMerge, Lock, ShieldCheck, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { StatTile } from "@/components/charts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Table, Td } from "@/components/ui/extra";
import { Input, Select } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { relativeDays } from "@/lib/utils";

interface AuditRow { id: number; user_id: string | null; user: string; entity: string; record_id: string | null; action: string; field_name: string | null;
  old_value: string | null; new_value: string | null; encrypted: boolean; created_at: string }

const ENTITY_LINK: Record<string, string> = { accounts: "/accounts/", contacts: "/contacts/", deals: "/deals/", quotes: "/quotes/", documents: "/documents/" };

export function AuditPanel() {
  const [entity, setEntity] = useState("");
  const [action, setAction] = useState("");
  const [field, setField] = useState("");
  const q = useInfiniteQuery({
    queryKey: ["admin", "audit", entity, action, field],
    queryFn: ({ pageParam }) => get<{ items: AuditRow[]; next_before_id: number | null }>("/admin/audit", { entity: entity || undefined, action: action || undefined, field: field || undefined, before_id: pageParam, limit: 50 }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => (last.items.length === 50 ? last.next_before_id ?? undefined : undefined),
  });
  const rows = q.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <Card>
      <CardHeader icon={<Lock className="h-4 w-4" />} title="Immutable audit trail"
        description="Field-level history (user, record, field, old → new, timestamp). The table is append-only at the database level: UPDATE, DELETE and TRUNCATE are rejected by trigger. Personal data is encrypted per contact and becomes unreadable after erasure." />
      <div className="flex flex-wrap gap-2 px-5 pb-3">
        <Select aria-label="Entity" className="h-8 w-auto" value={entity} onChange={(e) => setEntity(e.target.value)}>
          <option value="">All records</option>{["accounts", "contacts", "deals", "quotes", "documents", "contracts", "tasks", "users", "role_permissions"].map((x) => <option key={x}>{x}</option>)}
        </Select>
        <Select aria-label="Action" className="h-8 w-auto" value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="">All actions</option>{["create", "update", "delete", "login", "export", "import", "erase", "merge"].map((x) => <option key={x}>{x}</option>)}
        </Select>
        <Input aria-label="Field" className="h-8 w-40" placeholder="Field name" value={field} onChange={(e) => setField(e.target.value)} />
      </div>
      {q.isLoading ? <Skeleton className="m-5 h-40" /> : (
        <Table head={["When", "User", "Action", "Record", "Field", "Old", "New"]} minWidth={980}>
          {rows.map((r) => (
            <tr key={r.id}>
              <Td className="whitespace-nowrap text-[12px] text-muted-foreground">{new Date(r.created_at).toLocaleString()}</Td>
              <Td className="text-[12.5px]">{r.user}</Td>
              <Td><Badge>{r.action}</Badge></Td>
              <Td className="text-[12.5px]">{r.record_id && ENTITY_LINK[r.entity] ? <Link href={`${ENTITY_LINK[r.entity]}${r.record_id}`} className="hover:underline">{r.entity}</Link> : r.entity}
                {r.record_id && <span className="block font-mono text-[11px] text-subtle">{r.record_id.slice(0, 8)}</span>}</Td>
              <Td className="font-mono text-[12px]">{r.field_name ?? "—"}</Td>
              <Td className="max-w-[200px] truncate text-[12px] text-muted-foreground" >{r.encrypted && <Lock className="mr-1 inline h-3 w-3" />}{r.old_value ?? "—"}</Td>
              <Td className="max-w-[220px] truncate text-[12px]">{r.new_value ?? "—"}</Td>
            </tr>
          ))}
        </Table>
      )}
      {q.hasNextPage && <div className="p-3 text-center"><Button size="sm" variant="outline" loading={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>Load older</Button></div>}
    </Card>
  );
}

interface Compliance {
  stats: Record<string, number>;
  erasures: { id: string; contact_id: string; subject_hash: string; fields_erased: string[]; regulation: string; key_destroyed: boolean; created_at: string }[];
  consent_events: { id: string; contact_id: string; contact: string | null; event_type: string; channel: string; regulation: string | null; source: string; created_at: string }[];
}

export function CompliancePanel() {
  const { data } = useQuery({ queryKey: ["admin", "compliance"], queryFn: () => get<Compliance>("/admin/compliance") });
  if (!data) return <Skeleton className="h-60 w-full" />;
  const s = data.stats;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Email consent granted" value={String(s.consent_granted ?? 0)} icon={<ShieldCheck className="h-4 w-4" />} sub={`${s.consent_unknown ?? 0} unknown · ${s.consent_withdrawn ?? 0} withdrawn`} />
        <StatTile label="Email opt-outs" value={String(s.opted_out_email ?? 0)} sub="outbound email is blocked" />
        <StatTile label="GDPR / CCPA subjects" value={`${s.GDPR ?? 0} / ${s.CCPA ?? 0}`} />
        <StatTile label="Erasures" value={String(data.erasures.length)} sub="keys destroyed (crypto-shredding)" />
      </div>
      <Card>
        <CardHeader title="Erasure log" description="Proof of erasure without retaining the subject: a salted hash, the fields cleared and confirmation the encryption key was destroyed." />
        {!data.erasures.length ? <EmptyState icon={<ShieldCheck className="h-4 w-4" />} title="No erasure requests processed" /> : (
          <Table head={["When", "Regulation", "Subject hash", "Fields erased", "Key destroyed"]}>
            {data.erasures.map((e) => (
              <tr key={e.id}>
                <Td className="text-[12.5px]">{new Date(e.created_at).toLocaleString()}</Td><Td>{e.regulation}</Td>
                <Td className="font-mono text-[11.5px]">{e.subject_hash.slice(0, 16)}…</Td><Td className="text-[12px]">{e.fields_erased.join(", ")}</Td>
                <Td>{e.key_destroyed ? <Badge tone="good">Yes</Badge> : <Badge tone="critical">No</Badge>}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      <Card>
        <CardHeader title="Consent ledger" description="Append-only record of every grant, withdrawal and opt-out." />
        {!data.consent_events.length ? <EmptyState icon={<ShieldCheck className="h-4 w-4" />} title="No consent events yet" /> : (
          <Table head={["When", "Contact", "Event", "Channel", "Regulation", "Source"]}>
            {data.consent_events.map((e) => (
              <tr key={e.id}>
                <Td className="text-[12.5px]">{relativeDays(e.created_at)}</Td>
                <Td>{e.contact ? <Link href={`/contacts/${e.contact_id}`} className="hover:underline">{e.contact}</Link> : <span className="text-muted-foreground">erased subject</span>}</Td>
                <Td className="capitalize">{e.event_type.replace(/_/g, " ")}</Td><Td>{e.channel}</Td><Td>{e.regulation ?? "—"}</Td><Td className="text-[12.5px] text-muted-foreground">{e.source}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}

interface Candidate { score: number; reasons: string[]; same_domain: boolean; auto_mergeable: boolean; entity: "account" | "contact";
  a: Record<string, string | null> & { id: string }; b: Record<string, string | null> & { id: string } }
interface MergeHistory { id: string; entity: string; survivor_id: string; merged_name: string; score: number | null; automatic: boolean; field_resolution: Record<string, string>; created_at: string }

const label = (x: Candidate["a"]) => x.name ?? `${x.first_name ?? ""} ${x.last_name ?? ""}`.trim();

export function DedupPanel() {
  const qc = useQueryClient();
  const cands = useQuery({ queryKey: ["admin", "dedup"], queryFn: () => get<{ accounts: Candidate[]; contacts: Candidate[] }>("/admin/dedup") });
  const history = useQuery({ queryKey: ["admin", "dedup", "history"], queryFn: () => get<MergeHistory[]>("/admin/dedup/history") });
  const [survivor, setSurvivor] = useState<Record<string, string>>({});
  const done = (msg: string) => { qc.invalidateQueries(); toast.success(msg); };
  const merge = useMutation({
    mutationFn: async (c: Candidate) => {
      const key = `${c.a.id}:${c.b.id}`;
      const keep = survivor[key] ?? (c.a.created_at! < c.b.created_at! ? c.a.id : c.b.id);
      return (await api.post("/admin/dedup/merge", { entity: c.entity, survivor_id: keep, merged_id: keep === c.a.id ? c.b.id : c.a.id })).data;
    },
    onSuccess: () => done("Records merged. Deals, contacts and activity re-parented; the merge is logged."),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const dismiss = useMutation({
    mutationFn: async (c: Candidate) => (await api.post("/admin/dedup/dismiss", { entity: c.entity, id_a: c.a.id, id_b: c.b.id })).data,
    onSuccess: () => done("Marked as not a duplicate"),
  });
  const auto = useMutation({
    mutationFn: async () => (await api.post<Record<string, number>>("/admin/dedup/auto")).data,
    onSuccess: (r) => done(`Auto-merge: ${Object.entries(r).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ")}`),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const all = [...(cands.data?.accounts ?? []), ...(cands.data?.contacts ?? [])];
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader icon={<GitMerge className="h-4 w-4" />} title="Duplicate candidates"
          description="Jaro-Winkler + Levenshtein name similarity and registrable-domain matching. Pairs at ≥ 0.97, or on the same domain at ≥ 0.85, merge automatically every night."
          action={<Button size="sm" variant="outline" loading={auto.isPending} onClick={() => auto.mutate()}><Sparkles className="h-3.5 w-3.5" />Run auto-merge</Button>} />
        {cands.isLoading ? <Skeleton className="m-5 h-24" /> : !all.length ? <EmptyState icon={<GitMerge className="h-4 w-4" />} title="No likely duplicates" description="The graph is clean." /> : (
          <ul className="divide-y">
            {all.map((c) => {
              const key = `${c.a.id}:${c.b.id}`;
              const keep = survivor[key] ?? (c.a.created_at! < c.b.created_at! ? c.a.id : c.b.id);
              return (
                <li key={key} className="flex flex-wrap items-center gap-3 px-5 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 text-[13.5px]">
                      <Badge tone={c.auto_mergeable ? "warning" : "neutral"}>{Math.round(c.score * 100)}% match</Badge>
                      <span className="capitalize text-muted-foreground">{c.entity}</span>
                    </div>
                    <div className="mt-2 grid gap-2 sm:grid-cols-2">
                      {[c.a, c.b].map((x) => (
                        <label key={x.id} className={`flex cursor-pointer items-start gap-2 rounded-md border p-2 text-[13px] ${keep === x.id ? "border-primary bg-primary/5" : ""}`}>
                          <input type="radio" name={key} className="mt-1 accent-[hsl(var(--primary))]" checked={keep === x.id} onChange={() => setSurvivor({ ...survivor, [key]: x.id })} />
                          <span className="min-w-0"><span className="font-medium">{label(x)}</span><span className="block truncate text-[12px] text-muted-foreground">{x.domain ?? x.email ?? ""} · created {relativeDays(x.created_at)}</span>
                            {keep === x.id && <span className="text-[11.5px] text-primary">Survivor</span>}</span>
                        </label>
                      ))}
                    </div>
                    <p className="mt-1 text-[12px] text-muted-foreground">{c.reasons.join(" · ")}</p>
                  </div>
                  <div className="flex gap-2">
                    <Button size="sm" variant="ghost" onClick={() => dismiss.mutate(c)}><X className="h-3.5 w-3.5" />Not a duplicate</Button>
                    <Button size="sm" loading={merge.isPending && merge.variables === c} onClick={() => merge.mutate(c)}><GitMerge className="h-3.5 w-3.5" />Merge</Button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
      <Card>
        <CardHeader title="Merge history" description="Each merge keeps a snapshot of the absorbed record and the field-by-field resolution." />
        {!history.data?.length ? <EmptyState icon={<GitMerge className="h-4 w-4" />} title="No merges yet" /> : (
          <Table head={["When", "Entity", "Merged record", "Score", "Mode", "Fields taken from merged"]}>
            {history.data.map((m) => (
              <tr key={m.id}>
                <Td className="text-[12.5px]">{relativeDays(m.created_at)}</Td><Td className="capitalize">{m.entity}</Td>
                <Td><Link href={`/${m.entity === "account" ? "accounts" : "contacts"}/${m.survivor_id}`} className="hover:underline">{m.merged_name}</Link></Td>
                <Td>{m.score ? `${Math.round(m.score * 100)}%` : "—"}</Td><Td>{m.automatic ? <Badge tone="ai">Automatic</Badge> : <Badge>Manual</Badge>}</Td>
                <Td className="text-[12px] text-muted-foreground">{Object.entries(m.field_resolution).filter(([, v]) => v === "merged").map(([k]) => k).join(", ") || "none (survivor kept)"}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
