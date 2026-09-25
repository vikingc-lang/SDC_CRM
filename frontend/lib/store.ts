"use client";

import { useSyncExternalStore } from "react";

/** Tiny global store for UI state shared across the shell (Quick-Log modal, copilot panel). */
type State = { quickLogOpen: boolean; quickLogText: string; quickLogAccountId: string | null; copilotOpen: boolean };
let state: State = { quickLogOpen: false, quickLogText: "", quickLogAccountId: null, copilotOpen: false };
const listeners = new Set<() => void>();

export const ui = {
  get: () => state,
  set(patch: Partial<State>) {
    state = { ...state, ...patch };
    listeners.forEach((l) => l());
  },
  subscribe(l: () => void) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  openQuickLog(opts: { text?: string; accountId?: string | null } = {}) {
    ui.set({ quickLogOpen: true, quickLogText: opts.text ?? "", quickLogAccountId: opts.accountId ?? null });
  },
};

export function useUI<T>(selector: (s: State) => T): T {
  return useSyncExternalStore(ui.subscribe, () => selector(state), () => selector(state));
}
