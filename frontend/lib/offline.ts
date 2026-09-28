/**
 * Offline outbox: notes, calls and tasks created (or tasks completed) without a connection are kept on this device and
 * sent in order when the connection returns. Only these everyday writes are queued; anything else fails as usual so
 * nobody believes a quote or an approval went through when it didn't. The outbox is cleared on sign-out.
 */
import type { InternalAxiosRequestConfig } from "axios";

const KEY = "cirra.outbox";
const QUEUEABLE: [string, RegExp][] = [
  ["post", /^\/activities$/],
  ["post", /^\/tasks$/],
  ["patch", /^\/tasks\/[0-9a-f-]{36}$/],
];

export interface OutboxItem { id: string; method: string; url: string; data: unknown; queued_at: string; label: string }

function read(): OutboxItem[] {
  try {
    return JSON.parse(window.localStorage.getItem(KEY) || "[]") as OutboxItem[];
  } catch {
    return [];
  }
}

function write(items: OutboxItem[]) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(items));
  } catch {
    /* storage full or blocked: the write fails like any offline request */
  }
  window.dispatchEvent(new CustomEvent("cirra:outbox", { detail: items.length }));
}

export function outbox(): OutboxItem[] {
  return typeof window === "undefined" ? [] : read();
}

export function clearOutbox() {
  if (typeof window !== "undefined") write([]);
}

export function queueable(config: InternalAxiosRequestConfig | undefined): boolean {
  if (!config?.url || !config.method) return false;
  const path = config.url.replace(/^https?:\/\/[^/]+/, "").replace(/^\/api\/v1/, "").split("?")[0];
  return QUEUEABLE.some(([m, re]) => m === config.method?.toLowerCase() && re.test(path));
}

function describe(method: string, url: string, data: Record<string, unknown> | null): string {
  if (url.startsWith("/activities")) return `Log ${String(data?.activity_type ?? "activity")}: ${String(data?.summary ?? "").slice(0, 60)}`;
  if (method === "post") return `New task: ${String(data?.title ?? "").slice(0, 60)}`;
  return "Update a task";
}

export function enqueue(config: InternalAxiosRequestConfig): OutboxItem {
  const data = typeof config.data === "string" ? JSON.parse(config.data) : config.data;
  const url = (config.url || "").replace(/^https?:\/\/[^/]+/, "").replace(/^\/api\/v1/, "");
  const item: OutboxItem = { id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, method: (config.method || "post").toLowerCase(), url, data,
    queued_at: new Date().toISOString(), label: describe((config.method || "post").toLowerCase(), url, data) };
  write([...read(), item]);
  return item;
}

/** Send the outbox in order. Stops at the first network failure; a request the server refuses is dropped (and reported). */
export async function flush(send: (item: OutboxItem) => Promise<unknown>): Promise<{ sent: number; failed: OutboxItem[] }> {
  let sent = 0;
  const failed: OutboxItem[] = [];
  for (const item of read()) {
    try {
      await send(item);
      sent += 1;
    } catch (e) {
      const status = (e as { response?: { status?: number } }).response?.status;
      if (!status) break;  // still offline: try again later
      failed.push(item);
    }
    write(read().filter((x) => x.id !== item.id));
  }
  return { sent, failed };
}
