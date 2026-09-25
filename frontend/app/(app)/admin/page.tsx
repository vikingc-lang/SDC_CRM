"use client";

import { useState } from "react";
import { PageHeader } from "@/components/AppShell";
import { RbacPanel, UsersPanel } from "@/components/admin/access";
import { CustomFieldsPanel, GatesPanel } from "@/components/admin/config";
import { DataPanel } from "@/components/admin/data";
import { AuditPanel, CompliancePanel, DedupPanel } from "@/components/admin/governance";
import { JobsPanel } from "@/components/admin/integrations";
import { Tabs } from "@/components/ui/extra";
import { useMe } from "@/lib/me";

type Tab = "users" | "rbac" | "audit" | "privacy" | "dedup" | "fields" | "gates" | "data" | "jobs";

export default function AdminPage() {
  const { can } = useMe();
  const tabs: { value: Tab; label: string; show: boolean }[] = [
    { value: "users", label: "Users", show: can("admin", "read") },
    { value: "rbac", label: "Roles & permissions", show: can("admin", "read") },
    { value: "audit", label: "Audit trail", show: can("audit", "read") },
    { value: "privacy", label: "Privacy & consent", show: can("audit", "read") },
    { value: "dedup", label: "Duplicates", show: can("accounts", "update") },
    { value: "fields", label: "Custom fields", show: can("admin", "create") },
    { value: "gates", label: "Pipelines & gates", show: can("admin", "update") },
    { value: "data", label: "Import / export", show: can("data", "create") || can("data", "export") },
    { value: "jobs", label: "Jobs", show: can("admin", "update") },
  ];
  const visible = tabs.filter((t) => t.show);
  const [tab, setTab] = useState<Tab | null>(null);
  const current = tab && visible.some((t) => t.value === tab) ? tab : visible[0]?.value;
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Admin" description="Access control, governance, data quality and platform operations." />
      {current && <Tabs value={current} onChange={setTab} tabs={visible} />}
      {current === "users" && <UsersPanel />}
      {current === "rbac" && <RbacPanel />}
      {current === "audit" && <AuditPanel />}
      {current === "privacy" && <CompliancePanel />}
      {current === "dedup" && <DedupPanel />}
      {current === "fields" && <CustomFieldsPanel />}
      {current === "gates" && <GatesPanel />}
      {current === "data" && <DataPanel />}
      {current === "jobs" && <JobsPanel />}
    </div>
  );
}
