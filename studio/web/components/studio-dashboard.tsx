"use client";

import { AlertTriangle, GitCompare, ShieldCheck } from "lucide-react";
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
import type { RegressionCase, Report, RunSummary, StudioSection, TraceEvent, TraceSummary } from "@/lib/types";
import { CompareDrawer } from "@/components/compare-drawer";
import { EventDetailsPanel } from "@/components/event-details-panel";
import { HomePanel } from "@/components/home-panel";
import { IntegrationPanel } from "@/components/integration-panel";
import { IssuesPanel } from "@/components/issues-panel";
import { MobileNav } from "@/components/mobile-nav";
import { RegressionCaseLibrary } from "@/components/regression-case-library";
import { RunList } from "@/components/run-list";
import { SectionOverview, sectionContent } from "@/components/section-overview";
import { SettingsPanel } from "@/components/settings-panel";
import { SessionsPanel } from "@/components/sessions-panel";
import { Sidebar } from "@/components/sidebar";
import { Topbar } from "@/components/topbar";
import { TraceList } from "@/components/trace-list";
import { TraceWorkbench } from "@/components/trace-workbench";
import { UploadComparePanel } from "@/components/upload-compare-panel";
import { WorkflowSteps } from "@/components/workflow-steps";
import { friendlyTraceName } from "@/lib/format";

export function StudioDashboard() {
  const [report, setReport] = useState<Report | null>(null);
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [cases, setCases] = useState<RegressionCase[]>([]);
  const [baselineId, setBaselineId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [activeSide, setActiveSide] = useState<"baseline" | "candidate">("candidate");
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState<StudioSection>("home");
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "passing" | "failing">("all");
  const [severityFilter, setSeverityFilter] = useState<
    "all" | "INFO" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
  >("all");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

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
  const hasSideRail = false;
  const selectedEvent =
    activeEvents.find((event) => event.id === selectedEventId) ??
    activeEvents.find((event) => highlightedIds.has(event.id)) ??
    activeEvents[0] ??
    null;
  const content = sectionContent(activeSection);

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

  function handleSectionChange(section: StudioSection) {
    setSearchQuery("");
    setNotice(null);
    setActiveSection(section);
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
      setBaselineId(demoReport.baseline.id);
      setCandidateId(demoReport.candidate.id);
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
      setActiveSection("runs");
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
        name: `${friendlyTraceName(report.candidate.display_name)} guardrail`,
        description: report.first_divergence?.description ?? "Saved TraceBisect comparison.",
        tags: [report.first_divergence?.type ?? "regression", "pytest-ready"],
        baseline_trace_id: report.baseline.id,
        candidate_trace_id: report.candidate.id,
      });
      setCases((items) => [savedCase, ...items.filter((item) => item.case_id !== savedCase.case_id)]);
      setNotice("Guardrail saved. Its generated pytest test is ready to copy.");
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
      <div className={sidebarCollapsed ? "dashboard-frame dashboard-frame-sidebar-collapsed" : "dashboard-frame"}>
        <Sidebar
          activeSection={activeSection}
          collapsed={sidebarCollapsed}
          onPrimaryAction={() => handleSectionChange("sources")}
          onSectionChange={handleSectionChange}
          onThemeToggle={toggleTheme}
          onToggleCollapsed={() => setSidebarCollapsed((current) => !current)}
          theme={theme}
        />
        <div className="dashboard-main">
          <Topbar
            activeSection={activeSection}
            onHelp={() => handleSectionChange("home")}
            searchValue={searchQuery}
            onSearchChange={setSearchQuery}
          />

          <section className="page-header">
            <div>
              <h1 data-testid="studio-title">{content.title}</h1>
              <p>{content.description}</p>
            </div>
            {activeSection !== "home" && activeSection !== "setup" ? (
              <div className="page-header-actions">
                <div className="header-action" data-testid="comparison-summary">
                  <GitCompare size={15} aria-hidden />
                  <span>
                    {report
                      ? `${friendlyTraceName(report.baseline.display_name)} → ${friendlyTraceName(report.candidate.display_name)}`
                      : "Loading comparison"}
                  </span>
                </div>
                {activeSection === "runs" ? (
                  <button className="header-save-action" disabled={!report || busy} onClick={() => void handleSaveCase()} type="button">
                    <ShieldCheck size={15} aria-hidden />
                    Save as guardrail
                  </button>
                ) : null}
              </div>
            ) : null}
          </section>

          {activeSection === "runs" || activeSection === "sources" || activeSection === "cases" ? (
            <WorkflowSteps activeSection={activeSection} onSectionChange={handleSectionChange} />
          ) : null}

          {error ? (
            <section className="error-band" role="alert">
              <AlertTriangle size={18} aria-hidden />
              <span>{error}</span>
            </section>
          ) : null}

          {notice ? (
            <section className="success-band" role="status">
              <ShieldCheck size={18} aria-hidden />
              <span>{notice}</span>
              <button onClick={() => handleSectionChange("cases")} type="button">View guardrails</button>
            </section>
          ) : null}

          <SectionOverview
            report={report}
            section={activeSection}
          />

          {activeSection === "home" ? (
            <HomePanel cases={cases} onSectionChange={handleSectionChange} report={report} traces={traces} />
          ) : null}

          {activeSection === "runs" ? (
            <div className="comparison-workbench" data-testid="comparison-workbench">
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
              <EventDetailsPanel
                divergence={first}
                event={selectedEvent}
                events={activeEvents}
                highlightedIds={highlightedIds}
                onSelectEvent={handleSelectEvent}
                side={activeSide}
                trace={activeTrace}
              />
              <TraceWorkbench
                events={activeEvents}
                highlightedIds={highlightedIds}
                onSelectEvent={handleSelectEvent}
                selectedEventId={selectedEvent?.id ?? null}
                side={activeSide}
                trace={activeTrace}
              />
            </div>
          ) : null}

          {activeSection === "divergences" ? (
            <div className="issues-page-frame" data-testid="review-workbench">
              <IssuesPanel runs={runs} searchQuery={searchQuery} />
              <CompareDrawer divergence={first} report={report} />
            </div>
          ) : null}

          {activeSection !== "home" && activeSection !== "runs" && activeSection !== "divergences" ? (
            <div
              className={[
                "observability-grid",
                hasSideRail ? "" : "observability-grid-single",
              ]
                .filter(Boolean)
                .join(" ")}
            >
            <div className="primary-column">
              {activeSection === "cases" ? (
                <RegressionCaseLibrary
                  busy={busy}
                  cases={cases}
                  onRunCase={(item) => void handleRunCase(item)}
                  onSaveCase={() => void handleSaveCase()}
                  report={report}
                />
              ) : null}
              {activeSection === "setup" ? (
                <SettingsPanel />
              ) : null}
              {activeSection === "sources" ? (
                <>
                  <div className="trace-source-actions">
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
                  </div>
                  <TraceList traces={traces} searchQuery={searchQuery} />
                </>
              ) : null}
              {activeSection === "sessions" ? (
                <SessionsPanel traces={traces} searchQuery={searchQuery} />
              ) : null}
            </div>

            {hasSideRail ? (
            <aside className="side-column" aria-label="Trace inspector">
              {activeSection === "sources" ? <IntegrationPanel integrations={report?.integrations ?? []} /> : null}
            </aside>
            ) : null}
          </div>
          ) : null}
        </div>
      </div>
      <MobileNav activeSection={activeSection} onSectionChange={handleSectionChange} />
    </main>
  );
}
