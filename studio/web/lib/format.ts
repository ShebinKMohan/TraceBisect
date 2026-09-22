import type { JsonValue, RunSummary, TraceEvent } from "@/lib/types";

export function formatJson(value: JsonValue): string {
  return JSON.stringify(value, null, 2);
}

export function formatEventLabel(event: TraceEvent): string {
  return `${event.sequence_index.toString().padStart(2, "0")} ${friendlyEventType(event.type)}`;
}

const divergenceLabels: Record<string, string> = {
  changed_tool_args: "Tool arguments changed",
  changed_final_output: "Final answer changed",
  cost_regression: "Cost increased",
  extra_event: "Unexpected step added",
  missing_event: "Expected step missing",
  error_absence: "Expected error missing",
  error_presence: "New error introduced",
};

const eventLabels: Record<string, string> = {
  RUN_START: "Run started",
  RUN_END: "Run completed",
  LLM_CALL: "Model call",
  TOOL_CALL: "Tool call",
  MCP_CALL: "MCP call",
  RETRIEVAL: "Retrieval",
  STATE_TRANSITION: "State update",
  BRANCH_DECISION: "Decision",
  ERROR: "Error",
  HUMAN_INPUT: "Human input",
};

const sourceLabels: Record<string, string> = {
  native: "TraceBisect",
  openinference: "OpenInference",
  genai: "GenAI",
  mixed: "Mixed sources",
  unknown: "Unknown source",
};

function titleCase(value: string): string {
  return value
    .split(" ")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(" ");
}

export function friendlyTraceName(value: string | null | undefined): string {
  if (!value) return "Untitled trace";
  const trimmed = value.trim();
  const normalized = trimmed
    .toLowerCase()
    .replace(/\.(tbtrace|json)$/u, "")
    .replace(/[^a-z0-9]+/gu, "_")
    .replace(/^_+|_+$/gu, "");

  if (normalized.includes("refund_search_baseline")) return "Refund baseline";
  if (normalized.includes("refund_search_candidate")) return "Refund regression";
  if (normalized.includes("mixed_convention")) return "Mixed source sample";
  if (normalized.includes("openinference_refund_search")) return "OpenInference refund";
  if (normalized.includes("genai_refund_search")) return "GenAI refund";

  const cleaned = trimmed
    .replace(/\.(tbtrace|json)$/iu, "")
    .replace(/[_-]+/gu, " ")
    .replace(/\s+/gu, " ")
    .trim();
  return cleaned ? titleCase(cleaned) : "Untitled trace";
}

export function scenarioNameFromRun(run: RunSummary): string {
  const names = `${run.baseline.display_name} ${run.candidate.display_name}`.toLowerCase();
  if (names.includes("refund")) return "Refund search";
  return `${friendlyTraceName(run.baseline.display_name)} comparison`;
}

export function friendlyDivergenceType(value: string | null | undefined): string {
  if (!value) return "No behavior change";
  return divergenceLabels[value] ?? titleCase(value.replaceAll("_", " "));
}

export function friendlyDivergenceDescription(
  type: string | null | undefined,
  description: string | null | undefined,
): string {
  if (!description) return friendlyDivergenceType(type);
  const stepMatch = description.match(/\b(?:RUN_START|RUN_END|LLM_CALL|TOOL_CALL|MCP_CALL|RETRIEVAL|STATE_TRANSITION|BRANCH_DECISION|ERROR|HUMAN_INPUT)\s+([^\s]+)\s+(.+)/u);
  const stepName = stepMatch?.[1]?.replaceAll("_", " ");

  if (type === "changed_tool_args") {
    // Labels may contain spaces ("Files Server/read_file"), so match the swap
    // sentence as a whole instead of relying on the single-token step capture.
    const swap = description.endsWith(" arguments differ")
      ? null
      : description.match(/\b(?:TOOL_CALL|MCP_CALL)\s+(.+?)\s+replaced by\s+(.+)$/u);
    if (swap) return `${swap[1].replaceAll("_", " ")} was replaced by ${swap[2].replaceAll("_", " ")}`;
    if (stepName) return `${stepName} used different tool arguments`;
  }
  if (type === "changed_final_output") return "The final answer changed";
  if (type === "cost_regression") return "The run became more expensive";
  if (type === "extra_event") return "The candidate added an unexpected step";
  if (type === "missing_event") return "The candidate skipped an expected step";

  return description.replace(
    /\b(RUN_START|RUN_END|LLM_CALL|TOOL_CALL|MCP_CALL|RETRIEVAL|STATE_TRANSITION|BRANCH_DECISION|ERROR|HUMAN_INPUT)\b/gu,
    (eventType) => friendlyEventType(eventType),
  );
}

export function friendlySeverity(value: string | null | undefined): string {
  if (!value) return "Info";
  return titleCase(value);
}

export function friendlyStatus(value: string | null | undefined): string {
  if (value === "failing") return "Regression found";
  if (value === "passing") return "No regression found";
  if (value === "unknown") return "Not checked";
  return "Waiting";
}

export function friendlySourceConvention(value: string | null | undefined): string {
  if (!value) return "Unknown source";
  return sourceLabels[value] ?? titleCase(value.replaceAll("_", " "));
}

export function friendlyEventType(value: string | null | undefined): string {
  if (!value) return "Event";
  return eventLabels[value] ?? titleCase(value.replaceAll("_", " "));
}

export function formatShortDate(value: string): string {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function formatDuration(value: number | null | undefined): string {
  if (value === null || value === undefined) return "0.00s";
  return `${(value / 1000).toFixed(2)}s`;
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "--";
  return new Intl.DateTimeFormat("en", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}
