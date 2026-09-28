"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BookOpen, Check, CircleCheck, Keyboard, Lightbulb, Map, Network, Rocket, Search, Sparkles, UserRound } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { PageHeader } from "@/components/AppShell";
import { AidenAvatar } from "@/components/Brand";
import { AreaIcon, DataModelExplorer, ProcessMap } from "@/components/help";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Table, Tabs, Td } from "@/components/ui/extra";
import { Kbd, Skeleton } from "@/components/ui/misc";
import { get } from "@/lib/api";
import type { HelpCatalog } from "@/lib/help";
import { ROLE_LABELS } from "@/lib/me";
import { ui } from "@/lib/store";
import { cn, shortDate } from "@/lib/utils";

type Tab = "role" | "areas" | "processes" | "model" | "glossary" | "shortcuts" | "new";
const TABS: { value: Tab; label: string }[] = [
  { value: "role", label: "Your role" }, { value: "areas", label: "Capabilities" }, { value: "processes", label: "Processes" },
  { value: "model", label: "Data model" }, { value: "glossary", label: "Glossary" }, { value: "shortcuts", label: "Shortcuts" }, { value: "new", label: "What's new" },
];

export default function HelpCenterPage() {
  const data = useQuery({ queryKey: ["help", "catalog"], queryFn: () => get<HelpCatalog>("/help") });
  const [tab, setTab] = useState<Tab>("role");
  const [proc, setProc] = useState<string | null>(null);
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const t = q.get("tab") as Tab | null;
    if (t && TABS.some((x) => x.value === t)) setTab(t);
    if (q.get("p")) setProc(q.get("p"));
  }, []);
  if (!data.data) return <div className="mx-auto max-w-6xl space-y-4"><Skeleton className="h-16" /><Skeleton className="h-96" /></div>;
  const d = data.data;
  const process = d.processes.find((p) => p.key === proc) ?? d.processes[0];
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Help center" description="How Cirra works for you: your role, what each area does, how the processes flow, and the data behind them."
        actions={<>
          <Button variant="outline" size="sm" onClick={() => ui.set({ helpOpen: true })}><Search className="h-3.5 w-3.5" />Search help</Button>
          <Button size="sm" onClick={() => ui.set({ helpOpen: true })}><AidenAvatar size={16} />Ask Aiden</Button>
        </>} />
      <Tabs value={tab} onChange={setTab} tabs={TABS} />

      {tab === "role" && <RoleTab d={d} />}

      {tab === "areas" && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {d.areas.map((a) => (
            <Link key={a.key} href={`/help/areas/${a.key}`} className="group rounded-lg border bg-surface p-4 shadow-card transition-colors hover:border-primary/40">
              <div className="flex items-center gap-2"><span className="flex h-8 w-8 items-center justify-center rounded-md bg-primary-soft text-primary"><AreaIcon icon={a.icon} /></span>
                <p className="text-[14px] font-semibold">{a.title}</p></div>
              <p className="mt-2 text-[12.5px] text-muted-foreground">{a.summary}</p>
              <p className="mt-2 text-[12px] text-subtle">{a.howto.length} how-to{a.howto.length === 1 ? "" : "s"} · {a.capabilities.length} capabilities</p>
            </Link>
          ))}
        </div>
      )}

      {tab === "processes" && (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2">
            {d.processes.map((p) => (
              <button key={p.key} type="button" onClick={() => setProc(p.key)}
                className={cn("rounded-full border px-3 py-1.5 text-[13px]", p.key === process.key ? "border-primary bg-primary-soft font-medium text-primary" : "text-muted-foreground hover:border-primary/40")}>
                {p.title}</button>
            ))}
          </div>
          <Card>
            <CardHeader title={process.title} description={process.summary} icon={<Map className="h-4 w-4" />} />
            <CardBody><ProcessMap process={process} /></CardBody>
          </Card>
          <Card>
            <CardHeader title="Step by step" />
            <CardBody>
              <ol className="space-y-2">
                {process.steps.map((s, i) => (
                  <li key={s.id} className="flex gap-3 text-[13px]">
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary-soft text-[11px] font-semibold text-primary">{i + 1}</span>
                    <span className="min-w-0 flex-1"><span className="font-medium">{s.label}</span> <Badge tone="outline" className="ml-1">{s.lane}</Badge>
                      <span className="block text-muted-foreground">{s.detail}</span></span>
                    <Link href={s.page} className="shrink-0 self-center text-[12.5px] text-primary hover:underline">Open</Link>
                  </li>
                ))}
              </ol>
            </CardBody>
          </Card>
        </div>
      )}

      {tab === "model" && (
        <Card>
          <CardHeader title="Data model" icon={<Network className="h-4 w-4" />}
            description="The records Cirra keeps and how they connect. Choose a record type, or a neighbour on the map, to explore." />
          <CardBody><DataModelExplorer /></CardBody>
        </Card>
      )}

      {tab === "glossary" && (
        <Card>
          <CardBody className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
            {Object.entries(d.glossary).sort(([a], [b]) => a.localeCompare(b)).map(([term, meaning]) => (
              <div key={term}><p className="text-[13.5px] font-semibold">{term}</p><p className="text-[13px] text-muted-foreground">{meaning}</p></div>
            ))}
          </CardBody>
        </Card>
      )}

      {tab === "shortcuts" && (
        <Card>
          <CardHeader title="Keyboard shortcuts" icon={<Keyboard className="h-4 w-4" />} />
          <CardBody className="space-y-2">
            {d.shortcuts.map((s) => (
              <div key={s.keys} className="flex items-center justify-between border-b pb-2 text-[13.5px] last:border-0"><span>{s.action}</span><Kbd>{s.keys}</Kbd></div>
            ))}
          </CardBody>
        </Card>
      )}

      {tab === "new" && (
        <div className="space-y-3">
          {d.whats_new.map((w) => (
            <Card key={w.title}>
              <CardHeader title={w.title} description={shortDate(w.date, true)} icon={<Rocket className="h-4 w-4" />} />
              <CardBody><ul className="space-y-1 text-[13px]">{w.items.map((i) => <li key={i} className="flex gap-2"><Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />{i}</li>)}</ul></CardBody>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function RoleTab({ d }: { d: HelpCatalog }) {
  const role = d.role;
  const [done, setDone] = useState<Record<number, boolean>>({});
  useEffect(() => {
    try { setDone(JSON.parse(localStorage.getItem(`cirra.help.checklist.${role?.key}`) ?? "{}")); } catch { setDone({}); }
  }, [role?.key]);
  const toggle = (i: number) => {
    const next = { ...done, [i]: !done[i] };
    setDone(next);
    try { localStorage.setItem(`cirra.help.checklist.${role?.key}`, JSON.stringify(next)); } catch { /* per-browser convenience only */ }
  };
  if (!role) return null;
  const finished = role.checklist.filter((_, i) => done[i]).length;
  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
      <div className="min-w-0 space-y-6">
        <Card className="ai-border">
          <CardBody className="flex items-start gap-3 pt-5">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary-soft text-primary"><UserRound className="h-5 w-5" /></span>
            <div><p className="text-[12px] uppercase tracking-wide text-muted-foreground">You are a {ROLE_LABELS[role.key] ?? role.title}</p>
              <p className="mt-0.5 text-[16px] font-semibold">{role.mission}</p></div>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="A typical day" icon={<Sparkles className="h-4 w-4" />} />
          <CardBody className="space-y-1.5">
            {role.day.map((s, i) => (
              <Link key={i} href={s.href} className="flex items-center gap-3 rounded-md px-2 py-2 text-[13.5px] hover:bg-muted">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary-soft text-[11px] font-semibold text-primary">{i + 1}</span>
                <span className="flex-1">{s.label}</span><ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
              </Link>
            ))}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="What you can do" description="Straight from your role's permissions in this workspace." icon={<CircleCheck className="h-4 w-4" />} />
          <Table head={["Area", "Create", "View", "Edit", "Delete", "Export", "Records"]} minWidth={560}>
            {d.permissions.map((p) => (
              <tr key={p.resource}>
                <Td className="text-[13px] font-medium">{p.label}</Td>
                {["create", "read", "update", "delete", "export"].map((a) => (
                  <Td key={a}>{p.actions.includes(a) ? <Check className="h-4 w-4 text-primary" aria-label="yes" /> : <span className="text-subtle" aria-label="no">·</span>}</Td>
                ))}
                <Td className="text-[12.5px] text-muted-foreground">{p.scope}</Td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>
      <div className="min-w-0 space-y-6">
        <Card>
          <CardHeader title="Getting started" description={`${finished} of ${role.checklist.length} done`} icon={<Rocket className="h-4 w-4" />} />
          <CardBody className="space-y-2">
            <div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary" style={{ width: `${(finished / Math.max(role.checklist.length, 1)) * 100}%` }} /></div>
            {role.checklist.map((c, i) => (
              <label key={i} className="flex items-start gap-2 text-[13px]"><input id={`help-check-${i}`} type="checkbox" className="mt-0.5" checked={!!done[i]} onChange={() => toggle(i)} />
                <span className={cn(done[i] && "text-muted-foreground line-through")}>{c}</span></label>
            ))}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Your key areas" icon={<BookOpen className="h-4 w-4" />} />
          <CardBody className="space-y-1">
            {role.areas.map((a) => <Link key={a.key} href={`/help/areas/${a.key}`} className="block rounded-md px-2 py-1.5 text-[13.5px] hover:bg-muted">{a.title}</Link>)}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Tips" icon={<Lightbulb className="h-4 w-4" />} />
          <CardBody className="space-y-2 text-[13px] text-muted-foreground">{role.tips.map((t) => <p key={t}>{t}</p>)}</CardBody>
        </Card>
      </div>
    </div>
  );
}
