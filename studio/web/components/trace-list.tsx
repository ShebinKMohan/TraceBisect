import {
  Bot,
  CheckCircle2,
  CircleAlert,
  CircleDollarSign,
  Clock3,
  Code2,
  Database,
  GitCommit,
  Hash,
  KeyRound,
  MessageSquareText,
  Search,
} from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import type { TraceSummary } from "@/lib/types";
import {
  formatDuration,
  formatShortDate,
  friendlySourceConvention,
  friendlyTraceName,
} from "@/lib/format";

type TraceListProps = {
  traces: TraceSummary[];
  searchQuery: string;
};

type TraceStatusFilter = "all" | TraceSummary["status"];
type TraceSortKey = "created_at" | "duration_ms" | "total_tokens" | "total_cost_usd";

const statusFilters: { label: string; value: TraceStatusFilter }[] = [
  { label: "All traces", value: "all" },
  { label: "Successful", value: "success" },
  { label: "Errored", value: "error" },
  { label: "Unknown", value: "unknown" },
];

function traceStatusLabel(status: TraceSummary["status"]): string {
  if (status === "success") return "Success";
  if (status === "error") return "Error";
  return "Unknown";
}

function formatTokens(value: number): string {
  return value > 0 ? value.toLocaleString() : "--";
}

function formatCost(value: number): string {
  return value > 0 ? `$${value.toFixed(5)}` : "$0.00000";
}

function formatCodeSha(value: string | null): string {
  return value ? value.slice(0, 8) : "--";
}

