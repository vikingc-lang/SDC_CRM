"use client";

import { PageHeader } from "@/components/AppShell";
import { PerformanceView } from "@/components/performance";

export default function PerformancePage() {
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Quotas & commission" description="Quota attainment, pipeline coverage and your commission statement for each quarter." />
      <PerformanceView />
    </div>
  );
}
