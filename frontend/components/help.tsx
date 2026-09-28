"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ArrowRight, ArrowUp, BarChart3, BookOpen, Boxes, Building2, CheckSquare, Columns3, Database, FileSignature, Handshake, HeartHandshake,
  Home, Landmark, LifeBuoy, Lock, Magnet, Megaphone, Package, PenLine, Search, ShieldCheck, Sparkles, Target, Workflow, X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { AidenAvatar } from "@/components/Brand";
import { Badge } from "@/components/ui/badge";
import { api, errorMessage, get } from "@/lib/api";
import type { DataModel, HelpArea, HelpHit, HelpProcess, HowTo } from "@/lib/help";
import { ui, useUI } from "@/lib/store";
import { cn } from "@/lib/utils";

export const AREA_ICONS: Record<string, typeof Home> = {
  home: Home, sparkles: Sparkles, magnet: Magnet, columns: Columns3, building: Building2, check: CheckSquare, file: FileSignature,
  pen: PenLine, package: Package, target: Target, chart: BarChart3, megaphone: Megaphone, lifebuoy: LifeBuoy, heart: HeartHandshake,
  landmark: Landmark, handshake: Handshake, workflow: Workflow, shield: ShieldCheck, boxes: Boxes, database: Database, lock: Lock,
};

export function AreaIcon({ icon, className }: { icon: string; className?: string }) {
  const Icon = AREA_ICONS[icon] ?? BookOpen;
  return <Icon className={className ?? "h-4 w-4"} />;
}

