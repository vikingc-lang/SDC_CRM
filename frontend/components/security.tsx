"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, KeyRound, PlugZap, Save, ShieldCheck, Smartphone } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Label, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get, setToken } from "@/lib/api";
import { ROLE_LABELS, useMe } from "@/lib/me";

interface Enrollment { secret: string; otpauth_uri: string; qr_svg: string }

/** Scan-and-confirm TOTP enrolment. With `mfaToken` it finishes a forced first sign-in and returns an access token. */
export function MfaEnrollment({ mfaToken, onDone }: { mfaToken?: string; onDone: (codes: string[], accessToken?: string) => void }) {
  const [code, setCode] = useState("");
  const start = useMutation({
    mutationFn: async () => (await api.post<Enrollment>("/auth/mfa/enroll/start", { mfa_token: mfaToken ?? null })).data,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const confirm = useMutation({
    mutationFn: async () => (await api.post<{ recovery_codes: string[]; access_token?: string }>("/auth/mfa/enroll/confirm",
      { code, mfa_token: mfaToken ?? null })).data,
    onSuccess: (r) => onDone(r.recovery_codes, r.access_token),
  });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { start.mutate(); }, []);
  const e = start.data;
  return (
    <div className="space-y-4">
      <ol className="space-y-1 text-[13px] text-muted-foreground">
        <li>1. Open an authenticator app (Microsoft Authenticator, Google Authenticator, 1Password, Authy…).</li>
        <li>2. Scan the QR code, or enter the setup key by hand.</li>
        <li>3. Enter the 6-digit code the app shows.</li>
      </ol>
      <div className="flex flex-wrap items-center gap-4">
        {e ? (
          // Server-rendered QR (qrcode SVG path image); contains only the otpauth URI
          <div className="h-40 w-40 shrink-0 rounded-lg border bg-white p-1.5 [&_svg]:h-full [&_svg]:w-full" dangerouslySetInnerHTML={{ __html: e.qr_svg }} />
        ) : <Skeleton className="h-40 w-40" />}
        <div className="min-w-0 flex-1 space-y-1">
          <p className="text-[12px] font-medium text-muted-foreground">Setup key</p>
          <code className="block break-all rounded-md bg-muted px-2 py-1.5 font-mono text-[12.5px] tracking-wide">{e?.secret.replace(/(.{4})/g, "$1 ").trim() ?? "…"}</code>
          <p className="text-[12px] text-subtle">Time-based, 6 digits, 30 seconds</p>
        </div>
      </div>
      <form className="flex items-end gap-2" onSubmit={(ev) => { ev.preventDefault(); confirm.mutate(); }}>
        <div className="flex-1"><Label htmlFor="mfa-enrol-code">Code from the app</Label>
          <Input id="mfa-enrol-code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}" maxLength={7} required placeholder="123 456"
            value={code} onChange={(ev) => setCode(ev.target.value)} /></div>
        <Button type="submit" loading={confirm.isPending} disabled={!e}>Verify</Button>
      </form>
      {confirm.isError && <p className="text-sm text-destructive">{errorMessage(confirm.error)}</p>}
    </div>
  );
}

export function RecoveryCodes({ codes, onContinue, continueLabel = "I've saved these codes" }: { codes: string[]; onContinue: () => void; continueLabel?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(codes.join("\n")); setCopied(true); } catch { toast.error("Copy failed. Select the codes and copy them by hand."); }
  };
  return (
    <div className="space-y-4">
      <p className="text-[13px] text-muted-foreground">
        Each code signs you in once if you lose your phone. Store them somewhere safe, like a password manager. They won&apos;t be shown again.
      </p>
      <div className="grid grid-cols-2 gap-1.5 rounded-lg border bg-muted/50 p-3 font-mono text-[13px] tabular-nums">
        {codes.map((c) => <span key={c}>{c}</span>)}
      </div>
      <div className="flex justify-between gap-2">
        <Button variant="outline" size="sm" onClick={copy}>{copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}{copied ? "Copied" : "Copy codes"}</Button>
        <Button size="sm" onClick={onContinue}>{continueLabel}</Button>
      </div>
    </div>
  );
}

