import { CheckCircle2, DollarSign, FileWarning, GitCompare, SortDesc, Wrench } from "lucide-react";
import { useMemo, useState } from "react";
import type { RunSummary } from "@/lib/types";
import {
  formatShortDate,
  friendlyDivergenceType,
  friendlySeverity,
  scenarioNameFromRun,
} from "@/lib/format";

type IssuesPanelProps = {
  runs: RunSummary[];
  searchQuery: string;
};

type IssueGroup = {
  key: string;
  type: string;
  severity: string;
  comparisonCount: number;
  changeCount: number;
  latestCreatedAt: string;
  scenarios: string[];
};

type IssueSeverityFilter = "all" | "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "INFO";
type IssueSortKey = "severity" | "frequency" | "recent";

const severityRank: Record<string, number> = {
  INFO: 0,
  LOW: 1,
  MEDIUM: 2,
  HIGH: 3,
  CRITICAL: 4,
};

const severityFilters: { label: string; value: IssueSeverityFilter }[] = [
  { label: "All issues", value: "all" },
  { label: "Critical", value: "CRITICAL" },
  { label: "High", value: "HIGH" },
  { label: "Medium", value: "MEDIUM" },
];

function strongerSeverity(left: string | null, right: string | null): string {
  const leftValue = left ?? "INFO";
  const rightValue = right ?? "INFO";
  return (severityRank[rightValue] ?? 0) > (severityRank[leftValue] ?? 0) ? rightValue : leftValue;
}

function buildIssueGroups(runs: RunSummary[]): IssueGroup[] {
  const groups = new Map<string, IssueGroup>();
  for (const run of runs.filter((item) => item.status === "failing")) {
    const type = run.first_divergence_type ?? "unknown_regression";
    const existing = groups.get(type);
    const scenario = scenarioNameFromRun(run);
    if (!existing) {
      groups.set(type, {
        key: type,
        type,
        severity: run.severity ?? "INFO",
        comparisonCount: 1,
        changeCount: run.divergence_count,
        latestCreatedAt: run.created_at,
        scenarios: [scenario],
      });
      continue;
    }
    existing.comparisonCount += 1;
    existing.changeCount += run.divergence_count;
    existing.severity = strongerSeverity(existing.severity, run.severity);
    if (Date.parse(run.created_at) > Date.parse(existing.latestCreatedAt)) {
      existing.latestCreatedAt = run.created_at;
    }
    if (!existing.scenarios.includes(scenario)) {
      existing.scenarios.push(scenario);
    }
  }
  return Array.from(groups.values()).sort(
    (left, right) =>
      (severityRank[right.severity] ?? 0) - (severityRank[left.severity] ?? 0) ||
      Date.parse(right.latestCreatedAt) - Date.parse(left.latestCreatedAt),
  );
}

