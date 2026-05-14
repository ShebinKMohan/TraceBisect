"use client";

import { AlertTriangle, GitCompare } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { compareTraces, fetchDemoReport, fetchTraces, uploadTrace } from "@/lib/api";
import type { Report, TraceEvent, TraceSummary } from "@/lib/types";
import { CompareDrawer } from "@/components/compare-drawer";
import { EventDetailsPanel } from "@/components/event-details-panel";
import { IntegrationPanel } from "@/components/integration-panel";
import { RunList } from "@/components/run-list";
import { Sidebar } from "@/components/sidebar";
import { Topbar } from "@/components/topbar";
import { TraceWorkbench } from "@/components/trace-workbench";
import { UploadComparePanel } from "@/components/upload-compare-panel";

export function StudioDashboard() {
  const [report, setReport] = useState<Report | null>(null);
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [baselineId, setBaselineId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [activeSide, setActiveSide] = useState<"baseline" | "candidate">("candidate");
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
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

  function handleSelectEvent(event: TraceEvent) {
    setSelectedEventId(event.id);
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
                activeSide={activeSide}
                first={first}
                onSelectSide={setActiveSide}
                report={report}
                searchQuery={searchQuery}
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
              <EventDetailsPanel divergence={first} event={selectedEvent} side={activeSide} />
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
