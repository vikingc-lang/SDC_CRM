"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Cpu, Database, RefreshCw, Server, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import type { User } from "@/lib/types";

interface AIStatus { llm_provider: string; model: string; embedding_provider: string; embedding_dim: number; background: string }

const PROVIDER_LABEL: Record<string, string> = {
  ollama: "Ollama (local, on-prem)",
  aws_bedrock: "Claude on AWS Bedrock (your VPC)",
  anthropic: "Claude API",
  heuristic: "relate deterministic engine (offline)",
};

export default function SettingsPage() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => get<User>("/users/me") });
  const status = useQuery({ queryKey: ["ai-status"], queryFn: () => get<AIStatus>("/ai/status") });
  const rescore = useMutation({
    mutationFn: async () => (await api.post<{ accounts_rescored: number }>("/admin/rescore")).data,
    onSuccess: (d) => { qc.invalidateQueries(); toast.success(`Re-scored ${d.accounts_rescored} accounts`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const s = status.data;
  const rows = s ? [
    { icon: Brain, label: "Language model", value: PROVIDER_LABEL[s.llm_provider] ?? s.llm_provider, sub: s.model },
    { icon: Database, label: "Semantic memory", value: `pgvector · ${s.embedding_dim} dimensions`, sub: `Embeddings: ${s.embedding_provider === "hash" ? "offline feature hashing" : s.embedding_provider}` },
    { icon: Cpu, label: "Background jobs", value: s.background === "celery" ? "Celery + Redis workers" : "In-process", sub: "Embedding, re-scoring and stage-gate AI actions" },
    { icon: ShieldCheck, label: "Data residency", value: "Single-tenant, self-hosted", sub: "No CRM data leaves your infrastructure unless you choose a cloud model" },
  ] : [];

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="Settings" description="Workspace, intelligence layer and scoring engine" />
      <div className="space-y-6">
        <Card>
          <CardHeader title="Intelligence layer" icon={<Server className="h-4 w-4 text-muted-foreground" />} description="Configured with LLM_PROVIDER and EMBEDDING_PROVIDER in your environment." />
          <CardBody className="divide-y">
            {status.isLoading && <Skeleton className="h-32 w-full" />}
            {rows.map(({ icon: Icon, label, value, sub }) => (
              <div key={label} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
                <Icon className="mt-0.5 h-4 w-4 text-muted-foreground" />
                <div className="flex-1">
                  <p className="text-[12.5px] text-muted-foreground">{label}</p>
                  <p className="text-sm font-medium">{value}</p>
                  <p className="text-[12.5px] text-muted-foreground">{sub}</p>
                </div>
              </div>
            ))}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Scoring engine" description="Health and risk are re-computed on every activity. Recency decays daily, so run a full re-score after imports." />
          <CardBody className="flex flex-wrap items-center gap-3">
            <Button variant="outline" size="sm" loading={rescore.isPending} onClick={() => rescore.mutate()} disabled={!me.data || !["super_admin", "sales_manager"].includes(me.data.role)}>
              <RefreshCw className="h-3.5 w-3.5" />Re-score all accounts
            </Button>
            {me.data && !["super_admin", "sales_manager"].includes(me.data.role) && <span className="text-[12.5px] text-muted-foreground">Managers and admins only.</span>}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Your profile" />
          <CardBody>
            {me.data ? (
              <div className="flex items-center gap-3 text-sm">
                <div className="flex-1"><p className="font-medium">{me.data.full_name}</p><p className="text-muted-foreground">{me.data.email}</p></div>
                <Badge tone="primary" className="capitalize">{me.data.role.replace("_", " ")}</Badge>
              </div>
            ) : <Skeleton className="h-10 w-full" />}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
