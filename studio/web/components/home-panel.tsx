import { ArrowRight, Braces, CheckCircle2, GitCompare, ShieldCheck, UploadCloud } from "lucide-react";
import type { RegressionCase, Report, StudioHealth, StudioSection, TraceSummary } from "@/lib/types";
import { friendlyDivergenceType, friendlyTraceName } from "@/lib/format";

type HomePanelProps = {
  authRequired: boolean;
  canEdit: boolean;
  cases: RegressionCase[];
  report: Report | null;
  runtime: StudioHealth["runtime"] | null;
  traces: TraceSummary[];
  onSectionChange: (section: StudioSection) => void;
};

const glossary = [
  ["Trace", "The step-by-step record of one AI run."],
  ["Baseline", "The known-good run you expect the agent to match."],
  ["Candidate", "The new run you want to check for changes."],
  ["First change", "The earliest meaningful point where the two runs differ."],
  ["Guardrail", "A saved pytest check that helps stop the same bug returning."],
];

export function HomePanel({ authRequired, canEdit, cases, report, runtime, traces, onSectionChange }: HomePanelProps) {
  const first = report?.first_divergence ?? null;
  const durable = runtime?.durable ?? false;
  const workspaceTitle = runtime
    ? durable
      ? "Your durable workspace"
      : "Your local workspace"
    : "Your workspace";
  return (
    <div className="home-page" data-testid="home-page">
      <section className="home-intro">
        <div>
          <h2>Find where an AI run changed.</h2>
          <p>
            TraceBisect compares a run that worked with a new run, explains the first important difference,
            and creates a regression test you can keep.
          </p>
          <div className="home-actions">
            <button className="home-primary-action" onClick={() => onSectionChange("sources")} type="button">
              {canEdit ? "Compare your traces" : "Browse workspace traces"}
              <ArrowRight size={16} aria-hidden />
            </button>
            <button className="home-secondary-action" onClick={() => onSectionChange("runs")} type="button">
              Review the demo
            </button>
          </div>
        </div>
        <div className="home-demo-summary" aria-label="Loaded demo summary">
          <span className="home-demo-icon"><GitCompare size={19} aria-hidden /></span>
          <div>
            <small>{report ? "Comparison ready" : canEdit ? "Preparing demo" : "Read-only workspace"}</small>
            <strong>{first ? friendlyDivergenceType(first.type) : report ? "No behavior change" : "No saved comparison yet"}</strong>
            <p>
              {report
                ? `${friendlyTraceName(report.baseline.display_name)} compared with ${friendlyTraceName(report.candidate.display_name)}.`
                : canEdit
                  ? "Loading the built-in refund-agent example."
                  : "An editor can add the first comparison; viewer access never creates demo data."}
            </p>
          </div>
          <button onClick={() => onSectionChange("runs")} type="button">Open result</button>
        </div>
      </section>

      <section className="home-workflow" aria-labelledby="home-workflow-title">
        <div className="home-section-heading">
          <h2 id="home-workflow-title">One workflow, three steps</h2>
          <p>You do not need an observability background to start.</p>
        </div>
        <ol>
          <li>
            <span>1</span>
            <div>
              <UploadCloud size={18} aria-hidden />
              <h3>Choose two traces</h3>
              <p>Select the run that worked, then the new run you want to check.</p>
              <button onClick={() => onSectionChange("sources")} type="button">{canEdit ? "Choose traces" : "Browse traces"} <ArrowRight size={14} aria-hidden /></button>
            </div>
          </li>
          <li>
            <span>2</span>
            <div>
              <GitCompare size={18} aria-hidden />
              <h3>Review the first change</h3>
              <p>Start where the behavior first diverged instead of reading every event.</p>
              <button onClick={() => onSectionChange("runs")} type="button">Review a comparison <ArrowRight size={14} aria-hidden /></button>
            </div>
          </li>
          <li>
            <span>3</span>
            <div>
              <ShieldCheck size={18} aria-hidden />
              <h3>Save the fix</h3>
              <p>Turn the important behavior into a pytest guardrail for your CI pipeline.</p>
              <button onClick={() => onSectionChange("cases")} type="button">View guardrails <ArrowRight size={14} aria-hidden /></button>
            </div>
          </li>
        </ol>
      </section>

      <section className="home-bottom-grid">
        <article className="home-glossary">
          <div className="home-section-heading">
            <h2>Plain-English glossary</h2>
            <p>The five terms used throughout Studio.</p>
          </div>
          <dl>
            {glossary.map(([term, definition]) => (
              <div key={term}>
                <dt>{term}</dt>
                <dd>{definition}</dd>
              </div>
            ))}
          </dl>
        </article>
        <article className="home-workspace-status">
          <div className="home-section-heading">
            <h2>{workspaceTitle}</h2>
            <p>Everything here is honest about what is running now.</p>
          </div>
          <ul>
            <li><CheckCircle2 size={16} aria-hidden /> {traces.length} demo or uploaded traces ready</li>
            <li><CheckCircle2 size={16} aria-hidden /> {cases.length} saved guardrails {runtime ? (durable ? "in this workspace" : "in this session") : "ready"}</li>
            <li><CheckCircle2 size={16} aria-hidden /> Files are parsed by the local Studio API</li>
          </ul>
          <div className="local-boundary-note">
            <Braces size={17} aria-hidden />
            <p>
              <strong>
                {runtime
                  ? authRequired
                    ? "Protected workspace:"
                    : durable
                      ? "Durable local mode:"
                      : "Local MVP:"
                  : "Checking storage mode:"}
              </strong>{" "}
              {runtime
                ? authRequired
                  ? "your session key grants access only to this workspace, and its data survives API restarts."
                  : durable
                    ? "traces, comparisons, and guardrails survive API restarts."
                    : "no account is required and data resets when the API restarts."
                : "the API will report whether this workspace survives a restart."}
            </p>
          </div>
          <button onClick={() => onSectionChange("setup")} type="button">Open the setup guide</button>
        </article>
      </section>
    </div>
  );
}
