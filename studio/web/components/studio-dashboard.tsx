"use client";

import { AlertTriangle, GitCompare, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  compareTraces,
  createRegressionCase,
  fetchDemoReport,
  fetchRegressionCases,
  fetchRunReport,
  fetchRuns,
  fetchStudioSession,
  fetchStudioHealth,
  fetchTraces,
  forgetStudioApiKey,
  hasStoredStudioApiKey,
  isUnauthorizedStudioError,
  loginStudioAccount,
  logoutStudioWorkspace,
  runRegressionCase,
  unlockStudioWorkspace,
  uploadTrace,
} from "@/lib/api";
import type { IdentityLoginResult, RegressionCase, Report, RunSummary, StudioHealth, StudioSection, TraceEvent, TraceSummary, WorkspaceRole } from "@/lib/types";
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
import { WorkspaceConnecting, WorkspaceUnlock } from "@/components/workspace-unlock";
import { friendlyTraceName } from "@/lib/format";
import {
  searchableStudioSections,
  studioSectionFromUrl,
  studioSectionUrl,
} from "@/lib/navigation";

export function StudioDashboard() {
  const [report, setReport] = useState<Report | null>(null);
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [cases, setCases] = useState<RegressionCase[]>([]);
  const [health, setHealth] = useState<StudioHealth | null>(null);
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
  const [locked, setLocked] = useState(false);
  const [unlockError, setUnlockError] = useState<string | null>(null);
  const [workspaceRole, setWorkspaceRole] = useState<WorkspaceRole | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const stored = window.localStorage.getItem("tracebisect-theme");
    const nextTheme = stored === "dark" ? "dark" : "light";
    setTheme(nextTheme);
    document.documentElement.dataset.theme = nextTheme;
    setActiveSection(studioSectionFromUrl(window.location.href));
    void initializeStudio();
  }, []);

  useEffect(() => {
    function restoreSectionFromHistory() {
      setSearchQuery("");
      setNotice(null);
      setActiveSection(studioSectionFromUrl(window.location.href));
    }

    window.addEventListener("popstate", restoreSectionFromHistory);
    return () => window.removeEventListener("popstate", restoreSectionFromHistory);
  }, []);

  useEffect(() => {
    document.title = `${sectionContent(activeSection).title} · TraceBisect Studio`;
  }, [activeSection]);

  useEffect(() => {
    function focusSectionSearch(event: KeyboardEvent) {
      if (
        event.key.toLowerCase() !== "k" ||
        (!event.metaKey && !event.ctrlKey) ||
        !searchableStudioSections.has(activeSection)
      ) {
        return;
      }
      event.preventDefault();
      searchInputRef.current?.focus();
      searchInputRef.current?.select();
    }

    window.addEventListener("keydown", focusSectionSearch);
    return () => window.removeEventListener("keydown", focusSectionSearch);
  }, [activeSection]);

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
  const canEdit = workspaceRole !== "viewer";

  useEffect(() => {
    const preferred =
      activeSide === "baseline" ? first?.baseline_event_id : first?.candidate_event_id;
    const nextEvent =
      activeEvents.find((event) => event.id === preferred) ?? activeEvents[0] ?? null;
    setSelectedEventId(nextEvent?.id ?? null);
  }, [activeEvents, activeSide, first?.baseline_event_id, first?.candidate_event_id]);

  useEffect(() => {
    if (loading) return;
    if (locked) return;
    void refreshRuns().catch((err: unknown) => {
      presentApiError(err, "Failed to refresh comparison history.");
    });
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
    const nextUrl = studioSectionUrl(section, window.location.href);
    const currentUrl = `${window.location.pathname}${window.location.search}`;
    if (nextUrl !== currentUrl) {
      window.history.pushState(null, "", nextUrl);
    }
  }

  async function initializeStudio() {
    setLoading(true);
    setError(null);
    try {
      const studioHealth = await fetchStudioHealth();
      setHealth(studioHealth);
      let role: WorkspaceRole = "admin";
      if (studioHealth.auth.required) {
        const managedBrowserSession = studioHealth.auth.browser_sessions;
        if (managedBrowserSession) forgetStudioApiKey();
        if (!managedBrowserSession && !hasStoredStudioApiKey()) {
          setLocked(true);
          return;
        }
        try {
          const session = await fetchStudioSession();
          setHealth({ ...studioHealth, runtime: session.runtime });
          role = session.role;
        } catch (err) {
          if (isUnauthorizedStudioError(err)) {
            forgetStudioApiKey();
            setLocked(true);
            setUnlockError(
              managedBrowserSession
                ? null
                : "Your saved workspace key is no longer valid. Enter a current key.",
            );
            return;
          }
          throw err;
        }
      }
      setWorkspaceRole(role);
      await loadWorkspaceData(role);
    } catch (err) {
      presentApiError(err, "Failed to load the Studio workspace.");
    } finally {
      setLoading(false);
    }
  }

  async function loadWorkspaceData(role: WorkspaceRole) {
    if (role === "viewer") {
      const [traceList, caseList, runList] = await Promise.all([
        fetchTraces(),
        fetchRegressionCases(),
        fetchRuns(),
      ]);
      const latestReport = runList[0]
        ? await fetchRunReport(runList[0].report_id)
        : null;
      setReport(latestReport);
      setSelectedReportId(latestReport?.report_id ?? null);
      setTraces(traceList);
      setCases(caseList);
      setRuns(runList);
      setBaselineId(latestReport?.baseline.id ?? "");
      setCandidateId(latestReport?.candidate.id ?? "");
      return;
    }
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
  }

  async function handleUnlock(apiKey: string) {
    setBusy(true);
    setUnlockError(null);
    try {
      const session = await unlockStudioWorkspace(
        apiKey,
        health?.auth.browser_sessions ?? false,
      );
      setHealth((current) => current ? { ...current, runtime: session.runtime } : current);
      setWorkspaceRole(session.role);
      await loadWorkspaceData(session.role);
      setLocked(false);
    } catch (err) {
      if (isUnauthorizedStudioError(err)) {
        forgetStudioApiKey();
        setUnlockError("That workspace key was not accepted. Check it and try again.");
      } else {
        setUnlockError(err instanceof Error ? err.message : "Could not open the workspace.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleAccountLogin(
    email: string,
    password: string,
    workspaceId?: string,
  ): Promise<IdentityLoginResult> {
    setBusy(true);
    setUnlockError(null);
    try {
      const result = await loginStudioAccount({
        email,
        password,
        workspace_id: workspaceId,
      });
      if (result.kind === "session") {
        const session = result.session;
        setHealth((current) => current ? { ...current, runtime: session.runtime } : current);
        setWorkspaceRole(session.role);
        await loadWorkspaceData(session.role);
        setLocked(false);
      }
      return result;
    } finally {
      setBusy(false);
    }
  }

  async function handleLock() {
    setBusy(true);
    setError(null);
    try {
      await logoutStudioWorkspace(health?.auth.browser_sessions ?? false);
      forgetStudioApiKey();
      clearWorkspaceData();
      setUnlockError(null);
      setLocked(true);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Studio could not lock this workspace. Please retry.",
      );
    } finally {
      setBusy(false);
    }
  }

  function clearWorkspaceData() {
    setReport(null);
    setTraces([]);
    setRuns([]);
    setCases([]);
    setBaselineId("");
    setCandidateId("");
    setError(null);
    setNotice(null);
    setWorkspaceRole(null);
    setHealth((current) =>
      current?.auth.required
        ? {
            ...current,
            runtime: {
              ...current.runtime,
              workspace_id: "protected",
              trace_count: 0,
              report_count: 0,
              case_count: 0,
            },
          }
        : current,
    );
  }

  function presentApiError(err: unknown, fallback: string) {
    if (health?.auth.required && isUnauthorizedStudioError(err)) {
      forgetStudioApiKey();
      clearWorkspaceData();
      setUnlockError(
        health.auth.browser_sessions
          ? "Your workspace session ended or its source key was revoked. Sign in again."
          : "Your workspace key expired or was revoked. Enter a current key.",
      );
      setLocked(true);
      return;
    }
    setError(err instanceof Error ? err.message : fallback);
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
    if (!canEdit) {
      setError("Your current access is read-only. Ask a workspace admin for editor access to upload traces.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const uploaded = await uploadTrace(file);
      await refreshTraces();
      await refreshRuns();
      if (role === "baseline") setBaselineId(uploaded.id);
      if (role === "candidate") setCandidateId(uploaded.id);
    } catch (err) {
      presentApiError(err, "Upload failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCompare() {
    if (!canEdit) {
      setError("Your current access is read-only. Ask a workspace admin for editor access to run comparisons.");
      return;
    }
    if (!baselineId || !candidateId) return;
    setBusy(true);
    setError(null);
    try {
      const nextReport = await compareTraces(baselineId, candidateId);
      setReport(nextReport);
      setSelectedReportId(nextReport.report_id);
      handleSectionChange("runs");
      await refreshRuns();
    } catch (err) {
      presentApiError(err, "Comparison failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveCase() {
    if (!canEdit) {
      setError("Your current access is read-only. Ask a workspace admin for editor access to save guardrails.");
      return;
    }
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
      presentApiError(err, "Failed to save regression case.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRunCase(item: RegressionCase) {
    if (!canEdit) {
      setError("Your current access is read-only. Ask a workspace admin for editor access to recheck guardrails.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await runRegressionCase(item.case_id, item.candidate_trace_id);
      setReport(result.report);
      setSelectedReportId(result.report.report_id);
      await refreshCases();
      await refreshRuns();
    } catch (err) {
      presentApiError(err, "Failed to recheck regression case.");
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
      presentApiError(err, "Failed to load comparison history item.");
    } finally {
      setBusy(false);
    }
  }

  if (loading && health === null) {
    return <WorkspaceConnecting />;
  }

  if (locked) {
    return (
      <WorkspaceUnlock
        busy={busy}
        error={unlockError}
        humanAccounts={health?.auth.human_accounts ?? false}
        managedSession={health?.auth.browser_sessions ?? false}
        onAccountLogin={handleAccountLogin}
        onUnlock={(apiKey) => void handleUnlock(apiKey)}
        sessionTtlSeconds={health?.auth.browser_session_ttl_seconds ?? 0}
      />
    );
  }

  return (
    <main className="studio-shell" data-active-section={activeSection}>
      <div className={sidebarCollapsed ? "dashboard-frame dashboard-frame-sidebar-collapsed" : "dashboard-frame"}>
        <Sidebar
          activeSection={activeSection}
          canEdit={canEdit}
          collapsed={sidebarCollapsed}
          onPrimaryAction={() => handleSectionChange("sources")}
          authRequired={health?.auth.required ?? false}
          onSectionChange={handleSectionChange}
          onLock={() => void handleLock()}
          onThemeToggle={toggleTheme}
          onToggleCollapsed={() => setSidebarCollapsed((current) => !current)}
          runtime={health?.runtime ?? null}
          theme={theme}
          workspaceRole={workspaceRole}
        />
        <div className="dashboard-main">
          <Topbar
            activeSection={activeSection}
            authRequired={health?.auth.required ?? false}
            onHelp={() => handleSectionChange("home")}
            searchValue={searchQuery}
            onSearchChange={setSearchQuery}
            runtime={health?.runtime ?? null}
            searchInputRef={searchInputRef}
          />

          <div aria-atomic="true" aria-live="polite" className="sr-only" role="status">
            Opened {content.title}
          </div>

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
                  <button className="header-save-action" disabled={!report || busy || !canEdit} onClick={() => void handleSaveCase()} title={canEdit ? "Save this comparison as a guardrail" : "Editor access is required"} type="button">
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

          {workspaceRole === "viewer" ? (
            <section className="role-access-band" role="status">
              <ShieldCheck size={18} aria-hidden />
              <div>
                <strong>Read-only workspace</strong>
                <span>You can inspect traces, comparisons, issues, sessions, and generated tests. An editor or admin key is required to change data.</span>
              </div>
            </section>
          ) : null}

          <SectionOverview
            report={report}
            section={activeSection}
          />

          {activeSection === "home" ? (
            <HomePanel authRequired={health?.auth.required ?? false} canEdit={canEdit} cases={cases} onSectionChange={handleSectionChange} report={report} runtime={health?.runtime ?? null} traces={traces} />
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
                  readOnly={!canEdit}
                  onRunCase={(item) => void handleRunCase(item)}
                  onSaveCase={() => void handleSaveCase()}
                  report={report}
                />
              ) : null}
              {activeSection === "setup" ? (
                <SettingsPanel health={health} workspaceRole={workspaceRole} />
              ) : null}
              {activeSection === "sources" ? (
                <>
                  <div className="trace-source-actions">
                    <UploadComparePanel
                      traces={traces}
                      baselineId={baselineId}
                      candidateId={candidateId}
                      busy={busy}
                      readOnly={!canEdit}
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
