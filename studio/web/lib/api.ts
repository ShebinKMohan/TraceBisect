import type { Report, TraceSummary } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_TRACEBISECT_API_URL ?? "http://127.0.0.1:8000";

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with ${response.status}`);
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
