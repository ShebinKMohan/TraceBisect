import { CheckCircle2, CircleAlert, Clock3, Database, GitCompare, Search } from "lucide-react";
import type { Divergence, Report, TraceSummary } from "@/lib/types";

type RunListProps = {
  report: Report | null;
  activeSide: "baseline" | "candidate";
  first: Divergence | null;
  searchQuery: string;
  onSelectSide: (side: "baseline" | "candidate") => void;
};

function formatRunDate(value?: string): string {
  if (!value) return "Pending";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function summaryFor(trace?: TraceSummary, side?: "baseline" | "candidate") {
  if (!trace) {
    return {
      id: "loading",
      display_name: "Loading trace",
      created_at: undefined,
      event_count: 0,
      root_event: "pending",
      source_convention: "unknown",
    };
  }

  return {
    ...trace,
    root_event:
      side === "candidate" ? "query adds deleted=false filter" : "query active users and refund context",
  };
}

export function RunList({ report, activeSide, first, searchQuery, onSelectSide }: RunListProps) {
  const rows = [
    {
      side: "baseline" as const,
      status: "Baseline",
      health: "stable",
      trace: summaryFor(report?.baseline, "baseline"),
    },
    {
      side: "candidate" as const,
      status: "Regression",
      health: "warning",
      trace: summaryFor(report?.candidate, "candidate"),
    },
  ];
  const normalizedQuery = searchQuery.trim().toLowerCase();
  const visibleRows = normalizedQuery
    ? rows.filter((row) =>
        [
          row.trace.display_name,
          row.trace.root_event,
          row.status,
          row.trace.source_convention,
        ].some((value) => value.toLowerCase().includes(normalizedQuery)),
      )
    : rows;

  return (
    <section className="panel run-list-panel" aria-label="Trace runs" data-testid="runs-table">
      <div className="stat-strip" aria-label="Trace summary">
        <article>
          <span>Divergences</span>
          <strong>{report?.divergence_count ?? 0}</strong>
          <small>first break detected</small>
        </article>
        <article>
          <span>Severity</span>
          <strong>{first?.severity ?? "INFO"}</strong>
          <small>{first?.type ?? "no drift"}</small>
        </article>
        <article>
          <span>Cost ratio</span>
          <strong>{first ? `${first.impact.cost_delta_ratio.toFixed(2)}x` : "1.00x"}</strong>
          <small>fixed 20% severity</small>
        </article>
        <article>
          <span>Compared runs</span>
          <strong>{rows.length}</strong>
          <small>baseline + candidate</small>
        </article>
      </div>

      <div className="run-toolbar" aria-label="Run filters">
        <span className="filter-chip filter-chip-active">
          <GitCompare size={14} aria-hidden />
          Compared
        </span>
        <span className="filter-chip">
          <Clock3 size={14} aria-hidden />
          Last 7 days
        </span>
        <span className="filter-chip">
          <Database size={14} aria-hidden />
          OTel + native
        </span>
        <div className="table-search" aria-hidden="true">
          <Search size={14} aria-hidden />
          <span>Search runs or events</span>
        </div>
      </div>

      <div className="run-table">
        <div className="run-table-head" aria-hidden="true">
          <span>Run</span>
          <span>Input</span>
          <span>Status</span>
          <span>Events</span>
          <span>Started</span>
        </div>
        {visibleRows.map((row) => (
          <button
            className={activeSide === row.side ? "run-row run-row-active" : "run-row"}
            aria-label={`${row.status}: ${row.trace.display_name}. ${row.trace.root_event}. ${row.trace.event_count} events. Started ${formatRunDate(row.trace.created_at)}.`}
            aria-pressed={activeSide === row.side}
            data-testid={`run-row-${row.side}`}
            key={row.side}
            onClick={() => onSelectSide(row.side)}
            type="button"
          >
            <span className="run-content">
              <span className={`run-status run-status-${row.health}`} aria-hidden>
                {row.health === "stable" ? <CheckCircle2 size={15} /> : <CircleAlert size={15} />}
              </span>
              <strong>{row.trace.display_name}</strong>
            </span>
            <span>{row.trace.root_event}</span>
            <span>
              <em className={`tier-pill tier-${row.health}`}>{row.status}</em>
            </span>
            <span>{row.trace.event_count}</span>
            <span>{formatRunDate(row.trace.created_at)}</span>
          </button>
        ))}
        {visibleRows.length === 0 ? (
          <div className="run-empty" role="status">
            No trace runs match "{searchQuery}".
          </div>
        ) : null}
      </div>
    </section>
  );
}
