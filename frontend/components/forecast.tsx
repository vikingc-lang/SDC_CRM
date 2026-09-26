"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Send, Users } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { StatTile } from "@/components/charts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Table, Tabs, Td } from "@/components/ui/extra";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { cn, money, relativeDays, shortDate } from "@/lib/utils";

type Cat = "closed" | "commit" | "best_case" | "pipeline" | "omitted";
interface Totals { closed: number; commit: number; best_case: number; pipeline: number; omitted: number; commit_call: number; best_case_call: number; counts: Record<Cat, number> }
interface FDeal { id: string; title: string; account: string; stage: string; amount_usd: number; close_date: string | null; closed_at: string | null; category: Cat; overridden: boolean; stage_category: Cat; risk_score: number; is_closed: boolean }
interface Sub { id: string; commit: number; best_case: number; note: string | null; calculated: Partial<Totals> & Record<string, number>; created_at: string }
interface MyView { period: string; totals: Totals; deals: FDeal[]; submission: Sub | null; history: Sub[]; is_manager: boolean }
interface TeamRow {
  user: { id: string; name: string; role: string }; is_self: boolean; calculated: Totals; submission: Sub | null;
  adjustment: { commit: number; best_case: number; note: string | null; updated_at: string } | null;
  final: { commit: number; best_case: number; source: "adjusted" | "submitted" | "calculated" };
}
interface TeamView { period: string; rows: TeamRow[]; team: Record<string, number>; submission: Sub | null; history: Sub[] }
interface Periods { periods: { period: string; label: string; current: boolean }[] }

const CAT_LABEL: Record<Cat, string> = { closed: "Closed", commit: "Commit", best_case: "Best case", pipeline: "Pipeline", omitted: "Omitted" };
const CAT_ORDER: Cat[] = ["closed", "commit", "best_case", "pipeline", "omitted"];

export function ForecastCall() {
  const periods = useQuery({ queryKey: ["forecast", "periods"], queryFn: () => get<Periods>("/forecast/periods") });
  const [period, setPeriod] = useState("");
  const [view, setView] = useState<"me" | "team">("me");
  useEffect(() => { if (!period && periods.data) setPeriod(periods.data.periods.find((p) => p.current)!.period); }, [period, periods.data]);
  const mine = useQuery({ queryKey: ["forecast", "me", period], queryFn: () => get<MyView>("/forecast/me", { period }), enabled: !!period });
  if (!period || !mine.data) return <Skeleton className="h-96" />;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Select aria-label="Quarter" className="w-auto" value={period} onChange={(e) => setPeriod(e.target.value)}>
          {periods.data!.periods.map((p) => <option key={p.period} value={p.period}>{p.label}{p.current ? " (this quarter)" : ""}</option>)}
        </Select>
        {mine.data.is_manager && <Tabs className="mb-0 border-b-0" value={view} onChange={setView} tabs={[{ value: "me", label: "My forecast" }, { value: "team", label: "My team" }]} />}
        <span className="text-[12.5px] text-muted-foreground">Commit = closed + commit. Best case adds best-case deals. Amounts in USD.</span>
      </div>
      {view === "me" ? <Mine data={mine.data} /> : <Team period={period} />}
    </div>
  );
}

