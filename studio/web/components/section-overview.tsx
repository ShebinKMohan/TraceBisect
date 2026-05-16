import {
  AlertTriangle,
  Cable,
  CheckCircle2,
  Clock3,
  FileCode2,
  GitCompare,
  ShieldCheck,
} from "lucide-react";
import type { Divergence, RegressionCase, Report, RunSummary, StudioSection, TraceSummary } from "@/lib/types";
import { formatShortDate } from "@/lib/format";

type SectionOverviewProps = {
  section: StudioSection;
  report: Report | null;
  runs: RunSummary[];
  traces: TraceSummary[];
  cases: RegressionCase[];
  first: Divergence | null;
  onSectionChange: (section: StudioSection) => void;
};

const sectionCopy: Record<StudioSection, { eyebrow: string; title: string; description: string }> = {
  runs: {
    eyebrow: "Project / Refund Ops",
    title: "Trace runs",
    description:
      "Compare baseline and candidate agent executions, inspect the event chain, and export the first regression as a test.",
  },
  sources: {
    eyebrow: "Sources",
    title: "Trace ingestion",
    description:
      "Upload native .tbtrace files or OTel/OpenInference JSON exports, then compare them against known-good baselines.",
  },
  divergences: {
    eyebrow: "Analysis",
    title: "Divergence review",
    description:
      "Review the first meaningful regression, inspect expected versus actual payloads, and decide whether to save a guardrail.",
  },
  cases: {
    eyebrow: "Regression testing",
    title: "Case library",
    description:
      "Persist important comparisons as rerunnable regression cases and copy generated pytest checks into your repository.",
  },
  setup: {
    eyebrow: "Setup",
    title: "CI guardrails",
    description:
      "Use the generated pytest export with the TraceBisect Python package to catch agent regressions before deployment.",
  },
};

export function sectionContent(section: StudioSection) {
  return sectionCopy[section];
}

