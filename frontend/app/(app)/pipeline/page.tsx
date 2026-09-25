"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Tabs } from "@/components/ui/extra";
import { PageHeader } from "@/components/AppShell";
import { NewDealDialog } from "@/components/forms";
import { KanbanBoard } from "@/components/KanbanBoard";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { get } from "@/lib/api";
import type { Kanban, PipelineFull } from "@/lib/types";
import { money } from "@/lib/utils";

export default function PipelinePage() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [owner, setOwner] = useState("");
  const [risk, setRisk] = useState("");
  const [newDeal, setNewDeal] = useState(false);
  const pipelines = useQuery({ queryKey: ["pipelines"], queryFn: () => get<PipelineFull[]>("/pipelines") });
  const [selected, setSelected] = useState<string | null>(null);
  const pipelineId = selected ?? pipelines.data?.[0]?.id;
  const current = pipelines.data?.find((p) => p.id === pipelineId);
  const board = useQuery({
    queryKey: ["kanban", pipelineId],
    queryFn: () => get<Kanban>(`/pipeline/${pipelineId}/kanban`),
    enabled: !!pipelineId,
  });

  const owners = useMemo(() => {
    const m = new Map<string, string>();
    board.data?.columns.forEach((c) => c.deals.forEach((d) => d.owner && m.set(d.owner.id, d.owner.full_name)));
    return [...m.entries()];
  }, [board.data]);

  const filtered = useMemo<Kanban | undefined>(() => {
    if (!board.data) return undefined;
    const q = search.toLowerCase();
    return {
      ...board.data,
      columns: board.data.columns.map((c) => {
        const deals = c.deals.filter(
          (d) =>
            (!q || d.title.toLowerCase().includes(q) || d.account.name.toLowerCase().includes(q)) &&
            (!owner || d.owner?.id === owner) &&
            (!risk || (risk === "high" ? d.risk_score >= 60 : risk === "watch" ? d.risk_score >= 30 && d.risk_score < 60 : d.risk_score < 30)),
        );
        return {
          ...c,
          deals,
          metrics: { count: deals.length, total: deals.reduce((a, d) => a + (d.amount_usd ?? d.amount), 0), weighted: deals.reduce((a, d) => a + d.weighted_value, 0) },
        };
      }),
    };
  }, [board.data, search, owner, risk]);

  const open = board.data?.columns.filter((c) => !c.is_closed_won && !c.is_closed_lost) ?? [];
  const total = open.reduce((a, c) => a + c.metrics.total, 0);
  const weighted = open.reduce((a, c) => a + c.metrics.weighted, 0);

  const optimisticMove = (dealId: string, toStageId: string) => {
    qc.setQueryData<Kanban>(["kanban", pipelineId], (prev) => {
      if (!prev) return prev;
      const deal = prev.columns.flatMap((c) => c.deals).find((d) => d.id === dealId);
      if (!deal) return prev;
      return {
        ...prev,
        columns: prev.columns.map((c) => ({
          ...c,
          deals: c.id === toStageId ? [{ ...deal, stage_id: toStageId, stage: c.name, days_in_stage: 0 }, ...c.deals.filter((d) => d.id !== dealId)] : c.deals.filter((d) => d.id !== dealId),
        })),
      };
    });
  };

  return (
    <div>
      <PageHeader
        title="Pipeline"
        description={board.data ? `${current?.name}: ${money(total)} open · ${money(weighted)} risk-adjusted weighted forecast (USD)` : "Loading…"}
        actions={<Button size="sm" onClick={() => setNewDeal(true)}><Plus className="h-4 w-4" />New deal</Button>}
      />
      {pipelines.data && pipelines.data.length > 1 && (
        <Tabs value={pipelineId ?? ""} onChange={(v) => setSelected(v)}
          tabs={pipelines.data.map((p) => ({ value: p.id, label: p.name }))} className="mb-4" />
      )}
      {current?.description && <p className="-mt-2 mb-4 text-[12.5px] text-muted-foreground">{current.description}</p>}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-64">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-subtle" />
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Filter deals or accounts" className="pl-8" aria-label="Filter deals" />
        </div>
        <Select value={owner} onChange={(e) => setOwner(e.target.value)} className="w-auto" aria-label="Owner">
          <option value="">All owners</option>
          {owners.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
        </Select>
        <Select value={risk} onChange={(e) => setRisk(e.target.value)} className="w-auto" aria-label="Risk">
          <option value="">Any risk</option>
          <option value="high">High risk (60+)</option>
          <option value="watch">Watch (30–59)</option>
          <option value="low">On track (&lt;30)</option>
        </Select>
        <p className="ml-auto hidden text-[12px] text-subtle md:block">Drag cards between stages. Stage gates and AI actions run automatically.</p>
      </div>
      {filtered ? (
        <KanbanBoard board={filtered} onOptimisticMove={optimisticMove} />
      ) : (
        <div className="flex gap-3 overflow-hidden">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="w-[272px] shrink-0 space-y-2"><Skeleton className="h-5 w-32" /><Skeleton className="h-28 w-full" /><Skeleton className="h-28 w-full" /></div>
          ))}
        </div>
      )}
      <NewDealDialog open={newDeal} onOpenChange={setNewDeal} pipelineId={pipelineId} />
    </div>
  );
}
