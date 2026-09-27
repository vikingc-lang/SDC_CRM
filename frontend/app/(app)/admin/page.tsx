"use client";

import { useEffect, useState } from "react";
import { PageHeader } from "@/components/AppShell";
import { RbacPanel, UsersPanel } from "@/components/admin/access";
import { CustomFieldsPanel, GatesPanel } from "@/components/admin/config";
import { DataPanel } from "@/components/admin/data";
import { AuditPanel, CompliancePanel, DedupPanel } from "@/components/admin/governance";
import { JobsPanel } from "@/components/admin/integrations";
import { ApprovalChainPanel, LeadManagementPanel, StagesPanel } from "@/components/admin/leadtoorder";
import { ApiKeysPanel, WebhooksPanel } from "@/components/admin/developer";
import { CommissionPlansPanel, TerritoriesPanel } from "@/components/admin/performance";
import { ServicePanel } from "@/components/admin/service";
import { WorkflowsPanel } from "@/components/admin/workflows";
import { SecurityPanel } from "@/components/security";
import { Tabs } from "@/components/ui/extra";
import { useMe } from "@/lib/me";

type Tab = "users" | "security" | "rbac" | "audit" | "privacy" | "dedup" | "fields" | "leads" | "workflows" | "service" | "territories" | "chain" | "stages" | "gates" | "data" | "developer" | "jobs";

export default function AdminPage() {
  const { can } = useMe();
  const tabs: { value: Tab; label: string; show: boolean }[] = [
    { value: "users", label: "Users", show: can("admin", "read") },
    { value: "security", label: "Sign-in security", show: can("admin", "update") },
    { value: "rbac", label: "Roles & permissions", show: can("admin", "read") },
    { value: "audit", label: "Audit trail", show: can("audit", "read") },
    { value: "privacy", label: "Privacy & consent", show: can("audit", "read") },
    { value: "dedup", label: "Duplicates", show: can("accounts", "update") },
    { value: "fields", label: "Custom fields", show: can("admin", "create") },
    { value: "leads", label: "Lead management", show: can("admin", "update") },
    { value: "workflows", label: "Workflows", show: can("admin", "read") },
    { value: "service", label: "Service", show: can("admin", "update") },
    { value: "territories", label: "Territories & incentives", show: can("admin", "update") },
    { value: "chain", label: "Approval chain", show: can("admin", "update") },
    { value: "stages", label: "Stages", show: can("admin", "update") },
    { value: "gates", label: "Stage gates", show: can("admin", "update") },
    { value: "data", label: "Import / export", show: can("data", "create") || can("data", "export") },
    { value: "developer", label: "API & webhooks", show: can("admin", "update") },
    { value: "jobs", label: "Jobs", show: can("admin", "update") },
  ];
  const visible = tabs.filter((t) => t.show);
  const [tab, setTab] = useState<Tab | null>(null);
  useEffect(() => { const t = new URLSearchParams(window.location.search).get("tab"); if (t) setTab(t as Tab); }, []);
  const current = tab && visible.some((t) => t.value === tab) ? tab : visible[0]?.value;
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Admin" description="Access control, governance, data quality and platform operations." />
      {current && <Tabs value={current} onChange={setTab} tabs={visible} />}
      {current === "users" && <UsersPanel />}
      {current === "security" && <SecurityPanel />}
      {current === "rbac" && <RbacPanel />}
      {current === "audit" && <AuditPanel />}
      {current === "privacy" && <CompliancePanel />}
      {current === "dedup" && <DedupPanel />}
      {current === "fields" && <CustomFieldsPanel />}
      {current === "leads" && <LeadManagementPanel />}
      {current === "workflows" && <WorkflowsPanel />}
      {current === "service" && <ServicePanel />}
      {current === "territories" && <div className="space-y-6"><TerritoriesPanel /><CommissionPlansPanel /></div>}
      {current === "chain" && <ApprovalChainPanel />}
      {current === "stages" && <StagesPanel />}
      {current === "gates" && <GatesPanel />}
      {current === "data" && <DataPanel />}
      {current === "developer" && <div className="space-y-6"><ApiKeysPanel /><WebhooksPanel /></div>}
      {current === "jobs" && <JobsPanel />}
    </div>
  );
}