export function SectionOverview({
  section,
  report,
  runs,
  traces,
  cases,
  first,
  onSectionChange,
}: SectionOverviewProps) {
  if (section === "runs") return null;

  if (section === "sources") {
    return (
      <section className="section-grid" data-testid="sources-section">
        <article className="panel section-panel section-panel-wide">
          <div className="section-heading">
            <div>
              <p>Sources</p>
              <h2>Connected trace inputs</h2>
            </div>
            <Cable size={18} aria-hidden />
          </div>
          <div className="source-grid">
            {[
              {
                name: "Native .tbtrace",
                status: "Available",
                copy: "Canonical TraceBisect JSONL fixtures and recorder output.",
              },
              {
                name: "OpenTelemetry / OpenInference",
                status: "Available",
                copy: "OTLP-style JSON exports are imported into the canonical schema.",
              },
              {
                name: "Langfuse",
                status: "Via OpenTelemetry",
                copy: "Use OTel-compatible export paths for the current Studio MVP.",
              },
              {
                name: "LangSmith",
                status: "Planned",
                copy: "Direct API import is planned after the visual MVP path is stable.",
              },
            ].map((source) => (
              <article className="source-card" key={source.name}>
                <CheckCircle2 size={18} aria-hidden />
                <strong>{source.name}</strong>
                <span>{source.status}</span>
                <p>{source.copy}</p>
              </article>
            ))}
          </div>
        </article>
        <article className="panel section-panel">
          <div className="section-heading compact">
            <div>
              <p>Inventory</p>
              <h2>Uploaded traces</h2>
            </div>
          </div>
          <div className="mini-list">
            {traces.map((trace) => (
              <div key={trace.id}>
                <strong>{trace.display_name}</strong>
                <span>{trace.source_convention} · {trace.event_count} events</span>
              </div>
            ))}
          </div>
        </article>
      </section>
    );
  }

  if (section === "divergences") {
    return (
      <section className="section-grid" data-testid="divergences-section">
        <article className="panel section-panel section-panel-wide">
          <div className="section-heading">
            <div>
              <p>First divergence</p>
              <h2>{first?.description ?? "No divergence detected"}</h2>
            </div>
            {first ? <AlertTriangle size={18} aria-hidden /> : <CheckCircle2 size={18} aria-hidden />}
          </div>
          <div className="divergence-review">
            <div>
              <span>Type</span>
              <strong>{first?.type ?? "none"}</strong>
            </div>
            <div>
              <span>Severity</span>
              <strong>{first?.severity ?? "INFO"}</strong>
            </div>
            <div>
              <span>Token delta</span>
              <strong>{first?.impact.tokens_delta ?? 0}</strong>
            </div>
            <div>
              <span>Cost ratio</span>
              <strong>{first ? `${first.impact.cost_delta_ratio.toFixed(2)}x` : "1.00x"}</strong>
            </div>
          </div>
          <button className="secondary-action" onClick={() => onSectionChange("runs")} type="button">
            <GitCompare size={14} aria-hidden />
            Inspect in run workbench
          </button>
        </article>
        <article className="panel section-panel">
          <div className="section-heading compact">
            <div>
              <p>History</p>
              <h2>Recent signals</h2>
            </div>
          </div>
          <div className="mini-list">
            {runs.map((run) => (
              <div key={run.report_id}>
                <strong>{run.first_divergence_type?.replaceAll("_", " ") ?? "No drift"}</strong>
                <span>{run.severity ?? "INFO"} · {run.divergence_count} divergences</span>
              </div>
            ))}
          </div>
        </article>
      </section>
    );
  }

  if (section === "cases") {
    return (
      <section className="section-grid" data-testid="cases-section">
        <article className="panel section-panel">
          <div className="section-heading compact">
            <div>
              <p>Summary</p>
              <h2>Case readiness</h2>
            </div>
            <ShieldCheck size={18} aria-hidden />
          </div>
          <div className="setup-steps">
            <div>
              <CheckCircle2 size={16} aria-hidden />
              <span>{cases.length} saved cases</span>
            </div>
            <div>
              <AlertTriangle size={16} aria-hidden />
              <span>{cases.filter((item) => item.last_result.status === "failing").length} active failures</span>
            </div>
            <div>
              <FileCode2 size={16} aria-hidden />
              <span>{cases.length} pytest exports ready</span>
            </div>
          </div>
        </article>
        <article className="panel section-panel section-panel-wide">
          <div className="section-heading compact">
            <div>
              <p>Latest</p>
              <h2>Saved regression cases</h2>
            </div>
          </div>
          <div className="mini-list">
            {cases.length === 0 ? (
              <div>
                <strong>No saved cases yet</strong>
                <span>Save the current comparison from the case library panel.</span>
              </div>
            ) : (
              cases.map((item) => (
                <div key={item.case_id}>
                  <strong>{item.name}</strong>
                  <span>{item.last_result.status} · {formatShortDate(item.updated_at)}</span>
                </div>
              ))
            )}
          </div>
        </article>
      </section>
    );
  }

  return (
    <section className="section-grid" data-testid="setup-section">
      <article className="panel section-panel section-panel-wide">
        <div className="section-heading">
          <div>
            <p>Operational checklist</p>
            <h2>Ship a CI regression guardrail</h2>
          </div>
          <ShieldCheck size={18} aria-hidden />
        </div>
        <div className="setup-steps">
          <div>
            <CheckCircle2 size={16} aria-hidden />
            <span>Export or record a baseline trace as `.tbtrace`.</span>
          </div>
          <div>
            <CheckCircle2 size={16} aria-hidden />
            <span>Run the same scenario after code, prompt, or model changes.</span>
          </div>
          <div>
            <CheckCircle2 size={16} aria-hidden />
            <span>Copy the generated pytest guardrail into your test suite.</span>
          </div>
          <div>
            <Clock3 size={16} aria-hidden />
            <span>Wire the test into GitHub Actions or your deploy gate.</span>
          </div>
        </div>
      </article>
      <article className="panel section-panel">
        <div className="section-heading compact">
          <div>
            <p>Active export</p>
            <h2>{report?.pytest.filename ?? "No export yet"}</h2>
          </div>
        </div>
        <p className="section-note">
          {report
            ? `${report.divergence_count} divergences are represented in the current generated test.`
            : "Load or compare traces to generate a pytest export."}
        </p>
      </article>
    </section>
  );
}
