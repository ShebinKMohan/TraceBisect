import { CheckCircle2, CircleAlert, Clock3, Database, GitCompare } from "lucide-react";
import type { Report, TraceSummary } from "@/lib/types";

type RunListProps = {
  report: Report | null;
  traces: TraceSummary[];
  activeSide: "baseline" | "candidate";
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

export function RunList({ report, traces, activeSide, onSelectSide }: RunListProps) {
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

  return (
    <section className="panel run-list-panel" aria-label="Trace runs" data-testid="runs-table">
      <div className="workbench-heading">
        <div>
          <p>Runs</p>
          <h2>Refund search comparisons</h2>
        </div>
        <span>{traces.length} stored</span>
      </div>

      <div className="run-toolbar" aria-label="Run filters">
        <button type="button" className="filter-chip filter-chip-active">
          <GitCompare size={14} aria-hidden />
          Compared
        </button>
        <button type="button" className="filter-chip">
          <Clock3 size={14} aria-hidden />
          Last 7 days
        </button>
        <button type="button" className="filter-chip">
          <Database size={14} aria-hidden />
          OTel + native
        </button>
      </div>

      <div className="run-table" role="list">
        {rows.map((row) => (
          <button
            className={activeSide === row.side ? "run-row run-row-active" : "run-row"}
            data-testid={`run-row-${row.side}`}
            key={row.side}
            onClick={() => onSelectSide(row.side)}
            role="listitem"
            type="button"
          >
            <span className={`run-status run-status-${row.health}`} aria-hidden>
              {row.health === "stable" ? <CheckCircle2 size={16} /> : <CircleAlert size={16} />}
            </span>
            <span className="run-content">
              <strong>{row.trace.display_name}</strong>
              <small>{row.trace.root_event}</small>
              <em>
                {row.status} · {row.trace.event_count} events · {formatRunDate(row.trace.created_at)}
              </em>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}
