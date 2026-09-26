"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowDown, Building2, Plus, Search } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { PageHeader } from "@/components/AppShell";
import { NewAccountDialog } from "@/components/forms";
import { HealthMeter } from "@/components/indicators";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Avatar, EmptyState, Skeleton } from "@/components/ui/misc";
import { get } from "@/lib/api";
import type { AccountListItem } from "@/lib/types";
import { cn, money, relativeDays } from "@/lib/utils";

type SortKey = "name" | "health" | "open_pipeline" | "last_activity_at";

export default function AccountsPage() {
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "open_pipeline", dir: -1 });
  const [creating, setCreating] = useState(false);
  const { data, isLoading } = useQuery({ queryKey: ["accounts", search], queryFn: () => get<AccountListItem[]>("/accounts", { search: search || undefined, limit: 200 }), placeholderData: (p) => p });

  const rows = useMemo(() => {
    const r = [...(data ?? [])];
    r.sort((a, b) => {
      const av = a[sort.key] ?? "";
      const bv = b[sort.key] ?? "";
      return (av < bv ? -1 : av > bv ? 1 : 0) * sort.dir;
    });
    return r;
  }, [data, sort]);

  const Th = ({ k, children, className }: { k: SortKey; children: React.ReactNode; className?: string }) => (
    <th className={cn("px-4 py-2.5 font-medium", className)}>
      <button onClick={() => setSort((s) => ({ key: k, dir: s.key === k ? (s.dir === 1 ? -1 : 1) : k === "name" ? 1 : -1 }))} className="inline-flex items-center gap-1 hover:text-foreground">
        {children}
        {sort.key === k && <ArrowDown className={cn("h-3 w-3 transition-transform", sort.dir === 1 && "rotate-180")} />}
      </button>
    </th>
  );

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader title="Accounts" description={data ? `${data.length} accounts` : undefined} actions={<Button size="sm" onClick={() => setCreating(true)}><Plus className="h-4 w-4" />New account</Button>} />
      <div className="relative mb-4 w-full sm:w-72">
        <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-subtle" />
        <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search name, domain, industry" className="pl-8" aria-label="Search accounts" />
      </div>
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="border-b bg-surface-2/60 text-[12px] text-muted-foreground">
              <tr>
                <Th k="name">Account</Th>
                <th className="px-4 py-2.5 font-medium">Tier</th>
                <Th k="health">Health</Th>
                <Th k="open_pipeline" className="text-right">Open pipeline</Th>
                <th className="px-4 py-2.5 font-medium">People</th>
                <th className="px-4 py-2.5 font-medium">Owner</th>
                <Th k="last_activity_at">Last touch</Th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {isLoading && Array.from({ length: 6 }).map((_, i) => (
                <tr key={i}><td colSpan={7} className="px-4 py-3"><Skeleton className="h-5 w-full" /></td></tr>
              ))}
              {rows.map((a) => (
                <tr key={a.id} className="group transition-colors hover:bg-muted/50">
                  <td className="px-4 py-3">
                    <Link href={`/accounts/${a.id}`} className="flex items-center gap-3">
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border bg-surface-2 text-[12px] font-semibold text-muted-foreground">{a.name[0]}</span>
                      <span className="min-w-0">
                        <span className="block truncate font-medium group-hover:underline">{a.name}</span>
                        <span className="block truncate text-[12px] text-muted-foreground">{a.domain}{a.industry ? ` · ${a.industry}` : ""}</span>
                      </span>
                    </Link>
                  </td>
                  <td className="px-4 py-3"><Badge tone="outline">{a.tier}</Badge></td>
                  <td className="px-4 py-3"><HealthMeter score={a.health} /></td>
                  <td className="tabular px-4 py-3 text-right">
                    <span className="font-medium">{money(a.open_pipeline, { compact: true })}</span>
                    <span className="block text-[12px] text-muted-foreground">{a.open_deals} deal{a.open_deals === 1 ? "" : "s"}</span>
                  </td>
                  <td className="tabular px-4 py-3 text-muted-foreground">{a.contacts}</td>
                  <td className="px-4 py-3">{a.owner && <span className="flex items-center gap-2 text-[13px]"><Avatar name={a.owner.full_name} size={22} />{a.owner.full_name}</span>}</td>
                  <td className="px-4 py-3 text-[13px] text-muted-foreground">{relativeDays(a.last_activity_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!isLoading && rows.length === 0 && <EmptyState icon={<Building2 className="h-4 w-4" />} title="No accounts found" description="Create one, or log a conversation with ⌘K and Cirra will create it for you." />}
      </Card>
      <NewAccountDialog open={creating} onOpenChange={setCreating} />
    </div>
  );
}
