"use client";

import { useQuery } from "@tanstack/react-query";
import { Filter, Megaphone, Plus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { PageHeader } from "@/components/AppShell";
import { type Campaign, CampaignDialog, CampaignStatusBadge, TYPE_LABELS, pctText } from "@/components/campaigns";
import { StatTile } from "@/components/charts";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Table, Tabs, Td } from "@/components/ui/extra";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { get } from "@/lib/api";
import { useMe } from "@/lib/me";
import { money, shortDate } from "@/lib/utils";

interface ListResponse { campaigns: Campaign[]; totals: { members: number; responses: number; sourced_pipeline: number; influenced_won: number; cost: number; active: number; roi_pct: number | null } }
type Filter = "all" | "active" | "planned" | "completed";

export default function CampaignsPage() {
  const { can } = useMe();
  const [filter, setFilter] = useState<Filter>("all");
  const [creating, setCreating] = useState(false);
  const list = useQuery({ queryKey: ["campaigns", "list"], queryFn: () => get<ListResponse>("/campaigns") });
  const rows = list.data?.campaigns.filter((c) => filter === "all" || c.status === filter) ?? [];
  const t = list.data?.totals;
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Campaigns" description="Plan campaigns, build member lists, send consent-checked email and see the pipeline each one sources and influences."
        actions={<>
          <Link href="/campaigns/segments" className="inline-flex h-8 items-center gap-1.5 rounded-md border border-input bg-surface px-2.5 text-[13px] hover:bg-muted"><Filter className="h-3.5 w-3.5" />Segments</Link>
          {can("campaigns", "create") && <Button size="sm" onClick={() => setCreating(true)}><Plus className="h-3.5 w-3.5" />New campaign</Button>}
        </>} />
      {!list.data ? <Skeleton className="h-96" /> : <>
        <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatTile label="Active campaigns" value={String(t!.active)} sub={`${list.data.campaigns.length} in total`} />
          <StatTile label="Responses" value={t!.responses.toLocaleString()} sub={`from ${t!.members.toLocaleString()} members`} />
          <StatTile label="Sourced pipeline" value={money(t!.sourced_pipeline, { compact: true })} emphasis sub="Open deals from campaign leads" />
          <StatTile label="Marketing ROI" value={pctText(t!.roi_pct)} sub={`Won ${money(t!.influenced_won, { compact: true })} on ${money(t!.cost, { compact: true })} spend`} />
        </div>
        <Tabs value={filter} onChange={setFilter} tabs={[{ value: "all", label: "All" }, { value: "active", label: "Active" }, { value: "planned", label: "Planned" }, { value: "completed", label: "Completed" }]} />
        <Card className="overflow-hidden">
          {!rows.length ? <EmptyState icon={<Megaphone className="h-4 w-4" />} title="No campaigns here" description="Create a campaign, then add members from a filter or let captured leads join it automatically." /> : (
            <Table head={["Campaign", "Type", "Status", "Dates", "Members", "Response rate", "Sourced pipeline", "Won (influenced)", "ROI"]} minWidth={1040}>
              {rows.map((c) => (
                <tr key={c.id}>
                  <Td><Link href={`/campaigns/${c.id}`} className="font-medium hover:text-primary hover:underline">{c.name}</Link>
                    <span className="block font-mono text-[11.5px] text-muted-foreground">{c.code}</span></Td>
                  <Td className="text-[13px]">{TYPE_LABELS[c.type] ?? c.type}</Td>
                  <Td><CampaignStatusBadge status={c.status} /></Td>
                  <Td className="whitespace-nowrap text-[12.5px] text-muted-foreground">{c.start_date ? shortDate(c.start_date) : "—"}{c.end_date ? ` – ${shortDate(c.end_date)}` : ""}</Td>
                  <Td className="tabular text-[13px]">{c.metrics.members.toLocaleString()}</Td>
                  <Td className="tabular text-[13px]">{pctText(c.metrics.response_rate)}</Td>
                  <Td className="tabular text-[13px]">{money(c.metrics.sourced_pipeline, { compact: true })}</Td>
                  <Td className="tabular text-[13px]">{money(c.metrics.influenced_won, { compact: true })}</Td>
                  <Td className="tabular text-[13px]">{pctText(c.metrics.roi_pct)}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      </>}
      {creating && <CampaignDialog onClose={() => setCreating(false)} />}
    </div>
  );
}
