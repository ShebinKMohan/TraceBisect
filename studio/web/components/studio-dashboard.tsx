"use client";

import { AlertTriangle, GitCompare } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  compareTraces,
  createRegressionCase,
  fetchDemoReport,
  fetchRegressionCases,
  fetchRunReport,
  fetchRuns,
  fetchTraces,
  runRegressionCase,
  uploadTrace,
} from "@/lib/api";
import type { RegressionCase, Report, RunSummary, TraceEvent, TraceSummary } from "@/lib/types";
import { CompareDrawer } from "@/components/compare-drawer";
import { EventDetailsPanel } from "@/components/event-details-panel";
import { IntegrationPanel } from "@/components/integration-panel";
import { RegressionCaseLibrary } from "@/components/regression-case-library";
import { RunList } from "@/components/run-list";
import { Sidebar } from "@/components/sidebar";
import { Topbar } from "@/components/topbar";
import { TraceWorkbench } from "@/components/trace-workbench";
import { UploadComparePanel } from "@/components/upload-compare-panel";

export function StudioDashboard() {
  const [report, setReport] = useState<Report | null>(null);
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [cases, setCases] = useState<RegressionCase[]>([]);
  const [baselineId, setBaselineId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [activeSide, setActiveSide] = useState<"baseline" | "candidate">("candidate");
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "passing" | "failing">("all");
  const [severityFilter, setSeverityFilter] = useState<
    "all" | "INFO" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
  >("all");
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
  const activeTrace = activeSide === "baseline" ? report?.baseline : report?.candidate;
  const activeEvents = activeSide === "baseline" ? (report?.events.baseline ?? []) : (report?.events.candidate ?? []);
  const selectedEvent =
    activeEvents.find((event) => event.id === selectedEventId) ??
    activeEvents.find((event) => highlightedIds.has(event.id)) ??
    activeEvents[0] ??
    null;

  useEffect(() => {
    const preferred =
      activeSide === "baseline" ? first?.baseline_event_id : first?.candidate_event_id;
    const nextEvent =
      activeEvents.find((event) => event.id === preferred) ?? activeEvents[0] ?? null;
    setSelectedEventId(nextEvent?.id ?? null);
  }, [activeEvents, activeSide, first?.baseline_event_id, first?.candidate_event_id]);

  useEffect(() => {
    if (loading) return;
    void refreshRuns();
  }, [loading, searchQuery, severityFilter, statusFilter]);

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
      const demoReport = await fetchDemoReport();
      const [traceList, caseList, runList] = await Promise.all([
        fetchTraces(),
        fetchRegressionCases(),
        fetchRuns(),
      ]);
      setReport(demoReport);
      setSelectedReportId(demoReport.report_id);
      setTraces(traceList);
      setCases(caseList);
      setRuns(runList);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load the demo report.");
    } finally {
      setLoading(false);
    }
  }

  async function refreshTraces() {
    setTraces(await fetchTraces());
  }

  async function refreshCases() {
    setCases(await fetchRegressionCases());
  }

  async function refreshRuns() {
    setRuns(
      await fetchRuns({
        q: searchQuery,
        status: statusFilter === "all" ? undefined : statusFilter,
        severity: severityFilter === "all" ? undefined : severityFilter,
      }),
    );
  }

  async function handleUpload(file: File, role: "baseline" | "candidate") {
    setBusy(true);
    setError(null);
    try {
      const uploaded = await uploadTrace(file);
      await refreshTraces();
      await refreshRuns();
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
      const nextReport = await compareTraces(baselineId, candidateId);
      setReport(nextReport);
      setSelectedReportId(nextReport.report_id);
      await refreshRuns();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Comparison failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveCase() {
    if (!report) return;
    setBusy(true);
    setError(null);
    try {
      const savedCase = await createRegressionCase({
        name: `${report.candidate.display_name} regression`,
        description: report.first_divergence?.description ?? "Saved TraceBisect comparison.",
        tags: [report.first_divergence?.type ?? "regression", "pytest-ready"],
        baseline_trace_id: report.baseline.id,
        candidate_trace_id: report.candidate.id,
      });
      setCases((items) => [savedCase, ...items.filter((item) => item.case_id !== savedCase.case_id)]);
      await refreshRuns();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save regression case.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRunCase(item: RegressionCase) {
    setBusy(true);
    setError(null);
    try {
      const result = await runRegressionCase(item.case_id, item.candidate_trace_id);
      setReport(result.report);
      setSelectedReportId(result.report.report_id);
      await refreshCases();
      await refreshRuns();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to rerun regression case.");
    } finally {
      setBusy(false);
    }
  }

  function handleSelectEvent(event: TraceEvent) {
    setSelectedEventId(event.id);
  }

  async function handleSelectRun(reportId: string) {
    setBusy(true);
    setError(null);
    try {
      const selectedReport = await fetchRunReport(reportId);
      setReport(selectedReport);
      setSelectedReportId(selectedReport.report_id);
      setActiveSide("candidate");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load run history item.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="studio-shell">
      <div className="dashboard-frame">
        <Sidebar />
        <div className="dashboard-main">
          <Topbar
            searchValue={searchQuery}
            theme={theme}
            onSearchChange={setSearchQuery}
            onThemeToggle={toggleTheme}
          />

          <section className="page-header">
            <div>
              <p className="eyebrow">Project / Refund Ops</p>
              <h1 data-testid="studio-title">Trace runs</h1>
              <p>
                Compare baseline and candidate agent executions, inspect the event chain, and export the first regression as a test.
              </p>
            </div>
            <div className="header-action" data-testid="comparison-summary">
              <GitCompare size={15} aria-hidden />
              <span>{report ? `${report.baseline.display_name} → ${report.candidate.display_name}` : "Loading report"}</span>
            </div>
          </section>

          <nav className="project-tabs" aria-label="Project sections">
            <a href="#" aria-current="page">Runs</a>
            <a href="#">Threads</a>
            <a href="#">Divergences</a>
            <a href="#">Cases</a>
            <a href="#">Setup</a>
          </nav>

          {error ? (
            <section className="error-band" role="alert">
              <AlertTriangle size={18} aria-hidden />
              <span>{error}</span>
            </section>
          ) : null}

          <div className="observability-grid">
            <div className="primary-column">
              <RunList
                first={first}
                onSearchReset={() => setSearchQuery("")}
                onSelectRun={(reportId) => void handleSelectRun(reportId)}
                onSeverityFilterChange={setSeverityFilter}
                onStatusFilterChange={setStatusFilter}
                report={report}
                runs={runs}
                searchQuery={searchQuery}
                selectedReportId={selectedReportId}
                severityFilter={severityFilter}
                statusFilter={statusFilter}
              />
              <RegressionCaseLibrary
                busy={busy}
                cases={cases}
                onRunCase={(item) => void handleRunCase(item)}
                onSaveCase={() => void handleSaveCase()}
                report={report}
              />
              <CompareDrawer divergence={first} report={report} />
            </div>

            <aside className="side-column" aria-label="Trace inspector">
              <TraceWorkbench
                events={activeEvents}
                highlightedIds={highlightedIds}
                onSelectEvent={handleSelectEvent}
                selectedEventId={selectedEvent?.id ?? null}
                side={activeSide}
                trace={activeTrace}
              />
              <EventDetailsPanel
                divergence={first}
                event={selectedEvent}
                events={activeEvents}
                highlightedIds={highlightedIds}
                onSelectEvent={handleSelectEvent}
                side={activeSide}
                trace={activeTrace}
              />
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
