"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Check, Map } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { PageHeader } from "@/components/AppShell";
import { AidenAvatar } from "@/components/Brand";
import { AreaIcon, HowToList } from "@/components/help";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/misc";
import { get } from "@/lib/api";
import type { HelpArea } from "@/lib/help";
import { ui } from "@/lib/store";

export default function HelpAreaPage() {
  const { key } = useParams<{ key: string }>();
  const area = useQuery({ queryKey: ["help", "area", key], queryFn: () => get<HelpArea>(`/help/areas/${key}`) });
  if (area.isError) return <p className="text-sm text-muted-foreground">No help article with that name. <Link href="/help?tab=areas" className="text-primary hover:underline">All capabilities</Link></p>;
  if (!area.data) return <div className="mx-auto max-w-4xl space-y-4"><Skeleton className="h-16" /><Skeleton className="h-72" /></div>;
  const a = area.data;
  return (
    <div className="mx-auto max-w-4xl">
      <Link href="/help?tab=areas" className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />Help center</Link>
      <PageHeader title={<span className="inline-flex items-center gap-2"><AreaIcon icon={a.icon} className="h-5 w-5 text-primary" />{a.title}</span>} description={a.summary}
        actions={<>
          <Link href={a.pages[0]}><Button variant="outline" size="sm">Open {a.title.split(/[,&]/)[0].trim().toLowerCase()}<ArrowRight className="h-3.5 w-3.5" /></Button></Link>
          <Button size="sm" onClick={() => ui.set({ helpOpen: true })}><AidenAvatar size={16} />Ask Aiden</Button>
        </>} />
      <div className="grid gap-6 lg:grid-cols-[1fr_300px]">
        <Card className="min-w-0">
          <CardHeader title="How to" />
          <CardBody><HowToList items={a.howto} page={a.pages[0]} /></CardBody>
        </Card>
        <div className="space-y-6">
          <Card>
            <CardHeader title="What it does" />
            <CardBody><ul className="space-y-1.5 text-[13px]">{a.capabilities.map((c) => <li key={c} className="flex gap-2"><Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />{c}</li>)}</ul></CardBody>
          </Card>
          {a.processes.length > 0 && (
            <Card>
              <CardHeader title="Part of these processes" icon={<Map className="h-4 w-4" />} />
              <CardBody className="space-y-1">{a.processes.map((p) => <Link key={p.key} href={`/help?tab=processes&p=${p.key}`} className="block rounded-md px-2 py-1.5 text-[13.5px] hover:bg-muted">{p.title}</Link>)}</CardBody>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
