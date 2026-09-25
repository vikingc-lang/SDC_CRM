import * as React from "react";
import { cn } from "@/lib/utils";

type Tone = "neutral" | "primary" | "ai" | "good" | "warning" | "critical" | "outline";
const tones: Record<Tone, string> = {
  neutral: "bg-muted text-muted-foreground",
  primary: "bg-primary-soft text-primary",
  ai: "bg-ai-soft text-ai",
  good: "bg-[color-mix(in_srgb,var(--status-good)_14%,transparent)] text-foreground",
  warning: "bg-[color-mix(in_srgb,var(--status-warning)_18%,transparent)] text-foreground",
  critical: "bg-[color-mix(in_srgb,var(--status-critical)_14%,transparent)] text-foreground",
  outline: "border border-border text-muted-foreground",
};

export function Badge({ tone = "neutral", className, ...props }: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  return (
    <span
      className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11.5px] font-medium leading-4", tones[tone], className)}
      {...props}
    />
  );
}
