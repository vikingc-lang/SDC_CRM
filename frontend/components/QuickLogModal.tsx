"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, ArrowRight, Building2, CalendarDays, Check, CheckCircle2, CircleDollarSign, Columns3, CornerDownLeft, Link2, ListTodo,
  Mic, Plus, Search, Sparkles, Swords, Trash2, User as UserIcon, Wand2, X,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { BUYING_ROLES, SentimentIcon } from "@/components/indicators";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Kbd } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { ui, useUI } from "@/lib/store";
import type { BuyingRole, QuickLogResponse } from "@/lib/types";
import { cn, money } from "@/lib/utils";

const EXAMPLES = [
  {
    label: "Discovery call",
    text: "Call with Dana Whitfield, VP Operations at Lumen Robotics (lumenrobotics.com). Dana is the decision maker and is excited about a warehouse automation platform. Budget about $150k, decision by end of Q4. Their pain: pick rates dropped 20% and they rely on spreadsheets for slotting.\nNext steps:\n- Send two case studies by Friday\n- Schedule a technical demo next week with their IT lead",
  },
  {
    label: "Deal update",
    text: "Met with Elena Rostova at Apex Industrial Supply. Pricing for the Supply Chain Analytics Platform landed well, she confirmed $82k and wants to sign by Nov 20. James Cole still has concerns about data residency. I'll send the data residency addendum tomorrow.",
  },
  {
    label: "Risk signal",
    text: "Email from Liam Chen at Northwind Logistics: the pilot is delayed again and leadership is now also evaluating HubSpot. Frustrated tone. Need to set up an exec call with Samantha Okafor this week.",
  },
];

const STEPS = ["Reading your notes", "Identifying people & buying roles", "Detecting deal value & timeline", "Extracting action items", "Validating against schema"];

type Phase = "input" | "analyzing" | "review";

interface SearchResults {
  accounts: { id: string; name: string; domain: string; health: number }[];
  contacts: { id: string; name: string; job_title: string | null; account_id: string }[];
  deals: { id: string; title: string; account: string; stage: string; amount: number }[];
}