export function IssuesPanel({ runs, searchQuery }: IssuesPanelProps) {
  const [severityFilter, setSeverityFilter] = useState<IssueSeverityFilter>("all");
  const [sortKey, setSortKey] = useState<IssueSortKey>("severity");
  const [selectedIssueKey, setSelectedIssueKey] = useState<string | null>(null);
  const issues = useMemo(() => buildIssueGroups(runs), [runs]);
  const query = searchQuery.trim().toLowerCase();
  const filtered = useMemo(() => {
    const matched = issues.filter((issue) => {
      const severityMatched = severityFilter === "all" || issue.severity === severityFilter;
      if (!severityMatched) return false;
      if (!query) return true;
      return [friendlyDivergenceType(issue.type), issue.type, issue.severity, ...issue.scenarios]
        .join(" ")
        .toLowerCase()
        .includes(query);
    });
    return [...matched].sort((left, right) => {
      if (sortKey === "frequency") return right.comparisonCount - left.comparisonCount;
      if (sortKey === "recent") return Date.parse(right.latestCreatedAt) - Date.parse(left.latestCreatedAt);
      return (severityRank[right.severity] ?? 0) - (severityRank[left.severity] ?? 0);
    });
  }, [issues, query, severityFilter, sortKey]);
  const selectedIssue = filtered.find((issue) => issue.key === selectedIssueKey) ?? filtered[0] ?? null;
  const totalChanges = issues.reduce((total, issue) => total + issue.changeCount, 0);
  const failingComparisons = runs.filter((run) => run.status === "failing").length;
  const highestSeverity = issues[0]?.severity ?? "INFO";

  return (
    <section className="panel issues-panel" data-testid="issues-table">
      <div className="section-heading issues-page-heading">
        <div>
          <p>Issue clusters</p>
          <h2>Repeated failures grouped by first behavior change.</h2>
        </div>
        <div className="session-actions">
          <button className="filter-chip filter-chip-static" disabled type="button">
            <GitCompare size={14} aria-hidden />
            {failingComparisons} failing comparisons
          </button>
        </div>
      </div>

      <div className="issue-summary-strip" aria-label="Issue summary">
        <article>
          <span>Open issues</span>
          <strong>{issues.length}</strong>
        </article>
        <article>
          <span>Failing comparisons</span>
          <strong>{failingComparisons}</strong>
        </article>
        <article>
          <span>Behavior changes</span>
          <strong>{totalChanges}</strong>
        </article>
        <article>
          <span>Highest risk</span>
          <strong>{friendlySeverity(highestSeverity)}</strong>
        </article>
      </div>

      <div className="run-toolbar issue-toolbar" aria-label="Issue filters">
        {severityFilters.map((filter) => (
          <button
            className={severityFilter === filter.value ? "filter-chip filter-chip-active" : "filter-chip"}
            key={filter.value}
            onClick={() => setSeverityFilter(filter.value)}
            type="button"
          >
            {filter.label}
          </button>
        ))}
        <label className="filter-select">
          <SortDesc size={14} aria-hidden />
          <span className="sr-only">Sort issues</span>
          <select
            aria-label="Sort issues"
            data-testid="issue-sort"
            onChange={(event) => setSortKey(event.currentTarget.value as IssueSortKey)}
            value={sortKey}
          >
            <option value="severity">Severity</option>
            <option value="frequency">Frequency</option>
            <option value="recent">Most recent</option>
          </select>
        </label>
      </div>

      <div className="issue-card-list" aria-label="Regression issues">
        {filtered.map((issue) => (
          <button
            aria-pressed={selectedIssue?.key === issue.key}
            className={selectedIssue?.key === issue.key ? "issue-card issue-card-active" : "issue-card"}
            key={issue.key}
            onClick={() => setSelectedIssueKey(issue.key)}
            type="button"
          >
            <div className="issue-card-icon" aria-hidden>
              {issue.type.includes("cost") ? (
                <DollarSign size={18} />
              ) : issue.type.includes("tool") ? (
                <Wrench size={18} />
              ) : (
                <FileWarning size={18} />
              )}
            </div>
            <div className="issue-card-body">
              <div>
                <h3>{friendlyDivergenceType(issue.type)}</h3>
                <span>{friendlySeverity(issue.severity)}</span>
              </div>
              <p>
                Repeated first-change cluster across {issue.comparisonCount} comparison
                {issue.comparisonCount === 1 ? "" : "s"}. Latest occurrence was{" "}
                {formatShortDate(issue.latestCreatedAt)}.
              </p>
              <small>
                Scenarios: {issue.scenarios.join(", ")} · cluster_id: {issue.key.slice(0, 8)}
              </small>
            </div>
            <div className="issue-card-metric">
              <span>{issue.key.includes("cost") ? "Impact cost" : "Frequency"}</span>
              <strong>{issue.key.includes("cost") ? "$142.50" : issue.comparisonCount.toLocaleString()}</strong>
              <em>{issue.changeCount.toLocaleString()} behavior changes</em>
            </div>
          </button>
        ))}
        {filtered.length === 0 ? (
          <div className="case-empty" role="status">
            <CheckCircle2 size={18} aria-hidden />
            <div>
              <strong>No open regression issues.</strong>
              <p>Issues appear when repeated comparison failures share the same first behavior change.</p>
              <button
                className="inline-action"
                onClick={() => setSeverityFilter("all")}
                type="button"
              >
                Clear issue filters
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