/** Settings → Two-factor authentication for the signed-in user. */
export function SecurityCard() {
  const qc = useQueryClient();
  const { me } = useMe();
  const [dialog, setDialog] = useState<null | "enrol" | "codes" | "regen" | "disable">(null);
  const [codes, setCodes] = useState<string[]>([]);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const close = () => { setDialog(null); setCode(""); setPassword(""); qc.invalidateQueries({ queryKey: ["me"] }); };
  const regen = useMutation({
    mutationFn: async () => (await api.post<{ recovery_codes: string[] }>("/auth/mfa/recovery-codes", { code })).data,
    onSuccess: (r) => { setCodes(r.recovery_codes); setCode(""); setDialog("codes"); },
  });
  const disable = useMutation({
    mutationFn: async () => (await api.post<{ access_token: string }>("/auth/mfa/disable", { code, password })).data,
    onSuccess: (r) => { setToken(r.access_token); close(); toast.success("Two-factor authentication is off"); },
  });
  const s = me?.security;
  if (!s) return null;
  return (
    <Card>
      <CardHeader title="Two-factor authentication" icon={<ShieldCheck className="h-4 w-4" />}
        description="A code from your phone at every sign-in, so a stolen password alone can't open your account."
        action={s.mfa_enabled ? <Badge tone="good">On</Badge> : s.sso_linked && !s.has_password ? <Badge tone="neutral">Via single sign-on</Badge> : <Badge tone="warning">Off</Badge>} />
      <CardBody className="flex flex-wrap items-center justify-between gap-3">
        {s.mfa_enabled ? (
          <>
            <p className="text-[13px] text-muted-foreground"><Smartphone className="mr-1 inline h-3.5 w-3.5" />Authenticator app · {s.recovery_codes_left} recovery code{s.recovery_codes_left === 1 ? "" : "s"} left
              {s.recovery_codes_left <= 3 && <span className="text-destructive">. Generate new ones soon.</span>}</p>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={() => setDialog("regen")}><KeyRound className="h-3.5 w-3.5" />New recovery codes</Button>
              {!s.mfa_required && <Button size="sm" variant="ghost" onClick={() => setDialog("disable")}>Turn off</Button>}
            </div>
          </>
        ) : (
          <>
            <p className="text-[13px] text-muted-foreground">{s.sso_linked && !s.has_password ? "You sign in through your organisation's identity provider, which handles its own second factor." : "Use any authenticator app. Takes about a minute."}</p>
            {s.has_password && <Button size="sm" onClick={() => setDialog("enrol")}><Smartphone className="h-3.5 w-3.5" />Set up</Button>}
          </>
        )}
      </CardBody>
      <Dialog open={!!dialog} onOpenChange={(o) => !o && close()}>
        <DialogContent title="Two-factor authentication" className="max-w-md">
          <div className="p-5">
            {dialog === "enrol" && <><h2 className="mb-3 text-[15px] font-semibold">Set up your authenticator</h2>
              <MfaEnrollment onDone={(c) => { setCodes(c); setDialog("codes"); toast.success("Two-factor authentication is on"); }} /></>}
            {dialog === "codes" && <><h2 className="mb-3 text-[15px] font-semibold">Save your recovery codes</h2><RecoveryCodes codes={codes} onContinue={close} /></>}
            {(dialog === "regen" || dialog === "disable") && (
              <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (dialog === "regen") regen.mutate(); else disable.mutate(); }}>
                <h2 className="text-[15px] font-semibold">{dialog === "regen" ? "Generate new recovery codes" : "Turn off two-factor authentication"}</h2>
                <p className="text-[13px] text-muted-foreground">{dialog === "regen" ? "Your old codes stop working." : "You'll sign in with just your password."} Confirm with a code from your authenticator app.</p>
                {dialog === "disable" && <div><Label htmlFor="mfa-pw">Password</Label><Input id="mfa-pw" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></div>}
                <div><Label htmlFor="mfa-code">Authenticator code</Label><Input id="mfa-code" inputMode="numeric" autoComplete="one-time-code" required value={code} onChange={(e) => setCode(e.target.value)} /></div>
                {(regen.isError || disable.isError) && <p className="text-sm text-destructive">{errorMessage(regen.error ?? disable.error)}</p>}
                <div className="flex justify-end"><Button type="submit" size="sm" variant={dialog === "disable" ? "destructive" : "primary"} loading={regen.isPending || disable.isPending}>
                  {dialog === "regen" ? "Generate codes" : "Turn off"}</Button></div>
              </form>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

interface SecurityPolicy {
  mfa_required_roles: string[];
  sso: { enabled: boolean; enforce: boolean; display_name: string; issuer: string; client_id: string; client_secret_set: boolean; scopes: string;
    allowed_domains: string[]; auto_provision: boolean; default_role: string; redirect_uri: string };
}

/** Admin → Sign-in security: MFA policy per role and OpenID Connect single sign-on. */
export function SecurityPanel() {
  const qc = useQueryClient();
  const pol = useQuery({ queryKey: ["admin", "security"], queryFn: () => get<SecurityPolicy>("/admin/security") });
  const [f, setF] = useState<(SecurityPolicy & { client_secret: string; domains: string }) | null>(null);
  useEffect(() => { if (pol.data) setF({ ...pol.data, client_secret: "", domains: pol.data.sso.allowed_domains.join(", ") }); }, [pol.data]);
  const save = useMutation({
    mutationFn: async () => (await api.put<SecurityPolicy>("/admin/security", {
      mfa_required_roles: f!.mfa_required_roles,
      sso: { ...f!.sso, allowed_domains: f!.domains.split(/[,\s]+/).filter(Boolean), ...(f!.client_secret ? { client_secret: f!.client_secret } : {}) },
    })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["admin", "security"] }); toast.success("Sign-in security saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const test = useMutation({
    mutationFn: async () => (await api.post<{ issuer: string; authorization_endpoint: string; pkce: boolean }>("/admin/security/sso/test", { issuer: f!.sso.issuer })).data,
    onSuccess: (r) => toast.success(`Connected to ${r.issuer}${r.pkce ? "" : ". Warning: this provider doesn't advertise PKCE"}`),
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (!f) return <Skeleton className="h-96" />;
  const sso = f.sso;
  const setSso = (patch: Partial<SecurityPolicy["sso"]>) => setF({ ...f, sso: { ...sso, ...patch } });
  const toggleRole = (r: string) => setF({ ...f, mfa_required_roles: f.mfa_required_roles.includes(r) ? f.mfa_required_roles.filter((x) => x !== r) : [...f.mfa_required_roles, r] });
  return (
    <form className="space-y-6" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <Card>
        <CardHeader title="Two-factor authentication" icon={<Smartphone className="h-4 w-4" />}
          description="Roles ticked here must set up an authenticator app at their next password sign-in and can't turn it off. Anyone else can opt in from Settings." />
        <CardBody className="flex flex-wrap gap-2">
          {Object.entries(ROLE_LABELS).map(([k, l]) => (
            <label key={k} className="flex items-center gap-2 rounded-md border px-3 py-1.5 text-[13px]">
              <input id={`mfa-role-${k}`} type="checkbox" checked={f.mfa_required_roles.includes(k)} onChange={() => toggleRole(k)} />{l}
            </label>
          ))}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Single sign-on (OpenID Connect)" icon={<KeyRound className="h-4 w-4" />}
          description="Works with Microsoft Entra ID, Okta, Google Workspace, Keycloak, ADFS and any OpenID Connect provider."
          action={sso.enabled ? <Badge tone="good">On</Badge> : <Badge tone="neutral">Off</Badge>} />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <div className="sm:col-span-2 rounded-md bg-muted p-3 text-[12.5px]">
            <p className="font-medium">Register Cirra with your identity provider using this redirect URI</p>
            <code className="mt-1 block break-all font-mono">{sso.redirect_uri}</code>
          </div>
          <div><Label htmlFor="sso-name">Button label</Label><Input id="sso-name" value={sso.display_name} onChange={(e) => setSso({ display_name: e.target.value })} placeholder="Sign in with Okta" /></div>
          <div><Label htmlFor="sso-issuer">Issuer URL</Label>
            <div className="flex gap-2"><Input id="sso-issuer" value={sso.issuer} onChange={(e) => setSso({ issuer: e.target.value })} placeholder="https://login.microsoftonline.com/<tenant>/v2.0" />
              <Button type="button" variant="outline" size="md" onClick={() => test.mutate()} loading={test.isPending} title="Check the issuer's discovery document"><PlugZap className="h-3.5 w-3.5" />Test</Button></div></div>
          <div><Label htmlFor="sso-client">Client ID</Label><Input id="sso-client" value={sso.client_id} onChange={(e) => setSso({ client_id: e.target.value })} /></div>
          <div><Label htmlFor="sso-secret">Client secret</Label><Input id="sso-secret" type="password" autoComplete="new-password" value={f.client_secret}
            onChange={(e) => setF({ ...f, client_secret: e.target.value })} placeholder={sso.client_secret_set ? "Saved. Leave blank to keep it." : "Not needed for public clients"} /></div>
          <div><Label htmlFor="sso-scopes">Scopes</Label><Input id="sso-scopes" value={sso.scopes} onChange={(e) => setSso({ scopes: e.target.value })} /></div>
          <div><Label htmlFor="sso-domains">Allowed email domains</Label><Input id="sso-domains" value={f.domains} onChange={(e) => setF({ ...f, domains: e.target.value })} placeholder="example.com, example.co.uk (blank = any)" /></div>
          <label className="flex items-start gap-2 text-[13px]"><input id="sso-provision" type="checkbox" className="mt-0.5" checked={sso.auto_provision} onChange={(e) => setSso({ auto_provision: e.target.checked })} />
            <span>Create users on first sign-in<span className="block text-[12px] text-muted-foreground">Otherwise only invited users can sign in.</span></span></label>
          <div><Label htmlFor="sso-role">Role for new users</Label>
            <Select id="sso-role" value={sso.default_role} disabled={!sso.auto_provision} onChange={(e) => setSso({ default_role: e.target.value })}>
              {Object.entries(ROLE_LABELS).filter(([k]) => !["super_admin", "partner"].includes(k)).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </Select></div>
          <label className="flex items-start gap-2 text-[13px]"><input id="sso-enabled" type="checkbox" className="mt-0.5" checked={sso.enabled} onChange={(e) => setSso({ enabled: e.target.checked })} />
            <span>Show the single sign-on button on the sign-in page</span></label>
          <label className="flex items-start gap-2 text-[13px]"><input id="sso-enforce" type="checkbox" className="mt-0.5" checked={sso.enforce} disabled={!sso.enabled} onChange={(e) => setSso({ enforce: e.target.checked })} />
            <span>Require single sign-on<span className="block text-[12px] text-muted-foreground">Turns off password sign-in for everyone except Super Admins, who keep it for emergencies.</span></span></label>
        </CardBody>
      </Card>
      <div className="flex justify-end"><Button type="submit" loading={save.isPending}><Save className="h-4 w-4" />Save sign-in security</Button></div>
    </form>
  );
}
