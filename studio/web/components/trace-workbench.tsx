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

function eventParentChain(event: TraceEvent, byId: Map<string, TraceEvent>): TraceEvent[] {
  const chain: TraceEvent[] = [];
  let parentId = event.parent_id;
  while (parentId) {
    const parent = byId.get(parentId);
    if (!parent) break;
    chain.unshift(parent);
    parentId = parent.parent_id;
  }
  return chain.slice(-3);
}

function hasLaterSibling(event: TraceEvent, events: TraceEvent[]): boolean {
  const index = events.findIndex((item) => item.id === event.id);
  if (index < 0) return false;
  return events.slice(index + 1).some((item) => item.parent_id === event.parent_id);
}

function durationLabel(event: TraceEvent): string {
  return event.duration_ms === null ? "0.00s" : `${(event.duration_ms / 1000).toFixed(2)}s`;
}

function TreeGutter({
  ancestorContinuation,
  depth,
  hasNextSibling,
}: {
  ancestorContinuation: boolean[];
  depth: number;
  hasNextSibling: boolean;
}) {
  return (
    <span className="tree-gutter" aria-hidden data-depth={depth}>
      {Array.from({ length: 3 }).map((_, index) => {
        const isCurrentLevel = depth > 0 && index === depth - 1;
        const shouldContinue = ancestorContinuation[index] ?? false;
        return (
          <span
            className={[
              "tree-guide",
              shouldContinue ? "tree-guide-continue" : "",
              isCurrentLevel ? "tree-guide-branch" : "",
              isCurrentLevel && hasNextSibling ? "tree-guide-branch-open" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            key={index}
          >
            {isCurrentLevel ? <span className="tree-arrow" /> : null}
          </span>
        );
      })}
    </span>
  );
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
          const chain = eventParentChain(event, byId);
          const depth = eventDepth(event, byId);
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
                data-depth={depth}
                data-testid={`trace-event-${event.id}`}
                onClick={() => onSelectEvent(event)}
                type="button"
              >
                <TreeGutter
                  ancestorContinuation={chain.slice(1).map((ancestorChild) => hasLaterSibling(ancestorChild, events))}
                  depth={depth}
                  hasNextSibling={hasLaterSibling(event, events)}
                />
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
