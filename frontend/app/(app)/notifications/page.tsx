"use client";

import { PageHeader } from "@/components/AppShell";
import { NotificationCenter } from "@/components/notifications";

export default function NotificationsPage() {
  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title="Notifications" description="Everything Cirra told you: snooze what can wait, archive what's done, and choose where each kind reaches you." />
      <NotificationCenter />
    </div>
  );
}
