"use client";

import { CalendarClock, CheckCircle2, Copy, GitBranch, Hash, Timer } from "lucide-react";
import { useMemo, useState } from "react";
import type { Divergence, JsonObject, JsonValue, TraceEvent, TraceSummary } from "@/lib/types";
import { formatDuration, formatEventLabel, formatJson, formatShortDate, formatTime } from "@/lib/format";

type InspectorTab = "metadata" | "observations" | "timeline" | "payload";

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
  { id: "metadata", label: "Metadata" },
  { id: "observations", label: "Observations" },
  { id: "timeline", label: "Timeline" },
  { id: "payload", label: "Payload" },
];

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
  if (trace?.status) return trace.status;
  if (divergence) return divergence.severity;
  return "No divergence";
}

function modelLabel(event: TraceEvent | null): string {
  if (!event) return "--";
  return event.model_version ?? payloadValue(event.payload, ["model", "model_name"]) ?? "--";
}

function sourceLabel(event: TraceEvent | null): string {
  if (!event) return "--";
  return event.source_event_id ?? event.source_format ?? "native";
}

function eventDepth(event: TraceEvent, byId: Map<string, TraceEvent>): number {
  let depth = 0;
  let parentId = event.parent_id;
  while (parentId) {
    const parent = byId.get(parentId);
    if (!parent) break;
    depth += 1;
    parentId = parent.parent_id;
  }
  return Math.min(depth, 3);
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
  const [activeTab, setActiveTab] = useState<InspectorTab>("metadata");
  const byId = useMemo(() => new Map(events.map((item) => [item.id, item])), [events]);
  const bounds = useMemo(() => timelineBounds(events), [events]);
  const isFirstDrift = selectedHasDivergence(event, divergence);

  return (
    <aside className="panel event-details-panel" aria-label="Selected event details" data-testid="details-panel">
      <div className="workbench-heading">
        <div>
          <p>Inspector</p>
          <h2>{event ? event.semantic_name : "Select an event"}</h2>
        </div>
        <span>{side}</span>
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
              <strong>{event?.type ?? "--"}</strong>
            </div>
            <div>
              <CheckCircle2 size={15} aria-hidden />
              <span>Status</span>
              <strong>{eventStatus(event)}</strong>
            </div>
          </div>

          <div className="detail-section detail-section-compact">
            <div className="detail-section-title">
              <span>Trace context</span>
              {isFirstDrift ? <strong>First drift</strong> : null}
            </div>
            <dl className="metadata-list metadata-list-grid">
              <div>
                <dt>Trace id</dt>
                <dd>{trace?.trace_id ?? "--"}</dd>
              </div>
              <div>
                <dt>Name</dt>
                <dd>{trace?.display_name ?? "--"}</dd>
              </div>
              <div>
                <dt>Source convention</dt>
                <dd>{trace?.source_convention ?? event?.source_format ?? "--"}</dd>
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
                <dt>Event id</dt>
                <dd>{event?.id ?? "--"}</dd>
              </div>
              <div>
                <dt>Source id</dt>
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
                <dt>Code sha</dt>
                <dd>{event?.code_sha ?? payloadValue(event?.payload, ["code_sha", "commit"]) ?? "--"}</dd>
              </div>
              <div>
                <dt>Sampling</dt>
                <dd>{samplingSummary(event)}</dd>
              </div>
            </dl>
          </div>
        </section>
      ) : null}

      {activeTab === "observations" ? (
        <section
          aria-labelledby="trace-detail-tab-observations"
          data-testid="trace-detail-observations"
          id="trace-detail-observations"
          role="tabpanel"
        >
          <ol className="observation-list">
            {events.map((item) => {
              const highlighted = highlightedIds.has(item.id);
              const active = event?.id === item.id;
              return (
                <li key={item.id}>
                  <button
                    className={[
                      "observation-row",
                      active ? "observation-row-active" : "",
                      highlighted ? "observation-row-drift" : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    data-depth={eventDepth(item, byId)}
                    onClick={() => onSelectEvent(item)}
                    type="button"
                  >
                    <span className="observation-type">{item.type}</span>
                    <span className="observation-name">{item.semantic_name}</span>
                    <span>{formatDuration(item.duration_ms)}</span>
                    <span>{item.source_format}</span>
                    {highlighted ? <em>first drift</em> : <i aria-hidden />}
                  </button>
                </li>
              );
            })}
          </ol>
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
              <span>Payload</span>
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
