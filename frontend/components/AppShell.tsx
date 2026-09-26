"use client";

import { useQuery } from "@tanstack/react-query";
import {
  BarChart3, Bell, Building2, CheckSquare, ChevronsUpDown, Columns3, FileSignature, Handshake, HeartHandshake, Home, Magnet, PackageCheck, Landmark, LogOut, Menu,
  Monitor, Moon, Package, Search, Settings, ShieldCheck, Sparkles, Stamp, Sun, Users, X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { AidenAvatar, CirraLogo, CirraMark } from "@/components/Brand";
import { CopilotPanel } from "@/components/CopilotPanel";
import { QuickLogModal } from "@/components/QuickLogModal";
import { Button } from "@/components/ui/button";
import { Avatar, Dropdown, DropdownContent, DropdownItem, DropdownSeparator, DropdownTrigger, Kbd } from "@/components/ui/misc";
import { api, get, getToken, setToken } from "@/lib/api";
import { ROLE_LABELS, useMe } from "@/lib/me";
import { ui } from "@/lib/store";
import type { Me, Notification } from "@/lib/types";
import { cn, relativeDays } from "@/lib/utils";

type NavItem = { href: string; label: string; icon: typeof Home; resource?: string; action?: "read" | "update" };
const NAV: { group: string | null; items: NavItem[] }[] = [
  { group: null, items: [
    { href: "/", label: "Home", icon: Home },
    { href: "/leads", label: "Leads", icon: Magnet, resource: "leads" },
    { href: "/pipeline", label: "Pipeline", icon: Columns3, resource: "deals" },
    { href: "/accounts", label: "Accounts", icon: Building2, resource: "accounts" },
    { href: "/contacts", label: "Contacts", icon: Users, resource: "contacts" },
    { href: "/tasks", label: "Tasks", icon: CheckSquare, resource: "tasks" },
    { href: "/ask", label: "Ask Aiden", icon: Sparkles, resource: "activities" },
  ] },
  { group: "Revenue", items: [
    { href: "/quotes", label: "Quotes", icon: FileSignature, resource: "quotes" },
    { href: "/approvals", label: "Approvals", icon: Stamp, resource: "approvals" },
    { href: "/orders", label: "Orders", icon: PackageCheck, resource: "orders" },
    { href: "/products", label: "Products", icon: Package, resource: "products" },
    { href: "/reports", label: "Reports", icon: BarChart3, resource: "reports" },
  ] },
  { group: "Customers", items: [
    { href: "/success", label: "Customer success", icon: HeartHandshake, resource: "success" },
    { href: "/finance", label: "Finance & ERP", icon: Landmark, resource: "finance" },
    { href: "/partners", label: "Partners", icon: Handshake, resource: "partners" },
  ] },
  { group: "Workspace", items: [
    { href: "/admin", label: "Admin", icon: ShieldCheck, resource: "admin" },
  ] },
];

/** The SDC Solutions product family (Functional Solution Specification section 1). */
const SDC_SUITE = [
  { name: "Cirra", mark: "", desc: "AI CRM · Connect what matters.", active: true },
  { name: "promo", mark: "Q", desc: "SDC Solutions" },
  { name: "Yield", mark: "S", desc: "SDC Solutions" },
  { name: "deduct", mark: "✔", desc: "SDC Solutions" },
  { name: "nexora", mark: "§", desc: "SDC Solutions" },
];

export function Logo({ className, height = 26 }: { className?: string; height?: number }) {
  return <CirraLogo height={height} className={className} />;
}

function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const next = theme === "light" ? "dark" : theme === "dark" ? "system" : "light";
  const Icon = !mounted ? Monitor : theme === "light" ? Sun : theme === "dark" ? Moon : Monitor;
  return (
    <Button variant="ghost" size="icon" onClick={() => setTheme(next)} aria-label={`Theme: ${theme}. Switch to ${next}`} title={`Theme: ${mounted ? theme : "system"}`}>
      <Icon className="h-4 w-4" />
    </Button>
  );
}

