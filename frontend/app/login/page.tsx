"use client";

import { useMutation } from "@tanstack/react-query";
import { ArrowRight, Brain, Lock, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Logo } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { api, errorMessage, setToken } from "@/lib/api";

const DEMO_USERS = [
  { email: "marcus@cirra.demo", role: "Sales Manager" },
  { email: "priya@cirra.demo", role: "Account Executive" },
  { email: "sam@cirra.demo", role: "SDR" },
  { email: "admin@cirra.demo", role: "Super Admin" },
  { email: "viewer@cirra.demo", role: "Auditor" },
  { email: "partner@northstar-partners.com", role: "Partner portal" },
];

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("marcus@cirra.demo");
  const [password, setPassword] = useState("cirra123");
  const login = useMutation({
    mutationFn: async () => (await api.post<{ access_token: string }>("/auth/login", { username: email, password })).data,
    onSuccess: (d) => {
      setToken(d.access_token);
      router.replace("/");
    },
  });

  return (
    <div className="grid min-h-screen lg:grid-cols-[1fr_1.1fr]">
      <div className="flex flex-col px-6 py-8 sm:px-12">
        <Logo height={32} />
        <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-12">
          <h1 className="text-2xl font-semibold tracking-tight">Welcome back</h1>
          <p className="mt-1 text-sm text-muted-foreground">Sign in to your private Cirra workspace.</p>
          <form onSubmit={(e) => { e.preventDefault(); login.mutate(); }} className="mt-8 space-y-4">
            <div><Label htmlFor="email">Work email</Label><Input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></div>
            <div><Label htmlFor="password">Password</Label><Input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></div>
            {login.isError && <p className="text-sm text-destructive">{errorMessage(login.error, "Sign in failed")}</p>}
            <Button type="submit" className="w-full" size="lg" loading={login.isPending}>Sign in<ArrowRight className="h-4 w-4" /></Button>
          </form>
          <div className="mt-8 rounded-lg border border-dashed p-3">
            <p className="text-[12px] font-medium text-muted-foreground">Demo accounts · password <code className="font-mono">cirra123</code></p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {DEMO_USERS.map((u) => (
                <button key={u.email} type="button" onClick={() => { setEmail(u.email); setPassword("cirra123"); }} className="rounded-full border px-2.5 py-1 text-[12px] text-muted-foreground hover:border-primary/40 hover:text-foreground">
                  {u.role}
                </button>
              ))}
            </div>
          </div>
        </div>
        <p className="text-[12px] text-subtle">Cirra is part of the SDC Solutions portfolio of distinguished products.</p>
      </div>
      <div className="relative hidden overflow-hidden border-l bg-surface-2 lg:block">
        <div className="absolute -right-24 -top-24 h-96 w-96 rounded-full ai-gradient opacity-20 blur-3xl" />
        <div className="absolute -bottom-32 left-10 h-80 w-80 rounded-full ai-gradient opacity-10 blur-3xl" />
        <div className="relative flex h-full flex-col justify-center px-14">
          <p className="text-[13px] font-medium italic text-primary">Connect what matters.</p>
          <h2 className="mt-3 max-w-md text-4xl font-semibold leading-tight tracking-tight">
            A CRM built around relationships, <span className="ai-gradient-text">not just records.</span>
          </h2>
          <div className="mt-10 max-w-md space-y-3">
            {[
              { icon: Sparkles, title: "Quick-Log from meeting notes", body: "Paste notes, dictate or log a conversation, and Cirra files it against the right account and deal." },
              { icon: Brain, title: "Deal risk you can see", body: "Every deal gets a risk score with the reason (stale, no champion, sentiment drop) while there\u2019s still time to act." },
              { icon: Lock, title: "Private by design", body: "Single-tenant, self-hosted, and your models run inside your own cloud." },
            ].map(({ icon: Icon, title, body }) => (
              <div key={title} className="flex gap-3 rounded-xl border bg-surface/80 p-4 shadow-card backdrop-blur">
                <Icon className="mt-0.5 h-4 w-4 shrink-0 text-ai" />
                <div><p className="text-sm font-medium">{title}</p><p className="mt-0.5 text-[13px] text-muted-foreground">{body}</p></div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
