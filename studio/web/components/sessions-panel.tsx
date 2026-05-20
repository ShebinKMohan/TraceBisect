import { CalendarDays, ChevronDown, ChevronRight, Filter, MessagesSquare } from "lucide-react";
import type { TraceSummary } from "@/lib/types";
import {
  formatDuration,
  formatShortDate,
  friendlyTraceName,
} from "@/lib/format";

type SessionsPanelProps = {
  traces: TraceSummary[];
  searchQuery: string;
};

type SessionGroup = {
  id: string;
  label: string;
  traces: TraceSummary[];
  sources: string[];
  models: string[];
  totalTokens: number;
  durationMs: number;
  latestCreatedAt: string;
  successRate: number;
};

function sessionLabel(value: string): string {
  if (value === "refund_search" || value === "refund_042") return "Refund search";
  return friendlyTraceName(value);
}

function buildSessionGroups(traces: TraceSummary[]): SessionGroup[] {
  const groups = new Map<string, TraceSummary[]>();
  for (const trace of traces) {
    const key = trace.session_id || trace.root_event || "ungrouped";
    groups.set(key, [...(groups.get(key) ?? []), trace]);
  }
  return Array.from(groups.entries())
    .map(([id, items]) => {
      const sorted = [...items].sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at));
      return {
        id,
        label: sessionLabel(id),
        traces: sorted,
        sources: Array.from(new Set(sorted.map((trace) => trace.source_convention))).sort(),
        models: Array.from(new Set(sorted.map((trace) => trace.model).filter((model): model is string => Boolean(model)))).sort(),
        totalTokens: sorted.reduce((total, trace) => total + trace.total_tokens, 0),
        durationMs: sorted.reduce((total, trace) => total + trace.duration_ms, 0),
        latestCreatedAt: sorted[0]?.created_at ?? "",
        successRate:
          sorted.length > 0
            ? (sorted.filter((trace) => trace.status !== "error").length / sorted.length) * 100
            : 0,
      };
    })
    .sort((left, right) => Date.parse(right.latestCreatedAt) - Date.parse(left.latestCreatedAt));
}

export function SessionsPanel({ traces, searchQuery }: SessionsPanelProps) {
  const groups = buildSessionGroups(traces);
  const query = searchQuery.trim().toLowerCase();
  const filtered = groups.filter((group) => {
    if (!query) return true;
    return [group.label, group.id, ...group.sources, ...group.models]
      .join(" ")
      .toLowerCase()
      .includes(query);
  });

  return (
    <section className="panel session-panel" data-testid="sessions-table">
      <div className="section-heading session-page-heading">
        <div>
          <p>Session groups</p>
          <h2>Grouped traces by scenario or session ID.</h2>
        </div>
        <div className="session-actions">
          <button type="button">
            <Filter size={14} aria-hidden />
            Filter
          </button>
          <button type="button">
            <CalendarDays size={14} aria-hidden />
            Last 7 Days
          </button>
        </div>
      </div>

      <div className="trace-table session-table" role="table" aria-label="Trace sessions">
        <div className="session-table-head" role="row">
          <span>Scenario / Session ID</span>
          <span>Traces</span>
          <span>Success rate</span>
          <span>Avg tokens</span>
          <span>Avg duration</span>
        </div>
        {filtered.map((group, index) => {
          const expanded = index === 0;
          return (
            <div className={expanded ? "session-group session-group-open" : "session-group"} key={group.id}>
              <article className="session-row" role="row">
                <span className="trace-main-cell session-main-cell">
                  {expanded ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
                  <MessagesSquare size={14} aria-hidden />
                  <span>
                    <strong>{group.label}</strong>
                    <small>{group.id}</small>
                  </span>
                </span>
                <span>{group.traces.length.toLocaleString()}</span>
                <span>
                  {group.successRate.toFixed(1)}%
                  <i aria-hidden />
                </span>
                <span>{group.traces.length > 0 ? Math.round(group.totalTokens / group.traces.length).toLocaleString() : "--"}</span>
                <span>{group.traces.length > 0 ? formatDuration(group.durationMs / group.traces.length) : "--"}</span>
              </article>
              {expanded ? (
                <div className="session-expanded" role="rowgroup">
                  <div className="session-expanded-head">
                    <span>Trace ID</span>
                    <span>Timestamp</span>
                    <span>Tokens</span>
                    <span>Duration</span>
                    <span>Status</span>
                  </div>
                  {group.traces.slice(0, 3).map((trace) => (
                    <div className="session-trace-row" key={trace.id}>
                      <code>{trace.trace_id}</code>
                      <span>{formatShortDate(trace.created_at)}</span>
                      <span>{trace.total_tokens > 0 ? trace.total_tokens.toLocaleString() : "--"}</span>
                      <span>{formatDuration(trace.duration_ms)}</span>
                      <strong className={trace.status === "error" ? "session-status-failed" : "session-status-success"}>
                        {trace.status === "error" ? "Failed" : "Success"}
                      </strong>
                    </div>
                  ))}
                  {group.traces.length > 3 ? (
                    <button className="session-more-row" type="button">
                      View {(group.traces.length - 3).toLocaleString()} more traces...
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>
          );
        })}
        {filtered.length === 0 ? (
          <div className="run-empty" role="status">
            <span>No sessions match the current search.</span>
          </div>
        ) : null}
      </div>
    </section>
  );
}
