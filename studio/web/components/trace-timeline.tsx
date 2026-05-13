import type { TraceEvent, TraceSummary } from "@/lib/types";
import { formatEventLabel } from "@/lib/format";

type TraceTimelineProps = {
  title: string;
  trace?: TraceSummary;
  events: TraceEvent[];
  highlightedIds: Set<string>;
};

function eventDepth(event: TraceEvent): number {
  if (event.parent_id === null) return 0;
  if (event.parent_id.includes("llm") || event.parent_id.endsWith("002")) return 2;
  return 1;
}

export function TraceTimeline({ title, trace, events, highlightedIds }: TraceTimelineProps) {
  return (
    <section className="panel trace-panel">
      <div className="trace-title">
        <div>
          <p>{title}</p>
          <h2>{trace?.display_name ?? "Trace"}</h2>
        </div>
        <span>{trace?.event_count ?? events.length} events</span>
      </div>
      <ol className="timeline">
        {events.map((event) => {
          const highlighted = highlightedIds.has(event.id);
          return (
            <li
              className={highlighted ? "timeline-row timeline-row-active" : "timeline-row"}
              data-depth={eventDepth(event)}
              key={event.id}
            >
              <span className="timeline-node" aria-hidden />
              <div>
                <strong>{formatEventLabel(event)}</strong>
                <span>{event.semantic_name}</span>
              </div>
              <small>{event.duration_ms === null ? "instant" : `${event.duration_ms}ms`}</small>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