function Mine({ data }: { data: MyView }) {
  const qc = useQueryClient();
  const t = data.totals;
  const setCat = useMutation({
    mutationFn: async (v: { id: string; category: Cat | null }) => (await api.put(`/forecast/deals/${v.id}/category`, { category: v.category })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["forecast"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Closed" value={money(t.closed, { compact: true })} sub={`${t.counts.closed} won`} />
        <StatTile label="Commit call" value={money(t.commit_call, { compact: true })} emphasis sub={`closed + ${t.counts.commit} commit`} />
        <StatTile label="Best case call" value={money(t.best_case_call, { compact: true })} sub={`+ ${t.counts.best_case} best case`} />
        <StatTile label="Pipeline" value={money(t.pipeline, { compact: true })} sub={`${t.counts.pipeline} more open`} />
      </div>
      <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
        <Card className="min-w-0">
          <CardHeader title="Deals in this quarter" description="Move a deal between categories to shape your call. Stage defaults are shown in grey." />
          {!data.deals.length ? <EmptyState icon={<Send className="h-4 w-4" />} title="No deals close in this quarter" /> : (
            <Table head={["Deal", "Stage", "Close", "Amount", "Category"]} minWidth={680}>
              {CAT_ORDER.flatMap((c) => data.deals.filter((d) => d.category === c)).map((d) => (
                <tr key={d.id}>
                  <Td><Link href={`/deals/${d.id}`} className="font-medium hover:text-primary hover:underline">{d.title}</Link><span className="block text-[12px] text-muted-foreground">{d.account}</span></Td>
                  <Td className="text-[13px]">{d.stage}</Td>
                  <Td className="whitespace-nowrap text-[13px]">{shortDate(d.closed_at ?? d.close_date)}</Td>
                  <Td className="tabular text-right text-[13px]">{money(d.amount_usd)}</Td>
                  <Td>
                    {d.is_closed ? <Badge tone={d.category === "closed" ? "good" : "neutral"}>{CAT_LABEL[d.category]}</Badge> : (
                      <Select aria-label={`Forecast category for ${d.title}`} className={cn("h-8 w-44 text-[13px]", !d.overridden && "text-muted-foreground")}
                        value={d.overridden ? d.category : ""} onChange={(e) => setCat.mutate({ id: d.id, category: (e.target.value || null) as Cat | null })}>
                        <option value="">{CAT_LABEL[d.stage_category]} · stage default</option>
                        {(["commit", "best_case", "pipeline", "omitted"] as Cat[]).map((c) => <option key={c} value={c}>{CAT_LABEL[c]}</option>)}
                      </Select>
                    )}
                  </Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
        <SubmitCard period={data.period} scope="self" calcCommit={t.commit_call} calcBest={t.best_case_call} last={data.submission} history={data.history} />
      </div>
    </>
  );
}

function SubmitCard({ period, scope, calcCommit, calcBest, last, history }: {
  period: string; scope: "self" | "team"; calcCommit: number; calcBest: number; last: Sub | null; history: Sub[];
}) {
  const qc = useQueryClient();
  const [f, setF] = useState({ commit: "", best_case: "", note: "" });
  useEffect(() => { setF({ commit: String(Math.round(last?.commit ?? calcCommit)), best_case: String(Math.round(last?.best_case ?? calcBest)), note: "" }); }, [period, last, calcCommit, calcBest]);
  const submit = useMutation({
    mutationFn: async () => (await api.post("/forecast/submissions", { period, scope, commit: Number(f.commit), best_case: Number(f.best_case), note: f.note || null })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["forecast"] }); toast.success(scope === "team" ? "Team call submitted" : "Forecast submitted"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const id = `fc-${scope}`;
  return (
    <Card className="h-fit">
      <CardHeader title={scope === "team" ? "Team call" : "Your call"} icon={scope === "team" ? <Users className="h-4 w-4" /> : <Send className="h-4 w-4" />}
        description={last ? `Last submitted ${relativeDays(last.created_at)}` : "Not submitted for this quarter yet"} />
      <CardBody>
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); submit.mutate(); }}>
          <div className="grid grid-cols-2 gap-2">
            <div><Label htmlFor={`${id}-commit`}>Commit (USD)</Label><Input id={`${id}-commit`} type="number" min={0} required value={f.commit} onChange={(e) => setF({ ...f, commit: e.target.value })} /></div>
            <div><Label htmlFor={`${id}-best`}>Best case (USD)</Label><Input id={`${id}-best`} type="number" min={0} required value={f.best_case} onChange={(e) => setF({ ...f, best_case: e.target.value })} /></div>
          </div>
          <p className="text-[12px] text-subtle">Calculated from {scope === "team" ? "your team's numbers" : "your deals"}: commit {money(calcCommit, { compact: true })}, best case {money(calcBest, { compact: true })}.</p>
          <div><Label htmlFor={`${id}-note`}>Note</Label><Textarea id={`${id}-note`} rows={2} placeholder="What's changed, what's at risk" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></div>
          <Button type="submit" size="sm" className="w-full" loading={submit.isPending}>Submit {scope === "team" ? "team call" : "forecast"}</Button>
        </form>
        {history.length > 0 && (
          <div className="mt-4 space-y-1.5 border-t pt-3">
            <p className="text-[12px] font-medium text-muted-foreground">History</p>
            {history.slice(0, 5).map((h) => (
              <div key={h.id} className="text-[12.5px]">
                <span className="tabular">{money(h.commit, { compact: true })} / {money(h.best_case, { compact: true })}</span>
                <span className="text-muted-foreground"> · {relativeDays(h.created_at)}</span>
                {h.note && <p className="text-[12px] text-muted-foreground">{h.note}</p>}
              </div>
            ))}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function Team({ period }: { period: string }) {
  const qc = useQueryClient();
  const team = useQuery({ queryKey: ["forecast", "team", period], queryFn: () => get<TeamView>("/forecast/team", { period }) });
  const [edit, setEdit] = useState<{ rep_id: string; commit: string; best_case: string; note: string } | null>(null);
  const save = useMutation({
    mutationFn: async () => (await api.put("/forecast/adjustments", { period, rep_id: edit!.rep_id, commit: Number(edit!.commit), best_case: Number(edit!.best_case), note: edit!.note || null })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["forecast", "team"] }); setEdit(null); toast.success("Adjustment saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const clear = useMutation({
    mutationFn: async (rep_id: string) => api.delete("/forecast/adjustments", { params: { period, rep_id } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["forecast", "team"] }),
  });
  if (!team.data) return <Skeleton className="h-72" />;
  const t = team.data.team;
  const sourceTone = { adjusted: "primary", submitted: "good", calculated: "neutral" } as const;
  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Team closed" value={money(t.closed, { compact: true })} />
        <StatTile label="Team commit (final)" value={money(t.adjusted_commit, { compact: true })} emphasis sub={`calculated ${money(t.calculated_commit, { compact: true })}`} />
        <StatTile label="Team best case (final)" value={money(t.adjusted_best_case, { compact: true })} sub={`calculated ${money(t.calculated_best_case, { compact: true })}`} />
        <StatTile label="Not yet submitted" value={String(t.not_submitted)} sub={`of ${team.data.rows.length} people`} />
      </div>
      <div className="grid gap-4 2xl:grid-cols-[1fr_340px]">
        <Card className="min-w-0">
          <CardHeader title="Team roll-up" description="Final = your adjustment if you made one, else the rep's submission, else the calculated number." />
          <Table head={["Person", "Calculated", "Submitted", "Final", ""]} minWidth={720}>
            {team.data.rows.map((r) => (
              <tr key={r.user.id}>
                <Td><span className="font-medium">{r.user.name}</span>{r.is_self && <span className="text-[12px] text-muted-foreground"> (you)</span>}
                  <span className="block text-[12px] text-muted-foreground">closed {money(r.calculated.closed, { compact: true })}</span></Td>
                <Td className="tabular text-[13px]">{money(r.calculated.commit_call, { compact: true })} / {money(r.calculated.best_case_call, { compact: true })}</Td>
                <Td className="tabular text-[13px]">
                  {r.submission ? <>{money(r.submission.commit, { compact: true })} / {money(r.submission.best_case, { compact: true })}
                    <span className="block text-[12px] text-muted-foreground" title={r.submission.note ?? undefined}>{relativeDays(r.submission.created_at)}{r.submission.note ? " · note" : ""}</span></>
                    : <span className="text-muted-foreground">Not submitted</span>}
                </Td>
                <Td className="tabular text-[13px]">
                  {edit?.rep_id === r.user.id ? (
                    <form className="flex flex-wrap items-center gap-1" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
                      <Input aria-label="Adjusted commit" className="h-8 w-24" type="number" min={0} value={edit.commit} onChange={(e) => setEdit({ ...edit, commit: e.target.value })} />
                      <Input aria-label="Adjusted best case" className="h-8 w-24" type="number" min={0} value={edit.best_case} onChange={(e) => setEdit({ ...edit, best_case: e.target.value })} />
                      <Input aria-label="Reason" className="h-8 w-32" placeholder="Reason" value={edit.note} onChange={(e) => setEdit({ ...edit, note: e.target.value })} />
                      <Button type="submit" size="sm" loading={save.isPending}>Save</Button>
                      <Button type="button" size="sm" variant="ghost" onClick={() => setEdit(null)}>Cancel</Button>
                    </form>
                  ) : (
                    <>{money(r.final.commit, { compact: true })} / {money(r.final.best_case, { compact: true })} <Badge tone={sourceTone[r.final.source]}>{r.final.source}</Badge>
                      {r.adjustment?.note && <span className="block text-[12px] text-muted-foreground">{r.adjustment.note}</span>}</>
                  )}
                </Td>
                <Td className="whitespace-nowrap text-right">
                  {!r.is_self && edit?.rep_id !== r.user.id && (
                    <>
                      <Button size="sm" variant="ghost" onClick={() => setEdit({ rep_id: r.user.id, commit: String(Math.round(r.final.commit)), best_case: String(Math.round(r.final.best_case)), note: r.adjustment?.note ?? "" })}>Adjust</Button>
                      {r.adjustment && <Button size="sm" variant="ghost" onClick={() => clear.mutate(r.user.id)}>Clear</Button>}
                    </>
                  )}
                </Td>
              </tr>
            ))}
          </Table>
        </Card>
        <SubmitCard period={period} scope="team" calcCommit={t.adjusted_commit} calcBest={t.adjusted_best_case} last={team.data.submission} history={team.data.history} />
      </div>
    </>
  );
}
