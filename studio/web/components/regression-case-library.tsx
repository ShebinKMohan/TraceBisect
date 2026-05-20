"use client";

import {
  Archive,
  Copy,
  FileCode2,
  FlaskConical,
  Play,
  Save,
  SortDesc,
  TriangleAlert,
} from "lucide-react";
import { useMemo, useState } from "react";
import type { RegressionCase, Report } from "@/lib/types";
import {
  formatShortDate,
  friendlyDivergenceDescription,
  friendlyDivergenceType,
  friendlySeverity,
  friendlyTraceName,
} from "@/lib/format";

type RegressionCaseLibraryProps = {
  cases: RegressionCase[];
  report: Report | null;
  busy: boolean;
  onRunCase: (item: RegressionCase) => void;
  onSaveCase: () => void;
};

type CaseFilter = "all" | "failing" | "passing" | "unknown";
type CaseSort = "recent" | "severity" | "name";

type GuardrailDataset = {
  key: string;
  latest: RegressionCase;
  revisions: RegressionCase[];
};

const caseFilters: { label: string; value: CaseFilter }[] = [
  { label: "All guardrails", value: "all" },
  { label: "Needs review", value: "failing" },
  { label: "Passing", value: "passing" },
];

const severityRank: Record<string, number> = {
  INFO: 0,
  LOW: 1,
  MEDIUM: 2,
  HIGH: 3,
  CRITICAL: 4,
};

function caseStatusLabel(item: RegressionCase): string {
  if (item.last_result.status === "passing") return "Passed";
  if (item.last_result.status === "failing") return "Needs review";
  return "Not checked";
}

function severityLabel(item: RegressionCase): string {
  return item.first_divergence?.severity ?? item.last_result.severity ?? "INFO";
}

function datasetKey(item: RegressionCase): string {
  return [
    item.name,
    item.baseline_trace_id,
    item.candidate_trace_id,
    item.first_divergence?.type ?? "no-first-change",
  ].join("::");
}

function buildGuardrailDatasets(cases: RegressionCase[]): GuardrailDataset[] {
  const grouped = new Map<string, RegressionCase[]>();
  for (const item of cases) {
    const key = datasetKey(item);
    grouped.set(key, [...(grouped.get(key) ?? []), item]);
  }

  return Array.from(grouped.entries())
    .map(([key, revisions]) => {
      const sorted = [...revisions].sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at));
      return {
        key,
        latest: sorted[0],
        revisions: sorted,
      };
    })
    .sort((left, right) => Date.parse(right.latest.updated_at) - Date.parse(left.latest.updated_at));
}

function statusClass(item: RegressionCase): string {
  if (item.last_result.status === "passing") return "status-passing";
  if (item.last_result.status === "failing") return "status-failing";
  return "status-unknown";
}

function formattedCommand(item: RegressionCase): string {
  if (item.scenario_cmd.length === 0) return "Manual rerun";
  return item.scenario_cmd.join(" ");
}

function formatJsonPreview(value: unknown): string {
  if (value === null || value === undefined) return "--";
  return JSON.stringify(value, null, 2);
}

async function copyText(value: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "true");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }
}

