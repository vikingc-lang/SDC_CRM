"use client";

import { Boxes, Plus } from "lucide-react";
import { useParams } from "next/navigation";
import { useState } from "react";
import { PageHeader } from "@/components/AppShell";
import { ListViewPicker, useListView, ViewGrid, type ViewSource } from "@/components/listviews";
import { NewRecordDialog, useObjects } from "@/components/objects";
import { Button } from "@/components/ui/button";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { useMe } from "@/lib/me";

export default function CustomObjectListPage() {
  const { key } = useParams<{ key: string }>();
  const { can } = useMe();
  const objects = useObjects();
  const obj = objects.data?.find((o) => o.key === key);
  const lv = useListView(`obj_${key}` as ViewSource, true);
  const [creating, setCreating] = useState(false);

  if (objects.isLoading) return <Skeleton className="h-96" />;
  if (!obj) return <EmptyState icon={<Boxes className="h-4 w-4" />} title="No such object" description="It may have been removed, or your role can't use custom objects." />;
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader title={obj.plural_label} description={obj.description ?? "Custom object"}
        actions={can("custom_objects", "create") ? <Button size="sm" onClick={() => setCreating(true)}><Plus className="h-3.5 w-3.5" />New {obj.label.toLowerCase()}</Button> : undefined} />
      <ListViewPicker lv={lv} className="mb-3 flex-wrap" />
      {lv.view ? <ViewGrid lv={lv} /> : <Skeleton className="h-64" />}
      <NewRecordDialog obj={obj} open={creating} onOpenChange={setCreating} />
    </div>
  );
}
