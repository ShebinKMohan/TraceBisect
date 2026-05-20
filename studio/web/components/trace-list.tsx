import { CheckCircle2, CircleAlert, Columns3, Database, Search } from "lucide-react";
import { useMemo, useState } from "react";
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

export function TraceList({ traces, searchQuery }: TraceListProps) {
  const [statusFilter, setStatusFilter] = useState<TraceStatusFilter>("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [modelFilter, setModelFilter] = useState("all");

  const sources = useMemo(
    () => Array.from(new Set(traces.map((trace) => trace.source_convention))).sort(),
    [traces],
  );
  const models = useMemo(
    () =>
      Array.from(new Set(traces.map((trace) => trace.model).filter((model): model is string => Boolean(model)))).sort(),
    [traces],
  );

  const filtered = traces.filter((trace) => {
    const query = searchQuery.trim().toLowerCase();
    const haystack = [
      trace.display_name,
      trace.trace_id,
      trace.source_convention,
      trace.model,
      trace.model_version,
      trace.prompt_version,
      trace.code_sha,
      trace.session_id,
      trace.root_event,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return (
      (statusFilter === "all" || trace.status === statusFilter) &&
      (sourceFilter === "all" || trace.source_convention === sourceFilter) &&
      (modelFilter === "all" || trace.model === modelFilter) &&
      (!query || haystack.includes(query))
    );
  });

  const totalTokens = traces.reduce((total, trace) => total + trace.total_tokens, 0);
  const totalCost = traces.reduce((total, trace) => total + trace.total_cost_usd, 0);
  const sourceCount = new Set(traces.map((trace) => trace.source_convention)).size;

  return (
    <section className="panel trace-list-panel" data-testid="trace-table">
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
        <div className="table-search">
          <Search size={14} aria-hidden />
          <span>{searchQuery.trim() ? `Searching "${searchQuery.trim()}"` : "Search from the top bar"}</span>
        </div>
        <button className="filter-chip filter-chip-static" disabled type="button">
          <Columns3 size={14} aria-hidden />
          Columns
        </button>
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
        {filtered.map((trace) => (
          <article className="trace-row" key={trace.id} role="row">
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
          </article>
        ))}
        {filtered.length === 0 ? (
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
    </section>
  );
}
