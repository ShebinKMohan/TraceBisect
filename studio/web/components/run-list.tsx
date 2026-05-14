import { CheckCircle2, CircleAlert, Clock3, Database, GitCompare, Search } from "lucide-react";
import type { Divergence, Report, RunSummary, RunStatus } from "@/lib/types";

type RunFilterStatus = "all" | RunStatus;
type RunFilterSeverity = "all" | "INFO" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

type RunListProps = {
  report: Report | null;
  runs: RunSummary[];
  first: Divergence | null;
  searchQuery: string;
  statusFilter: RunFilterStatus;
  severityFilter: RunFilterSeverity;
  selectedReportId: string | null;
  onStatusFilterChange: (status: RunFilterStatus) => void;
  onSeverityFilterChange: (severity: RunFilterSeverity) => void;
  onSearchReset: () => void;
  onSelectRun: (reportId: string) => void;
};

const statusFilters: { label: string; value: RunFilterStatus }[] = [
  { label: "All", value: "all" },
  { label: "Failing", value: "failing" },
  { label: "Passing", value: "passing" },
];

const severityFilters: RunFilterSeverity[] = ["all", "INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"];

function formatRunDate(value?: string): string {
  if (!value) return "Pending";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatDivergenceType(value: string | null): string {
  if (!value) return "No drift";
  return value.replaceAll("_", " ");
}

function sourceLabel(value: string): string {
  if (value === "openinference") return "OpenInference";
  if (value === "genai") return "GenAI";
  if (value === "native") return "Native";
  if (value === "mixed") return "Mixed";
  return value || "Unknown";
}

export function RunList({
  report,
  runs,
  first,
  searchQuery,
  statusFilter,
  severityFilter,
  selectedReportId,
  onStatusFilterChange,
  onSeverityFilterChange,
  onSearchReset,
  onSelectRun,
}: RunListProps) {
  const activeReportId = selectedReportId ?? report?.report_id ?? null;
  const totalDivergences = runs.reduce((total, run) => total + run.divergence_count, 0);
  const failingCount = runs.filter((run) => run.status === "failing").length;
  const emptyCopy =
    searchQuery.trim() || statusFilter !== "all" || severityFilter !== "all"
      ? "No comparison runs match the current filters."
      : "Run history will appear after the first comparison.";

  return (
    <section className="panel run-list-panel" aria-label="Trace runs" data-testid="runs-table">
      <div className="stat-strip" aria-label="Trace summary">
        <article>
          <span>Run history</span>
          <strong>{runs.length}</strong>
          <small>{failingCount} failing comparisons</small>
        </article>
        <article>
          <span>Divergences</span>
          <strong>{report?.divergence_count ?? totalDivergences}</strong>
          <small>{first?.type ?? "no active drift"}</small>
        </article>
        <article>
          <span>Severity</span>
          <strong>{first?.severity ?? "INFO"}</strong>
          <small>current selected run</small>
        </article>
        <article>
          <span>Cost ratio</span>
          <strong>{first ? `${first.impact.cost_delta_ratio.toFixed(2)}x` : "1.00x"}</strong>
          <small>current selected run</small>
        </article>
      </div>

      <div className="run-toolbar" aria-label="Run filters">
        {statusFilters.map((filter) => (
          <button
            className={statusFilter === filter.value ? "filter-chip filter-chip-active" : "filter-chip"}
            data-testid={`run-filter-${filter.value}`}
            key={filter.value}
            onClick={() => onStatusFilterChange(filter.value)}
            type="button"
          >
            <GitCompare size={14} aria-hidden />
            {filter.label}
          </button>
        ))}
        <label className="filter-select">
          <Clock3 size={14} aria-hidden />
          <span className="sr-only">Severity filter</span>
          <select
            aria-label="Severity filter"
            data-testid="run-filter-severity"
            onChange={(event) => onSeverityFilterChange(event.target.value as RunFilterSeverity)}
            value={severityFilter}
          >
            {severityFilters.map((severity) => (
              <option key={severity} value={severity}>
                {severity === "all" ? "All severities" : severity}
              </option>
            ))}
          </select>
        </label>
        <span className="filter-chip filter-chip-static">
          <Database size={14} aria-hidden />
          Report history
        </span>
        <div className="table-search" aria-hidden="true">
          <Search size={14} aria-hidden />
          <span>{searchQuery.trim() ? `Searching "${searchQuery.trim()}"` : "Search runs from the top bar"}</span>
        </div>
      </div>

      <div className="run-table">
        <div className="run-table-head" aria-hidden="true">
          <span>Comparison</span>
          <span>Signal</span>
          <span>Status</span>
          <span>Source</span>
          <span>Events</span>
          <span>Created</span>
        </div>
        {runs.map((run) => {
          const active = activeReportId === run.report_id;
          return (
            <button
              aria-label={`${run.baseline.display_name} compared with ${run.candidate.display_name}. ${run.status}. ${run.divergence_count} divergences.`}
              aria-pressed={active}
              className={active ? "run-row run-row-active" : "run-row"}
              data-testid={`run-row-${run.report_id}`}
              key={run.report_id}
              onClick={() => onSelectRun(run.report_id)}
              type="button"
            >
              <span className="run-content">
                <span className={`run-status run-status-${run.status}`} aria-hidden>
                  {run.status === "passing" ? <CheckCircle2 size={15} /> : <CircleAlert size={15} />}
                </span>
                <strong>
                  {run.baseline.display_name} → {run.candidate.display_name}
                </strong>
              </span>
              <span>
                <strong className="run-signal">{formatDivergenceType(run.first_divergence_type)}</strong>
                <small>{run.severity ?? "INFO"}</small>
              </span>
              <span>
                <em className={`tier-pill tier-${run.status}`}>{run.status}</em>
              </span>
              <span>{sourceLabel(run.source_convention)}</span>
              <span>{run.event_count}</span>
              <span>{formatRunDate(run.created_at)}</span>
            </button>
          );
        })}
        {runs.length === 0 ? (
          <div className="run-empty" role="status">
            <span>{emptyCopy}</span>
            {(searchQuery.trim() || statusFilter !== "all" || severityFilter !== "all") && (
              <button
                className="inline-action"
                data-testid="clear-run-filters"
                onClick={() => {
                  onStatusFilterChange("all");
                  onSeverityFilterChange("all");
                  onSearchReset();
                }}
                type="button"
              >
                Clear filters
              </button>
            )}
          </div>
        ) : null}
      </div>
    </section>
  );
}
