import { CheckCircle2, CircleAlert, GitCompare, Search } from "lucide-react";
import type { Divergence, Report, RunSummary, RunStatus } from "@/lib/types";
import {
  friendlyDivergenceType,
  friendlySeverity,
  friendlySourceConvention,
  friendlyStatus,
  friendlyTraceName,
  scenarioNameFromRun,
} from "@/lib/format";

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
  { label: "All results", value: "all" },
  { label: "Regressions", value: "failing" },
  { label: "Clean", value: "passing" },
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

type DisplayRun = RunSummary & {
  checkCount: number;
};

function groupRuns(runs: RunSummary[]): DisplayRun[] {
  const grouped = new Map<string, DisplayRun>();
  for (const run of runs) {
    const key = [
      scenarioNameFromRun(run),
      run.baseline.id,
      run.candidate.id,
      run.first_divergence_type ?? "none",
      run.status,
      run.severity ?? "INFO",
    ].join("::");
    const existing = grouped.get(key);
    if (existing) {
      existing.checkCount += 1;
    } else {
      grouped.set(key, { ...run, checkCount: 1 });
    }
  }
  return Array.from(grouped.values());
}

function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

export function RunList({
  report,
  runs,
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
  const displayRuns = groupRuns(runs);
  const emptyCopy =
    searchQuery.trim() || statusFilter !== "all" || severityFilter !== "all"
      ? "No comparison runs match the current filters."
      : "Comparison history will appear after the first check.";

  return (
    <section className="panel run-list-panel" aria-label="Comparison history" data-testid="runs-table">
      <div className="comparison-list-header">
        <div>
          <p>Comparison history</p>
          <strong>{pluralize(displayRuns.length, "check")}</strong>
        </div>
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
          <span className="sr-only">Severity filter</span>
          <select
            aria-label="Severity filter"
            data-testid="run-filter-severity"
            onChange={(event) => onSeverityFilterChange(event.target.value as RunFilterSeverity)}
            value={severityFilter}
          >
            {severityFilters.map((severity) => (
              <option key={severity} value={severity}>
                {severity === "all" ? "All severities" : friendlySeverity(severity)}
              </option>
            ))}
          </select>
        </label>
        <div className="table-search" aria-hidden="true">
          <Search size={14} aria-hidden />
          <span>
            {searchQuery.trim() ? `Searching "${searchQuery.trim()}"` : "Search comparisons from the top bar"}
          </span>
        </div>
      </div>

      <div className="run-table">
        <div className="run-table-head" aria-hidden="true">
          <span>Comparison</span>
          <span>Result</span>
          <span>First change</span>
          <span>Impact</span>
          <span>Steps</span>
          <span>Last checked</span>
        </div>
        {displayRuns.map((run) => {
          const active = activeReportId === run.report_id;
          const scenarioName = scenarioNameFromRun(run);
          const baselineName = friendlyTraceName(run.baseline.display_name);
          const candidateName = friendlyTraceName(run.candidate.display_name);
          const statusLabel = friendlyStatus(run.status);
          const divergenceLabel = friendlyDivergenceType(run.first_divergence_type);
          return (
            <button
              aria-label={`${scenarioName}. ${statusLabel}. ${pluralize(run.divergence_count, "behavior change")}.`}
              aria-pressed={active}
              className={active ? "run-row run-row-active" : "run-row"}
              data-testid={`run-row-${run.report_id}`}
              key={run.report_id}
              onClick={() => onSelectRun(run.report_id)}
              type="button"
            >
              <span className="run-meta-line">
                <code>{run.report_id.slice(0, 10)}</code>
                <small>{formatRunDate(run.created_at)}</small>
              </span>
              <span className="run-content">
                <span className={`run-status run-status-${run.status}`} aria-hidden>
                  {run.status === "passing" ? <CheckCircle2 size={15} /> : <CircleAlert size={15} />}
                </span>
                <span className="run-title-group">
                  <strong>{scenarioName}</strong>
                  <small>
                    {baselineName} vs {candidateName}
                    {run.checkCount > 1 ? ` · ${pluralize(run.checkCount, "check")}` : ""}
                  </small>
                </span>
              </span>
              <span>
                <em className={`tier-pill tier-${run.status}`}>{statusLabel}</em>
              </span>
              <span>
                <strong className="run-signal">{divergenceLabel}</strong>
                <small>{friendlySeverity(run.severity)}</small>
              </span>
              <span>{pluralize(run.divergence_count, "change")}</span>
              <span>{run.event_count}</span>
              <span>{friendlySourceConvention(run.source_convention)}</span>
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