function Sidebar({ user, onNavigate }: { user?: Me; onNavigate?: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const { can } = useMe();
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 items-center px-4">
        <Link href="/" onClick={onNavigate}><Logo /></Link>
      </div>
      <div className="px-3 pb-2">
        <button
          onClick={() => { ui.openQuickLog(); onNavigate?.(); }}
          className="ai-border group flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm shadow-card transition-shadow hover:shadow-pop"
        >
          <Sparkles className="h-4 w-4 text-ai" />
          <span className="flex-1 font-medium">Quick-Log</span>
          <Kbd>⌘K</Kbd>
        </button>
      </div>
      <nav className="flex-1 space-y-4 overflow-y-auto px-3 py-2 scrollbar-thin">
        {NAV.map(({ group, items }) => {
          const visible = items.filter((i) => !i.resource || can(i.resource, i.action ?? "read"));
          if (!visible.length) return null;
          return (
            <div key={group ?? "main"} className="space-y-0.5">
              {group && <p className="px-2.5 pb-1 text-[11px] font-medium uppercase tracking-wide text-subtle">{group}</p>}
              {visible.map(({ href, label, icon: Icon }) => {
                const active = href === "/" ? pathname === "/" : pathname.startsWith(href) || (href === "/pipeline" && pathname.startsWith("/deals")) ||
                  (href === "/quotes" && pathname.startsWith("/documents"));
                return (
                  <Link
                    key={href}
                    href={href}
                    onClick={onNavigate}
                    className={cn(
                      "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm transition-colors",
                      active ? "bg-surface font-medium text-foreground shadow-card ring-1 ring-border" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                    )}
                  >
                    <Icon className={cn("h-4 w-4", active && "text-primary")} />
                    {label}
                  </Link>
                );
              })}
            </div>
          );
        })}
      </nav>
      <div className="space-y-1 border-t p-3">
        <Dropdown>
          <DropdownTrigger asChild>
            <button className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12.5px] text-muted-foreground hover:bg-muted">
              <span className="flex h-5 items-center justify-center rounded bg-foreground px-1 text-[9.5px] font-bold tracking-tight text-background">SDC</span>
              <span className="flex-1">SDC Solutions suite</span>
              <ChevronsUpDown className="h-3.5 w-3.5" />
            </button>
          </DropdownTrigger>
          <DropdownContent side="top" align="start" className="w-64">
            <div className="px-2 pb-1 pt-1.5 text-[11px] font-medium uppercase tracking-wide text-subtle">SDC Solutions portfolio</div>
            {SDC_SUITE.map((p) => (
              <DropdownItem key={p.name} disabled={!p.active} className={cn(!p.active && "opacity-60")}>
                {p.active ? <CirraMark size={24} /> : <span className="flex h-6 w-6 items-center justify-center rounded-md bg-muted text-xs font-bold text-muted-foreground">{p.mark}</span>}
                <span className="flex-1">
                  <span className="block font-medium">{p.mark ? `${p.name} [${p.mark}]` : p.name}</span>
                  <span className="block text-[11.5px] text-muted-foreground">{p.desc}</span>
                </span>
                {p.active && <span className="text-[11px] text-primary">Current</span>}
              </DropdownItem>
            ))}
          </DropdownContent>
        </Dropdown>
        {user && (
          <Dropdown>
            <DropdownTrigger asChild>
              <button className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-muted">
                <Avatar name={user.full_name} size={26} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-medium">{user.full_name}</span>
                  <span className="block truncate text-[11.5px] text-muted-foreground">{ROLE_LABELS[user.role] ?? user.role}</span>
                </span>
              </button>
            </DropdownTrigger>
            <DropdownContent side="top" align="start">
              <DropdownItem onSelect={() => router.push("/settings")}><Settings className="h-4 w-4" />Settings & AI engine</DropdownItem>
              <DropdownSeparator />
              <DropdownItem onSelect={() => { setToken(null); window.location.href = "/login"; }}><LogOut className="h-4 w-4" />Sign out</DropdownItem>
            </DropdownContent>
          </Dropdown>
        )}
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [ready, setReady] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    if (!getToken()) router.replace("/login");
    else setReady(true);
  }, [router]);

  const { data: user } = useQuery({ queryKey: ["me"], queryFn: () => get<Me>("/users/me"), enabled: ready });

  useEffect(() => {
    if (user?.role === "partner") router.replace("/portal");
  }, [user, router]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        ui.openQuickLog();
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        ui.set({ copilotOpen: !ui.get().copilotOpen });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => setMobileOpen(false), [pathname]);

  // Hold rendering until the role is known, so partner users never fire internal CRM queries.
  if (!ready || !user || user.role === "partner") return <div className="min-h-screen bg-background" />;

  return (
    <div className="flex min-h-screen bg-background">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 border-r bg-surface-2/60 lg:block">
        <Sidebar user={user} />
      </aside>
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setMobileOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 border-r bg-surface animate-slide-up">
            <button className="absolute right-3 top-4 rounded p-1 text-muted-foreground" onClick={() => setMobileOpen(false)} aria-label="Close menu"><X className="h-4 w-4" /></button>
            <Sidebar user={user} onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b bg-background/80 px-4 backdrop-blur lg:px-6">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open menu"><Menu className="h-4 w-4" /></Button>
          <button
            onClick={() => ui.openQuickLog()}
            className="flex h-9 min-w-0 max-w-md flex-1 items-center gap-2 rounded-lg border bg-surface px-3 text-left text-sm text-subtle shadow-card transition-colors hover:border-input"
          >
            <Search className="h-4 w-4 shrink-0" />
            <span className="truncate">Search, or paste meeting notes to log…</span>
            <span className="ml-auto hidden shrink-0 items-center gap-1 sm:flex"><Kbd>⌘</Kbd><Kbd>K</Kbd></span>
          </button>
          <div className="ml-auto flex items-center gap-1">
            <Button variant="outline" size="sm" onClick={() => ui.set({ copilotOpen: true })} className="hidden sm:inline-flex">
              <AidenAvatar size={18} />Aiden<Kbd className="ml-1">⌘J</Kbd>
            </Button>
            <NotificationBell />
            <ThemeToggle />
          </div>
        </header>
        <main className="flex-1 px-4 py-6 lg:px-8">{children}</main>
      </div>
      <QuickLogModal />
      <CopilotPanel />
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: React.ReactNode; description?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-[22px] font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}


