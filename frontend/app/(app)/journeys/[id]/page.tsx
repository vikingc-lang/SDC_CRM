"use client";

import { useParams } from "next/navigation";
import { JourneyBuilder } from "@/components/journeys";

export default function JourneyPage() {
  const { id } = useParams<{ id: string }>();
  return <div className="mx-auto max-w-6xl"><JourneyBuilder id={id} /></div>;
}
