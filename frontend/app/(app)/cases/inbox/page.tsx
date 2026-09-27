"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { PageHeader } from "@/components/AppShell";
import { SupportInbox } from "@/components/serviceops";

export default function SupportInboxPage() {
  return (
    <div className="mx-auto max-w-6xl">
      <Link href="/cases" className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />Service</Link>
      <PageHeader title="Support email" description="Every message sent to a support address and what became of it: new cases, replies added to cases, and mail that needs filing." />
      <SupportInbox />
    </div>
  );
}
