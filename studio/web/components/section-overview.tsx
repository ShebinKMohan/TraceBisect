import { CheckCircle2, Clock3, ShieldCheck } from "lucide-react";
import type { Report, StudioSection } from "@/lib/types";

type SectionOverviewProps = {
  section: StudioSection;
  report: Report | null;
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
}: SectionOverviewProps) {
  if (section !== "setup") return null;

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
