import type { RegressionCase, Report, RunSummary, StudioHealth, TraceSummary } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_TRACEBISECT_API_URL ?? "http://127.0.0.1:8000";
const MAX_TRACE_UPLOAD_BYTES = 5 * 1024 * 1024;
const ALLOWED_TRACE_UPLOAD_EXTENSIONS = [".tbtrace", ".json"];

type ApiErrorDetail =
  | string
  | {
      msg?: string;
      message?: string;
    }
  | ApiErrorDetail[];

type ApiErrorPayload = {
  detail?: ApiErrorDetail;
  message?: string;
};

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response));
  }
  return (await response.json()) as T;
}

export async function fetchDemoReport(): Promise<Report> {
  return parseResponse<Report>(await fetch(`${API_BASE}/api/demo-report`));
}

export async function fetchStudioHealth(): Promise<StudioHealth> {
  return parseResponse<StudioHealth>(await fetch(`${API_BASE}/api/health`));
}

export async function fetchTraces(): Promise<TraceSummary[]> {
  const payload = await parseResponse<{ traces: TraceSummary[] }>(
    await fetch(`${API_BASE}/api/traces`),
  );
  return payload.traces;
}

export async function fetchRegressionCases(): Promise<RegressionCase[]> {
  const payload = await parseResponse<{ cases: RegressionCase[] }>(
    await fetch(`${API_BASE}/api/regression-cases`),
  );
  return payload.cases;
}

export type RunHistoryFilters = {
  q?: string;
  status?: string;
  severity?: string;
  source_convention?: string;
  divergence_type?: string;
};

export async function fetchRuns(filters: RunHistoryFilters = {}): Promise<RunSummary[]> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value?.trim()) query.set(key, value.trim());
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  const payload = await parseResponse<{ runs: RunSummary[] }>(
    await fetch(`${API_BASE}/api/runs${suffix}`),
  );
  return payload.runs;
}

export async function fetchRunReport(reportId: string): Promise<Report> {
  return parseResponse<Report>(await fetch(`${API_BASE}/api/runs/${encodeURIComponent(reportId)}`));
}

export async function uploadTrace(file: File): Promise<TraceSummary> {
  validateTraceUpload(file);
  const body = new FormData();
  body.append("file", file);
  const payload = await parseResponse<{ trace: TraceSummary }>(
    await fetch(`${API_BASE}/api/traces/upload`, {
      method: "POST",
      body,
    }),
  );
  return payload.trace;
}

export type CreateRegressionCasePayload = {
  name: string;
  description?: string;
  tags?: string[];
  baseline_trace_id: string;
  candidate_trace_id: string;
};

export async function createRegressionCase(
  payload: CreateRegressionCasePayload,
): Promise<RegressionCase> {
  const response = await parseResponse<{ case: RegressionCase }>(
    await fetch(`${API_BASE}/api/regression-cases`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
  return response.case;
}

export async function runRegressionCase(
  caseId: string,
  candidateTraceId: string,
): Promise<{ case: RegressionCase; report: Report }> {
  return parseResponse<{ case: RegressionCase; report: Report }>(
    await fetch(`${API_BASE}/api/regression-cases/${caseId}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidate_trace_id: candidateTraceId }),
    }),
  );
}

export async function compareTraces(
  baselineTraceId: string,
  candidateTraceId: string,
): Promise<Report> {
  return parseResponse<Report>(
    await fetch(`${API_BASE}/api/compare`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        baseline_trace_id: baselineTraceId,
        candidate_trace_id: candidateTraceId,
      }),
    }),
  );
}

function validateTraceUpload(file: File): void {
  const filename = file.name.toLowerCase();
  const hasAllowedExtension = ALLOWED_TRACE_UPLOAD_EXTENSIONS.some((extension) =>
    filename.endsWith(extension),
  );
  if (!hasAllowedExtension) {
    throw new Error("Upload a .tbtrace or .json trace file.");
  }
  if (file.size === 0) {
    throw new Error("The selected trace file is empty.");
  }
  if (file.size > MAX_TRACE_UPLOAD_BYTES) {
    throw new Error("Trace file is too large. Maximum upload size is 5 MB.");
  }
}

async function responseErrorMessage(response: Response): Promise<string> {
  const fallback = `Request failed with ${response.status}`;
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    const text = (await response.text()).trim();
    return text || fallback;
  }

  try {
    const payload = (await response.json()) as ApiErrorPayload;
    return detailToMessage(payload.detail) ?? payload.message ?? fallback;
  } catch {
    return fallback;
  }
}

function detailToMessage(detail: ApiErrorDetail | undefined): string | undefined {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => detailToMessage(item)).filter((item) => item);
    return messages.length > 0 ? messages.join("; ") : undefined;
  }
  return detail?.msg ?? detail?.message;
}
