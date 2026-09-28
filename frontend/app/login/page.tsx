"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Brain, KeyRound, Lock, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Logo } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { MfaEnrollment, RecoveryCodes } from "@/components/security";
import { api, errorMessage, get, getWorkspace, setToken, setWorkspace } from "@/lib/api";

interface LoginResult { access_token?: string; mfa_required?: boolean; mfa_setup_required?: boolean; mfa_token?: string }
interface Methods { password: boolean; sso: { enabled: boolean; display_name: string } }

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
  // password → (code | enrol → recovery codes) → app
  const [step, setStep] = useState<"password" | "code" | "enrol" | "codes">("password");
  const [mfaToken, setMfaToken] = useState("");
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[]>([]);
  const [pendingToken, setPendingToken] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [workspace, setWs] = useState<string | null>(null);
  useEffect(() => {
    // ?workspace=acme picks a tenant workspace (when this site's host name doesn't already imply one)
    const q = new URLSearchParams(window.location.search).get("workspace");
    if (q !== null) setWorkspace(q.trim() || null);
    setWs(getWorkspace());
  }, []);
  const methods = useQuery({ queryKey: ["auth-methods"], queryFn: () => get<Methods>("/auth/methods"), retry: false });
  const finish = (token: string) => { setToken(token); router.replace("/"); };
  const login = useMutation({
    mutationFn: async () => (await api.post<LoginResult>("/auth/login", { username: email, password })).data,
    onSuccess: (d) => {
      if (d.access_token) return finish(d.access_token);
      setMfaToken(d.mfa_token ?? "");
      setStep(d.mfa_required ? "code" : "enrol");
    },
  });
  const verify = useMutation({
    mutationFn: async () => (await api.post<LoginResult>("/auth/mfa/verify", { mfa_token: mfaToken, code })).data,
    onSuccess: (d) => d.access_token && finish(d.access_token),
  });
  const sso = useMutation({
    mutationFn: async () => (await api.get<{ authorization_url: string }>("/auth/sso/start")).data,
    onSuccess: (d) => { window.location.href = d.authorization_url; },
  });
  const ssoOn = !!methods.data?.sso.enabled;
  const passwordOn = methods.data?.password !== false || showPassword;
  const back = () => { setStep("password"); setCode(""); verify.reset(); };

  return (
    <div className="grid min-h-screen lg:grid-cols-[1fr_1.1fr]">
      <div className="flex flex-col px-6 py-8 sm:px-12">
        <Logo height={32} />
        <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-12">
          {step === "code" && (
            <>
              <button type="button" onClick={back} className="mb-6 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft className="h-3.5 w-3.5" />Back</button>
              <h1 className="text-2xl font-semibold tracking-tight">Two-factor authentication</h1>
              <p className="mt-1 text-sm text-muted-foreground">Enter the 6-digit code from your authenticator app, or one of your recovery codes.</p>
              <form onSubmit={(e) => { e.preventDefault(); verify.mutate(); }} className="mt-8 space-y-4">
                <div><Label htmlFor="mfa-code">Code</Label>
                  <Input id="mfa-code" autoFocus inputMode="numeric" autoComplete="one-time-code" required placeholder="123 456" value={code} onChange={(e) => setCode(e.target.value)} /></div>
                {verify.isError && <p className="text-sm text-destructive">{errorMessage(verify.error, "Verification failed")}</p>}
                <Button type="submit" className="w-full" size="lg" loading={verify.isPending}>Verify<ArrowRight className="h-4 w-4" /></Button>
              </form>
            </>
          )}
          {step === "enrol" && (
            <>
              <h1 className="text-2xl font-semibold tracking-tight">Set up two-factor authentication</h1>
              <p className="mb-6 mt-1 text-sm text-muted-foreground">Your organisation requires a second sign-in step for your role.</p>
              <MfaEnrollment mfaToken={mfaToken} onDone={(c, token) => { setCodes(c); setPendingToken(token ?? ""); setStep("codes"); }} />
              <button type="button" onClick={back} className="mt-6 text-[13px] text-muted-foreground hover:text-foreground">Cancel</button>
            </>
          )}
          {step === "codes" && (
            <>
              <h1 className="text-2xl font-semibold tracking-tight">Save your recovery codes</h1>
              <div className="mt-6"><RecoveryCodes codes={codes} continueLabel="Continue to Cirra" onContinue={() => finish(pendingToken)} /></div>
            </>
          )}
          {step === "password" && <>
          <h1 className="text-2xl font-semibold tracking-tight">Welcome back</h1>
          <p className="mt-1 text-sm text-muted-foreground">Sign in to your private Cirra workspace.</p>
          {workspace && (
            <p className="mt-2 text-[12.5px] text-muted-foreground">Workspace <span className="font-medium text-foreground">{workspace}</span> ·{" "}
              <button type="button" className="text-primary hover:underline" onClick={() => { setWorkspace(null); setWs(null); }}>use the default</button></p>
          )}
          {ssoOn && (
            <div className="mt-8 space-y-3">
              <Button type="button" variant="outline" className="w-full" size="lg" loading={sso.isPending} onClick={() => sso.mutate()}><KeyRound className="h-4 w-4" />{methods.data!.sso.display_name}</Button>
              {sso.isError && <p className="text-sm text-destructive">{errorMessage(sso.error, "Single sign-on is unavailable")}</p>}
              {passwordOn ? <p className="flex items-center gap-3 text-[12px] text-subtle before:h-px before:flex-1 before:bg-border after:h-px after:flex-1 after:bg-border">or use your password</p>
                : <button type="button" onClick={() => setShowPassword(true)} className="text-[12px] text-subtle hover:text-foreground">Administrator sign-in with password</button>}
            </div>
          )}
          {passwordOn && <form onSubmit={(e) => { e.preventDefault(); login.mutate(); }} className={ssoOn ? "mt-3 space-y-4" : "mt-8 space-y-4"}>
            <div><Label htmlFor="email">Work email</Label><Input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></div>
            <div><Label htmlFor="password">Password</Label><Input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></div>
            {login.isError && <p className="text-sm text-destructive">{errorMessage(login.error, "Sign in failed")}</p>}
            <Button type="submit" className="w-full" size="lg" loading={login.isPending}>Sign in<ArrowRight className="h-4 w-4" /></Button>
          </form>}
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
          </>}
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
