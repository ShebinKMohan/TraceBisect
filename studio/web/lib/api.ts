import type {
  IdentityLoginResult,
  IdentityRecoveryResult,
  InvitationAcceptance,
  IssuedWorkspaceAccessKey,
  RegressionCase,
  Report,
  RunSummary,
  StudioHealth,
  StudioSession,
  TraceSummary,
  WorkspaceAccessKey,
  WorkspaceAccessKeyList,
  WorkspaceInvitation,
  WorkspaceMembership,
  WorkspaceRole,
} from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_TRACEBISECT_API_URL ?? "http://127.0.0.1:8000";
const MAX_TRACE_UPLOAD_BYTES = 5 * 1024 * 1024;
const ALLOWED_TRACE_UPLOAD_EXTENSIONS = [".tbtrace", ".json"];
const STUDIO_API_KEY_STORAGE = "tracebisect-workspace-api-key";

export class StudioApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly requestId: string | null = null,
  ) {
    super(message);
    this.name = "StudioApiError";
  }
}

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
  request_id?: string;
};

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const error = await responseError(response);
    throw new StudioApiError(error.message, response.status, error.requestId);
  }
  return (await response.json()) as T;
}

function storedStudioApiKey(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(STUDIO_API_KEY_STORAGE);
}

async function authorizedFetch(
  input: string,
  init: RequestInit = {},
  explicitApiKey?: string,
): Promise<Response> {
  const headers = new Headers(init.headers);
  const method = (init.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-TraceBisect-CSRF", "1");
  }
  const apiKey = explicitApiKey ?? storedStudioApiKey();
  if (apiKey) headers.set("Authorization", `Bearer ${apiKey}`);
  return fetch(input, { ...init, credentials: "include", headers });
}

export function hasStoredStudioApiKey(): boolean {
  return Boolean(storedStudioApiKey());
}

export function forgetStudioApiKey(): void {
  if (typeof window !== "undefined") {
    window.sessionStorage.removeItem(STUDIO_API_KEY_STORAGE);
  }
}

export function isUnauthorizedStudioError(error: unknown): boolean {
  return error instanceof StudioApiError && error.status === 401;
}

export async function fetchDemoReport(): Promise<Report> {
  return parseResponse<Report>(await authorizedFetch(`${API_BASE}/api/demo-report`));
}

export async function fetchStudioHealth(): Promise<StudioHealth> {
  return parseResponse<StudioHealth>(await fetch(`${API_BASE}/api/health`));
}

export async function fetchStudioSession(apiKey?: string): Promise<StudioSession> {
  return parseResponse<StudioSession>(
    await authorizedFetch(`${API_BASE}/api/session`, {}, apiKey),
  );
}

export async function unlockStudioWorkspace(
  apiKey: string,
  managedBrowserSession: boolean,
): Promise<StudioSession> {
  const normalizedApiKey = apiKey.trim();
  if (managedBrowserSession) {
    forgetStudioApiKey();
    return parseResponse<StudioSession>(
      await authorizedFetch(
        `${API_BASE}/api/browser-session`,
        { method: "POST" },
        normalizedApiKey,
      ),
    );
  }
  const session = await fetchStudioSession(normalizedApiKey);
  window.sessionStorage.setItem(STUDIO_API_KEY_STORAGE, normalizedApiKey);
  return session;
}

export async function loginStudioAccount(payload: {
  email: string;
  password: string;
  workspace_id?: string;
}): Promise<IdentityLoginResult> {
  forgetStudioApiKey();
  const response = await authorizedFetch(`${API_BASE}/api/identity/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (response.status === 409) {
    const choice = (await response.json()) as {
      workspaces?: { workspace_id: string; role: WorkspaceRole }[];
    };
    if (choice.workspaces?.length) {
      return { kind: "workspace_choice", workspaces: choice.workspaces };
    }
  }
  return { kind: "session", session: await parseResponse<StudioSession>(response) };
}

export async function previewWorkspaceInvitation(invitationToken: string): Promise<WorkspaceInvitation> {
  const payload = await parseResponse<{ invitation: WorkspaceInvitation }>(
    await authorizedFetch(`${API_BASE}/api/identity/invitation-preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ invitation_token: invitationToken }),
    }),
  );
  return payload.invitation;
}

