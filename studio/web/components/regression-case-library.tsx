"use client";

import { Archive, CheckCircle2, FileCode2, FlaskConical, Play, Save, TriangleAlert } from "lucide-react";
import { useState } from "react";
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

function caseStatusLabel(item: RegressionCase): string {
  if (item.last_result.status === "passing") return "Passed";
  if (item.last_result.status === "failing") return "Needs review";
  return "Not checked";
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
          <p>Guardrail tests</p>
          <h2>Saved guardrails</h2>
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
          <strong>{cases.length}</strong>
        </article>
        <article>
          <span>Active failures</span>
          <strong>{cases.filter((item) => item.last_result.status === "failing").length}</strong>
        </article>
        <article>
          <span>Generated tests</span>
          <strong>{cases.length}</strong>
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
        <div className="case-table" role="table" aria-label="Saved regression cases">
          <div className="case-table-head" role="row">
            <span>Guardrail</span>
            <span>First change</span>
            <span>Last run</span>
            <span>Actions</span>
          </div>
          {cases.map((item) => (
            <article className="case-row" data-testid={`regression-case-${item.case_id}`} key={item.case_id} role="row">
              <div className="case-main">
                <strong>{friendlyTraceName(item.name.replace(/ regression$/u, ""))}</strong>
                <span>
                  {item.first_divergence
                    ? friendlyDivergenceDescription(item.first_divergence.type, item.first_divergence.description)
                    : item.description || "Saved TraceBisect regression."}
                </span>
                <div className="case-tags">
                  {item.tags.map((tag) => (
                    <em key={tag}>{friendlyDivergenceType(tag)}</em>
                  ))}
                </div>
              </div>
              <div className="case-signal">
                <span className={`severity-pill severity-${severityLabel(item).toLowerCase()}`}>
                  {friendlySeverity(severityLabel(item))}
                </span>
                <small>{friendlyDivergenceType(item.first_divergence?.type)}</small>
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
                  {copiedCaseId === item.case_id ? "Copied" : "Copy test"}
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
