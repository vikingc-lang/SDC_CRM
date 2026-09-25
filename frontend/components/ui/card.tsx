import * as React from "react";
import { cn } from "@/lib/utils";

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-lg border bg-surface shadow-card", className)} {...props} />;
}

export function CardHeader({ className, title, description, action, icon }: {
  className?: string; title: React.ReactNode; description?: React.ReactNode; action?: React.ReactNode; icon?: React.ReactNode;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-3 px-5 pb-3 pt-4", className)}>
      <div className="min-w-0">
        <h3 className="flex items-center gap-2 text-sm font-semibold tracking-tight">{icon}{title}</h3>
        {description && <p className="mt-0.5 text-[13px] text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function CardBody({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-5 pb-5", className)} {...props} />;
}