function NotificationBell() {
  const router = useRouter();
  const { data, refetch } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => get<{ unread: number; items: Notification[] }>("/notifications"),
    refetchInterval: 60_000,
  });
  const markAll = async () => {
    await api.post("/notifications/read");
    refetch();
  };
  return (
    <Dropdown onOpenChange={(o) => { if (!o && data?.unread) markAll(); }}>
      <DropdownTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={`Notifications${data?.unread ? ` (${data.unread} unread)` : ""}`} className="relative">
          <Bell className="h-4 w-4" />
          {!!data?.unread && (
            <span className="absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold text-destructive-foreground">
              {data.unread > 9 ? "9+" : data.unread}
            </span>
          )}
        </Button>
      </DropdownTrigger>
      <DropdownContent align="end" className="w-[340px] p-0">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-sm font-semibold">Notifications</span>
          <span className="text-[12px] text-muted-foreground">{data?.unread ?? 0} unread</span>
        </div>
        <div className="max-h-[380px] overflow-y-auto p-1 scrollbar-thin">
          {!data?.items.length && <p className="px-3 py-6 text-center text-[13px] text-muted-foreground">You are all caught up.</p>}
          {data?.items.map((n) => (
            <DropdownItem key={n.id} onSelect={() => n.link && router.push(n.link)} className="items-start">
              <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", n.read ? "bg-transparent" : "bg-primary")} />
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-medium leading-snug">{n.title}</span>
                {n.body && <span className="block truncate text-[12px] text-muted-foreground">{n.body}</span>}
                <span className="block text-[11px] capitalize text-subtle">{n.kind} · {relativeDays(n.created_at)}</span>
              </span>
            </DropdownItem>
          ))}
        </div>
      </DropdownContent>
    </Dropdown>
  );
}
