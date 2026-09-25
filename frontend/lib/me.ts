"use client";

import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/api";
import type { Action, Me } from "@/lib/types";

export function useMe() {
  const q = useQuery({ queryKey: ["me"], queryFn: () => get<Me>("/users/me"), staleTime: 60_000 });
  const can = (resource: string, action: Action = "read") => !!q.data?.permissions?.[resource]?.[action];
  return { me: q.data, can, isLoading: q.isLoading };
}

export const ROLE_LABELS: Record<string, string> = {
  super_admin: "Super Admin", sales_manager: "Sales Manager", account_executive: "Account Executive", sdr: "SDR",
  auditor: "Auditor", partner: "Partner",
};
