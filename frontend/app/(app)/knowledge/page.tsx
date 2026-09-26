"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Pencil, Plus, Search, ThumbsDown, ThumbsUp, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";
import { useMe } from "@/lib/me";
import { cn, relativeDays } from "@/lib/utils";

interface ArticleItem { id: string; title: string; category: string | null; status: "draft" | "published"; tags: string[]; author: string | null; views: number; helpful: number; not_helpful: number; updated_at: string; excerpt?: string; body?: string }
interface ListOut { articles: ArticleItem[]; categories: string[]; can_edit: boolean }
type Draft = { id?: string; title: string; category: string; body: string; status: "draft" | "published" };

export default function KnowledgePage() {
  const qc = useQueryClient();
  const { can } = useMe();
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  useEffect(() => { const a = new URLSearchParams(window.location.search).get("article"); if (a) setOpenId(a); }, []);
  const list = useQuery({ queryKey: ["knowledge", search, category], placeholderData: (p) => p,
    queryFn: () => get<ListOut>("/knowledge", { search: search || undefined, category: category || undefined }) });
  const article = useQuery({ queryKey: ["knowledge", "article", openId], enabled: !!openId, queryFn: () => get<ArticleItem>(`/knowledge/${openId}`) });
  const save = useMutation({
    mutationFn: async () => {
      const body = { title: draft!.title, body: draft!.body, category: draft!.category || null, status: draft!.status, tags: [] };
      return draft!.id ? (await api.put(`/knowledge/${draft!.id}`, body)).data : (await api.post<{ id: string }>("/knowledge", body)).data;
    },
    onSuccess: (r: { id?: string }) => { qc.invalidateQueries({ queryKey: ["knowledge"] }); setOpenId(draft!.id ?? r.id ?? null); setDraft(null); toast.success("Article saved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const del = useMutation({
    mutationFn: async (aid: string) => api.delete(`/knowledge/${aid}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["knowledge"] }); setOpenId(null); toast.success("Article deleted"); },
  });
  const vote = useMutation({
    mutationFn: async (helpful: boolean) => (await api.post(`/knowledge/${openId}/feedback`, null, { params: { helpful } })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["knowledge", "article", openId] }); toast.success("Thanks for the feedback"); },
  });
  const a = article.data;
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Knowledge" description="Answers your team reuses in case replies. Published articles are suggested on matching cases."
        actions={can("knowledge", "create") && <Button size="sm" onClick={() => setDraft({ title: "", category: "", body: "", status: "draft" })}><Plus className="h-3.5 w-3.5" />New article</Button>} />
      <div className="mb-4 flex flex-wrap gap-2">
        <div className="relative min-w-0 flex-1 sm:max-w-sm">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input aria-label="Search articles" className="pl-8" placeholder="Search articles" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <Select aria-label="Category" className="w-auto" value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">All categories</option>{list.data?.categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </Select>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,360px)_1fr]">
        <Card className="h-fit">
          {!list.data ? <Skeleton className="m-4 h-40" /> : !list.data.articles.length ? (
            <EmptyState icon={<BookOpen className="h-4 w-4" />} title="No articles found" />
          ) : (
            <ul className="divide-y">
              {list.data.articles.map((x) => (
                <li key={x.id}>
                  <button type="button" onClick={() => setOpenId(x.id)} className={cn("w-full px-4 py-3 text-left hover:bg-muted/50", openId === x.id && "bg-primary-soft")}>
                    <span className="flex items-center gap-2"><span className="font-medium">{x.title}</span>{x.status === "draft" && <Badge tone="neutral">Draft</Badge>}</span>
                    <span className="mt-0.5 block text-[12px] text-muted-foreground">{x.category ?? "Uncategorised"} · {x.views} views · updated {relativeDays(x.updated_at)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card className="min-w-0">
          {!openId ? <EmptyState icon={<BookOpen className="h-4 w-4" />} title="Choose an article to read it" /> : !a ? <Skeleton className="m-5 h-64" /> : (
            <CardBody>
              <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h2 className="text-lg font-semibold tracking-tight">{a.title}</h2>
                  <p className="text-[12.5px] text-muted-foreground">{a.category ?? "Uncategorised"} · {a.author ?? "Unknown"} · updated {relativeDays(a.updated_at)}</p>
                </div>
                {list.data?.can_edit && (
                  <div className="flex gap-1">
                    <Button size="sm" variant="outline" onClick={() => setDraft({ id: a.id, title: a.title, category: a.category ?? "", body: a.body ?? "", status: a.status })}><Pencil className="h-3.5 w-3.5" />Edit</Button>
                    {can("knowledge", "delete") && <Button size="sm" variant="ghost" onClick={() => del.mutate(a.id)}><Trash2 className="h-3.5 w-3.5" />Delete</Button>}
                  </div>
                )}
              </div>
              <div className="max-w-[68ch] whitespace-pre-wrap text-[14px] leading-relaxed">{a.body}</div>
              <div className="mt-6 flex flex-wrap items-center gap-2 border-t pt-4 text-[13px]">
                <span className="text-muted-foreground">Was this helpful?</span>
                <Button size="sm" variant="outline" onClick={() => vote.mutate(true)}><ThumbsUp className="h-3.5 w-3.5" />Yes · {a.helpful}</Button>
                <Button size="sm" variant="outline" onClick={() => vote.mutate(false)}><ThumbsDown className="h-3.5 w-3.5" />No · {a.not_helpful}</Button>
              </div>
            </CardBody>
          )}
        </Card>
      </div>
      <Dialog open={!!draft} onOpenChange={(o) => !o && setDraft(null)}>
        <DialogContent title="Article" className="max-w-2xl">
          {draft && (
            <form className="space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
              <h2 className="text-[15px] font-semibold">{draft.id ? "Edit article" : "New article"}</h2>
              <div><Label htmlFor="kb-title">Title</Label><Input id="kb-title" required minLength={3} maxLength={200} value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></div>
              <div className="grid grid-cols-2 gap-2">
                <div><Label htmlFor="kb-cat">Category</Label><Input id="kb-cat" list="kb-cats" maxLength={60} value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })} />
                  <datalist id="kb-cats">{list.data?.categories.map((c) => <option key={c} value={c} />)}</datalist></div>
                <div><Label htmlFor="kb-status">Status</Label><Select id="kb-status" value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value as Draft["status"] })}>
                  <option value="draft">Draft (only editors see it)</option><option value="published">Published</option></Select></div>
              </div>
              <div><Label htmlFor="kb-body">Article</Label><Textarea id="kb-body" rows={12} required value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} /></div>
              <div className="flex justify-end"><Button type="submit" size="sm" loading={save.isPending}>Save article</Button></div>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
