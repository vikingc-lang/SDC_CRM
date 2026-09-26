"use client";

import { Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { Logo } from "@/components/AppShell";
import { api, errorMessage, setToken } from "@/lib/api";

/** The identity provider redirects here with ?code&state; the API verifies them and issues a Cirra session. */
function Callback() {
  const params = useSearchParams();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const sent = useRef(false);
  useEffect(() => {
    if (sent.current) return;  // the code is single use: never post it twice (StrictMode double effects)
    sent.current = true;
    const idpError = params.get("error_description") ?? params.get("error");
    const code = params.get("code"), state = params.get("state");
    if (idpError || !code || !state) {
      setError(idpError ?? "The identity provider didn't return a sign-in code.");
      return;
    }
    api.post<{ access_token: string; return_to: string | null }>("/auth/sso/callback", { code, state })
      .then(({ data }) => { setToken(data.access_token); router.replace(data.return_to ?? "/"); })
      .catch((e) => setError(errorMessage(e, "Single sign-on failed")));
  }, [params, router]);
  return error ? (
    <div className="space-y-3">
      <h1 className="text-xl font-semibold tracking-tight">Couldn&apos;t sign you in</h1>
      <p className="text-sm text-destructive">{error}</p>
      <Link href="/login" className="inline-block text-sm text-primary hover:underline">Back to sign-in</Link>
    </div>
  ) : (
    <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Signing you in…</p>
  );
}

export default function SsoCallbackPage() {
  return (
    <div className="flex min-h-screen flex-col px-6 py-8 sm:px-12">
      <Logo height={32} />
      <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center">
        <Suspense fallback={null}><Callback /></Suspense>
      </div>
    </div>
  );
}
