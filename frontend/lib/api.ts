import axios, { AxiosError } from "axios";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "relate.token";

export const api = axios.create({ baseURL: `${API_URL}/api/v1` });

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
}

api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (error: AxiosError) => {
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
  if (e?.message === "Network Error") return "Can't reach the relate API. Is the backend running?";
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
