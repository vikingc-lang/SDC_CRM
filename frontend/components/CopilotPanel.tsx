"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { useMutation } from "@tanstack/react-query";
import { ArrowUp, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { AnswerBlock, type AskResponse } from "@/components/AskAnswer";
import { api, errorMessage } from "@/lib/api";
import { ui, useUI } from "@/lib/store";

const SUGGESTIONS = ["Which deals are at risk?", "What's our weighted forecast?", "Who are the champions?", "What did we learn from lost deals?", "Any security concerns in the pipeline?"];

export function CopilotPanel() {
  const open = useUI((s) => s.copilotOpen);
  const [question, setQuestion] = useState("");
  const [thread, setThread] = useState<{ q: string; a?: AskResponse; error?: string }[]>([]);
  const bottom = useRef<HTMLDivElement>(null);

  const ask = useMutation({
    mutationFn: async (q: string) => (await api.post<AskResponse>("/ai/ask", { question: q })).data,
    onMutate: (q) => setThread((t) => [...t, { q }]),
    onSuccess: (a) => setThread((t) => t.map((m, i) => (i === t.length - 1 ? { ...m, a } : m))),
    onError: (e) => setThread((t) => t.map((m, i) => (i === t.length - 1 ? { ...m, error: errorMessage(e) } : m))),
  });

  useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth" }), [thread]);

  const submit = (q: string) => {
    if (!q.trim() || ask.isPending) return;
    ask.mutate(q.trim());
    setQuestion("");
  };

  return (
    <DialogPrimitive.Root open={open} onOpenChange={(o) => ui.set({ copilotOpen: o })}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/20 animate-fade-in lg:bg-transparent" />
        <DialogPrimitive.Content className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l bg-surface shadow-pop animate-slide-up focus:outline-none">
          <DialogPrimitive.Title className="sr-only">Cirra Copilot</DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">Ask questions about your pipeline</DialogPrimitive.Description>
          <div className="flex h-14 items-center gap-2 border-b px-4">
            <span className="ai-gradient flex h-7 w-7 items-center justify-center rounded-lg"><Sparkles className="h-3.5 w-3.5 text-white" /></span>
            <div>
              <p className="text-sm font-semibold leading-4">Copilot</p>
              <p className="text-[11.5px] text-muted-foreground">Grounded in your CRM memory</p>
            </div>
            <DialogPrimitive.Close className="ml-auto rounded p-1.5 text-muted-foreground hover:bg-muted" aria-label="Close"><X className="h-4 w-4" /></DialogPrimitive.Close>
          </div>
          <div className="flex-1 space-y-5 overflow-y-auto p-4 scrollbar-thin">
            {thread.length === 0 && (
              <div className="pt-6">
                <p className="text-[15px] font-medium">What do you want to know?</p>
                <p className="mt-1 text-[13px] text-muted-foreground">I search every note, email and call you have logged, plus live pipeline data.</p>
                <div className="mt-4 flex flex-col items-start gap-2">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} onClick={() => submit(s)} className="rounded-full border px-3 py-1.5 text-left text-[13px] text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground">{s}</button>
                  ))}
                </div>
              </div>
            )}
            {thread.map((m, i) => (
              <div key={i} className="space-y-2">
                <div className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-3.5 py-2 text-sm text-primary-foreground">{m.q}</div>
                {m.a ? <AnswerBlock answer={m.a} onNavigate={() => ui.set({ copilotOpen: false })} /> : m.error ? (
                  <p className="text-sm text-destructive">{m.error}</p>
                ) : (
                  <div className="space-y-2"><div className="skeleton h-3.5 w-3/4" /><div className="skeleton h-3.5 w-1/2" /></div>
                )}
              </div>
            ))}
            <div ref={bottom} />
          </div>
          <form onSubmit={(e) => { e.preventDefault(); submit(question); }} className="border-t p-3">
            <div className="flex items-end gap-2 rounded-xl border bg-surface-2/50 p-2 focus-within:border-ring">
              <textarea
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(question); } }}
                rows={1}
                placeholder="Ask about deals, accounts, people…"
                className="max-h-32 min-h-[36px] flex-1 resize-none bg-transparent px-1.5 py-1.5 text-sm outline-none placeholder:text-subtle"
              />
              <button type="submit" disabled={!question.trim() || ask.isPending} className="ai-gradient flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-white disabled:opacity-40" aria-label="Send">
                <ArrowUp className="h-4 w-4" />
              </button>
            </div>
          </form>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
