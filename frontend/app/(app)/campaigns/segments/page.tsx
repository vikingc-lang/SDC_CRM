"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { PageHeader } from "@/components/AppShell";
import { SegmentList, TrackingCard } from "@/components/segments";

export default function SegmentsPage() {
  return (
    <div className="mx-auto max-w-5xl">
      <Link href="/campaigns" className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />Campaigns</Link>
      <PageHeader title="Segments and website tracking" description="Build audiences from who people are and what they did on your website, in your emails and in your product." />
      <div className="space-y-6">
        <SegmentList />
        <TrackingCard />
      </div>
    </div>
  );
}