export function QuickLogModal() {
  const open = useUI((s) => s.quickLogOpen);
  const initialText = useUI((s) => s.quickLogText);
  const accountId = useUI((s) => s.quickLogAccountId);
  const [text, setText] = useState("");
  const [phase, setPhase] = useState<Phase>("input");
  const [draft, setDraft] = useState<QuickLogResponse | null>(null);
  const [createDeal, setCreateDeal] = useState(true);
  const [step, setStep] = useState(0);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const router = useRouter();
  const qc = useQueryClient();

  useEffect(() => {
    if (open) {
      setText(initialText);
      setPhase("input");
      setDraft(null);
      setTimeout(() => textRef.current?.focus(), 50);
    }
  }, [open, initialText]);

  const close = () => ui.set({ quickLogOpen: false });
  const isSearch = text.trim().length > 1 && text.trim().length < 60 && !text.includes("\n");
  const { data: results } = useQuery({
    queryKey: ["global-search", text.trim()],
    queryFn: () => get<SearchResults>("/search/global", { q: text.trim() }),
    enabled: open && phase === "input" && isSearch,
    placeholderData: (prev) => prev,
  });

  const extract = useMutation({
    mutationFn: async () => (await api.post<QuickLogResponse>("/ai/quick-log", { raw_text: text, account_id: accountId })).data,
    onMutate: () => {
      setPhase("analyzing");
      setStep(0);
    },
    onSuccess: (data) => {
      setDraft(data);
      setCreateDeal(!!data.deal || !!data.matched_deal_id);
      setPhase("review");
    },
    onError: (e) => {
      toast.error(errorMessage(e, "Extraction failed"));
      setPhase("input");
    },
  });

  useEffect(() => {
    if (phase !== "analyzing") return;
    const t = setInterval(() => setStep((s) => Math.min(STEPS.length - 1, s + 1)), 280);
    return () => clearInterval(t);
  }, [phase]);

  const commit = useMutation({
    mutationFn: async () => {
      if (!draft) throw new Error("Nothing to save");
      const payload = {
        ...draft,
        contacts: draft.contacts.filter((c) => c.first_name.trim()),
        action_items: draft.action_items.filter((a) => a.task.trim()),
        raw_text: text,
        account_id: accountId,
        create_deal: createDeal,
      };
      return (await api.post("/ai/commit-log", payload)).data;
    },
    onSuccess: (res: { account_id: string; deal_id: string | null; contacts_created: number; tasks_created: number }) => {
      qc.invalidateQueries();
      close();
      toast.success("Logged to Cirra", {
        description: [
          res.deal_id ? "Deal updated" : null,
          res.contacts_created ? `${res.contacts_created} new contact${res.contacts_created > 1 ? "s" : ""}` : null,
          res.tasks_created ? `${res.tasks_created} action item${res.tasks_created > 1 ? "s" : ""}` : null,
        ].filter(Boolean).join(" · ") || "Activity saved",
        action: { label: "Open account", onClick: () => router.push(`/accounts/${res.account_id}`) },
      });
    },
    onError: (e) => toast.error(errorMessage(e, "Could not save")),
  });

  const [recording, setRecording] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const transcribe = useMutation({
    mutationFn: async (blob: Blob) => {
      const fd = new FormData();
      fd.append("file", blob, "quick-log.webm");
      if (accountId) fd.append("account_id", accountId);
      return (await api.post<QuickLogResponse>("/ai/transcribe", fd)).data;
    },
    onMutate: () => { setPhase("analyzing"); setStep(0); },
    onSuccess: (data) => {
      const transcript = (data.signals as { transcript?: string } | undefined)?.transcript ?? "";
      setText(transcript);
      setDraft(data);
      setCreateDeal(!!data.deal || !!data.matched_deal_id);
      setPhase("review");
    },
    onError: (e) => { toast.error(errorMessage(e, "Transcription failed")); setPhase("input"); },
  });
  const toggleRecording = async () => {
    if (recording) { recorder.current?.stop(); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        if (chunks.length) transcribe.mutate(new Blob(chunks, { type: rec.mimeType || "audio/webm" }));
      };
      recorder.current = rec;
      rec.start();
      setRecording(true);
    } catch {
      toast.error("Microphone unavailable. Check browser permissions.");
    }
  };
  useEffect(() => { if (!open && recorder.current?.state === "recording") recorder.current.stop(); }, [open]);

  const go = (href: string) => {
    close();
    router.push(href);
  };

  return (
    <Dialog open={open} onOpenChange={(o) => ui.set({ quickLogOpen: o })}>
      <DialogContent title="Quick-Log" description="Paste notes and let Aiden structure them" className="max-w-2xl p-0" hideClose>
        {phase === "input" && (
          <div>
            <div className="flex items-center gap-2 border-b px-4 py-3">
              <Sparkles className="h-4 w-4 text-ai" />
              <span className="text-sm font-medium">Quick-Log</span>
              <span className="text-[12.5px] text-muted-foreground">Type to search. Paste notes, emails or dictation to log.</span>
              <button onClick={close} className="ml-auto rounded p-1 text-muted-foreground hover:bg-muted" aria-label="Close"><X className="h-4 w-4" /></button>
            </div>
            <div className="p-4">
              <Textarea
                ref={textRef}
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && text.trim().length >= 3) {
                    e.preventDefault();
                    extract.mutate();
                  }
                }}
                placeholder={"e.g. \"Met Elena at Apex. Pricing landed well, she wants to sign by Nov 20 for $82k. I'll send the addendum tomorrow.\""}
                className="min-h-[150px] resize-none border-0 bg-transparent px-0 text-[15px] shadow-none focus-visible:ring-0"
              />
              {isSearch && results && (results.accounts.length + results.contacts.length + results.deals.length > 0) && (
                <div className="-mx-2 mb-2 max-h-56 space-y-0.5 overflow-y-auto border-t pt-2 scrollbar-thin">
                  {results.accounts.map((a) => (
                    <ResultRow key={a.id} icon={<Building2 className="h-4 w-4" />} title={a.name} meta={a.domain} onClick={() => go(`/accounts/${a.id}`)} />
                  ))}
                  {results.deals.map((d) => (
                    <ResultRow key={d.id} icon={<Columns3 className="h-4 w-4" />} title={d.title} meta={`${d.account} · ${d.stage} · ${money(d.amount, { compact: true })}`} onClick={() => go(`/deals/${d.id}`)} />
                  ))}
                  {results.contacts.map((c) => (
                    <ResultRow key={c.id} icon={<UserIcon className="h-4 w-4" />} title={c.name} meta={c.job_title ?? ""} onClick={() => go(`/accounts/${c.account_id}`)} />
                  ))}
                </div>
              )}
              {!text && (
                <div className="flex flex-wrap items-center gap-2 border-t pt-3">
                  <span className="text-[12px] text-subtle">Try an example:</span>
                  {EXAMPLES.map((ex) => (
                    <button key={ex.label} onClick={() => { setText(ex.text); textRef.current?.focus(); }} className="rounded-full border px-2.5 py-1 text-[12px] text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground">
                      {ex.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="flex items-center gap-2 border-t bg-surface-2/60 px-4 py-2.5">
              <Button variant={recording ? "outline" : "ghost"} size="sm" onClick={toggleRecording} aria-pressed={recording}
                title="Record a voice note. Audio is transcribed on your own infrastructure (Whisper) and never stored.">
                <Mic className={cn("h-3.5 w-3.5", recording && "animate-pulse text-[color:var(--status-critical)]")} />{recording ? "Stop & transcribe" : "Voice note"}
              </Button>
              {isSearch && (
                <Button variant="ghost" size="sm" className="ml-auto" onClick={() => go(`/ask?q=${encodeURIComponent(text.trim())}`)}>
                  <Search className="h-3.5 w-3.5" />Ask Aiden
                </Button>
              )}
              <Button variant="ai" size="sm" className={cn(!isSearch && "ml-auto")} disabled={text.trim().length < 3} onClick={() => extract.mutate()}>
                <Wand2 className="h-3.5 w-3.5" />Extract with Aiden
                <span className="ml-1 flex items-center gap-0.5 opacity-80"><Kbd className="border-white/30 bg-white/15 text-white">⌘</Kbd><Kbd className="border-white/30 bg-white/15 text-white"><CornerDownLeft className="h-3 w-3" /></Kbd></span>
              </Button>
            </div>
          </div>
        )}

        {phase === "analyzing" && (
          <div className="px-6 py-10">
            <div className="mx-auto max-w-sm">
              <div className="mb-6 flex items-center gap-3">
                <span className="ai-gradient flex h-9 w-9 animate-pulse items-center justify-center rounded-xl"><Sparkles className="h-4 w-4 text-white" /></span>
                <div>
                  <p className="text-sm font-medium">Structuring your notes</p>
                  <p className="text-[12.5px] text-muted-foreground">Private model · nothing leaves your cloud</p>
                </div>
              </div>
              <ol className="space-y-2.5">
                {STEPS.map((s, i) => (
                  <li key={s} className={cn("flex items-center gap-2.5 text-[13px] transition-opacity", i > step ? "opacity-40" : "opacity-100")}>
                    {i < step ? <CheckCircle2 className="h-4 w-4 text-primary" /> : i === step ? <span className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-r-transparent" /> : <span className="h-4 w-4 rounded-full border-2 border-border" />}
                    {s}
                  </li>
                ))}
              </ol>
            </div>
          </div>
        )}

        {phase === "review" && draft && (
          <Review draft={draft} setDraft={setDraft} createDeal={createDeal} setCreateDeal={setCreateDeal} onBack={() => setPhase("input")} onCommit={() => commit.mutate()} committing={commit.isPending} />
        )}
      </DialogContent>
    </Dialog>
  );
}

function ResultRow({ icon, title, meta, onClick }: { icon: React.ReactNode; title: string; meta: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="group flex w-full items-center gap-3 rounded-md px-2 py-1.5 text-left hover:bg-muted">
      <span className="text-muted-foreground">{icon}</span>
      <span className="text-sm font-medium">{title}</span>
      <span className="truncate text-[12.5px] text-muted-foreground">{meta}</span>
      <ArrowRight className="ml-auto h-3.5 w-3.5 text-subtle opacity-0 group-hover:opacity-100" />
    </button>
  );
}

function Section({ icon, title, badge, children }: { icon: React.ReactNode; title: string; badge?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="animate-slide-up rounded-lg border bg-surface p-3.5">
      <div className="mb-2.5 flex items-center gap-2">
        <span className="text-muted-foreground">{icon}</span>
        <h4 className="text-[13px] font-semibold">{title}</h4>
        {badge && <span className="ml-auto">{badge}</span>}
      </div>
      {children}
    </section>
  );
}

function Review({ draft, setDraft, createDeal, setCreateDeal, onBack, onCommit, committing }: {
  draft: QuickLogResponse; setDraft: (d: QuickLogResponse) => void; createDeal: boolean; setCreateDeal: (v: boolean) => void;
  onBack: () => void; onCommit: () => void; committing: boolean;
}) {
  const patch = (p: Partial<QuickLogResponse>) => setDraft({ ...draft, ...p });
  const deal = draft.deal ?? { title: null, amount: null, target_close_date: null, suggested_stage: null };
  const patchDeal = (p: Partial<typeof deal>) => patch({ deal: { ...deal, ...p } });
  const signals = useMemo(() => [...(draft.signals.competitors ?? [])], [draft.signals]);

  return (
    <div>
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <button onClick={onBack} className="rounded p-1 text-muted-foreground hover:bg-muted" aria-label="Back"><ArrowLeft className="h-4 w-4" /></button>
        <span className="text-sm font-medium">Review before saving</span>
        <Badge tone="ai" className="ml-1"><Sparkles className="h-3 w-3" />{draft.engine === "heuristic" ? "Aiden · offline engine" : `Aiden · ${draft.engine}`}</Badge>
        <span className="ml-auto hidden text-[12px] text-muted-foreground sm:block">Everything is editable</span>
      </div>

      <div className="max-h-[62vh] space-y-3 overflow-y-auto bg-surface-2/50 p-4 scrollbar-thin">
        <Section
          icon={<Building2 className="h-4 w-4" />}
          title="Account"
          badge={draft.matched_account_id ? <Badge tone="primary"><Link2 className="h-3 w-3" />Matched existing</Badge> : <Badge tone="neutral"><Plus className="h-3 w-3" />New account</Badge>}
        >
          <div className="grid gap-2 sm:grid-cols-2">
            <Input value={draft.account_name ?? ""} onChange={(e) => patch({ account_name: e.target.value, matched_account_id: null })} placeholder="Account name" aria-label="Account name" />
            <Input value={draft.domain ?? ""} onChange={(e) => patch({ domain: e.target.value, matched_account_id: null })} placeholder="domain.com" aria-label="Domain" />
          </div>
        </Section>

        {(draft.deal || draft.matched_deal_id) && (
          <Section
            icon={<CircleDollarSign className="h-4 w-4" />}
            title="Deal"
            badge={
              draft.matched_deal_id ? <Badge tone="primary"><Link2 className="h-3 w-3" />Updates existing deal</Badge> : (
                <label className="flex cursor-pointer items-center gap-1.5 text-[12px] text-muted-foreground">
                  <input type="checkbox" checked={createDeal} onChange={(e) => setCreateDeal(e.target.checked)} className="accent-[hsl(var(--primary))]" />
                  Create deal
                </label>
              )
            }
          >
            <div className={cn("grid gap-2 sm:grid-cols-[1fr_130px_150px]", !createDeal && !draft.matched_deal_id && "opacity-50")}>
              <Input value={deal.title ?? ""} onChange={(e) => patchDeal({ title: e.target.value })} placeholder="Deal title" aria-label="Deal title" disabled={!!draft.matched_deal_id} />
              <Input type="number" value={deal.amount ?? ""} onChange={(e) => patchDeal({ amount: e.target.value ? Number(e.target.value) : null })} placeholder="Amount" aria-label="Amount" />
              <Input type="date" value={deal.target_close_date ?? ""} onChange={(e) => patchDeal({ target_close_date: e.target.value || null })} aria-label="Target close date" />
            </div>
            {!draft.matched_deal_id && (
              <div className="mt-2 flex items-center gap-2 text-[12.5px] text-muted-foreground">
                Suggested stage
                <Select value={deal.suggested_stage ?? "Discovery"} onChange={(e) => patchDeal({ suggested_stage: e.target.value })} className="h-8 w-44 text-[13px]">
                  {["Discovery", "Pain Fit", "Solution Demo", "Proposal/InfoSec"].map((s) => <option key={s}>{s}</option>)}
                </Select>
              </div>
            )}
          </Section>
        )}

        <Section icon={<UserIcon className="h-4 w-4" />} title={`People (${draft.contacts.length})`}>
          {draft.contacts.length === 0 && <p className="text-[12.5px] text-muted-foreground">No people detected.</p>}
          <div className="space-y-2">
            {draft.contacts.map((c, i) => {
              const upd = (p: Partial<typeof c>) => patch({ contacts: draft.contacts.map((x, j) => (j === i ? { ...x, ...p } : x)) });
              return (
                <div key={i} className="grid items-center gap-2 sm:grid-cols-[1fr_1fr_150px_28px]">
                  <Input value={`${c.first_name} ${c.last_name}`.trim()} onChange={(e) => { const [f, ...rest] = e.target.value.split(" "); upd({ first_name: f ?? "", last_name: rest.join(" ") }); }} aria-label="Name" />
                  <Input value={c.job_title ?? ""} onChange={(e) => upd({ job_title: e.target.value || null })} placeholder="Title" aria-label="Title" />
                  <Select value={c.buying_role} onChange={(e) => upd({ buying_role: e.target.value as BuyingRole })} aria-label="Buying role">
                    {BUYING_ROLES.map((r) => <option key={r}>{r}</option>)}
                  </Select>
                  <button onClick={() => patch({ contacts: draft.contacts.filter((_, j) => j !== i) })} className="flex h-8 w-7 items-center justify-center rounded text-subtle hover:bg-muted hover:text-foreground" aria-label="Remove contact"><Trash2 className="h-3.5 w-3.5" /></button>
                  {c.email && <p className="-mt-1 text-[11.5px] text-muted-foreground sm:col-span-4">{c.email}</p>}
                </div>
              );
            })}
          </div>
        </Section>

        <Section icon={<CalendarDays className="h-4 w-4" />} title="Activity" badge={<span className="flex items-center gap-1 text-[12px] capitalize text-muted-foreground"><SentimentIcon sentiment={draft.sentiment} />{draft.sentiment}</span>}>
          <div className="mb-2 grid grid-cols-2 gap-2 sm:w-80">
            <Select value={draft.activity_type} onChange={(e) => patch({ activity_type: e.target.value as QuickLogResponse["activity_type"] })} aria-label="Activity type">
              {["meeting", "call", "email", "note"].map((t) => <option key={t} value={t}>{t[0].toUpperCase() + t.slice(1)}</option>)}
            </Select>
            <Select value={draft.sentiment} onChange={(e) => patch({ sentiment: e.target.value as QuickLogResponse["sentiment"] })} aria-label="Sentiment">
              {["positive", "neutral", "negative"].map((t) => <option key={t} value={t}>{t[0].toUpperCase() + t.slice(1)}</option>)}
            </Select>
          </div>
          <Textarea value={draft.summary} onChange={(e) => patch({ summary: e.target.value })} className="min-h-[64px] text-[13.5px]" aria-label="Summary" />
        </Section>

        <Section icon={<ListTodo className="h-4 w-4" />} title={`Action items (${draft.action_items.length})`}>
          <div className="space-y-2">
            {draft.action_items.map((a, i) => (
              <div key={i} className="grid items-center gap-2 sm:grid-cols-[1fr_150px_28px]">
                <Input value={a.task} onChange={(e) => patch({ action_items: draft.action_items.map((x, j) => (j === i ? { ...x, task: e.target.value } : x)) })} aria-label="Task" />
                <Input type="date" value={a.due_date ?? ""} onChange={(e) => patch({ action_items: draft.action_items.map((x, j) => (j === i ? { ...x, due_date: e.target.value || null } : x)) })} aria-label="Due date" />
                <button onClick={() => patch({ action_items: draft.action_items.filter((_, j) => j !== i) })} className="flex h-8 w-7 items-center justify-center rounded text-subtle hover:bg-muted hover:text-foreground" aria-label="Remove action item"><Trash2 className="h-3.5 w-3.5" /></button>
              </div>
            ))}
            <button onClick={() => patch({ action_items: [...draft.action_items, { task: "", due_date: null }] })} className="flex items-center gap-1 text-[12.5px] font-medium text-primary hover:underline">
              <Plus className="h-3.5 w-3.5" />Add action item
            </button>
          </div>
        </Section>

        {(signals.length > 0 || (draft.signals.pain_points ?? []).length > 0) && (
          <Section icon={<Swords className="h-4 w-4" />} title="Signals detected">
            <div className="flex flex-wrap gap-1.5">
              {signals.map((c) => <Badge key={c} tone="warning">Competitor: {c}</Badge>)}
              {(draft.signals.pain_points ?? []).slice(0, 3).map((p) => <Badge key={p} tone="outline" className="max-w-full truncate whitespace-normal text-left">Pain: {p}</Badge>)}
            </div>
          </Section>
        )}
      </div>

      <div className="flex items-center gap-2 border-t px-4 py-3">
        <Button variant="ghost" size="sm" onClick={onBack}>Back</Button>
        <Button className="ml-auto" size="sm" onClick={onCommit} loading={committing} disabled={!draft.account_name && !draft.matched_account_id}>
          <Check className="h-4 w-4" />Save to Cirra
        </Button>
      </div>
    </div>
  );
}
