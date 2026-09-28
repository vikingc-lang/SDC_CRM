import axios, { AxiosError, type AxiosResponse } from "axios";
import { clearOutbox, enqueue, queueable } from "@/lib/offline";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "cirra.token";

export const api = axios.create({ baseURL: `${API_URL}/api/v1` });
const WORKSPACE_KEY = "cirra.workspace";

/** The tenant workspace to sign in to, when it isn't implied by this site's host name (``?workspace=acme`` on the
 * sign-in page remembers it). The API also routes by the host name the web app sends. */
export function getWorkspace(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(WORKSPACE_KEY);
  } catch {
    return null;
  }
}

export function setWorkspace(slug: string | null) {
  try {
    if (slug) window.localStorage.setItem(WORKSPACE_KEY, slug.toLowerCase());
    else window.localStorage.removeItem(WORKSPACE_KEY);
  } catch {
    /* storage unavailable */
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: session-only auth */
  }
  if (!token && typeof window !== "undefined") {
    // signing out: forget queued changes and every API answer kept on this device
    clearOutbox();
    navigator.serviceWorker?.controller?.postMessage({ type: "clear" });
  }
}

/** Headers for requests made without the api client (file downloads with fetch). */
export function authHeaders(): Record<string, string> {
  const h: Record<string, string> = {};
  const token = getToken();
  if (token) h.Authorization = `Bearer ${token}`;
  if (typeof window !== "undefined") {
    h["X-Cirra-Host"] = window.location.host;
    const ws = getWorkspace();
    if (ws) h["X-Cirra-Tenant"] = ws;
  }
  return h;
}

api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  if (typeof window !== "undefined") {
    config.headers["X-Cirra-Host"] = window.location.host;
    const ws = getWorkspace();
    if (ws) config.headers["X-Cirra-Tenant"] = ws;
  }
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (error: AxiosError) => {
    if (!error.response && queueable(error.config) && typeof window !== "undefined" && error.config) {
      // offline: keep the note or task on this device and send it when the connection is back
      const item = enqueue(error.config);
      return Promise.resolve({ data: { queued: true, id: item.id }, status: 202, statusText: "Queued", headers: {}, config: error.config } as AxiosResponse);
    }
    if (error.response?.status === 401 && typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      setToken(null);
      window.location.href = "/login";
    }
    return Promise.reject(error);
  },
);

export function errorMessage(error: unknown, fallback = "Something went wrong"): string {
  const e = error as AxiosError<{ detail?: unknown }>;
  const detail = e?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join(", ") || fallback;
  if (e?.message === "Network Error") return "Can't reach the Cirra API. Is the backend running?";
  return fallback;
}

export const get = async <T,>(url: string, params?: object) => (await api.get<T>(url, { params })).data;

/** Authenticated file download (exports, PDFs, collateral) via a temporary object URL. */
export async function downloadFile(path: string, filename: string, params?: object) {
  const res = await api.get(path, { params, responseType: "blob" });
  const url = URL.createObjectURL(res.data as Blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
