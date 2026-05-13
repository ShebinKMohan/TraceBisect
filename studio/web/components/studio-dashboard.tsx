"use client";

import {
  AlertTriangle,
  CircleDollarSign,
  FileCode2,
  GitCompare,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { compareTraces, fetchDemoReport, fetchTraces, uploadTrace } from "@/lib/api";
import type { Report, TraceSummary } from "@/lib/types";
import { FirstDivergenceCard } from "@/components/first-divergence-card";
import { IntegrationPanel } from "@/components/integration-panel";
import { MetricCard } from "@/components/metric-card";
import { PytestPanel } from "@/components/pytest-panel";
import { Sidebar } from "@/components/sidebar";
import { SignalChart } from "@/components/signal-chart";
import { Topbar } from "@/components/topbar";
import { TraceTimeline } from "@/components/trace-timeline";
import { UploadComparePanel } from "@/components/upload-compare-panel";

export function StudioDashboard() {
  const [report, setReport] = useState<Report | null>(null);
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [baselineId, setBaselineId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const stored = window.localStorage.getItem("tracebisect-theme");
    const nextTheme = stored === "dark" ? "dark" : "light";
    setTheme(nextTheme);
    document.documentElement.dataset.theme = nextTheme;
    void loadDemo();
  }, []);

  const first = report?.first_divergence ?? null;
  const highlightedIds = useMemo(() => {
    return new Set([first?.baseline_event_id, first?.candidate_event_id].filter((id): id is string => Boolean(id)));
  }, [first]);

  function toggleTheme() {
    const nextTheme = theme === "light" ? "dark" : "light";
    setTheme(nextTheme);
    document.documentElement.dataset.theme = nextTheme;
    window.localStorage.setItem("tracebisect-theme", nextTheme);
  }

  async function loadDemo() {
    setLoading(true);
    setError(null);
    try {
      const [demoReport, traceList] = await Promise.all([fetchDemoReport(), fetchTraces()]);
      setReport(demoReport);
      setTraces(traceList);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load the demo report.");
    } finally {
      setLoading(false);
    }
  }

  async function refreshTraces() {
    setTraces(await fetchTraces());
  }

  async function handleUpload(file: File, role: "baseline" | "candidate") {
    setBusy(true);
    setError(null);
    try {
      const uploaded = await uploadTrace(file);
      await refreshTraces();
      if (role === "baseline") setBaselineId(uploaded.id);
      if (role === "candidate") setCandidateId(uploaded.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCompare() {
    if (!baselineId || !candidateId) return;
    setBusy(true);
    setError(null);
    try {
      setReport(await compareTraces(baselineId, candidateId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Comparison failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="studio-shell">
      <div className="dashboard-frame">
        <Sidebar />
        <div className="dashboard-main">
          <Topbar theme={theme} onThemeToggle={toggleTheme} />

          <section className="hero-band">
            <div>
              <p className="eyebrow">Trace regression intelligence</p>
              <h1 data-testid="studio-title">TraceBisect Studio</h1>
              <p>
                Compare AI-agent runs, isolate the first behavioral break, and move the finding into CI.
              </p>
            </div>
            <div className="hero-status" data-testid="comparison-summary">
              <Sparkles size={18} aria-hidden />
              <span>{report ? `${report.baseline.display_name} → ${report.candidate.display_name}` : "Loading report"}</span>
            </div>
          </section>

          {error ? (
            <section className="error-band" role="alert">
              <AlertTriangle size={18} aria-hidden />
              <span>{error}</span>
            </section>
          ) : null}

          <section className="metric-grid" aria-label="Comparison metrics">
            <MetricCard
              featured
              icon={<GitCompare size={20} aria-hidden />}
              label="Divergences"
              value={loading ? "..." : (report?.divergence_count ?? 0)}
              detail="V1 detector output"
              testId="metric-divergences"
            />
            <MetricCard
              icon={<ShieldCheck size={20} aria-hidden />}
              label="First severity"
              value={first?.severity ?? "INFO"}
              detail={first?.type ?? "no drift"}
            />
            <MetricCard
              icon={<CircleDollarSign size={20} aria-hidden />}
              label="Cost ratio"
              value={first ? `${first.impact.cost_delta_ratio.toFixed(2)}x` : "1.00x"}
              detail="fixed 20% severity model"
            />
            <MetricCard
              icon={<FileCode2 size={20} aria-hidden />}
              label="Pytest export"
              value={report?.pytest.filename ?? "pending"}
              detail="copy-ready guardrail"
            />
          </section>

          <div className="content-grid">
            <div className="primary-column">
              <FirstDivergenceCard divergence={first} />
              <SignalChart divergences={report?.divergences ?? []} />
              <div className="trace-grid">
                <TraceTimeline
                  title="Baseline"
                  trace={report?.baseline}
                  events={report?.events.baseline ?? []}
                  highlightedIds={highlightedIds}
                />
                <TraceTimeline
                  title="Candidate"
                  trace={report?.candidate}
                  events={report?.events.candidate ?? []}
                  highlightedIds={highlightedIds}
                />
              </div>
              <PytestPanel filename={report?.pytest.filename} source={report?.pytest.source} />
            </div>

            <aside className="side-column">
              <UploadComparePanel
                traces={traces}
                baselineId={baselineId}
                candidateId={candidateId}
                busy={busy}
                onBaselineChange={setBaselineId}
                onCandidateChange={setCandidateId}
                onUpload={(file, role) => void handleUpload(file, role)}
                onCompare={() => void handleCompare()}
              />
              <IntegrationPanel integrations={report?.integrations ?? []} />
            </aside>
          </div>
        </div>
      </div>
    </main>
  );
}
