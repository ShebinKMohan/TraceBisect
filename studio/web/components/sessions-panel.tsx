import { CalendarDays, CheckCircle2, ChevronDown, ChevronRight, MessagesSquare, TriangleAlert, UploadCloud } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { TraceSummary } from "@/lib/types";
import {
  formatDuration,
  formatShortDate,
  friendlySourceConvention,
  friendlyTraceName,
} from "@/lib/format";

type SessionsPanelProps = {
  traces: TraceSummary[];
  searchQuery: string;
  readOnly: boolean;
  onChooseTraces: () => void;
  onSearchReset: () => void;
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

type SessionStatusFilter = "all" | "healthy" | "attention";
type SessionSortKey = "recent" | "trace_count" | "tokens" | "duration";

const sessionStatusFilters: { label: string; value: SessionStatusFilter }[] = [
  { label: "All sessions", value: "all" },
  { label: "Healthy", value: "healthy" },
  { label: "Needs attention", value: "attention" },
];

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

export function SessionsPanel({ traces, searchQuery, readOnly, onChooseTraces, onSearchReset }: SessionsPanelProps) {
  const [statusFilter, setStatusFilter] = useState<SessionStatusFilter>("all");
  const [sortKey, setSortKey] = useState<SessionSortKey>("recent");
  const groups = useMemo(() => buildSessionGroups(traces), [traces]);
  const filtered = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    const matched = groups.filter((group) => {
      const statusMatched =
        statusFilter === "all" ||
        (statusFilter === "healthy" && group.successRate === 100) ||
        (statusFilter === "attention" && group.successRate < 100);
      if (!statusMatched) return false;
      if (!query) return true;
      return [group.label, group.id, ...group.sources, ...group.models]
        .join(" ")
        .toLowerCase()
        .includes(query);
    });
    return [...matched].sort((left, right) => {
      if (sortKey === "trace_count") return right.traces.length - left.traces.length;
      if (sortKey === "tokens") return right.totalTokens - left.totalTokens;
      if (sortKey === "duration") return right.durationMs - left.durationMs;
      return Date.parse(right.latestCreatedAt) - Date.parse(left.latestCreatedAt);
    });
  }, [groups, searchQuery, sortKey, statusFilter]);
  const [expandedIds, setExpandedIds] = useState<string[]>([]);
  const [showAllTraceIds, setShowAllTraceIds] = useState<string[]>([]);
  const traceCount = traces.length;
  const successCount = traces.filter((trace) => trace.status !== "error").length;
  const successRate = traceCount > 0 ? (successCount / traceCount) * 100 : 0;
  const totalDuration = traces.reduce((total, trace) => total + trace.duration_ms, 0);
  const hasActiveFilters = Boolean(searchQuery.trim() || statusFilter !== "all");

  useEffect(() => {
    setExpandedIds((current) => {
      if (current.some((id) => filtered.some((group) => group.id === id))) return current;
      return filtered[0]?.id ? [filtered[0].id] : [];
    });
  }, [filtered]);

  function toggleExpanded(groupId: string) {
    setExpandedIds((current) =>
      current.includes(groupId)
        ? current.filter((id) => id !== groupId)
        : [...current, groupId],
    );
  }

  function toggleAllTraces(groupId: string) {
    setShowAllTraceIds((current) =>
      current.includes(groupId)
        ? current.filter((id) => id !== groupId)
        : [...current, groupId],
    );
  }

  return (
    <section className="panel session-panel" data-testid="sessions-table">
      <div className="section-heading session-page-heading">
        <div>
          <p>Related runs</p>
          <h2>Runs that belong to the same conversation or task.</h2>
        </div>
        <div className="session-actions">
          <span className="filter-chip filter-chip-static">
            <CalendarDays size={14} aria-hidden />
            All saved sessions
          </span>
        </div>
      </div>

      <div className="session-summary-strip" aria-label="Session summary">
        <article>
          <span>Sessions</span>
          <strong>{groups.length}</strong>
        </article>
        <article>
          <span>Traces</span>
          <strong>{traceCount}</strong>
        </article>
        <article>
          <span>Success rate</span>
          <strong>{successRate.toFixed(1)}%</strong>
        </article>
        <article>
          <span>Avg duration</span>
          <strong>{traceCount > 0 ? formatDuration(totalDuration / traceCount) : "--"}</strong>
        </article>
      </div>

      <div className="run-toolbar session-toolbar" aria-label="Session filters">
        {sessionStatusFilters.map((filter) => (
          <button
            className={statusFilter === filter.value ? "filter-chip filter-chip-active" : "filter-chip"}
            key={filter.value}
            onClick={() => setStatusFilter(filter.value)}
            type="button"
          >
            {filter.value === "attention" ? <TriangleAlert size={14} aria-hidden /> : <CheckCircle2 size={14} aria-hidden />}
            {filter.label}
          </button>
        ))}
        <label className="filter-select">
          <span className="sr-only">Sort sessions</span>
          <select
            aria-label="Sort sessions"
            data-testid="session-sort"
            onChange={(event) => setSortKey(event.currentTarget.value as SessionSortKey)}
            value={sortKey}
          >
            <option value="recent">Most recent</option>
            <option value="trace_count">Most traces</option>
            <option value="tokens">Most tokens</option>
            <option value="duration">Longest duration</option>
          </select>
        </label>
        <div className="table-search">
          <MessagesSquare size={14} aria-hidden />
          <span>{searchQuery.trim() ? `Searching "${searchQuery.trim()}"` : "Search from the top bar"}</span>
        </div>
      </div>

      <div className="trace-table session-table" role="table" aria-label="Trace sessions">
        <div className="session-table-head" role="row">
          <span>Conversation / task</span>
          <span>Traces</span>
          <span>Success rate</span>
          <span>Models / sources</span>
          <span>Avg tokens</span>
          <span>Avg duration</span>
          <span>Last trace</span>
        </div>
        {filtered.map((group) => {
          const expanded = expandedIds.includes(group.id);
          const showingAllTraces = showAllTraceIds.includes(group.id);
          const visibleTraces = showingAllTraces ? group.traces : group.traces.slice(0, 3);
          return (
            <div className={expanded ? "session-group session-group-open" : "session-group"} key={group.id}>
              <button
                aria-expanded={expanded}
                className="session-row"
                onClick={() => toggleExpanded(group.id)}
                role="row"
                type="button"
              >
                <span className="trace-main-cell session-main-cell">
                  {expanded ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
                  <MessagesSquare size={14} aria-hidden />
                  <span>
                    <strong>{group.label}</strong>
                    <small>{group.traces.length} related run{group.traces.length === 1 ? "" : "s"}</small>
                  </span>
                </span>
                <span>{group.traces.length.toLocaleString()}</span>
                <span>
                  {group.successRate.toFixed(1)}%
                  <i aria-hidden />
                </span>
                <span>
                  {group.models[0] ?? "--"}
                  <small>{group.sources.map((source) => friendlySourceConvention(source)).join(", ") || "--"}</small>
                </span>
                <span>{group.traces.length > 0 ? Math.round(group.totalTokens / group.traces.length).toLocaleString() : "--"}</span>
                <span>{group.traces.length > 0 ? formatDuration(group.durationMs / group.traces.length) : "--"}</span>
                <span>{formatShortDate(group.latestCreatedAt)}</span>
              </button>
              {expanded ? (
                <div className="session-expanded" role="rowgroup">
                  <div className="session-expanded-head">
                    <span>Trace ID</span>
                    <span>Timestamp</span>
                    <span>Model</span>
                    <span>Tokens</span>
                    <span>Duration</span>
                    <span>Status</span>
                  </div>
                  {visibleTraces.map((trace) => (
                    <div className="session-trace-row" key={trace.id}>
                      <code>{trace.trace_id}</code>
                      <span>{formatShortDate(trace.created_at)}</span>
                      <span>{trace.model ?? "--"}</span>
                      <span>{trace.total_tokens > 0 ? trace.total_tokens.toLocaleString() : "--"}</span>
                      <span>{formatDuration(trace.duration_ms)}</span>
                      <strong className={trace.status === "error" ? "session-status-failed" : "session-status-success"}>
                        {trace.status === "error" ? "Failed" : "Success"}
                      </strong>
                    </div>
                  ))}
                  {group.traces.length > 3 ? (
                    <button
                      aria-expanded={showingAllTraces}
                      className="session-more-row"
                      onClick={() => toggleAllTraces(group.id)}
                      type="button"
                    >
                      {showingAllTraces
                        ? "Show only the first 3 traces"
                        : `Show ${(group.traces.length - 3).toLocaleString()} more traces`}
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>
          );
        })}
        {filtered.length === 0 ? (
          <div className="table-empty-state" role="status">
            {traces.length === 0 ? <UploadCloud size={18} aria-hidden /> : <MessagesSquare size={18} aria-hidden />}
            <div>
              <strong>{traces.length === 0 ? "No sessions yet." : "No sessions match your search or filters."}</strong>
              <p>
                {traces.length === 0
                  ? "Sessions appear automatically when related traces share a conversation, thread, or scenario."
                  : "Clear the search and filters to return to all saved sessions."}
              </p>
            </div>
            {traces.length === 0 ? (
              <button className="inline-action" onClick={onChooseTraces} type="button">
                {readOnly ? "Browse traces" : "Choose traces"}
              </button>
            ) : hasActiveFilters ? (
              <button
                className="inline-action"
                onClick={() => {
                  setStatusFilter("all");
                  onSearchReset();
                }}
                type="button"
              >
                Clear search and filters
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}
