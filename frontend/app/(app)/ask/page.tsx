"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowUp, Building2, Search, Sparkles } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { AnswerBlock, type AskResponse } from "@/components/AskAnswer";
import { SentimentIcon } from "@/components/indicators";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage } from "@/lib/api";
import type { Activity } from "@/lib/types";
import { shortDate } from "@/lib/utils";

const PROMPTS = ["Which deals are at risk and why?", "What's our weighted forecast this quarter?", "Where are competitors showing up?", "Who are our champions?", "Security or InfoSec concerns", "Why did we lose deals?"];

function AskInner() {
  const params = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const ask = useMutation({ mutationFn: async (question: string) => (await api.post<AskResponse>("/ai/ask", { question })).data });
  const [memoryQuery, setMemoryQuery] = useState("");
  const memory = useQuery({
    queryKey: ["semantic", memoryQuery],
    queryFn: async () => (await api.post<Activity[]>("/search/semantic", { query: memoryQuery, limit: 12 })).data,
    enabled: memoryQuery.length > 1,
  });

  const run = (question: string) => {
    if (!question.trim()) return;
    setQ(question);
    ask.mutate(question);
    setMemoryQuery(question);
  };

  useEffect(() => {
    const initial = params.get("q");
    if (initial) run(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="mx-auto max-w-3xl">
      <div className="pb-6 pt-4 text-center">
        <span className="ai-gradient mx-auto flex h-11 w-11 items-center justify-center rounded-2xl shadow-pop"><Sparkles className="h-5 w-5 text-white" /></span>
        <h1 className="mt-4 text-[26px] font-semibold tracking-tight">Ask <span className="ai-gradient-text">relate</span></h1>
        <p className="mt-1 text-sm text-muted-foreground">Semantic search across every note, call and email, grounded in live pipeline data.</p>
      </div>
      <form onSubmit={(e) => { e.preventDefault(); run(q); }} className="flex items-center gap-2 rounded-2xl border bg-surface p-2 shadow-pop focus-within:border-ring">
        <Search className="ml-2 h-4 w-4 shrink-0 text-subtle" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. Which deals mention data residency?" className="h-10 flex-1 bg-transparent text-[15px] outline-none placeholder:text-subtle" autoFocus />
        <button type="submit" disabled={!q.trim() || ask.isPending} className="ai-gradient flex h-9 w-9 items-center justify-center rounded-xl text-white disabled:opacity-40" aria-label="Ask"><ArrowUp className="h-4 w-4" /></button>
      </form>
      {!ask.data && !ask.isPending && (
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          {PROMPTS.map((p) => <button key={p} onClick={() => run(p)} className="rounded-full border bg-surface px-3 py-1.5 text-[13px] text-muted-foreground hover:border-primary/40 hover:text-foreground">{p}</button>)}
        </div>
      )}
      {ask.isPending && <Card className="mt-6 space-y-2 p-5"><Skeleton className="w-3/4" /><Skeleton className="w-1/2" /><Skeleton className="w-2/3" /></Card>}
      {ask.isError && <p className="mt-6 text-sm text-destructive">{errorMessage(ask.error)}</p>}
      {ask.data && (
        <Card className="mt-6 p-5">
          <p className="mb-2 flex items-center gap-1.5 text-[11.5px] font-medium uppercase tracking-wide text-ai"><Sparkles className="h-3.5 w-3.5" />Answer</p>
          <AnswerBlock answer={{ ...ask.data, sources: [] }} />
        </Card>
      )}
      {memory.data && memory.data.length > 0 && (
        <div className="mt-8">
          <h2 className="mb-3 text-[13px] font-semibold text-muted-foreground">Related records · hybrid retrieval (full-text + vector, rank-fused)</h2>
          <div className="space-y-2">
            {memory.data.map((m) => m.entity === "account" ? (
              <Link key={`a-${m.id}`} href={`/accounts/${m.id}`} className="flex items-center gap-3 rounded-lg border bg-surface p-3.5 shadow-card transition-colors hover:border-primary/40">
                <Building2 className="h-4 w-4 text-muted-foreground" />
                <span className="min-w-0 flex-1"><span className="block text-[13.5px] font-medium">{m.name}</span><span className="text-[12px] text-muted-foreground">{m.summary}</span></span>
                <MatchChips m={m} />
              </Link>
            ) : (
              <Link key={m.id} href={m.deal ? `/deals/${m.deal.id}` : `/accounts/${m.account?.id}`} className="block rounded-lg border bg-surface p-3.5 shadow-card transition-colors hover:border-primary/40">
                <div className="flex items-center gap-2 text-[12px] text-muted-foreground">
                  <span className="font-medium text-foreground">{m.account?.name}</span>
                  <span className="capitalize">· {m.type}</span>
                  <span>· {shortDate(m.date, true)}</span>
                  <SentimentIcon sentiment={m.sentiment} />
                  <span className="ml-auto"><MatchChips m={m} /></span>
                </div>
                <p className="mt-1.5 text-[13.5px] leading-relaxed">{m.summary}</p>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function MatchChips({ m }: { m: Activity }) {
  if (m.matched_by?.length) {
    return (
      <span className="flex shrink-0 gap-1">
        {m.matched_by.map((k) => <span key={k} className="rounded-full border px-1.5 text-[10.5px] text-muted-foreground">{k === "vector" ? "semantic" : "keyword"}</span>)}
      </span>
    );
  }
  return m.similarity != null ? <span className="tabular text-[12px] text-subtle">{Math.round(m.similarity * 100)}%</span> : null;
}

export default function AskPage() {
  return <Suspense><AskInner /></Suspense>;
}
