"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Bell, CheckSquare, Columns3, Download, Home, Plus, Smartphone, WifiOff } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useMe } from "@/lib/me";
import { flush, outbox } from "@/lib/offline";
import { ui } from "@/lib/store";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** Registers the service worker (installable app, offline reading, push). Development builds skip it so hot reload
 * never serves stale code. */
export function useServiceWorker() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  }, []);
}

function useOnline(): boolean {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    setOnline(navigator.onLine);
    const on = () => setOnline(true), off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => { window.removeEventListener("online", on); window.removeEventListener("offline", off); };
  }, []);
  return online;
}

/** Offline banner, and sending the changes queued while offline once the connection is back. */
export function OfflineBanner() {
  const online = useOnline();
  const qc = useQueryClient();
  const t = useT();
  const [queued, setQueued] = useState(0);
  useEffect(() => {
    setQueued(outbox().length);
    const onChange = (e: Event) => setQueued((e as CustomEvent<number>).detail);
    window.addEventListener("cirra:outbox", onChange);
    return () => window.removeEventListener("cirra:outbox", onChange);
  }, []);
  useEffect(() => {
    if (!online || !outbox().length) return;
    flush((item) => api.request({ method: item.method, url: item.url, data: item.data })).then(({ sent, failed }) => {
      if (sent) {
        toast.success(`${sent} change${sent === 1 ? "" : "s"} made offline ${sent === 1 ? "was" : "were"} saved`);
        qc.invalidateQueries();
      }
      for (const f of failed) toast.error(`Couldn't save "${f.label}" made offline`);
    });
  }, [online, qc]);
  if (online && !queued) return null;
  return (
    <div role="status" className={cn("flex items-center gap-2 px-4 py-1.5 text-[12.5px]", online ? "bg-primary-soft text-primary" : "bg-[color-mix(in_srgb,var(--status-warning)_20%,transparent)]")}>
      <WifiOff className="h-3.5 w-3.5 shrink-0" />
      <span className="flex-1">{online ? t("shell.queued", { n: queued }) : t("shell.offline")}</span>
      {!online && queued > 0 && <span className="shrink-0 font-medium">{t("shell.queued", { n: queued })}</span>}
    </div>
  );
}

/** Thumb-reach navigation on phones: the four screens people use most, plus Quick-Log. */
export function MobileTabBar() {
  const pathname = usePathname();
  const { can } = useMe();
  const t = useT();
  const tabs = [
    { href: "/", label: t("nav.home"), icon: Home, show: can("deals", "read") },
    { href: "/pipeline", label: t("nav.pipeline"), icon: Columns3, show: can("deals", "read") },
    { href: "/tasks", label: t("nav.tasks"), icon: CheckSquare, show: can("tasks", "read") },
    { href: "/notifications", label: t("shell.notifications"), icon: Bell, show: true },
  ].filter((x) => x.show);
  return (
    <nav aria-label="Quick navigation" className="fixed inset-x-0 bottom-0 z-30 border-t bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">
      <div className="mx-auto flex max-w-lg items-stretch justify-around">
        {tabs.slice(0, 2).map((x) => <Tab key={x.href} {...x} active={x.href === "/" ? pathname === "/" : pathname.startsWith(x.href)} />)}
        <button onClick={() => ui.openQuickLog()} aria-label={t("shell.quick_log")}
          className="-mt-3 flex h-12 w-12 shrink-0 items-center justify-center self-center rounded-full bg-primary text-primary-foreground shadow-pop">
          <Plus className="h-5 w-5" />
        </button>
        {tabs.slice(2).map((x) => <Tab key={x.href} {...x} active={pathname.startsWith(x.href)} />)}
      </div>
    </nav>
  );
}

function Tab({ href, label, icon: Icon, active }: { href: string; label: string; icon: typeof Home; active: boolean }) {
  return (
    <Link href={href} className={cn("flex min-w-0 flex-1 flex-col items-center gap-0.5 py-2 text-[10.5px]", active ? "text-primary" : "text-muted-foreground")}
      aria-current={active ? "page" : undefined}>
      <Icon className="h-5 w-5" />
      <span className="max-w-full truncate">{label}</span>
    </Link>
  );
}

interface InstallPrompt extends Event { prompt: () => Promise<void>; userChoice: Promise<{ outcome: "accepted" | "dismissed" }> }

/** Settings → Cirra on your phone: install the app (or how to, on iPhone). */
export function InstallAppCard() {
  const [prompt, setPrompt] = useState<InstallPrompt | null>(null);
  const [installed, setInstalled] = useState(false);
  const [ios, setIos] = useState(false);
  useEffect(() => {
    setInstalled(window.matchMedia("(display-mode: standalone)").matches || (navigator as { standalone?: boolean }).standalone === true);
    setIos(/iPhone|iPad|iPod/.test(navigator.userAgent));
    const onPrompt = (e: Event) => { e.preventDefault(); setPrompt(e as InstallPrompt); };
    const onInstalled = () => setInstalled(true);
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => { window.removeEventListener("beforeinstallprompt", onPrompt); window.removeEventListener("appinstalled", onInstalled); };
  }, []);
  return (
    <Card id="mobile">
      <CardHeader icon={<Smartphone className="h-4 w-4 text-muted-foreground" />} title="Cirra on your phone"
        description="Install Cirra like an app: it opens full screen from your home screen, keeps recently viewed records for when you're offline, queues notes and tasks you add without a connection, and can send you push notifications." />
      <CardBody className="space-y-2 text-[13px]">
        {installed ? <p className="text-muted-foreground">Cirra is installed on this device.</p>
          : prompt ? (
            <Button size="sm" onClick={async () => { await prompt.prompt(); const c = await prompt.userChoice; if (c.outcome === "accepted") setInstalled(true); setPrompt(null); }}>
              <Download className="h-3.5 w-3.5" />Install Cirra
            </Button>
          ) : ios ? <p className="text-muted-foreground">In Safari, tap <b>Share</b>, then <b>Add to Home Screen</b>.</p>
            : <p className="text-muted-foreground">Open this page on your phone and choose <b>Install app</b> or <b>Add to Home screen</b> from the browser menu.</p>}
      </CardBody>
    </Card>
  );
}