/** Numbered steps for a how-to, with a link to the screen where it happens. */
export function HowToList({ items, page, idPrefix = "howto", onNavigate }: { items: HowTo[]; page?: string; idPrefix?: string; onNavigate?: () => void }) {
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => {
    const m = typeof window !== "undefined" ? window.location.hash.match(new RegExp(`^#${idPrefix}-(\\d+)$`)) : null;
    if (m) setOpen(Number(m[1]));
  }, [idPrefix]);
  return (
    <div className="divide-y rounded-lg border">
      {items.map((h, i) => (
        <div key={i} id={`${idPrefix}-${i}`}>
          <button type="button" className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-[13.5px] font-medium hover:bg-muted/50"
            aria-expanded={open === i} onClick={() => setOpen(open === i ? null : i)}>
            <span className="flex-1">{h.q}</span>
            <ArrowRight className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", open === i && "rotate-90")} />
          </button>
          {open === i && (
            <div className="px-3 pb-3">
              <ol className="list-decimal space-y-1 pl-5 text-[13px] text-muted-foreground">{h.steps.map((s, k) => <li key={k}>{s}</li>)}</ol>
              {page && <Link href={page} onClick={onNavigate} className="mt-2 inline-flex items-center gap-1 text-[12.5px] text-primary hover:underline">Take me there<ArrowRight className="h-3 w-3" /></Link>}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/** Swimlane process map: one row per role, steps in order left to right; each step opens its screen. */
export function ProcessMap({ process, onNavigate }: { process: HelpProcess; onNavigate?: () => void }) {
  const router = useRouter();
  const [hover, setHover] = useState<string | null>(null);
  const LANE_W = 128, COL_W = 176, BOX_W = 148, BOX_H = 50, ROW_H = 76, PAD = 12;
  const lanes = process.lanes;
  const width = LANE_W + process.steps.length * COL_W + PAD;
  const height = lanes.length * ROW_H + PAD;
  const pos = process.steps.map((s, i) => ({ s, x: LANE_W + i * COL_W + (COL_W - BOX_W) / 2, y: Math.max(0, lanes.indexOf(s.lane)) * ROW_H + (ROW_H - BOX_H) / 2 + PAD / 2 }));
  const active = pos.find((p) => p.s.id === hover);
  return (
    <div>
      <div className="overflow-x-auto rounded-lg border bg-surface-2/40 scrollbar-thin">
        <svg width={width} height={height} role="img" aria-label={`Process map: ${process.title}`} className="block">
          <defs>
            <marker id={`arrow-${process.key}`} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
              <path d="M0,0 L8,4 L0,8 z" fill="hsl(var(--muted-foreground))" />
            </marker>
          </defs>
          {lanes.map((lane, i) => (
            <g key={lane}>
              <rect x={0} y={i * ROW_H + PAD / 2} width={width} height={ROW_H} fill={i % 2 ? "transparent" : "hsl(var(--muted) / 0.45)"} />
              <text x={10} y={i * ROW_H + ROW_H / 2 + PAD / 2 + 4} fontSize={11.5} fontWeight={600} fill="hsl(var(--muted-foreground))">{lane}</text>
            </g>
          ))}
          {pos.slice(1).map((p, i) => {
            const a = pos[i];
            const x1 = a.x + BOX_W, y1 = a.y + BOX_H / 2, x2 = p.x, y2 = p.y + BOX_H / 2;
            const mid = (x1 + x2) / 2;
            return <path key={p.s.id} d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2 - 2},${y2}`} fill="none" stroke="hsl(var(--muted-foreground))"
              strokeWidth={1.3} markerEnd={`url(#arrow-${process.key})`} />;
          })}
          {pos.map(({ s, x, y }, i) => (
            <g key={s.id} role="link" tabIndex={0} aria-label={`${i + 1}. ${s.label}: ${s.detail}`} className="cursor-pointer outline-none"
              onClick={() => { onNavigate?.(); router.push(s.page); }} onKeyDown={(e) => { if (e.key === "Enter") { onNavigate?.(); router.push(s.page); } }}
              onMouseEnter={() => setHover(s.id)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(s.id)} onBlur={() => setHover(null)}>
              <rect x={x} y={y} width={BOX_W} height={BOX_H} rx={9} fill="hsl(var(--surface, var(--background)))"
                stroke={hover === s.id ? "var(--series-1)" : "hsl(var(--border))"} strokeWidth={hover === s.id ? 2 : 1} />
              <circle cx={x + 14} cy={y + 14} r={9} fill="var(--series-1)" />
              <text x={x + 14} y={y + 17.5} fontSize={10} fontWeight={700} textAnchor="middle" fill="white">{i + 1}</text>
              <text x={x + 28} y={y + 18} fontSize={12} fontWeight={600} fill="hsl(var(--foreground))">{s.label.length > 18 ? `${s.label.slice(0, 17)}…` : s.label}</text>
              <text x={x + 10} y={y + 38} fontSize={10.5} fill="hsl(var(--muted-foreground))">{s.page.split("?")[0]}</text>
            </g>
          ))}
        </svg>
      </div>
      <p className="mt-2 min-h-[20px] text-[12.5px] text-muted-foreground">
        {active ? <><span className="font-medium text-foreground">{active.s.label}:</span> {active.s.detail}</> : "Hover or tab to a step for details; select it to open that screen."}
      </p>
    </div>
  );
}

/** Live data model: a relationship map around the chosen entity, and its fields (standard and custom). */
export function DataModelExplorer() {
  const model = useQuery({ queryKey: ["help", "data-model"], queryFn: () => get<DataModel>("/help/data-model") });
  const [sel, setSel] = useState("Account");
  const [q, setQ] = useState("");
  const entities = useMemo(() => model.data?.entities ?? [], [model.data]);
  const current = entities.find((e) => e.label === sel) ?? entities[0];
  const neighbours = useMemo(() => {
    if (!current) return [];
    const inbound = entities.filter((e) => e.links.includes(current.label)).map((e) => e.label);
    return Array.from(new Set([...current.links, ...inbound])).filter((l) => l !== current.label);
  }, [current, entities]);
  if (!model.data || !current) return <div className="skeleton h-80 rounded-lg" />;
  // neighbours in two columns either side of the centre: readable at any count, never overlapping
  const perSide = Math.ceil(neighbours.length / 2);
  const W = 620, H = Math.max(220, perSide * 42 + 40), cx = W / 2, cy = H / 2;
  const shown = entities.filter((e) => !q || `${e.label} ${e.description} ${e.fields.map((f) => f.label).join(" ")}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
      <div className="min-w-0">
        <div className="relative mb-2"><Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-subtle" />
          <input aria-label="Filter entities" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a record type or field"
            className="h-9 w-full rounded-md border bg-surface pl-8 pr-2 text-[13px] outline-none focus:border-ring" /></div>
        <div className="max-h-[420px] space-y-0.5 overflow-y-auto scrollbar-thin">
          {shown.map((e) => (
            <button key={e.table} type="button" onClick={() => setSel(e.label)}
              className={cn("block w-full rounded-md px-2.5 py-1.5 text-left text-[13px]", e.label === current.label ? "bg-primary-soft font-medium text-primary" : "hover:bg-muted")}>
              {e.label}{e.custom_fields.length > 0 && <span className="ml-1 text-[11px] text-subtle">+{e.custom_fields.length}</span>}
            </button>
          ))}
          {model.data.custom_objects.map((o) => <p key={o.key} className="px-2.5 py-1.5 text-[13px] text-muted-foreground"><Boxes className="mr-1 inline h-3 w-3" />{o.label} <span className="text-[11px]">(custom)</span></p>)}
        </div>
      </div>
      <div className="min-w-0 space-y-4">
        <div className="overflow-x-auto rounded-lg border bg-surface-2/40 scrollbar-thin">
          <svg width={W} height={H} role="img" aria-label={`${current.label} and related records`} className="mx-auto block">
            {neighbours.map((n, i) => {
              const left = i < perSide, row = left ? i : i - perSide, count = left ? perSide : neighbours.length - perSide;
              const x = left ? 78 : W - 78, y = cy + (row - (count - 1) / 2) * 42;
              return (
                <g key={n} className="cursor-pointer" role="button" tabIndex={0} aria-label={`Show ${n}`} onClick={() => setSel(n)} onKeyDown={(e) => e.key === "Enter" && setSel(n)}>
                  <line x1={cx} y1={cy} x2={x} y2={y} stroke="hsl(var(--border))" strokeWidth={1.4} />
                  <rect x={x - 58} y={y - 14} width={116} height={28} rx={14} fill="hsl(var(--surface, var(--background)))" stroke="hsl(var(--border))" />
                  <text x={x} y={y + 4} fontSize={11.5} textAnchor="middle" fill="hsl(var(--foreground))">{n.length > 18 ? `${n.slice(0, 17)}…` : n}</text>
                </g>
              );
            })}
            <rect x={cx - 74} y={cy - 22} width={148} height={44} rx={22} fill="var(--series-1)" />
            <text x={cx} y={cy + 5} fontSize={14} fontWeight={700} textAnchor="middle" fill="white">{current.label}</text>
          </svg>
        </div>
        <div>
          <p className="text-[15px] font-semibold">{current.label}</p>
          <p className="text-[13px] text-muted-foreground">{current.description}</p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {current.fields.map((f) => <Badge key={f.key} tone="outline" className="gap-1">{f.label}<span className="text-subtle">· {f.kind}</span>{f.required && <span className="text-destructive">*</span>}</Badge>)}
            {current.custom_fields.map((f) => <Badge key={f.key} tone="primary" className="gap-1">{f.label}<span className="opacity-70">· custom {f.kind}</span></Badge>)}
          </div>
          <p className="mt-2 text-[11.5px] text-subtle">* required. Generated from this workspace&apos;s live schema, including your admin&apos;s custom fields you can see.</p>
        </div>
      </div>
    </div>
  );
}

interface HelpAnswer { answer: string; engine: string; help: { title: string; href: string; kind: string; page: string | null }[] }

/** The page-aware help drawer: help for this screen, search, and Aiden answering from the help center. */
export function HelpDrawer() {
  const open = useUI((s) => s.helpOpen);
  const pathname = usePathname();
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<{ q: string; a?: HelpAnswer; error?: string } | null>(null);
  useEffect(() => { const t = setTimeout(() => setDebounced(query.trim()), 250); return () => clearTimeout(t); }, [query]);
  const ctx = useQuery({ queryKey: ["help", "context", pathname], queryFn: () => get<{ areas: HelpArea[] }>("/help/context", { path: pathname }), enabled: open });
  const results = useQuery({ queryKey: ["help", "search", debounced], queryFn: () => get<{ results: HelpHit[] }>("/help/search", { q: debounced }),
    enabled: open && debounced.length > 1 });
  const ask = useMutation({
    mutationFn: async (q: string) => (await api.post<HelpAnswer>("/help/ask", { question: q, page: pathname })).data,
    onMutate: (q) => setAnswer({ q }),
    onSuccess: (a, q) => setAnswer({ q, a }),
    onError: (e, q) => setAnswer({ q, error: errorMessage(e) }),
  });
  const close = () => ui.set({ helpOpen: false });
  const area = ctx.data?.areas[0];
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(o) => ui.set({ helpOpen: o })}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/20 animate-fade-in lg:bg-transparent" />
        <DialogPrimitive.Content className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l bg-surface shadow-pop animate-slide-up focus:outline-none">
          <DialogPrimitive.Title className="sr-only">Help</DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">Help for this page, search and questions to Aiden</DialogPrimitive.Description>
          <div className="flex h-14 items-center gap-2 border-b px-4">
            <BookOpen className="h-5 w-5 text-primary" />
            <div className="min-w-0"><p className="text-sm font-semibold leading-4">Help</p>
              <p className="truncate text-[11.5px] text-muted-foreground">{area ? `On this page: ${area.title}` : "Guides, how-tos and processes"}</p></div>
            <DialogPrimitive.Close className="ml-auto rounded p-1.5 text-muted-foreground hover:bg-muted" aria-label="Close help"><X className="h-4 w-4" /></DialogPrimitive.Close>
          </div>
          <div className="flex-1 space-y-5 overflow-y-auto p-4 scrollbar-thin">
            <form onSubmit={(e) => { e.preventDefault(); if (question.trim()) { ask.mutate(question.trim()); setQuestion(""); } }}
              className="rounded-xl border bg-surface-2/50 p-2 focus-within:border-ring">
              <div className="mb-1.5 flex items-center gap-1.5 px-1 text-[12px] text-muted-foreground"><AidenAvatar size={16} />Ask Aiden how to do something</div>
              <div className="flex items-end gap-2">
                <input id="help-ask" value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="e.g. How do I split credit on a deal?"
                  aria-label="Ask Aiden a how-to question" className="h-8 flex-1 bg-transparent px-1.5 text-sm outline-none placeholder:text-subtle" />
                <button type="submit" disabled={!question.trim() || ask.isPending} aria-label="Ask" className="ai-gradient flex h-8 w-8 items-center justify-center rounded-lg text-white disabled:opacity-40"><ArrowUp className="h-4 w-4" /></button>
              </div>
            </form>
            {answer && (
              <div className="space-y-2 rounded-lg border p-3" aria-live="polite">
                <p className="text-[12.5px] font-medium text-muted-foreground">{answer.q}</p>
                {answer.a ? <>
                  <div className="whitespace-pre-wrap text-[13.5px] leading-relaxed">{answer.a.answer}</div>
                  <HelpLinks links={answer.a.help} onNavigate={close} />
                </> : answer.error ? <p className="text-sm text-destructive">{answer.error}</p> : <div className="skeleton h-4 w-3/4" />}
              </div>
            )}
            <div>
              <div className="relative"><Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-subtle" />
                <input id="help-search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search help" aria-label="Search help"
                  className="h-9 w-full rounded-md border bg-surface pl-8 pr-2 text-[13px] outline-none focus:border-ring" /></div>
              {debounced.length > 1 && (
                <div className="mt-2 space-y-1">
                  {results.data?.results.length === 0 && <p className="text-[12.5px] text-muted-foreground">Nothing found. Try Ask Aiden above.</p>}
                  {results.data?.results.map((r) => (
                    <Link key={r.href + r.title} href={r.href} onClick={close} className="block rounded-md px-2.5 py-2 hover:bg-muted">
                      <span className="flex items-center gap-2 text-[13px] font-medium">{r.title}<Badge tone="outline">{r.kind === "howto" ? "how-to" : r.kind}</Badge></span>
                      <span className="line-clamp-2 text-[12px] text-muted-foreground">{r.snippet}</span>
                    </Link>
                  ))}
                </div>
              )}
            </div>
            {!debounced && ctx.data?.areas.map((a) => (
              <div key={a.key} className="space-y-2">
                <div className="flex items-center gap-2"><AreaIcon icon={a.icon} className="h-4 w-4 text-primary" />
                  <p className="text-[14px] font-semibold">{a.title}</p></div>
                <p className="text-[13px] text-muted-foreground">{a.summary}</p>
                <HowToList items={a.howto} page={a.pages[0]} idPrefix={`drawer-${a.key}`} onNavigate={close} />
                <Link href={`/help/areas/${a.key}`} onClick={close} className="inline-flex items-center gap-1 text-[12.5px] text-primary hover:underline">Everything about {a.title.toLowerCase()}<ArrowRight className="h-3 w-3" /></Link>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-3 gap-1 border-t p-2 text-center text-[12px]">
            {[["Your role", "/help"], ["Processes", "/help?tab=processes"], ["Data model", "/help?tab=model"]].map(([l, h]) => (
              <Link key={h} href={h} onClick={close} className="rounded-md px-2 py-2 text-muted-foreground hover:bg-muted hover:text-foreground">{l}</Link>
            ))}
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

export function HelpLinks({ links, onNavigate }: { links: { title: string; href: string; kind: string; page?: string | null }[]; onNavigate?: () => void }) {
  if (!links?.length) return null;
  return (
    <div className="space-y-1">
      <p className="text-[11px] font-medium uppercase tracking-wide text-subtle">From the help center</p>
      {links.slice(0, 3).map((l, i) => (
        <div key={l.href + i} className="flex items-center gap-2 text-[12.5px]">
          <span className="font-semibold text-primary">[H{i + 1}]</span>
          <Link href={l.href} onClick={onNavigate} className="min-w-0 flex-1 truncate hover:underline">{l.title}</Link>
          {l.page && <Link href={l.page} onClick={onNavigate} className="shrink-0 text-primary hover:underline">Open screen</Link>}
        </div>
      ))}
    </div>
  );
}
