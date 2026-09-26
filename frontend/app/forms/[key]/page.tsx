"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import axios from "axios";
import { CheckCircle2, ShieldCheck } from "lucide-react";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Logo } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Input, Label, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { API_URL, errorMessage } from "@/lib/api";

const pub = axios.create({ baseURL: `${API_URL}/api/v1` });
const LABELS: Record<string, string> = {
  first_name: "First name", last_name: "Last name", email: "Work email", company_name: "Company", job_title: "Job title", phone: "Phone",
  country: "Country", employee_count: "Employees", message: "How can we help?",
};

/** Hosted lead-capture form: embed with an iframe or link from campaigns. Submissions are deduplicated, enriched, scored and routed. */
export default function HostedForm() {
  const { key } = useParams<{ key: string }>();
  const { data, isLoading, isError } = useQuery({ queryKey: ["form", key], queryFn: async () => (await pub.get<{ name: string; campaign: string | null; fields: string[] }>(`/intake/forms/${key}`)).data, retry: false });
  const [f, setF] = useState<Record<string, string>>({});
  const [consent, setConsent] = useState(false);
  const submit = useMutation({
    mutationFn: async () => (await pub.post("/intake/leads", { ...f, consent: consent ? "true" : "false", consent_text: "I agree to receive product updates from Cirra",
      page_url: typeof window !== "undefined" ? window.location.href : undefined }, { headers: { "X-Cirra-Key": key } })).data,
  });

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b bg-surface"><div className="mx-auto flex h-14 max-w-xl items-center px-4"><Logo /></div></header>
      <main className="mx-auto max-w-xl px-4 py-8">
        {isLoading && <Skeleton className="h-96 w-full" />}
        {isError && <p className="rounded-lg border p-6 text-center text-sm">This form is no longer available.</p>}
        {data && (submit.isSuccess ? (
          <div className="flex items-center gap-3 rounded-xl border bg-surface p-6 shadow-card">
            <CheckCircle2 className="h-6 w-6" style={{ color: "var(--status-good)" }} />
            <div><p className="font-medium">Thank you</p><p className="text-[13px] text-muted-foreground">Someone from our team will be in touch shortly.</p></div>
          </div>
        ) : (
          <form className="space-y-4 rounded-xl border bg-surface p-6 shadow-card" onSubmit={(e) => { e.preventDefault(); submit.mutate(); }}>
            <div>
              <h1 className="text-[20px] font-semibold tracking-tight">{data.name}</h1>
              {data.campaign && <p className="mt-1 text-[13px] text-muted-foreground">{data.campaign}</p>}
            </div>
            <div className="grid grid-cols-2 gap-3">
              {data.fields.map((k) => (
                <div key={k} className={k === "email" || k === "message" || k === "company_name" ? "col-span-2" : ""}>
                  <Label htmlFor={`f-${k}`}>{LABELS[k] ?? k}</Label>
                  {k === "message" ? <Textarea id={`f-${k}`} value={f[k] ?? ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} /> :
                    <Input id={`f-${k}`} required={k === "email"} type={k === "email" ? "email" : k === "employee_count" ? "number" : "text"}
                      value={f[k] ?? ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} />}
                </div>
              ))}
              {/* honeypot: hidden from people, filled by bots */}
              <input type="text" name="website_url_confirm" tabIndex={-1} autoComplete="off" className="hidden" aria-hidden
                value={f.website_url_confirm ?? ""} onChange={(e) => setF({ ...f, website_url_confirm: e.target.value })} />
            </div>
            <label className="flex items-start gap-2 text-[13px]">
              <input type="checkbox" className="mt-0.5" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              I agree to receive product updates by email. I can unsubscribe at any time.
            </label>
            {submit.isError && <p className="text-sm text-destructive">{errorMessage(submit.error)}</p>}
            <Button type="submit" loading={submit.isPending} className="w-full">Submit</Button>
            <p className="flex items-center gap-1.5 text-[11.5px] text-subtle"><ShieldCheck className="h-3.5 w-3.5" />Your details are processed under our privacy notice (GDPR / CCPA).</p>
          </form>
        ))}
      </main>
    </div>
  );
}
