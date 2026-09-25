"use client";

import * as DropdownPrimitive from "@radix-ui/react-dropdown-menu";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import * as React from "react";
import { cn, initials } from "@/lib/utils";

export function Kbd({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <kbd className={cn("inline-flex h-5 min-w-5 items-center justify-center rounded border border-border bg-surface-2 px-1 font-sans text-[11px] font-medium text-muted-foreground", className)}>
      {children}
    </kbd>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton h-4", className)} />;
}

const AVATAR_TINTS = ["bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300", "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300", "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300", "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300", "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300", "bg-indigo-100 text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-300"];

export function Avatar({ name, size = 28, className }: { name?: string | null; size?: number; className?: string }) {
  const tint = AVATAR_TINTS[(name ?? "").split("").reduce((a, c) => a + c.charCodeAt(0), 0) % AVATAR_TINTS.length];
  return (
    <span
      className={cn("inline-flex shrink-0 items-center justify-center rounded-full font-semibold", tint, className)}
      style={{ width: size, height: size, fontSize: Math.max(10, size * 0.38) }}
      title={name ?? undefined}
    >
      {initials(name)}
    </span>
  );
}

export function Tooltip({ content, children, side = "top" }: { content: React.ReactNode; children: React.ReactNode; side?: "top" | "bottom" | "left" | "right" }) {
  return (
    <TooltipPrimitive.Provider delayDuration={200}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content side={side} sideOffset={6} className="z-[60] max-w-xs rounded-md bg-foreground px-2.5 py-1.5 text-xs text-background shadow-pop animate-fade-in">
            {content}
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}

export const Dropdown = DropdownPrimitive.Root;
export const DropdownTrigger = DropdownPrimitive.Trigger;

export function DropdownContent({ className, ...props }: React.ComponentPropsWithoutRef<typeof DropdownPrimitive.Content>) {
  return (
    <DropdownPrimitive.Portal>
      <DropdownPrimitive.Content sideOffset={6} className={cn("z-50 min-w-[200px] rounded-lg border bg-surface p-1 shadow-pop animate-fade-in", className)} {...props} />
    </DropdownPrimitive.Portal>
  );
}

export function DropdownItem({ className, ...props }: React.ComponentPropsWithoutRef<typeof DropdownPrimitive.Item>) {
  return (
    <DropdownPrimitive.Item
      className={cn("flex cursor-pointer select-none items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-muted", className)}
      {...props}
    />
  );
}

export const DropdownSeparator = () => <DropdownPrimitive.Separator className="my-1 h-px bg-border" />;

export function EmptyState({ icon, title, description, action }: { icon: React.ReactNode; title: string; description?: string; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-muted text-muted-foreground">{icon}</div>
      <p className="text-sm font-medium">{title}</p>
      {description && <p className="mt-1 max-w-sm text-[13px] text-muted-foreground">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
