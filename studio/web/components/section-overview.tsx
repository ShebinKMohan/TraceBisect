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
import {
  formatShortDate,
  friendlyDivergenceDescription,
  friendlyDivergenceType,
  friendlySeverity,
  friendlySourceConvention,
  friendlyStatus,
  friendlyTraceName,
} from "@/lib/format";

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
    eyebrow: "Workspace / Refund Ops",
    title: "Comparison history",
    description:
      "Pick a known-good trace, compare a new run, inspect the first behavior change, and save a guardrail test.",
  },
  sources: {
    eyebrow: "Trace Sources",
    title: "Add trace sources",
    description:
      "Upload native TraceBisect files or OpenTelemetry exports, then compare them against known-good baselines.",
  },
  divergences: {
    eyebrow: "Regression Review",
    title: "Review behavior changes",
    description:
      "Start from the first meaningful change, inspect baseline versus candidate values, and decide whether to save a guardrail.",
  },
  cases: {
    eyebrow: "Regression testing",
    title: "Regression guardrails",
    description:
      "Save important comparisons as rerunnable guardrails and copy generated pytest checks into your repository.",
  },
  setup: {
    eyebrow: "CI Setup",
    title: "Ship CI protection",
    description:
      "Commit baseline traces and generated tests so agent regressions fail before deployment.",
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
                copy: "Canonical TraceBisect files from fixtures, the CLI recorder, or Studio exports.",
              },
              {
                name: "OpenTelemetry / OpenInference",
                status: "Available",
                copy: "OTLP-style JSON exports are imported into the canonical schema.",
              },
              {
                name: "Langfuse",
                status: "Via OpenTelemetry",
                copy: "Import OTel-compatible exports while direct API import is being built.",
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
                <strong>{friendlyTraceName(trace.display_name)}</strong>
                <span>{friendlySourceConvention(trace.source_convention)} · {trace.event_count} events</span>
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
              <p>First behavior change</p>
              <h2>
                {first
                  ? friendlyDivergenceDescription(first.type, first.description)
                  : "No behavior change detected"}
              </h2>
            </div>
            {first ? <AlertTriangle size={18} aria-hidden /> : <CheckCircle2 size={18} aria-hidden />}
          </div>
          <div className="divergence-review">
            <div>
              <span>Change</span>
              <strong>{friendlyDivergenceType(first?.type)}</strong>
            </div>
            <div>
              <span>Risk</span>
              <strong>{friendlySeverity(first?.severity)}</strong>
            </div>
            <div>
              <span>Token delta</span>
              <strong>{first?.impact.tokens_delta ?? 0}</strong>
            </div>
            <div>
              <span>Cost change</span>
              <strong>{first ? `${first.impact.cost_delta_ratio.toFixed(2)}x` : "1.00x"}</strong>
            </div>
          </div>
          <button className="secondary-action" onClick={() => onSectionChange("runs")} type="button">
            <GitCompare size={14} aria-hidden />
            Inspect comparison
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
                <strong>{friendlyDivergenceType(run.first_divergence_type)}</strong>
                <span>{friendlySeverity(run.severity)} · {run.divergence_count} changes</span>
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
              <h2>Guardrail readiness</h2>
            </div>
            <ShieldCheck size={18} aria-hidden />
          </div>
          <div className="setup-steps">
            <div>
              <CheckCircle2 size={16} aria-hidden />
              <span>{cases.length} saved guardrails</span>
            </div>
            <div>
              <AlertTriangle size={16} aria-hidden />
              <span>{cases.filter((item) => item.last_result.status === "failing").length} active failures</span>
            </div>
            <div>
              <FileCode2 size={16} aria-hidden />
              <span>{cases.length} generated tests ready</span>
            </div>
          </div>
        </article>
        <article className="panel section-panel section-panel-wide">
          <div className="section-heading compact">
            <div>
              <p>Latest</p>
              <h2>Saved guardrails</h2>
            </div>
          </div>
          <div className="mini-list">
            {cases.length === 0 ? (
              <div>
                <strong>No saved cases yet</strong>
                <span>Save the current comparison from the guardrail panel.</span>
              </div>
            ) : (
              cases.map((item) => (
                <div key={item.case_id}>
                  <strong>{item.name}</strong>
                  <span>{friendlyStatus(item.last_result.status)} · {formatShortDate(item.updated_at)}</span>
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
            <h2>{report ? "Generated pytest guardrail" : "No export yet"}</h2>
          </div>
        </div>
        <p className="section-note">
          {report
            ? `${report.divergence_count} behavior changes are represented in the current generated test.`
            : "Load or compare traces to generate a pytest export."}
        </p>
      </article>
    </section>
  );
}
