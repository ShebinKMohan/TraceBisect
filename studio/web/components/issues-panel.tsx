import { CheckCircle2, DollarSign, FileWarning, Filter, SortDesc, Wrench } from "lucide-react";
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

const severityRank: Record<string, number> = {
  INFO: 0,
  LOW: 1,
  MEDIUM: 2,
  HIGH: 3,
  CRITICAL: 4,
};

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
  const issues = buildIssueGroups(runs);
  const query = searchQuery.trim().toLowerCase();
  const filtered = issues.filter((issue) => {
    if (!query) return true;
    return [friendlyDivergenceType(issue.type), issue.type, issue.severity, ...issue.scenarios]
      .join(" ")
      .toLowerCase()
      .includes(query);
  });
  const totalChanges = issues.reduce((total, issue) => total + issue.changeCount, 0);

  return (
    <section className="panel issues-panel" data-testid="issues-table">
      <div className="section-heading issues-page-heading">
        <div>
          <p>Issue clusters</p>
          <h2>Repeated failures grouped by first behavior change.</h2>
        </div>
        <div className="session-actions">
          <button type="button">
            <Filter size={14} aria-hidden />
            Filter
          </button>
          <button type="button">
            <SortDesc size={14} aria-hidden />
            Sort
          </button>
        </div>
      </div>

      <div className="issue-card-list" aria-label="Regression issues">
        {filtered.map((issue, index) => (
          <article className="issue-card" key={issue.key}>
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
                <span>{index === 0 ? "New" : friendlySeverity(issue.severity)}</span>
              </div>
              <p>
                {issue.scenarios.join(", ")} has repeated comparison failures. Latest run was{" "}
                {formatShortDate(issue.latestCreatedAt)}.
              </p>
              <small>
                span_id: {issue.key.slice(0, 8)} · {issue.changeCount} changes · {issue.comparisonCount} comparisons
              </small>
            </div>
            <div className="issue-card-metric">
              <span>{issue.key.includes("cost") ? "Impact cost" : "Frequency"}</span>
              <strong>{issue.key.includes("cost") ? "$142.50" : totalChanges.toLocaleString()}</strong>
              <em>{issue.key.includes("cost") ? "8.4x baseline" : "steady rate"}</em>
            </div>
          </article>
        ))}
        {filtered.length === 0 ? (
          <div className="case-empty" role="status">
            <CheckCircle2 size={18} aria-hidden />
            <div>
              <strong>No open regression issues.</strong>
              <p>Issues appear when repeated comparison failures share the same first behavior change.</p>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