function traceSearchText(trace: TraceSummary): string {
  return [
    trace.display_name,
    trace.trace_id,
    trace.source_convention,
    trace.model,
    trace.model_version,
    trace.prompt_version,
    trace.code_sha,
    trace.agent_name,
    trace.agent_version,
    trace.session_id,
    trace.root_event,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

export function TraceList({ traces, searchQuery }: TraceListProps) {
  const [statusFilter, setStatusFilter] = useState<TraceStatusFilter>("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [modelFilter, setModelFilter] = useState("all");
  const [sortKey, setSortKey] = useState<TraceSortKey>("created_at");
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);

  const sources = useMemo(
    () => Array.from(new Set(traces.map((trace) => trace.source_convention))).sort(),
    [traces],
  );
  const models = useMemo(
    () =>
      Array.from(new Set(traces.map((trace) => trace.model).filter((model): model is string => Boolean(model)))).sort(),
    [traces],
  );

  const sorted = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    const filtered = traces.filter((trace) => {
      return (
        (statusFilter === "all" || trace.status === statusFilter) &&
        (sourceFilter === "all" || trace.source_convention === sourceFilter) &&
        (modelFilter === "all" || trace.model === modelFilter) &&
        (!query || traceSearchText(trace).includes(query))
      );
    });
    return [...filtered].sort((left, right) => {
      if (sortKey === "created_at") {
        return new Date(right.created_at).getTime() - new Date(left.created_at).getTime();
      }
      return right[sortKey] - left[sortKey];
    });
  }, [modelFilter, searchQuery, sortKey, sourceFilter, statusFilter, traces]);
  const selectedTrace =
    sorted.find((trace) => trace.id === selectedTraceId) ??
    sorted[0] ??
    null;

  useEffect(() => {
    if (!selectedTraceId || !sorted.some((trace) => trace.id === selectedTraceId)) {
      setSelectedTraceId(sorted[0]?.id ?? null);
    }
  }, [selectedTraceId, sorted]);

  const totalTokens = traces.reduce((total, trace) => total + trace.total_tokens, 0);
  const totalCost = traces.reduce((total, trace) => total + trace.total_cost_usd, 0);
  const sourceCount = new Set(traces.map((trace) => trace.source_convention)).size;
  const matchingText =
    sorted.length === traces.length
      ? `${traces.length} total`
      : `${sorted.length} of ${traces.length} visible`;

  return (
    <section className="trace-inventory-workbench" data-testid="trace-table">
      <div className="panel trace-list-panel">
        <div className="section-heading">
          <div>
            <p>Trace table</p>
            <h2>Captured traces</h2>
          </div>
          <Database size={18} aria-hidden />
        </div>

        <div className="trace-summary-strip" aria-label="Trace inventory summary">
          <article>
            <span>Traces</span>
            <strong>{traces.length}</strong>
          </article>
          <article>
            <span>Sources</span>
            <strong>{sourceCount}</strong>
          </article>
          <article>
            <span>Tokens</span>
            <strong>{formatTokens(totalTokens)}</strong>
          </article>
          <article>
            <span>Cost</span>
            <strong>{formatCost(totalCost)}</strong>
          </article>
        </div>

        <div className="run-toolbar trace-toolbar" aria-label="Trace filters">
          {statusFilters.map((filter) => (
            <button
              className={statusFilter === filter.value ? "filter-chip filter-chip-active" : "filter-chip"}
              key={filter.value}
              onClick={() => setStatusFilter(filter.value)}
              type="button"
            >
              {filter.value === "error" ? <CircleAlert size={14} aria-hidden /> : <CheckCircle2 size={14} aria-hidden />}
              {filter.label}
            </button>
          ))}
          <label className="filter-select">
            <span className="sr-only">Source filter</span>
            <select
              aria-label="Source filter"
              data-testid="trace-filter-source"
              onChange={(event) => setSourceFilter(event.currentTarget.value)}
              value={sourceFilter}
            >
              <option value="all">All sources</option>
              {sources.map((source) => (
                <option key={source} value={source}>
                  {friendlySourceConvention(source)}
                </option>
              ))}
            </select>
          </label>
          <label className="filter-select">
            <span className="sr-only">Model filter</span>
            <select
              aria-label="Model filter"
              data-testid="trace-filter-model"
              onChange={(event) => setModelFilter(event.currentTarget.value)}
              value={modelFilter}
            >
              <option value="all">All models</option>
              {models.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </select>
          </label>
          <label className="filter-select">
            <span className="sr-only">Sort traces</span>
            <select
              aria-label="Sort traces"
              data-testid="trace-sort"
              onChange={(event) => setSortKey(event.currentTarget.value as TraceSortKey)}
              value={sortKey}
            >
              <option value="created_at">Newest first</option>
              <option value="duration_ms">Slowest duration</option>
              <option value="total_tokens">Most tokens</option>
              <option value="total_cost_usd">Highest cost</option>
            </select>
          </label>
          <div className="table-search">
            <Search size={14} aria-hidden />
            <span>{searchQuery.trim() ? `Searching "${searchQuery.trim()}"` : "Search from the top bar"}</span>
          </div>
        </div>

        <div className="trace-table" role="table" aria-label="Trace inventory">
          <div className="trace-table-head" role="row">
            <span>Trace</span>
            <span>Status</span>
            <span>Source</span>
            <span>Model</span>
            <span>Prompt</span>
            <span>Tokens</span>
            <span>Duration</span>
            <span>Created</span>
          </div>
          {sorted.map((trace) => (
            <button
              aria-pressed={selectedTrace?.id === trace.id}
              className={selectedTrace?.id === trace.id ? "trace-row trace-row-active" : "trace-row"}
              data-testid={`trace-row-${trace.id}`}
              key={trace.id}
              onClick={() => setSelectedTraceId(trace.id)}
              role="row"
              type="button"
            >
              <span className="trace-main-cell">
                <strong>{friendlyTraceName(trace.display_name)}</strong>
                <small>{trace.trace_id}</small>
              </span>
              <span>
                <em className={`tier-pill tier-${trace.status === "error" ? "failing" : "passing"}`}>
                  {traceStatusLabel(trace.status)}
                </em>
              </span>
              <span>{friendlySourceConvention(trace.source_convention)}</span>
              <span>{trace.model ?? "--"}</span>
              <span>{trace.prompt_version ?? "--"}</span>
              <span>
                {formatTokens(trace.total_tokens)}
                <small>{formatCost(trace.total_cost_usd)}</small>
              </span>
              <span>{formatDuration(trace.duration_ms)}</span>
              <span>{formatShortDate(trace.created_at)}</span>
            </button>
          ))}
          {sorted.length === 0 ? (
            <div className="run-empty" role="status">
              <span>No traces match the current filters.</span>
              <button
                className="inline-action"
                onClick={() => {
                  setStatusFilter("all");
                  setSourceFilter("all");
                  setModelFilter("all");
                }}
                type="button"
              >
                Clear trace filters
              </button>
            </div>
          ) : null}
        </div>
      </div>

      <aside className="panel trace-detail-panel" data-testid="trace-detail-panel">
        <div className="section-heading">
          <div>
            <p>Selected trace</p>
            <h2>{selectedTrace ? friendlyTraceName(selectedTrace.display_name) : "No trace selected"}</h2>
          </div>
          <span className="tier-pill tier-neutral">{matchingText}</span>
        </div>

        {selectedTrace ? (
          <>
            <div className="trace-detail-hero">
              <div>
                <span>{traceStatusLabel(selectedTrace.status)}</span>
                <strong>{selectedTrace.root_event}</strong>
              </div>
              <div>
                <span>Trace ID</span>
                <code>{selectedTrace.trace_id}</code>
              </div>
            </div>

            <div className="trace-detail-metrics">
              <TraceMetric
                icon={<Clock3 size={15} aria-hidden />}
                label="Duration"
                value={formatDuration(selectedTrace.duration_ms)}
              />
              <TraceMetric
                icon={<Hash size={15} aria-hidden />}
                label="Events"
                value={selectedTrace.event_count.toString()}
              />
              <TraceMetric
                icon={<MessageSquareText size={15} aria-hidden />}
                label="Tokens"
                value={formatTokens(selectedTrace.total_tokens)}
              />
              <TraceMetric
                icon={<CircleDollarSign size={15} aria-hidden />}
                label="Cost"
                value={formatCost(selectedTrace.total_cost_usd)}
              />
            </div>

            <dl className="trace-detail-list">
              <TraceFact icon={<Bot size={15} aria-hidden />} label="Model" value={selectedTrace.model ?? "--"} />
              <TraceFact
                icon={<Code2 size={15} aria-hidden />}
                label="Model version"
                value={selectedTrace.model_version ?? "--"}
              />
              <TraceFact
                icon={<KeyRound size={15} aria-hidden />}
                label="Prompt version"
                value={selectedTrace.prompt_version ?? "--"}
              />
              <TraceFact icon={<GitCommit size={15} aria-hidden />} label="Code SHA" value={formatCodeSha(selectedTrace.code_sha)} />
              <TraceFact
                icon={<Database size={15} aria-hidden />}
                label="Source"
                value={friendlySourceConvention(selectedTrace.source_convention)}
              />
              <TraceFact icon={<MessageSquareText size={15} aria-hidden />} label="Session" value={selectedTrace.session_id ?? "--"} />
            </dl>
          </>
        ) : (
          <div className="trace-detail-empty" role="status">
            <Search size={18} aria-hidden />
            <strong>No trace selected</strong>
            <span>Adjust filters or search to find a captured trace.</span>
          </div>
        )}
      </aside>
    </section>
  );
}

function TraceMetric({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <article>
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function TraceFact({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div>
      <dt>
        {icon}
        {label}
      </dt>
      <dd>{value}</dd>
    </div>
  );
}
