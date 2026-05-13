import {
  AlertTriangle,
  ArrowRight,
  Braces,
  CheckCircle2,
  CircleDollarSign,
  Code2,
  GitCompare,
  GitPullRequestArrow,
  Network,
  Upload,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

type TraceSummary = {
  id: string;
  trace_id: string;
  display_name: string;
  source_convention: string;
  created_at: string;
  event_count: number;
  root_event: string;
};

type TraceEvent = {
  id: string;
  parent_id: string | null;
  sequence_index: number;
  type: string;
  semantic_name: string;
  duration_ms: number | null;
  payload: Record<string, JsonValue>;
};

type Divergence = {
  type: string;
  severity: string;
  baseline_event_id: string | null;
  candidate_event_id: string | null;
  description: string;
  expected: JsonValue;
  actual: JsonValue;
  impact: {
    tokens_delta: number;
    cost_delta_ratio: number;
    final_output_changed: boolean;
    errors_introduced: Record<string, JsonValue>[];
    affected_event_count: number;
  };
  source_metadata: Record<string, JsonValue>;
};

type Report = {
  report_id: string;
  created_at: string;
  baseline: TraceSummary;
  candidate: TraceSummary;
  events: {
    baseline: TraceEvent[];
    candidate: TraceEvent[];
  };
  divergence_count: number;
  first_divergence: Divergence | null;
  divergences: Divergence[];
  pytest: {
    filename: string;
    source: string;
  };
  integrations: {
    name: string;
    status: string;
    description: string;
  }[];
};

export function App() {
  const [report, setReport] = useState<Report | null>(null);
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [baselineId, setBaselineId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void loadDemo();
  }, []);

  const first = report?.first_divergence ?? null;
  const highlightedIds = useMemo(() => {
    return new Set([first?.baseline_event_id, first?.candidate_event_id].filter(Boolean));
  }, [first]);

  async function loadDemo() {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/api/demo-report`);
      if (!response.ok) throw new Error(await response.text());
      setReport((await response.json()) as Report);
      await refreshTraces();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load demo report");
    } finally {
      setLoading(false);
    }
  }

  async function refreshTraces() {
    const response = await fetch(`${API_BASE}/api/traces`);
    if (!response.ok) return;
    const payload = (await response.json()) as { traces: TraceSummary[] };
    setTraces(payload.traces);
  }

  async function uploadTrace(file: File, role: "baseline" | "candidate") {
    setBusy(true);
    setError(null);
    const body = new FormData();
    body.append("file", file);
    try {
      const response = await fetch(`${API_BASE}/api/traces/upload`, {
        method: "POST",
        body,
      });
      if (!response.ok) throw new Error(await response.text());
      const payload = (await response.json()) as { trace: TraceSummary };
      await refreshTraces();
      if (role === "baseline") setBaselineId(payload.trace.id);
      if (role === "candidate") setCandidateId(payload.trace.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function compareUploaded() {
    if (!baselineId || !candidateId) return;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/api/compare`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          baseline_trace_id: baselineId,
          candidate_trace_id: candidateId,
        }),
      });
      if (!response.ok) throw new Error(await response.text());
      setReport((await response.json()) as Report);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Compare failed");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <div className="loading">Loading TraceBisect Studio...</div>;
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">TraceBisect Studio</p>
          <h1>Agent regression reports</h1>
        </div>
        <div className="status-pill">
          <CheckCircle2 size={16} />
          Engine online
        </div>
      </header>

      {error && (
        <section className="error-band">
          <AlertTriangle size={18} />
          <span>{error}</span>
        </section>
      )}

      <section className="metric-grid">
        <Metric icon={<GitCompare size={20} />} label="Divergences" value={report?.divergence_count ?? 0} />
        <Metric icon={<AlertTriangle size={20} />} label="First severity" value={first?.severity ?? "NONE"} />
        <Metric
          icon={<CircleDollarSign size={20} />}
          label="Cost ratio"
          value={first ? `${first.impact.cost_delta_ratio.toFixed(2)}x` : "1.00x"}
        />
        <Metric
          icon={<GitPullRequestArrow size={20} />}
          label="Pytest export"
          value={report?.pytest.filename ?? "not ready"}
        />
      </section>

      <section className="workspace">
        <div className="main-column">
          <section className="panel first-panel">
            <div className="panel-header">
              <div>
                <p className="panel-kicker">First divergence</p>
                <h2>{first ? first.description : "No behavior drift detected"}</h2>
              </div>
              <span className={`severity severity-${(first?.severity ?? "info").toLowerCase()}`}>
                {first?.severity ?? "INFO"}
              </span>
            </div>
            {first && (
              <>
                <div className="diff-pair">
                  <Payload title="Expected" value={first.expected} tone="expected" />
                  <ArrowRight className="diff-arrow" size={22} />
                  <Payload title="Actual" value={first.actual} tone="actual" />
                </div>
                <div className="impact-row">
                  <span>Final output changed: {first.impact.final_output_changed ? "yes" : "no"}</span>
                  <span>Tokens delta: {first.impact.tokens_delta}</span>
                  <span>Affected events: {first.impact.affected_event_count}</span>
                </div>
              </>
            )}
          </section>

          <section className="trace-grid">
            <TraceColumn
              title={report?.baseline.display_name ?? "Baseline"}
              trace={report?.baseline}
              events={report?.events.baseline ?? []}
              highlightedIds={highlightedIds}
            />
            <TraceColumn
              title={report?.candidate.display_name ?? "Candidate"}
              trace={report?.candidate}
              events={report?.events.candidate ?? []}
              highlightedIds={highlightedIds}
            />
          </section>

          <section className="panel code-panel">
            <div className="panel-header">
              <div>
                <p className="panel-kicker">CI guardrail</p>
                <h2>Generated regression test</h2>
              </div>
              <Code2 size={20} />
            </div>
            <pre>{report?.pytest.source}</pre>
          </section>
        </div>

        <aside className="side-column">
          <section className="panel upload-panel">
            <div className="panel-header compact">
              <h2>Compare runs</h2>
              <Upload size={18} />
            </div>
            <UploadSlot
              label="Baseline trace"
              onFile={(file) => void uploadTrace(file, "baseline")}
            />
            <UploadSlot
              label="Candidate trace"
              onFile={(file) => void uploadTrace(file, "candidate")}
            />
            <select value={baselineId} onChange={(event) => setBaselineId(event.target.value)}>
              <option value="">Baseline</option>
              {traces.map((trace) => (
                <option key={trace.id} value={trace.id}>
                  {trace.display_name}
                </option>
              ))}
            </select>
            <select value={candidateId} onChange={(event) => setCandidateId(event.target.value)}>
              <option value="">Candidate</option>
              {traces.map((trace) => (
                <option key={trace.id} value={trace.id}>
                  {trace.display_name}
                </option>
              ))}
            </select>
            <button disabled={!baselineId || !candidateId || busy} onClick={() => void compareUploaded()}>
              <GitCompare size={16} />
              Compare
            </button>
          </section>

          <section className="panel integrations-panel">
            <div className="panel-header compact">
              <h2>Trace sources</h2>
              <Network size={18} />
            </div>
            {report?.integrations.map((item) => (
              <div className="integration" key={item.name}>
                <strong>{item.name}</strong>
                <span>{item.status}</span>
                <p>{item.description}</p>
              </div>
            ))}
          </section>
        </aside>
      </section>
    </main>
  );
}

