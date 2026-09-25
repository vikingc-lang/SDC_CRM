"use client";

import Link from "next/link";
import type { Activity } from "@/lib/types";
import { shortDate } from "@/lib/utils";

export interface AskResponse { answer: string; sources: Activity[]; engine: string }

export function AnswerBlock({ answer, onNavigate }: { answer: AskResponse; onNavigate?: () => void }) {
  return (
    <div className="animate-slide-up">
      <div className="whitespace-pre-wrap text-[14px] leading-relaxed">{answer.answer}</div>
      {answer.sources.length > 0 && (
        <div className="mt-3 space-y-1.5">
          <p className="text-[11px] font-medium uppercase tracking-wide text-subtle">Sources</p>
          {answer.sources.slice(0, 4).map((s, i) => (
            <Link
              key={s.id}
              href={s.deal ? `/deals/${s.deal.id}` : s.account ? `/accounts/${s.account.id}` : "#"}
              onClick={onNavigate}
              className="flex gap-2 rounded-md border bg-surface-2/50 px-2.5 py-2 text-[12.5px] transition-colors hover:border-primary/40"
            >
              <span className="font-semibold text-primary">[{i + 1}]</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{s.account?.name} · {shortDate(s.date)}</span>
                <span className="line-clamp-2 text-muted-foreground">{s.summary}</span>
              </span>
              {s.similarity != null && <span className="tabular shrink-0 text-subtle">{Math.round(s.similarity * 100)}%</span>}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