export function RegressionCaseLibrary({
  cases,
  report,
  busy,
  onRunCase,
  onSaveCase,
}: RegressionCaseLibraryProps) {
  const [copiedCaseId, setCopiedCaseId] = useState<string | null>(null);
  const [caseFilter, setCaseFilter] = useState<CaseFilter>("all");
  const [caseSort, setCaseSort] = useState<CaseSort>("recent");
  const [selectedDatasetKey, setSelectedDatasetKey] = useState<string | null>(null);
  const canSave = Boolean(report?.baseline.trace_id && report?.candidate.trace_id);
  const datasets = useMemo(() => buildGuardrailDatasets(cases), [cases]);
  const filteredDatasets = useMemo(() => {
    const matched = datasets.filter((dataset) => caseFilter === "all" || dataset.latest.last_result.status === caseFilter);
    return [...matched].sort((left, right) => {
      if (caseSort === "name") return left.latest.name.localeCompare(right.latest.name);
      if (caseSort === "severity") {
        return (severityRank[severityLabel(right.latest)] ?? 0) - (severityRank[severityLabel(left.latest)] ?? 0);
      }
      return Date.parse(right.latest.updated_at) - Date.parse(left.latest.updated_at);
    });
  }, [caseFilter, caseSort, datasets]);
  const selectedDataset =
    filteredDatasets.find((item) => item.key === selectedDatasetKey) ?? filteredDatasets[0] ?? datasets[0] ?? null;
  const selectedCase = selectedDataset?.latest ?? null;
  const activeFailures = datasets.filter((item) => item.latest.last_result.status === "failing").length;
  const passingCases = datasets.filter((item) => item.latest.last_result.status === "passing").length;

  async function handleCopy(item: RegressionCase) {
    await copyText(item.pytest.source);
    setCopiedCaseId(item.case_id);
    window.setTimeout(() => setCopiedCaseId(null), 1600);
  }

  return (
    <section className="panel regression-case-panel" data-testid="regression-case-library">
      <div className="section-heading">
        <div>
          <p>Guardrail datasets</p>
          <h2>Reusable regression suites.</h2>
        </div>
        <button
          className="secondary-action"
          data-testid="save-regression-case"
          disabled={!canSave || busy}
          onClick={onSaveCase}
          type="button"
        >
          <Save size={14} aria-hidden />
          Save guardrail
        </button>
      </div>

      <div className="case-library-summary">
        <article>
          <span>Saved guardrails</span>
          <strong>{datasets.length}</strong>
        </article>
        <article>
          <span>Active failures</span>
          <strong>{activeFailures}</strong>
        </article>
        <article>
          <span>Generated tests</span>
          <strong>{datasets.length}</strong>
        </article>
        <article>
          <span>Passing suites</span>
          <strong>{passingCases}</strong>
        </article>
      </div>

      {cases.length === 0 ? (
        <div className="case-empty" role="status">
          <FlaskConical size={18} aria-hidden />
          <div>
            <strong>No saved guardrails yet.</strong>
            <p>Save the current comparison to preserve the failing behavior and generated pytest guardrail.</p>
          </div>
        </div>
      ) : (
        <div className="guardrail-workbench">
          <aside className="guardrail-list-pane" aria-label="Saved guardrail datasets">
            <div className="case-toolbar" aria-label="Guardrail filters">
              {caseFilters.map((filter) => (
                <button
                  className={caseFilter === filter.value ? "filter-chip filter-chip-active" : "filter-chip"}
                  key={filter.value}
                  onClick={() => setCaseFilter(filter.value)}
                  type="button"
                >
                  {filter.label}
                </button>
              ))}
              <label className="filter-select">
                <SortDesc size={14} aria-hidden />
                <span className="sr-only">Sort guardrails</span>
                <select
                  aria-label="Sort guardrails"
                  data-testid="guardrail-sort"
                  onChange={(event) => setCaseSort(event.currentTarget.value as CaseSort)}
                  value={caseSort}
                >
                  <option value="recent">Recently updated</option>
                  <option value="severity">Highest risk</option>
                  <option value="name">Name</option>
                </select>
              </label>
            </div>

            <div className="guardrail-list">
              {filteredDatasets.map((dataset) => {
                const item = dataset.latest;
                return (
                <button
                  aria-pressed={selectedDataset?.key === dataset.key}
                  className={
                    selectedDataset?.key === dataset.key
                      ? "guardrail-list-item guardrail-list-item-active"
                      : "guardrail-list-item"
                  }
                  data-testid={`regression-case-${item.case_id}`}
                  key={dataset.key}
                  onClick={() => setSelectedDatasetKey(dataset.key)}
                  type="button"
                >
                  <span className={`case-state-dot ${statusClass(item)}`} aria-hidden />
                  <div>
                    <strong>{friendlyTraceName(item.name.replace(/ regression$/u, ""))}</strong>
                    <small>
                      {item.divergence_count} checks · {dataset.revisions.length} saved run
                      {dataset.revisions.length === 1 ? "" : "s"} ·{" "}
                      {item.candidate.model ?? "model unknown"}
                    </small>
                    <div className="case-tags">
                      {item.tags.slice(0, 2).map((tag) => (
                        <em key={tag}>{friendlyDivergenceType(tag)}</em>
                      ))}
                    </div>
                  </div>
                  <span className={`case-status-badge ${statusClass(item)}`}>{caseStatusLabel(item)}</span>
                </button>
              );
              })}
              {filteredDatasets.length === 0 ? (
                <div className="case-empty" role="status">
                  <Archive size={18} aria-hidden />
                  <div>
                    <strong>No guardrails match this filter.</strong>
                    <p>Switch back to all guardrails to see saved regression suites.</p>
                  </div>
                </div>
              ) : null}
            </div>
          </aside>

          <article className="guardrail-detail-pane" data-testid="guardrail-detail-panel">
            {selectedCase ? (
              <>
                <div className="guardrail-detail-header">
                  <div>
                    <p>Selected guardrail</p>
                    <h3>{friendlyTraceName(selectedCase.name.replace(/ regression$/u, ""))}</h3>
                    <span>
                      {selectedCase.description ||
                        "A saved regression dataset that can be rerun before deploys."}
                    </span>
                  </div>
                  <div className="guardrail-actions">
                    <button disabled={busy} onClick={() => onRunCase(selectedCase)} type="button">
                      <Play size={13} aria-hidden />
                      Rerun
                    </button>
                    <button disabled={!selectedCase.pytest.source} onClick={() => void handleCopy(selectedCase)} type="button">
                      <FileCode2 size={13} aria-hidden />
                      {copiedCaseId === selectedCase.case_id ? "Copied" : "Copy test"}
                    </button>
                  </div>
                </div>

                <dl className="guardrail-meta-grid">
                  <div>
                    <dt>Last run</dt>
                    <dd>{selectedCase.last_result.checked_at ? formatShortDate(selectedCase.last_result.checked_at) : "--"}</dd>
                  </div>
                  <div>
                    <dt>Status</dt>
                    <dd className={statusClass(selectedCase)}>{caseStatusLabel(selectedCase)}</dd>
                  </div>
                  <div>
                    <dt>Risk</dt>
                    <dd>{friendlySeverity(severityLabel(selectedCase))}</dd>
                  </div>
                  <div>
                    <dt>Dataset ID</dt>
                    <dd>{selectedCase.case_id}</dd>
                  </div>
                  <div>
                    <dt>Saved runs</dt>
                    <dd>{selectedDataset?.revisions.length ?? 1}</dd>
                  </div>
                  <div>
                    <dt>Baseline</dt>
                    <dd>{friendlyTraceName(selectedCase.baseline.display_name)}</dd>
                  </div>
                  <div>
                    <dt>Candidate</dt>
                    <dd>{friendlyTraceName(selectedCase.candidate.display_name)}</dd>
                  </div>
                  <div>
                    <dt>Prompt version</dt>
                    <dd>{selectedCase.candidate.prompt_version ?? "--"}</dd>
                  </div>
                  <div>
                    <dt>Code SHA</dt>
                    <dd>{selectedCase.candidate.code_sha ?? "--"}</dd>
                  </div>
                </dl>

                <div className="guardrail-run-command">
                  <span>Rerun command</span>
                  <code>{formattedCommand(selectedCase)}</code>
                </div>

                <div className="guardrail-code-card">
                  <header>
                    <span>
                      <FileCode2 size={14} aria-hidden />
                      Pytest integration · {selectedCase.pytest.filename}
                    </span>
                    <button disabled={!selectedCase.pytest.source} onClick={() => void handleCopy(selectedCase)} type="button">
                      <Copy size={13} aria-hidden />
                      {copiedCaseId === selectedCase.case_id ? "Copied" : "Copy"}
                    </button>
                  </header>
                  <pre>{selectedCase.pytest.source}</pre>
                </div>

                {selectedCase.first_divergence ? (
                  <div className="guardrail-failure-card">
                    <header>
                      <span>
                        <TriangleAlert size={14} aria-hidden />
                        Recent failure
                      </span>
                      <em>{friendlyDivergenceType(selectedCase.first_divergence.type)}</em>
                    </header>
                    <p>
                      {friendlyDivergenceDescription(
                        selectedCase.first_divergence.type,
                        selectedCase.first_divergence.description,
                      )}
                    </p>
                    <div>
                      <section>
                        <span>Expected</span>
                        <pre>{formatJsonPreview(selectedCase.first_divergence.expected)}</pre>
                      </section>
                      <section>
                        <span>Actual</span>
                        <pre>{formatJsonPreview(selectedCase.first_divergence.actual)}</pre>
                      </section>
                    </div>
                  </div>
                ) : null}
              </>
            ) : null}
          </article>
        </div>
      )}
    </section>
  );
}
