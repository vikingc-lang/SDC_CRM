"use client";

import { AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { Registration } from "@/lib/types";

export interface CommissionReport {
  partners: { partner_id: string; partner: string; tier: string; attributed_pipeline: number; attributed_won: number; commission_earned: number; deals: number }[];
  lines: { deal_id?: string; deal: string; account: string; stage: string; partner: string; role: string; split_pct: number; rate_pct: number; amount_usd: number; attributed_usd: number; commission_usd: number; status: string }[];
}
export interface CollateralItem {
  id: string; title: string; description: string | null; category: string; min_tier: string; allowed_domains: string[]; is_published: boolean; downloads: number;
  file: { filename: string; size_bytes: number; content_type: string }; created_at: string;
}

const TIER_TONE: Record<string, "neutral" | "primary" | "warning" | "ai"> = { registered: "neutral", silver: "primary", gold: "warning", platinum: "ai" };
export const TierBadge = ({ tier }: { tier: string }) => <Badge tone={TIER_TONE[tier] ?? "neutral"} className="capitalize">{tier}</Badge>;

export function ConflictList({ conflicts }: { conflicts: Registration["conflicts"] }) {
  if (!conflicts.length) return null;
  return (
    <ul className="mt-2 space-y-1">
      {conflicts.map((c, i) => (
        <li key={i} className="flex items-start gap-1.5 text-[12.5px]" style={{ color: c.severity === "high" ? "var(--status-critical)" : "var(--status-warning)" }}>
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            {c.type === "existing_account" ? `Existing account${c.account ? ` ${c.account}` : ""}${c.open_deals ? ` with ${c.open_deals} open deal(s)` : ""}`
              : c.type === "exclusivity" ? `Territory exclusivity already held${c.partner ? ` by ${c.partner}` : ""}${c.expires ? ` until ${c.expires}` : ""}`
              : c.type.replace(/_/g, " ")}
          </span>
        </li>
      ))}
    </ul>
  );
}