export async function acceptWorkspaceInvitation(payload: {
  invitation_token: string;
  display_name: string;
  password: string;
}): Promise<InvitationAcceptance> {
  return parseResponse<InvitationAcceptance>(
    await authorizedFetch(`${API_BASE}/api/identity/invitations/accept`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function recoverStudioAccount(payload: {
  email: string;
  recovery_code: string;
  new_password: string;
}): Promise<IdentityRecoveryResult> {
  return parseResponse<IdentityRecoveryResult>(
    await authorizedFetch(`${API_BASE}/api/identity/recover`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function logoutStudioWorkspace(managedBrowserSession: boolean): Promise<void> {
  if (!managedBrowserSession) {
    forgetStudioApiKey();
    return;
  }
  const response = await fetch(`${API_BASE}/api/browser-session/logout`, {
    method: "POST",
    credentials: "include",
    headers: { "X-TraceBisect-CSRF": "1" },
  });
  if (!response.ok) {
    const error = await responseError(response);
    throw new StudioApiError(error.message, response.status, error.requestId);
  }
}

export async function fetchWorkspaceAccessKeys(): Promise<WorkspaceAccessKeyList> {
  return parseResponse<WorkspaceAccessKeyList>(
    await authorizedFetch(`${API_BASE}/api/access-keys`),
  );
}

export async function createWorkspaceAccessKey(payload: {
  label: string;
  role: WorkspaceRole;
  expires_in_days: number;
}): Promise<IssuedWorkspaceAccessKey> {
  return parseResponse<IssuedWorkspaceAccessKey>(
    await authorizedFetch(`${API_BASE}/api/access-keys`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function revokeWorkspaceAccessKey(keyId: string): Promise<WorkspaceAccessKey> {
  const payload = await parseResponse<{ key: WorkspaceAccessKey }>(
    await authorizedFetch(`${API_BASE}/api/access-keys/${encodeURIComponent(keyId)}`, {
      method: "DELETE",
    }),
  );
  return payload.key;
}

export async function fetchWorkspaceMembers(): Promise<{
  members: WorkspaceMembership[];
  current_user_id: string | null;
}> {
  return parseResponse(
    await authorizedFetch(`${API_BASE}/api/team/members`),
  );
}

export async function updateWorkspaceMember(
  userId: string,
  role: WorkspaceRole,
): Promise<WorkspaceMembership> {
  const payload = await parseResponse<{ member: WorkspaceMembership }>(
    await authorizedFetch(`${API_BASE}/api/team/members/${encodeURIComponent(userId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
    }),
  );
  return payload.member;
}

export async function removeWorkspaceMember(userId: string): Promise<WorkspaceMembership> {
  const payload = await parseResponse<{ member: WorkspaceMembership }>(
    await authorizedFetch(`${API_BASE}/api/team/members/${encodeURIComponent(userId)}`, {
      method: "DELETE",
    }),
  );
  return payload.member;
}

export async function fetchWorkspaceInvitations(): Promise<WorkspaceInvitation[]> {
  const payload = await parseResponse<{ invitations: WorkspaceInvitation[] }>(
    await authorizedFetch(`${API_BASE}/api/team/invitations`),
  );
  return payload.invitations;
}

export async function createWorkspaceInvitation(payload: {
  email: string;
  role: WorkspaceRole;
  expires_in_days: number;
}): Promise<{ invitation_token: string; invitation: WorkspaceInvitation }> {
  return parseResponse(
    await authorizedFetch(`${API_BASE}/api/team/invitations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function revokeWorkspaceInvitation(
  invitationId: string,
): Promise<WorkspaceInvitation> {
  const payload = await parseResponse<{ invitation: WorkspaceInvitation }>(
    await authorizedFetch(
      `${API_BASE}/api/team/invitations/${encodeURIComponent(invitationId)}`,
      { method: "DELETE" },
    ),
  );
  return payload.invitation;
}

export async function fetchTraces(): Promise<TraceSummary[]> {
  const payload = await parseResponse<{ traces: TraceSummary[] }>(
    await authorizedFetch(`${API_BASE}/api/traces`),
  );
  return payload.traces;
}

export async function fetchRegressionCases(): Promise<RegressionCase[]> {
  const payload = await parseResponse<{ cases: RegressionCase[] }>(
    await authorizedFetch(`${API_BASE}/api/regression-cases`),
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
    await authorizedFetch(`${API_BASE}/api/runs${suffix}`),
  );
  return payload.runs;
}

export async function fetchRunReport(reportId: string): Promise<Report> {
  return parseResponse<Report>(
    await authorizedFetch(`${API_BASE}/api/runs/${encodeURIComponent(reportId)}`),
  );
}

export async function uploadTrace(file: File): Promise<TraceSummary> {
  validateTraceUpload(file);
  const body = new FormData();
  body.append("file", file);
  const payload = await parseResponse<{ trace: TraceSummary }>(
    await authorizedFetch(`${API_BASE}/api/traces/upload`, {
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
    await authorizedFetch(`${API_BASE}/api/regression-cases`, {
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
    await authorizedFetch(`${API_BASE}/api/regression-cases/${encodeURIComponent(caseId)}/run`, {
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
    await authorizedFetch(`${API_BASE}/api/compare`, {
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

async function responseError(
  response: Response,
): Promise<{ message: string; requestId: string | null }> {
  const fallback = `Request failed with ${response.status}`;
  const headerRequestId = safeRequestId(response.headers.get("x-request-id"));
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    const text = (await response.text()).trim();
    return withRequestId(text || fallback, headerRequestId, response.status);
  }

  try {
    const payload = (await response.json()) as ApiErrorPayload;
    const requestId = headerRequestId ?? safeRequestId(payload.request_id);
    const message = detailToMessage(payload.detail) ?? payload.message ?? fallback;
    return withRequestId(message, requestId, response.status);
  } catch {
    return withRequestId(fallback, headerRequestId, response.status);
  }
}

function safeRequestId(value: string | undefined | null): string | null {
  if (!value || !/^[A-Za-z0-9._-]{8,64}$/.test(value)) return null;
  return value;
}

function withRequestId(
  message: string,
  requestId: string | null,
  status: number,
): { message: string; requestId: string | null } {
  return {
    message:
      requestId && status >= 500 ? `${message} Request ID: ${requestId}` : message,
    requestId,
  };
}

function detailToMessage(detail: ApiErrorDetail | undefined): string | undefined {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => detailToMessage(item)).filter((item) => item);
    return messages.length > 0 ? messages.join("; ") : undefined;
  }
  return detail?.msg ?? detail?.message;
}
