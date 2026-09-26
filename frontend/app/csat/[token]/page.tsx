"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Logo } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Label, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { cn } from "@/lib/utils";

const FACES = ["Very unhappy", "Unhappy", "Okay", "Happy", "Very happy"];

/** Public one-question satisfaction survey sent after a case is resolved. No sign-in. */
export default function CsatPage() {
  const { token } = useParams<{ token: string }>();
  const info = useQuery({ queryKey: ["csat", token], retry: false,
    queryFn: () => get<{ case_number: string; subject: string; account: string | null; rated: boolean }>(`/public/csat/${token}`) });
  const [score, setScore] = useState(0);
  const [comment, setComment] = useState("");
  const send = useMutation({ mutationFn: async () => (await api.post(`/public/csat/${token}`, { score, comment: comment || null })).data });
  const done = send.isSuccess || info.data?.rated;
  return (
    <div className="flex min-h-screen flex-col bg-background px-4 py-8">
      <div className="mx-auto w-full max-w-md"><Logo height={28} /></div>
      <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center py-10">
        {info.isLoading ? <Skeleton className="h-64" /> : info.isError ? (
          <p className="text-sm text-muted-foreground">{errorMessage(info.error, "This survey link isn't valid.")}</p>
        ) : done ? (
          <div className="text-center">
            <CheckCircle2 className="mx-auto mb-3 h-8 w-8" style={{ color: "var(--status-good)" }} />
            <h1 className="text-xl font-semibold tracking-tight">Thank you</h1>
            <p className="mt-1 text-sm text-muted-foreground">Your feedback on case {info.data?.case_number} goes straight to the team that helped you.</p>
          </div>
        ) : (
          <form className="space-y-5" onSubmit={(e) => { e.preventDefault(); if (score) send.mutate(); }}>
            <div>
              <p className="text-[12.5px] text-muted-foreground">Case {info.data!.case_number}{info.data!.account ? ` · ${info.data!.account}` : ""}</p>
              <h1 className="mt-1 text-xl font-semibold tracking-tight">How satisfied are you with how we handled “{info.data!.subject}”?</h1>
            </div>
            <div role="radiogroup" aria-label="Rating from 1 to 5" className="grid grid-cols-5 gap-2">
              {FACES.map((label, i) => (
                <button key={label} type="button" role="radio" aria-checked={score === i + 1} aria-label={`${i + 1}: ${label}`} onClick={() => setScore(i + 1)}
                  className={cn("flex flex-col items-center gap-1 rounded-lg border py-3 text-[12px] transition-colors hover:border-primary/50",
                    score === i + 1 ? "border-primary bg-primary-soft font-medium" : "text-muted-foreground")}>
                  <span className="text-xl font-semibold text-foreground tabular">{i + 1}</span><span className="hidden sm:block">{label}</span>
                </button>
              ))}
            </div>
            <div><Label htmlFor="csat-comment">Anything we could do better? (optional)</Label>
              <Textarea id="csat-comment" rows={3} maxLength={2000} value={comment} onChange={(e) => setComment(e.target.value)} /></div>
            {send.isError && <p className="text-sm text-destructive">{errorMessage(send.error)}</p>}
            <Button type="submit" className="w-full" disabled={!score} loading={send.isPending}>Send feedback</Button>
          </form>
        )}
      </div>
    </div>
  );
}
