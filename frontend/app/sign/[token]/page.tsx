"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import axios from "axios";
import { CheckCircle2, Eraser, MessageSquarePlus, PenLine, ShieldCheck } from "lucide-react";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Logo } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Input, Label, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { API_URL, errorMessage } from "@/lib/api";

interface SignView {
  document: { title: string; status: string; body_html: string; content_sha256: string };
  signer: { name: string; email: string; party: string; status: string };
  your_turn: boolean;
  signers: { name: string; party: string; status: string; signed_at: string | null }[];
  comments?: { id: string; clause: string | null; body: string; party: string; author_name: string; resolved: boolean; version: number; created_at: string }[];
  can_comment?: boolean; version?: number;
}

const pub = axios.create({ baseURL: `${API_URL}/api/v1` });

function SignaturePad({ onChange }: { onChange: (dataUrl: string | null) => void }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);
  const dirty = useRef(false);
  useEffect(() => {
    const c = ref.current!;
    const ratio = window.devicePixelRatio || 1;
    c.width = c.offsetWidth * ratio;
    c.height = c.offsetHeight * ratio;
    const ctx = c.getContext("2d")!;
    ctx.scale(ratio, ratio);
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.strokeStyle = "#1B2240";
  }, []);
  const pos = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top] as const;
  };
  return (
    <div>
      <canvas
        ref={ref}
        className="h-32 w-full touch-none rounded-md border border-dashed border-input bg-white"
        aria-label="Draw your signature"
        onPointerDown={(e) => { drawing.current = true; const [x, y] = pos(e); const ctx = ref.current!.getContext("2d")!; ctx.beginPath(); ctx.moveTo(x, y); }}
        onPointerMove={(e) => { if (!drawing.current) return; const [x, y] = pos(e); const ctx = ref.current!.getContext("2d")!; ctx.lineTo(x, y); ctx.stroke(); dirty.current = true; }}
        onPointerUp={() => { drawing.current = false; if (dirty.current) onChange(ref.current!.toDataURL("image/png")); }}
        onPointerLeave={() => { drawing.current = false; }}
      />
      <button type="button" className="mt-1 inline-flex items-center gap-1 text-[12px] text-muted-foreground hover:text-foreground"
        onClick={() => { const c = ref.current!; c.getContext("2d")!.clearRect(0, 0, c.width, c.height); dirty.current = false; onChange(null); }}>
        <Eraser className="h-3 w-3" />Clear
      </button>
    </div>
  );
}

