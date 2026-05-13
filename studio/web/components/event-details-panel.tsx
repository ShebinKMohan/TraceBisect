import { CalendarClock, CheckCircle2, Copy, Hash, Timer } from "lucide-react";
import type { Divergence, TraceEvent } from "@/lib/types";

type EventDetailsPanelProps = {
  event: TraceEvent | null;
  side: "baseline" | "candidate";
  divergence: Divergence | null;
};

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function modelLabel(event: TraceEvent | null): string {
  if (!event) return "No event selected";
  return event.model_version ?? event.payload.model?.toString() ?? event.type;
}

function statusFor(event: TraceEvent | null): string {
  if (!event) return "Waiting";
  if (event.type === "ERROR") return "Error";
  if (event.type === "TOOL_CALL" || event.type === "LLM_CALL") return "Observed";
  return "Success";
}

export function EventDetailsPanel({ event, side, divergence }: EventDetailsPanelProps) {
  const selectedHasDivergence =
    event !== null &&
    (event.id === divergence?.baseline_event_id || event.id === divergence?.candidate_event_id);

  return (
    <aside className="panel event-details-panel" aria-label="Selected event details" data-testid="details-panel">
      <div className="workbench-heading">
        <div>
          <p>Details</p>
          <h2>{event ? event.semantic_name : "Select an event"}</h2>
        </div>
        <span>{side}</span>
      </div>

      <div className="detail-stat-grid">
        <div>
          <CalendarClock size={15} aria-hidden />
          <span>Started</span>
          <strong>{event ? new Date(event.timestamp).toLocaleTimeString() : "--"}</strong>
        </div>
        <div>
          <Timer size={15} aria-hidden />
          <span>Latency</span>
          <strong>{event?.duration_ms === null || !event ? "0.00s" : `${(event.duration_ms / 1000).toFixed(2)}s`}</strong>
        </div>
        <div>
          <Hash size={15} aria-hidden />
          <span>Type</span>
          <strong>{event?.type ?? "--"}</strong>
        </div>
        <div>
          <CheckCircle2 size={15} aria-hidden />
          <span>Status</span>
          <strong>{statusFor(event)}</strong>
        </div>
      </div>

      <div className="detail-section">
        <div className="detail-section-title">
          <span>Run context</span>
          {selectedHasDivergence ? <strong>First divergence</strong> : null}
        </div>
        <dl className="metadata-list">
          <div>
            <dt>Model / source</dt>
            <dd>{modelLabel(event)}</dd>
          </div>
          <div>
            <dt>Event id</dt>
            <dd>{event?.id ?? "--"}</dd>
          </div>
          <div>
            <dt>Source id</dt>
            <dd>{event?.source_event_id ?? "native"}</dd>
          </div>
        </dl>
      </div>

      <div className="detail-section">
        <div className="detail-section-title">
          <span>Payload</span>
          <Copy size={15} aria-hidden />
        </div>
        <pre className="payload-inspector">
          <code>{event ? pretty(event.payload) : "Select an event in the trace tree."}</code>
        </pre>
      </div>
    </aside>
  );
}
