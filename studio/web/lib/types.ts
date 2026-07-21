export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type JsonObject = { [key: string]: JsonValue };
export type WorkspaceRole = "viewer" | "editor" | "admin";

export type StudioHealth = {
  ok: boolean;
  product: string;
  auth: {
    mode: "none" | "api-key";
    required: boolean;
    credential_source: "none" | "environment" | "managed";
    browser_sessions: boolean;
    browser_session_ttl_seconds: number;
    browser_session_cookie_secure: boolean;
  };
  audit: {
    enabled: boolean;
    format: "json";
    request_id_header: "X-Request-ID";
  };
  metrics: {
    format: "prometheus_text_0.0.4";
    path: "/api/metrics";
    access: "open_local" | "bearer_token" | "unavailable";
    scope: "process";
    resets_on_restart: true;
  };
  runtime: {
    kind: "memory" | "sqlite";
    durable: boolean;
    workspace_id: string;
    trace_count: number;
    report_count: number;
    case_count: number;
  };
  readiness: {
    api_ready: boolean;
    production_saas_ready: boolean;
    completed: string[];
    blockers: string[];
  };
  limits: {
    max_upload_bytes: number;
    max_stored_traces: number;
    max_stored_reports: number;
    max_stored_cases: number;
    rate_limit_window_seconds: number;
    rate_limit_requests: number;
    rate_limit_upload_requests: number;
  };
};

export type StudioSession = {
  authenticated: boolean;
  workspace_id: string;
  role: WorkspaceRole;
  access_mode: "open_local" | "api_key" | "browser_session";
  expires_at: string | null;
  runtime: StudioHealth["runtime"];
};

export type TraceSummary = {
  id: string;
  trace_id: string;
  display_name: string;
  source_convention: string;
  created_at: string;
  event_count: number;
  root_event: string;
  status: "success" | "error" | "unknown";
  duration_ms: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
  model: string | null;
  model_version: string | null;
  prompt_version: string | null;
  code_sha: string | null;
  agent_name: string | null;
  agent_version: string | null;
  session_id: string | null;
};

export type StudioSection = "home" | "runs" | "sources" | "sessions" | "divergences" | "cases" | "setup";

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

export type RegressionCaseLastResult = {
  status: "failing" | "passing" | "unknown";
  report_id: string | null;
  divergence_count: number;
  severity: string | null;
  checked_at: string | null;
};

export type RunStatus = "passing" | "failing";

export type RunSummary = {
  report_id: string;
  created_at: string;
  baseline: Pick<TraceSummary, "id" | "display_name" | "source_convention" | "event_count">;
  candidate: Pick<TraceSummary, "id" | "display_name" | "source_convention" | "event_count">;
  source_convention: string;
  divergence_count: number;
  status: RunStatus;
  severity: string | null;
  first_divergence_type: string | null;
  event_count: number;
};

export type RegressionCase = {
  case_id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  tags: string[];
  source_report_id: string;
  baseline_trace_id: string;
  candidate_trace_id: string;
  baseline: TraceSummary;
  candidate: TraceSummary;
  first_divergence: Divergence | null;
  divergence_count: number;
  assertions: string[];
  cost_threshold: number;
  scenario_cmd: string[];
  pytest: {
    filename: string;
    source: string;
  };
  last_result: RegressionCaseLastResult;
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
  regression_case?: {
    case_id: string;
    name: string;
    status: string;
  } | null;
};
