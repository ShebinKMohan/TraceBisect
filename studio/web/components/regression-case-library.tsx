"use client";

import { Archive, CheckCircle2, FileCode2, FlaskConical, Play, Save, TriangleAlert } from "lucide-react";
import { useState } from "react";
import type { RegressionCase, Report } from "@/lib/types";
import { formatShortDate } from "@/lib/format";

type RegressionCaseLibraryProps = {
  cases: RegressionCase[];
  report: Report | null;
  busy: boolean;
  onRunCase: (item: RegressionCase) => void;
  onSaveCase: () => void;
};

function caseStatusLabel(item: RegressionCase): string {
  if (item.last_result.status === "passing") return "passing";
  if (item.last_result.status === "failing") return "failing";
  return "unknown";
}

function severityLabel(item: RegressionCase): string {
  return item.first_divergence?.severity ?? item.last_result.severity ?? "INFO";
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
  const canSave = Boolean(report?.baseline.trace_id && report?.candidate.trace_id);

  async function handleCopy(item: RegressionCase) {
    await copyText(item.pytest.source);
    setCopiedCaseId(item.case_id);
    window.setTimeout(() => setCopiedCaseId(null), 1600);
  }

  return (
    <section className="panel regression-case-panel" data-testid="regression-case-library">
      <div className="section-heading">
        <div>
          <p>Case Library</p>
          <h2>Regression cases</h2>
        </div>
        <button
          className="secondary-action"
          data-testid="save-regression-case"
          disabled={!canSave || busy}
          onClick={onSaveCase}
          type="button"
        >
          <Save size={14} aria-hidden />
          Save case
        </button>
      </div>

      <div className="case-library-summary">
        <article>
          <span>Saved cases</span>
          <strong>{cases.length}</strong>
        </article>
        <article>
          <span>Active failures</span>
          <strong>{cases.filter((item) => item.last_result.status === "failing").length}</strong>
        </article>
        <article>
          <span>Pytest exports</span>
          <strong>{cases.length}</strong>
        </article>
      </div>

      {cases.length === 0 ? (
        <div className="case-empty" role="status">
          <FlaskConical size={18} aria-hidden />
          <div>
            <strong>No saved regression cases yet.</strong>
            <p>Save the current comparison to preserve the failing behavior and generated pytest guardrail.</p>
          </div>
        </div>
      ) : (
        <div className="case-table" role="table" aria-label="Saved regression cases">
          <div className="case-table-head" role="row">
            <span>Case</span>
            <span>Signal</span>
            <span>Last run</span>
            <span>Actions</span>
          </div>
          {cases.map((item) => (
            <article className="case-row" data-testid={`regression-case-${item.case_id}`} key={item.case_id} role="row">
              <div className="case-main">
                <strong>{item.name}</strong>
                <span>{item.description || item.first_divergence?.description || "Saved TraceBisect regression."}</span>
                <div className="case-tags">
                  {item.tags.map((tag) => (
                    <em key={tag}>{tag}</em>
                  ))}
                </div>
              </div>
              <div className="case-signal">
                <span className={`severity-pill severity-${severityLabel(item).toLowerCase()}`}>
                  {severityLabel(item)}
                </span>
                <small>{item.first_divergence?.type ?? "no_divergence"}</small>
              </div>
              <div className="case-result">
                {item.last_result.status === "passing" ? (
                  <CheckCircle2 size={15} aria-hidden />
                ) : item.last_result.status === "failing" ? (
                  <TriangleAlert size={15} aria-hidden />
                ) : (
                  <Archive size={15} aria-hidden />
                )}
                <span>{caseStatusLabel(item)}</span>
                <small>{item.updated_at ? formatShortDate(item.updated_at) : "--"}</small>
              </div>
              <div className="case-actions">
                <button disabled={busy} onClick={() => onRunCase(item)} type="button">
                  <Play size={13} aria-hidden />
                  Rerun
                </button>
                <button disabled={!item.pytest.source} onClick={() => void handleCopy(item)} type="button">
                  <FileCode2 size={13} aria-hidden />
                  {copiedCaseId === item.case_id ? "Copied" : "Pytest"}
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
