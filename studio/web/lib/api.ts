import type { Report, TraceSummary } from "@/lib/types";

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

export async function fetchTraces(): Promise<TraceSummary[]> {
  const payload = await parseResponse<{ traces: TraceSummary[] }>(
    await fetch(`${API_BASE}/api/traces`),
  );
  return payload.traces;
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
