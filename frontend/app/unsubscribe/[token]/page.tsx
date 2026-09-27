"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import { useParams } from "next/navigation";
import { Logo } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/misc";
import { api, errorMessage, get } from "@/lib/api";

/** Public one-click unsubscribe from campaign email. Confirming is a POST, so link scanners can't unsubscribe people. */
export default function UnsubscribePage() {
  const { token } = useParams<{ token: string }>();
  const info = useQuery({ queryKey: ["unsubscribe", token], retry: false,
    queryFn: () => get<{ campaign: string; unsubscribed: boolean }>(`/public/unsubscribe/${token}`) });
  const confirm = useMutation({ mutationFn: async () => (await api.post(`/public/unsubscribe/${token}`)).data });
  const done = confirm.isSuccess || info.data?.unsubscribed;
  return (
    <div className="flex min-h-screen flex-col bg-background px-4 py-8">
      <div className="mx-auto w-full max-w-md"><Logo height={28} /></div>
      <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center py-10">
        {info.isLoading ? <Skeleton className="h-40" /> : info.isError ? (
          <p className="text-sm text-muted-foreground">{errorMessage(info.error, "This unsubscribe link isn't valid.")}</p>
        ) : done ? (
          <div className="text-center">
            <CheckCircle2 className="mx-auto mb-3 h-8 w-8" style={{ color: "var(--status-good)" }} />
            <h1 className="text-xl font-semibold tracking-tight">You’re unsubscribed</h1>
            <p className="mt-1 text-sm text-muted-foreground">We won’t send you marketing email again. You can still hear from people you work with directly.</p>
          </div>
        ) : (
          <div className="space-y-5">
            <div>
              <h1 className="text-xl font-semibold tracking-tight">Unsubscribe from our emails?</h1>
              <p className="mt-1 text-sm text-muted-foreground">You received this through “{info.data!.campaign}”. Unsubscribing stops all marketing email from us, not just this campaign.</p>
            </div>
            {confirm.isError && <p className="text-sm text-destructive">{errorMessage(confirm.error)}</p>}
            <Button className="w-full" loading={confirm.isPending} onClick={() => confirm.mutate()}>Unsubscribe</Button>
          </div>
        )}
      </div>
    </div>
  );
}
