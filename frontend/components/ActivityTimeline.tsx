import { ArrowRightLeft, Mail, MessageSquare, Phone, StickyNote, Users } from "lucide-react";
import Link from "next/link";
import { SentimentIcon } from "@/components/indicators";
import type { Activity, ActivityType } from "@/lib/types";
import { cn, relativeDays, shortDate } from "@/lib/utils";

const ICONS: Record<ActivityType, typeof Mail> = { meeting: Users, call: Phone, email: Mail, note: StickyNote, system: ArrowRightLeft };

export function ActivityTimeline({ activities, showAccount = false, empty }: { activities: Activity[]; showAccount?: boolean; empty?: React.ReactNode }) {
  if (!activities.length) return <>{empty ?? <p className="py-6 text-center text-sm text-muted-foreground">No activity yet.</p>}</>;
  return (
    <ol className="relative">
      {activities.map((a, i) => {
        const Icon = ICONS[a.type] ?? MessageSquare;
        const system = a.type === "system";
        return (
          <li key={a.id} className="relative flex gap-3 pb-5 last:pb-0">
            {i < activities.length - 1 && <span className="absolute left-[13px] top-7 h-[calc(100%-20px)] w-px bg-border" aria-hidden />}
            <span className={cn("relative z-[1] flex h-7 w-7 shrink-0 items-center justify-center rounded-full border bg-surface", system ? "text-subtle" : "text-muted-foreground")}>
              <Icon className="h-3.5 w-3.5" />
            </span>
            <div className="min-w-0 flex-1 pt-0.5">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[12px] text-muted-foreground">
                <span className="font-medium capitalize text-foreground">{system ? "Stage change" : a.type}</span>
                {showAccount && a.account && <Link href={`/accounts/${a.account.id}`} className="hover:text-foreground hover:underline">{a.account.name}</Link>}
                {a.user && !showAccount && <span>· {a.user.full_name}</span>}
                <span title={shortDate(a.date, true)}>· {relativeDays(a.date)}</span>
                {!system && <SentimentIcon sentiment={a.sentiment} className="ml-0.5" />}
              </div>
              <p className={cn("mt-1 text-[13.5px] leading-relaxed", system && "text-muted-foreground")}>{a.summary}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
