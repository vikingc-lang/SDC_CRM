"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { useState } from "react";
import { Toaster } from "sonner";
import { LocaleProvider } from "@/lib/i18n";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    // Offline: reads still run (the service worker answers from this device's copy) and writes are sent, so the
    // everyday ones land in the offline outbox (lib/offline.ts) instead of hanging.
    () => new QueryClient({ defaultOptions: {
      queries: { staleTime: 15_000, refetchOnWindowFocus: false, retry: 1, networkMode: "offlineFirst" },
      mutations: { networkMode: "always" },
    } }),
  );
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <QueryClientProvider client={client}>
        <LocaleProvider>{children}</LocaleProvider>
        <Toaster position="bottom-right" richColors closeButton toastOptions={{ className: "font-sans" }} />
      </QueryClientProvider>
    </ThemeProvider>
  );
}