function Metric({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string | number;
}) {
  return (
    <section className="metric">
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </section>
  );
}

function Payload({
  title,
  value,
  tone,
}: {
  title: string;
  value: JsonValue;
  tone: "expected" | "actual";
}) {
  return (
    <div className={`payload payload-${tone}`}>
      <span>{title}</span>
      <code>{formatJson(value)}</code>
    </div>
  );
}

function TraceColumn({
  title,
  trace,
  events,
  highlightedIds,
}: {
  title: string;
  trace?: TraceSummary;
  events: TraceEvent[];
  highlightedIds: Set<string | null | undefined>;
}) {
  return (
    <section className="panel trace-panel">
      <div className="trace-title">
        <div>
          <p className="panel-kicker">{trace?.source_convention ?? "trace"}</p>
          <h2>{title}</h2>
        </div>
        <span>{trace?.event_count ?? 0} events</span>
      </div>
      <div className="event-list">
        {events.map((event) => (
          <article
            className={`event-row ${highlightedIds.has(event.id) ? "event-active" : ""}`}
            key={event.id}
          >
            <span className="event-index">{event.sequence_index}</span>
            <div>
              <strong>{event.type}</strong>
              <p>{event.semantic_name}</p>
            </div>
            <small>{event.duration_ms === null ? "root" : `${event.duration_ms}ms`}</small>
          </article>
        ))}
      </div>
    </section>
  );
}

function UploadSlot({ label, onFile }: { label: string; onFile: (file: File) => void }) {
  return (
    <label className="upload-slot">
      <span>{label}</span>
      <input
        type="file"
        accept=".tbtrace,.json,application/json"
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          if (file) onFile(file);
        }}
      />
    </label>
  );
}

function formatJson(value: JsonValue): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}