export default function SignPage() {
  const { token } = useParams<{ token: string }>();
  const { data, isLoading, isError, error, refetch } = useQuery({ queryKey: ["sign", token], queryFn: async () => (await pub.get<SignView>(`/sign/${token}`)).data, retry: false });
  const [name, setName] = useState("");
  const [agree, setAgree] = useState(false);
  const [image, setImage] = useState<string | null>(null);
  const sign = useMutation({
    mutationFn: async (decline: boolean) => (await pub.post(`/sign/${token}`, { signature_text: name, signature_image: image, agree, decline })).data,
    onSuccess: () => refetch(),
  });
  const [clause, setClause] = useState("");
  const [comment, setComment] = useState("");
  const addComment = useMutation({
    mutationFn: async () => (await pub.post(`/sign/${token}/comments`, { clause: clause || null, body: comment })).data,
    onSuccess: () => { setClause(""); setComment(""); refetch(); },
  });

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b bg-surface">
        <div className="mx-auto flex h-14 max-w-4xl items-center gap-3 px-4">
          <Logo />
          <span className="ml-auto flex items-center gap-1.5 text-[12px] text-muted-foreground"><ShieldCheck className="h-3.5 w-3.5" />Secure e-signature</span>
        </div>
      </header>
      <main className="mx-auto max-w-4xl px-4 py-8">
        {isLoading && <Skeleton className="h-96 w-full" />}
        {isError && <p className="rounded-lg border p-6 text-center text-sm">{errorMessage(error, "This signing link is invalid or has expired.")}</p>}
        {data && (
          <div className="space-y-6">
            <div>
              <h1 className="text-[22px] font-semibold tracking-tight">{data.document.title}</h1>
              <p className="mt-1 text-sm text-muted-foreground">For {data.signer.name} ({data.signer.email})</p>
            </div>
            <article className="doc-body rounded-xl border bg-surface p-6 text-[14px] leading-relaxed shadow-card" dangerouslySetInnerHTML={{ __html: data.document.body_html }} />
            {data.signer.status === "signed" || data.document.status === "completed" ? (
              <div className="flex items-center gap-3 rounded-xl border bg-surface p-5 shadow-card">
                <CheckCircle2 className="h-6 w-6" style={{ color: "var(--status-good)" }} />
                <div>
                  <p className="font-medium">{data.document.status === "completed" ? "Fully executed" : "Thank you, your signature is recorded"}</p>
                  <p className="text-[13px] text-muted-foreground">{data.document.status === "completed" ? "All parties have signed. A copy is filed with your account team." : "We will notify you once the countersignature is complete."}</p>
                </div>
              </div>
            ) : data.document.status === "voided" ? (
              <p className="rounded-xl border p-5 text-sm">This document was declined and is no longer open for signature.</p>
            ) : data.document.status === "in_negotiation" ? (
              <p className="rounded-xl border p-5 text-sm">Changes were requested on this version. Your account team will send a revised version for signature.</p>
            ) : !data.your_turn ? (
              <p className="rounded-xl border p-5 text-sm">Waiting for an earlier signer. You will receive this link again when it is your turn.</p>
            ) : (
              <form className="space-y-4 rounded-xl border bg-surface p-5 shadow-card" onSubmit={(e) => { e.preventDefault(); sign.mutate(false); }}>
                <h2 className="flex items-center gap-2 font-semibold"><PenLine className="h-4 w-4" />Sign</h2>
                <div><Label htmlFor="sig-name">Type your full legal name</Label><Input id="sig-name" required value={name} onChange={(e) => setName(e.target.value)} placeholder={data.signer.name} /></div>
                <div><Label>Draw your signature (optional)</Label><SignaturePad onChange={setImage} /></div>
                <label className="flex items-start gap-2 text-[13px]">
                  <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} className="mt-0.5" />
                  I agree to sign this document electronically. My typed name, the date and time, and my IP address will be recorded as my signature.
                </label>
                {sign.isError && <p className="text-sm text-destructive">{errorMessage(sign.error)}</p>}
                <div className="flex flex-wrap gap-2">
                  <Button type="submit" disabled={!agree || name.trim().length < 2} loading={sign.isPending}>Sign document</Button>
                  <Button type="button" variant="ghost" onClick={() => confirm("Decline to sign this document?") && sign.mutate(true)}>Decline</Button>
                </div>
                <p className="break-all font-mono text-[10.5px] text-subtle">Document fingerprint (SHA-256): {data.document.content_sha256}</p>
              </form>
            )}
            {!!data.comments?.length && (
              <section className="rounded-xl border bg-surface p-5 shadow-card">
                <h2 className="font-semibold">Requested changes</h2>
                <ul className="mt-3 space-y-3">
                  {data.comments.map((c) => (
                    <li key={c.id} className="text-[13.5px]">
                      <p><span className="font-medium">{c.author_name}</span>{c.clause && <span className="text-muted-foreground"> on {c.clause}</span>}
                        <span className="text-[12px] text-subtle"> · v{c.version}{c.resolved ? " · resolved" : ""}</span></p>
                      <p className="mt-0.5">{c.body}</p>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {data.can_comment && (
              <form className="space-y-3 rounded-xl border bg-surface p-5 shadow-card" onSubmit={(e) => { e.preventDefault(); addComment.mutate(); }}>
                <h2 className="flex items-center gap-2 font-semibold"><MessageSquarePlus className="h-4 w-4" />Request a change</h2>
                <p className="text-[13px] text-muted-foreground">Propose a redline or ask a question on a clause. Signing pauses until a revised version is issued.</p>
                <div><Label htmlFor="c-clause">Clause (optional)</Label><Input id="c-clause" placeholder="e.g. 6. Limitation of liability" value={clause} onChange={(e) => setClause(e.target.value)} /></div>
                <div><Label htmlFor="c-body">Your comment or proposed wording</Label><Textarea id="c-body" required value={comment} onChange={(e) => setComment(e.target.value)} /></div>
                {addComment.isError && <p className="text-sm text-destructive">{errorMessage(addComment.error)}</p>}
                <Button type="submit" variant="outline" disabled={comment.trim().length < 3} loading={addComment.isPending}>Send to account team</Button>
              </form>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
