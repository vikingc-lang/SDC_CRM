"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus, Search, Users } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { PageHeader } from "@/components/AppShell";
import { NewContactDialog } from "@/components/forms";
import { BUYING_ROLES, RoleBadge } from "@/components/indicators";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Avatar, EmptyState, Skeleton } from "@/components/ui/misc";
import { get } from "@/lib/api";
import type { Contact } from "@/lib/types";

export default function ContactsPage() {
  const [search, setSearch] = useState("");
  const [role, setRole] = useState("");
  const [creating, setCreating] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["contacts", search, role],
    queryFn: () => get<Contact[]>("/contacts", { search: search || undefined, buying_role: role || undefined }),
    placeholderData: (p) => p,
  });

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader title="Contacts" description="Everyone on every buying committee" actions={<Button size="sm" onClick={() => setCreating(true)}><Plus className="h-4 w-4" />New contact</Button>} />
      <div className="mb-4 flex flex-wrap gap-2">
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-subtle" />
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search people, titles, companies" className="pl-8" aria-label="Search contacts" />
        </div>
        <Select value={role} onChange={(e) => setRole(e.target.value)} className="w-auto" aria-label="Buying role">
          <option value="">All buying roles</option>
          {BUYING_ROLES.map((r) => <option key={r}>{r}</option>)}
        </Select>
      </div>
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead className="border-b bg-surface-2/60 text-[12px] text-muted-foreground">
              <tr>
                <th className="px-4 py-2.5 font-medium">Name</th>
                <th className="px-4 py-2.5 font-medium">Account</th>
                <th className="px-4 py-2.5 font-medium">Buying role</th>
                <th className="px-4 py-2.5 font-medium">Email</th>
                <th className="px-4 py-2.5 font-medium">Phone</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {isLoading && Array.from({ length: 6 }).map((_, i) => <tr key={i}><td colSpan={5} className="px-4 py-3"><Skeleton className="h-5" /></td></tr>)}
              {data?.map((c) => (
                <tr key={c.id} className="hover:bg-muted/50">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-3">
                      <Avatar name={c.name} size={30} />
                      <div className="min-w-0"><p className="truncate font-medium">{c.name}</p><p className="truncate text-[12px] text-muted-foreground">{c.job_title ?? "—"}</p></div>
                    </div>
                  </td>
                  <td className="px-4 py-3"><Link href={`/accounts/${c.account_id}`} className="hover:underline">{c.account_name}</Link></td>
                  <td className="px-4 py-3"><RoleBadge role={c.buying_role} /></td>
                  <td className="px-4 py-3 text-muted-foreground">{c.email ? <a href={`mailto:${c.email}`} className="hover:text-foreground hover:underline">{c.email}</a> : "—"}</td>
                  <td className="px-4 py-3 text-muted-foreground">{c.phone ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data?.length === 0 && <EmptyState icon={<Users className="h-4 w-4" />} title="No contacts match" />}
      </Card>
      <NewContactDialog open={creating} onOpenChange={setCreating} />
    </div>
  );
}
