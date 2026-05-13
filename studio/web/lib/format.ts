import type { JsonValue, TraceEvent } from "@/lib/types";

export function formatJson(value: JsonValue): string {
  return JSON.stringify(value, null, 2);
}

export function formatEventLabel(event: TraceEvent): string {
  return `${event.sequence_index.toString().padStart(2, "0")} ${event.type}`;
}

export function formatShortDate(value: string): string {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
