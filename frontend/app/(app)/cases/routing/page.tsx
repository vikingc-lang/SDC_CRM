"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { PageHeader } from "@/components/AppShell";
import { PresenceControl, RoutingConsole } from "@/components/serviceops";
import { useMe } from "@/lib/me";

export default function RoutingPage() {
  const { can } = useMe();
  return (
    <div className="mx-auto max-w-6xl">
      <Link href="/cases" className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />Service</Link>
      <PageHeader title="Routing" description="Who is available, how loaded each agent is, and what waits in each queue. Refreshes every 30 seconds."
        actions={can("cases", "update") ? <PresenceControl /> : undefined} />
      <RoutingConsole />
    </div>
  );
}
