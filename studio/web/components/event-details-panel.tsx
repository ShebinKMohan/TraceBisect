"use client";

import { CalendarClock, CheckCircle2, Copy, GitBranch, Hash, Timer } from "lucide-react";
import { useMemo, useState } from "react";
import type { Divergence, JsonObject, JsonValue, TraceEvent, TraceSummary } from "@/lib/types";
import {
  formatDuration,
  formatEventLabel,
  formatJson,
  formatShortDate,
  formatTime,
  friendlyDivergenceDescription,
  friendlyDivergenceType,
  friendlyEventType,
  friendlySeverity,
  friendlySourceConvention,
  friendlyStatus,
  friendlyTraceName,
} from "@/lib/format";

type InspectorTab = "metadata" | "change" | "timeline" | "payload";

type EventDetailsPanelProps = {
  event: TraceEvent | null;
  events: TraceEvent[];
  side: "baseline" | "candidate";
  trace?: TraceSummary;
  divergence: Divergence | null;
  highlightedIds: Set<string>;
  onSelectEvent: (event: TraceEvent) => void;
};

const tabs: { id: InspectorTab; label: string }[] = [
  { id: "change", label: "First change" },
  { id: "metadata", label: "Run details" },
  { id: "timeline", label: "Timeline" },
  { id: "payload", label: "Raw data" },
];

function plainLanguageChange(divergence: Divergence | null, event: TraceEvent | null): string {
  if (!divergence) return "The two runs follow the same meaningful behavior.";
  const subject = event?.semantic_name ? `The ${event.semantic_name} step` : "The new run";
  if (divergence.type === "changed_tool_args") return `${subject} received different inputs.`;
  if (divergence.type === "missing_event") return "A step from the known-good run did not happen in the new run.";
  if (divergence.type === "extra_event") return "The new run added a step that was not present in the known-good run.";
  if (divergence.type === "branch_changed") return "The new run followed a different decision path.";
  if (divergence.type === "cost_regression") return "The new run cost more than the allowed threshold.";
  return friendlyDivergenceDescription(divergence.type, divergence.description);
}

function stringifyValue(value: JsonValue | undefined): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return formatJson(value);
}

function payloadValue(payload: JsonObject | undefined, keys: string[]): string | null {
  if (!payload) return null;
  for (const key of keys) {
    const value = stringifyValue(payload[key]);
    if (value) return value;
  }
  return null;
}

function eventStatus(event: TraceEvent | null): string {
  if (!event) return "Waiting";
  const payloadStatus = payloadValue(event.payload, ["status", "level", "outcome"]);
  if (payloadStatus) return payloadStatus;
  if (event.type === "ERROR") return "Error";
  if (event.type === "TOOL_CALL" || event.type === "LLM_CALL") return "Observed";
  return "Success";
}

function traceStatus(trace: TraceSummary | undefined, divergence: Divergence | null): string {
  if (trace?.status) return friendlyStatus(trace.status);
  if (divergence) return friendlySeverity(divergence.severity);
  return "No behavior change";
}

function modelLabel(event: TraceEvent | null): string {
  if (!event) return "--";
  return event.model_version ?? payloadValue(event.payload, ["model", "model_name"]) ?? "--";
}

function sourceLabel(event: TraceEvent | null): string {
  if (!event) return "--";
  if (event.source_format === "otel") return "OpenTelemetry";
  if (event.source_format === "native") return "TraceBisect";
  return event.source_format || "Unknown";
}

function eventParentChain(event: TraceEvent, byId: Map<string, TraceEvent>): TraceEvent[] {
  const chain: TraceEvent[] = [];
  let parentId = event.parent_id;
  while (parentId) {
    const parent = byId.get(parentId);
    if (!parent) break;
    chain.unshift(parent);
    parentId = parent.parent_id;
  }
  return chain.slice(-3);
}

function selectedHasDivergence(event: TraceEvent | null, divergence: Divergence | null): boolean {
  return Boolean(
    event &&
      (event.id === divergence?.baseline_event_id || event.id === divergence?.candidate_event_id),
  );
}

function samplingSummary(event: TraceEvent | null): string {
  if (!event?.sampling_params) return "--";
  return Object.entries(event.sampling_params)
    .map(([key, value]) => `${key}: ${stringifyValue(value) ?? "--"}`)
    .join(", ");
}

function timelineBounds(events: TraceEvent[]) {
  if (events.length === 0) return { min: Date.now(), span: 1 };
  const starts = events.map((item) => new Date(item.timestamp).getTime()).filter(Number.isFinite);
  if (starts.length === 0) return { min: Date.now(), span: 1 };
  const min = Math.min(...starts);
  const max = Math.max(
    ...events.map((item) => {
      const start = new Date(item.timestamp).getTime();
      return Number.isFinite(start) ? start + (item.duration_ms ?? 0) : 0;
    }),
  );
  const span = Math.max(max - min, 1);
  return { min, span };
}

