import {
  Bot,
  Braces,
  CheckCircle2,
  CircleAlert,
  GitBranch,
  MousePointer2,
  Play,
  RadioTower,
  Wrench,
} from "lucide-react";
import type { TraceEvent, TraceSummary } from "@/lib/types";
import { formatEventLabel, friendlySourceConvention, friendlyTraceName } from "@/lib/format";

type TraceWorkbenchProps = {
  side: "baseline" | "candidate";
  trace?: TraceSummary;
  events: TraceEvent[];
  highlightedIds: Set<string>;
  selectedEventId: string | null;
  onSelectEvent: (event: TraceEvent) => void;
};

function iconFor(type: string) {
  switch (type) {
    case "RUN_START":
      return Play;
    case "LLM_CALL":
      return Bot;
    case "TOOL_CALL":
      return Wrench;
    case "BRANCH_DECISION":
      return GitBranch;
    case "ERROR":
      return CircleAlert;
    case "RETRIEVAL":
      return RadioTower;
    case "RUN_END":
      return CheckCircle2;
    default:
      return Braces;
  }
}

function eventDepth(event: TraceEvent, byId: Map<string, TraceEvent>): number {
  let depth = 0;
  let parentId = event.parent_id;
  while (parentId) {
    const parent = byId.get(parentId);
    if (!parent) break;
    depth += 1;
    parentId = parent.parent_id;
  }
  return Math.min(depth, 3);
}

function durationLabel(event: TraceEvent): string {
  return event.duration_ms === null ? "0.00s" : `${(event.duration_ms / 1000).toFixed(2)}s`;
}

export function TraceWorkbench({
  side,
  trace,
  events,
  highlightedIds,
  selectedEventId,
  onSelectEvent,
}: TraceWorkbenchProps) {
  const byId = new Map(events.map((event) => [event.id, event]));

  return (
    <section className="panel trace-workbench" aria-label={`${side} execution trace`} data-testid="trace-tree">
      <div className="workbench-heading">
        <div>
          <p>Execution trace</p>
          <h2>{friendlyTraceName(trace?.display_name)}</h2>
        </div>
        <span>{friendlySourceConvention(trace?.source_convention)}</span>
      </div>

      <ol className="trace-tree-list">
        {events.map((event) => {
          const Icon = iconFor(event.type);
          const highlighted = highlightedIds.has(event.id);
          const active = selectedEventId === event.id;
          return (
            <li key={event.id}>
              <button
                className={[
                  "trace-tree-row",
                  highlighted ? "trace-tree-row-drift" : "",
                  active ? "trace-tree-row-active" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                data-depth={eventDepth(event, byId)}
                data-testid={`trace-event-${event.id}`}
                onClick={() => onSelectEvent(event)}
                type="button"
              >
                <span className="trace-event-icon" aria-hidden>
                  <Icon size={15} />
                </span>
                <span className="trace-event-main">
                  <strong>{formatEventLabel(event)}</strong>
                  <small>{event.semantic_name}</small>
                </span>
                <span className="trace-event-meta">
                  <small>{durationLabel(event)}</small>
                  {highlighted ? <em>First change</em> : null}
                </span>
              </button>
            </li>
          );
        })}
      </ol>

      <div className="trace-help">
        <MousePointer2 size={15} aria-hidden />
        Select an event to inspect timing, model inputs, outputs, and the regression impact.
      </div>
    </section>
  );
}
