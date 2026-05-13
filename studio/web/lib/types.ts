export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type JsonObject = { [key: string]: JsonValue };

export type TraceSummary = {
  id: string;
  trace_id: string;
  display_name: string;
  source_convention: string;
  created_at: string;
  event_count: number;
  root_event: string;
};

export type TraceEvent = {
  id: string;
  parent_id: string | null;
  sequence_index: number;
  type: string;
  semantic_name: string;
  timestamp: string;
  duration_ms: number | null;
  payload: JsonObject;
  source_format: string;
  source_event_id: string | null;
  model_version: string | null;
  prompt_version: string | null;
  code_sha: string | null;
  sampling_params: JsonObject | null;
};

export type Divergence = {
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
    errors_introduced: JsonObject[];
    affected_event_count: number;
  };
  source_metadata: JsonObject;
};

export type Report = {
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