export function EventDetailsPanel({
  event,
  events,
  side,
  trace,
  divergence,
  highlightedIds,
  onSelectEvent,
}: EventDetailsPanelProps) {
  const [activeTab, setActiveTab] = useState<InspectorTab>("change");
  const byId = useMemo(() => new Map(events.map((item) => [item.id, item])), [events]);
  const bounds = useMemo(() => timelineBounds(events), [events]);
  const isFirstDrift = selectedHasDivergence(event, divergence);
  const parentEvent = event?.parent_id ? byId.get(event.parent_id) : null;
  const parentChain = event ? eventParentChain(event, byId) : [];
  const baselineValue = stringifyValue(divergence?.expected);
  const candidateValue = stringifyValue(divergence?.actual);
  const affectedEventLabel =
    divergence && divergence.impact.affected_event_count > 0
      ? `${divergence.impact.affected_event_count} affected`
      : "First changed event";
  const changeSummary = divergence
    ? friendlyDivergenceDescription(divergence.type, divergence.description)
    : "No behavior change has been detected for this comparison.";

  return (
    <aside className="panel event-details-panel" aria-label="Selected event details" data-testid="details-panel">
      <div className="workbench-heading">
        <div>
          <p>Comparison explanation</p>
          <h2>{activeTab === "change" ? "First behavior change" : event ? event.semantic_name : "Select an event"}</h2>
        </div>
        <span>{side === "baseline" ? "Baseline" : "Candidate"}</span>
      </div>

      <div className="trace-detail-tabs" data-testid="trace-detail-tabs" role="tablist" aria-label="Trace detail sections">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            aria-controls={`trace-detail-${tab.id}`}
            aria-selected={activeTab === tab.id}
            className={activeTab === tab.id ? "trace-detail-tab-active" : ""}
            id={`trace-detail-tab-${tab.id}`}
            onClick={() => setActiveTab(tab.id)}
            role="tab"
            type="button"
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "metadata" ? (
        <section
          aria-labelledby="trace-detail-tab-metadata"
          data-testid="trace-detail-metadata"
          id="trace-detail-metadata"
          role="tabpanel"
        >
          <div className="detail-stat-grid">
            <div>
              <CalendarClock size={15} aria-hidden />
              <span>Started</span>
              <strong>{formatTime(event?.timestamp)}</strong>
            </div>
            <div>
              <Timer size={15} aria-hidden />
              <span>Latency</span>
              <strong>{formatDuration(event?.duration_ms)}</strong>
            </div>
            <div>
              <Hash size={15} aria-hidden />
              <span>Type</span>
              <strong>{friendlyEventType(event?.type)}</strong>
            </div>
            <div>
              <CheckCircle2 size={15} aria-hidden />
              <span>Status</span>
              <strong>{eventStatus(event)}</strong>
            </div>
          </div>

          <div className="detail-section detail-section-compact">
            <div className="detail-section-title">
              <span>Run context</span>
              {isFirstDrift ? <strong>First change</strong> : null}
            </div>
            <dl className="metadata-list metadata-list-grid">
              <div>
                <dt>Trace</dt>
                <dd>{friendlyTraceName(trace?.display_name)}</dd>
              </div>
              <div>
                <dt>Trace format</dt>
                <dd>{friendlySourceConvention(trace?.source_convention)}</dd>
              </div>
              <div>
                <dt>Source</dt>
                <dd>{sourceLabel(event)}</dd>
              </div>
              <div>
                <dt>Event count</dt>
                <dd>{trace?.event_count ?? events.length}</dd>
              </div>
              <div>
                <dt>Created</dt>
                <dd>{trace?.created_at ? formatShortDate(trace.created_at) : "--"}</dd>
              </div>
              <div>
                <dt>Trace status</dt>
                <dd>{traceStatus(trace, divergence)}</dd>
              </div>
            </dl>
          </div>

          <div className="detail-section detail-section-compact">
            <div className="detail-section-title">
              <span>Selected event</span>
              <GitBranch size={15} aria-hidden />
            </div>
            <dl className="metadata-list metadata-list-grid">
              <div>
                <dt>Step</dt>
                <dd>{event ? event.sequence_index + 1 : "--"}</dd>
              </div>
              <div>
                <dt>Source</dt>
                <dd>{sourceLabel(event)}</dd>
              </div>
              <div>
                <dt>Model</dt>
                <dd>{modelLabel(event)}</dd>
              </div>
              <div>
                <dt>Prompt</dt>
                <dd>{event?.prompt_version ?? payloadValue(event?.payload, ["prompt_version", "prompt"]) ?? "--"}</dd>
              </div>
              <div>
                <dt>Commit</dt>
                <dd>{event?.code_sha ?? payloadValue(event?.payload, ["code_sha", "commit"]) ?? "--"}</dd>
              </div>
              <div>
                <dt>Model settings</dt>
                <dd>{samplingSummary(event)}</dd>
              </div>
            </dl>
          </div>
        </section>
      ) : null}

      {activeTab === "change" ? (
        <section
          aria-labelledby="trace-detail-tab-change"
          data-testid="trace-detail-change"
          id="trace-detail-change"
          role="tabpanel"
        >
          <div className={["change-card", isFirstDrift ? "change-card-drift" : ""].filter(Boolean).join(" ")}>
            <div>
              <span>{isFirstDrift ? "What this means" : "Selected event"}</span>
              <strong>{isFirstDrift ? plainLanguageChange(divergence, event) : friendlyEventType(event?.type)}</strong>
              <p>{isFirstDrift ? changeSummary : "This event is part of the run, but it is not the first changed step."}</p>
            </div>
            <em>{isFirstDrift ? friendlySeverity(divergence?.severity) : eventStatus(event)}</em>
          </div>

          {isFirstDrift ? (
            <div className="change-value-grid">
              <div>
                <span>Expected · known-good run</span>
                <pre>{baselineValue ?? "--"}</pre>
              </div>
              <div>
                <span>Actual · new run</span>
                <pre>{candidateValue ?? "--"}</pre>
              </div>
            </div>
          ) : null}

          <div className="detail-section detail-section-compact">
            <div className="detail-section-title">
              <span>Technical details</span>
              <GitBranch size={15} aria-hidden />
            </div>
            <dl className="metadata-list metadata-list-grid">
              <div>
                <dt>Canonical event</dt>
                <dd>{event?.id ?? "--"}</dd>
              </div>
              <div>
                <dt>Source event</dt>
                <dd>{event?.source_event_id ?? "--"}</dd>
              </div>
              <div>
                <dt>Parent</dt>
                <dd>{parentEvent ? `${friendlyEventType(parentEvent.type)} / ${parentEvent.semantic_name}` : "Root event"}</dd>
              </div>
              <div>
                <dt>Path</dt>
                <dd>
                  {parentChain.length > 0
                    ? parentChain.map((ancestor) => ancestor.semantic_name).join(" -> ")
                    : "Trace root"}
                </dd>
              </div>
              <div>
                <dt>Impact</dt>
                <dd>
                  {divergence
                    ? `${affectedEventLabel}, ${formatDuration(event?.duration_ms)} selected`
                    : formatDuration(event?.duration_ms)}
                </dd>
              </div>
              <div>
                <dt>Cost delta</dt>
                <dd>{divergence ? `${divergence.impact.cost_delta_ratio.toFixed(2)}x` : "--"}</dd>
              </div>
            </dl>
          </div>
        </section>
      ) : null}

      {activeTab === "timeline" ? (
        <section
          aria-labelledby="trace-detail-tab-timeline"
          data-testid="trace-detail-timeline"
          id="trace-detail-timeline"
          role="tabpanel"
        >
          <ol className="timeline-list">
            {events.map((item) => {
              const start = new Date(item.timestamp).getTime();
              const left = Number.isFinite(start) ? ((start - bounds.min) / bounds.span) * 100 : 0;
              const width = Math.max(((item.duration_ms ?? 0) / bounds.span) * 100, 4);
              const highlighted = highlightedIds.has(item.id);
              const active = event?.id === item.id;
              const error = item.type === "ERROR";
              return (
                <li key={item.id}>
                  <button
                    className={[
                      "timeline-row",
                      active ? "timeline-row-active" : "",
                      highlighted ? "timeline-row-drift" : "",
                      error ? "timeline-row-error" : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    onClick={() => onSelectEvent(item)}
                    type="button"
                  >
                    <span className="timeline-label">
                      <strong>{formatEventLabel(item)}</strong>
                      <small>{item.semantic_name}</small>
                    </span>
                    <span className="timeline-track" aria-hidden>
                      <span style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%` }} />
                    </span>
                    <span className="timeline-duration">{formatDuration(item.duration_ms)}</span>
                  </button>
                </li>
              );
            })}
          </ol>
        </section>
      ) : null}

      {activeTab === "payload" ? (
        <section
          aria-labelledby="trace-detail-tab-payload"
          data-testid="trace-detail-payload"
          id="trace-detail-payload"
          role="tabpanel"
        >
          <div className="detail-section payload-section">
            <div className="detail-section-title">
              <span>Raw payload</span>
              <Copy size={15} aria-hidden />
            </div>
            <pre className="payload-inspector">
              <code>{event ? formatJson(event.payload) : "Select an event in the trace tree."}</code>
            </pre>
          </div>
        </section>
      ) : null}
    </aside>
  );
}
