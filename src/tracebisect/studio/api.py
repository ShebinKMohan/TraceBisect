"""FastAPI application for TraceBisect Studio."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, cast
from urllib.parse import urlsplit

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from starlette.responses import JSONResponse

from tracebisect.schema import JsonObject, JsonValue, TraceBisectSchemaError
from tracebisect.studio.access_keys import (
    MAX_ACTIVE_STUDIO_API_KEYS_PER_WORKSPACE,
    MAX_KEY_LABEL_LENGTH,
    MAX_KEY_LIFETIME_DAYS,
    StudioApiKeyError,
    StudioApiKeyRecord,
    WorkspaceRole,
)
from tracebisect.studio.access_sessions import (
    LOCAL_BROWSER_SESSION_COOKIE_NAME,
    SECURE_BROWSER_SESSION_COOKIE_NAME,
    StudioBrowserSessionError,
)
from tracebisect.studio.audit import AuditAuthOutcome, StudioAudit
from tracebisect.studio.auth import StudioAuthConfig
from tracebisect.studio.email_delivery import (
    MAX_EMAIL_DELIVERY_ATTEMPTS,
    MAX_STORED_EMAIL_MESSAGES_PER_WORKSPACE,
    MAX_STORED_WEBHOOK_EVENTS,
    MAX_WEBHOOK_BODY_BYTES,
    StudioEmailDelivery,
    StudioEmailDeliveryConflict,
    StudioEmailDeliveryError,
    StudioEmailDeliveryNotFound,
    StudioEmailDeliveryRecord,
    StudioEmailWebhookInvalid,
)
from tracebisect.studio.error_reporting import StudioErrorReporter
from tracebisect.studio.identity import (
    MAX_ACTIVE_INVITATIONS_PER_WORKSPACE,
    MAX_ACTIVE_MEMBERS_PER_WORKSPACE,
    MAX_DISPLAY_NAME_LENGTH,
    MAX_EMAIL_LENGTH,
    MAX_INVITATION_LIFETIME_DAYS,
    MAX_PASSWORD_LENGTH,
    MAX_STORED_INVITATIONS_PER_WORKSPACE,
    MIN_PASSWORD_LENGTH,
    StudioIdentityConflict,
    StudioIdentityError,
    StudioIdentityInvalidCredentials,
    StudioIdentityNotFound,
    StudioInvitationRecord,
    StudioMembershipRecord,
)
from tracebisect.studio.managed_database import StudioManagedDatabase
from tracebisect.studio.metrics import (
    PROMETHEUS_CONTENT_TYPE,
    StudioMetrics,
    StudioMetricsAccess,
)
from tracebisect.studio.service import (
    DEFAULT_SCENARIO_CMD,
    StudioStore,
    StudioStoreFullError,
    build_comparison_report,
    load_trace_from_path,
    seed_demo_report,
)
from tracebisect.studio.storage import (
    StudioConfigurationError,
    StudioPersistenceError,
    StudioStoreRegistry,
)


def _configured_allowed_origins(raw: str | None = None) -> list[str]:
    configured = (
        os.getenv("TRACEBISECT_STUDIO_ALLOWED_ORIGINS", "") if raw is None else raw
    ).strip()
    origins = (
        [item.strip() for item in configured.split(",") if item.strip()]
        if configured
        else [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )
    if not origins:
        raise StudioConfigurationError(
            "TRACEBISECT_STUDIO_ALLOWED_ORIGINS must contain at least one origin"
        )
    normalized: list[str] = []
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise StudioConfigurationError(
                "TRACEBISECT_STUDIO_ALLOWED_ORIGINS must contain only HTTP(S) origins "
                "without paths, credentials, queries, or fragments"
            )
        normalized.append(f"{parsed.scheme}://{parsed.netloc}")
    return list(dict.fromkeys(normalized))


def _positive_env_int(name: str, default: int, raw: str | None = None) -> int:
    configured = os.getenv(name, str(default)) if raw is None else raw
    try:
        value = int(configured)
    except ValueError as exc:
        raise StudioConfigurationError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise StudioConfigurationError(f"{name} must be a positive integer")
    return value


def _configured_secure_session_cookie(
    origins: list[str],
    raw: str | None = None,
) -> bool:
    configured = (
        (
            os.getenv("TRACEBISECT_STUDIO_BROWSER_SESSION_COOKIE_SECURE", "auto")
            if raw is None
            else raw
        )
        .strip()
        .lower()
    )
    if configured == "true":
        return True
    if configured == "false":
        return False
    if configured != "auto":
        raise StudioConfigurationError(
            "TRACEBISECT_STUDIO_BROWSER_SESSION_COOKIE_SECURE must be auto, true, or false"
        )
    parsed_origins = [urlsplit(origin) for origin in origins]
    if all(origin.scheme == "https" for origin in parsed_origins):
        return True
    loopback_hosts = {"localhost", "127.0.0.1", "::1"}
    if all(
        origin.scheme == "http" and origin.hostname in loopback_hosts for origin in parsed_origins
    ):
        return False
    raise StudioConfigurationError(
        "mixed or non-loopback HTTP origins require an explicit "
        "TRACEBISECT_STUDIO_BROWSER_SESSION_COOKIE_SECURE setting"
    )


def _browser_session_cookie_name(*, secure: bool) -> str:
    return SECURE_BROWSER_SESSION_COOKIE_NAME if secure else LOCAL_BROWSER_SESSION_COOKIE_NAME


MAX_UPLOAD_BYTES = _positive_env_int(
    "TRACEBISECT_STUDIO_MAX_UPLOAD_BYTES",
    5 * 1024 * 1024,
)
MAX_FILENAME_LENGTH = 120
MAX_SCENARIO_CMD_ITEMS = 16
MAX_SCENARIO_CMD_ITEM_LENGTH = 240
RATE_LIMIT_WINDOW_SECONDS = _positive_env_int(
    "TRACEBISECT_STUDIO_RATE_LIMIT_WINDOW_SECONDS",
    60,
)
RATE_LIMIT_REQUESTS = _positive_env_int(
    "TRACEBISECT_STUDIO_RATE_LIMIT_REQUESTS",
    180,
)
RATE_LIMIT_UPLOAD_REQUESTS = _positive_env_int(
    "TRACEBISECT_STUDIO_UPLOAD_RATE_LIMIT_REQUESTS",
    30,
)
ALLOWED_UPLOAD_SUFFIXES = frozenset({".tbtrace", ".json"})
UPLOAD_CHUNK_BYTES = 1024 * 1024
VALID_RUN_STATUSES = frozenset({"passing", "failing"})
VALID_RUN_SEVERITIES = frozenset({"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"})
BROWSER_SESSION_EXCHANGE_PATH = "/api/browser-session"
BROWSER_SESSION_LOGOUT_PATH = "/api/browser-session/logout"
IDENTITY_LOGIN_PATH = "/api/identity/login"
IDENTITY_INVITATION_PREVIEW_PATH = "/api/identity/invitation-preview"
IDENTITY_INVITATION_ACCEPT_PATH = "/api/identity/invitations/accept"
IDENTITY_RECOVERY_PATH = "/api/identity/recover"
TEAM_MEMBERS_API_PATH = "/api/team/members"
TEAM_INVITATIONS_API_PATH = "/api/team/invitations"
RESEND_WEBHOOK_API_PATH = "/api/webhooks/resend"
PUBLIC_API_PATHS = frozenset(
    {
        "/api/health",
        "/api/ready",
        BROWSER_SESSION_LOGOUT_PATH,
        IDENTITY_LOGIN_PATH,
        IDENTITY_INVITATION_PREVIEW_PATH,
        IDENTITY_INVITATION_ACCEPT_PATH,
        IDENTITY_RECOVERY_PATH,
        RESEND_WEBHOOK_API_PATH,
    }
)
VIEWER_BLOCKED_GET_PATHS = frozenset({"/api/demo-report"})
METRICS_API_PATH = "/api/metrics"
ALLOWED_ORIGINS = _configured_allowed_origins()
BROWSER_SESSION_COOKIE_SECURE = _configured_secure_session_cookie(ALLOWED_ORIGINS)
BROWSER_SESSION_COOKIE_NAME = _browser_session_cookie_name(secure=BROWSER_SESSION_COOKIE_SECURE)
BROWSER_CSRF_HEADER = "X-TraceBisect-CSRF"
BROWSER_CSRF_VALUE = "1"
ACCESS_KEYS_API_PATH = "/api/access-keys"
IDENTITY_RATE_LIMIT_REQUESTS = _positive_env_int(
    "TRACEBISECT_STUDIO_IDENTITY_RATE_LIMIT_REQUESTS",
    10,
)
IDENTITY_RECOVERY_RATE_LIMIT_REQUESTS = _positive_env_int(
    "TRACEBISECT_STUDIO_IDENTITY_RECOVERY_RATE_LIMIT_REQUESTS",
    5,
)

app = FastAPI(
    title="TraceBisect Studio API",
    version="0.1.0",
    description="SaaS-style API around the TraceBisect trace regression engine.",
)

STORE_REGISTRY = StudioStoreRegistry()
STORE: StudioStore = STORE_REGISTRY.default_store
AUTH_CONFIG = StudioAuthConfig.from_env(
    managed_database=STORE if isinstance(STORE, StudioManagedDatabase) else None,
)
AUDIT = StudioAudit.from_env()
ERROR_REPORTER = StudioErrorReporter.from_env()
EMAIL_DELIVERY = StudioEmailDelivery.from_env()
METRICS = StudioMetrics()
METRICS_ACCESS = StudioMetricsAccess.from_env()


def _request_store(request: Request) -> StudioStore:
    workspace_id = getattr(request.state, "workspace_id", STORE.workspace_id)
    if not isinstance(workspace_id, str):
        raise StudioPersistenceError("request workspace context is invalid")
    return STORE_REGISTRY.get(workspace_id)


StudioStoreDependency = Annotated[StudioStore, Depends(_request_store)]


class CompareRequest(BaseModel):
    baseline_trace_id: str = Field(min_length=1, max_length=256)
    candidate_trace_id: str = Field(min_length=1, max_length=256)
    scenario_cmd: list[str] | None = None


class RegressionCaseCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=24)
    baseline_trace_id: str = Field(min_length=1, max_length=256)
    candidate_trace_id: str = Field(min_length=1, max_length=256)
    scenario_cmd: list[str] | None = None
    assertions: list[str] = Field(default_factory=lambda: ["tool_args", "final_output", "cost"])
    cost_threshold: float = Field(default=1.5, ge=0)


class RegressionCaseRunRequest(BaseModel):
    candidate_trace_id: str = Field(min_length=1, max_length=256)
    scenario_cmd: list[str] | None = None


class AccessKeyCreateRequest(BaseModel):
    label: str = Field(
        min_length=1,
        max_length=MAX_KEY_LABEL_LENGTH,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    )
    role: WorkspaceRole
    expires_in_days: int = Field(default=90, ge=1, le=MAX_KEY_LIFETIME_DAYS)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Enter who or what will use this key.")
        return normalized


class IdentityLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=MAX_EMAIL_LENGTH)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=64)


class InvitationPreviewRequest(BaseModel):
    invitation_token: str = Field(min_length=1, max_length=96)


class InvitationAcceptRequest(InvitationPreviewRequest):
    display_name: str = Field(min_length=1, max_length=MAX_DISPLAY_NAME_LENGTH)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class IdentityRecoveryRequest(BaseModel):
    email: str = Field(min_length=3, max_length=MAX_EMAIL_LENGTH)
    recovery_code: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class InvitationCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=MAX_EMAIL_LENGTH)
    role: WorkspaceRole
    expires_in_days: int = Field(default=7, ge=1, le=MAX_INVITATION_LIFETIME_DAYS)


class MembershipUpdateRequest(BaseModel):
    role: WorkspaceRole


class RateLimiter:
    """Small per-process sliding-window limiter for the local Studio API."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = time.monotonic()
        async with self._lock:
            bucket = self._hits.setdefault(key, deque())
            while bucket and now - bucket[0] >= window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(window_seconds - (now - bucket[0])))
                return False, retry_after
            bucket.append(now)
            return True, 0

    async def reset(self) -> None:
        async with self._lock:
            self._hits.clear()


RATE_LIMITER = RateLimiter()


@app.exception_handler(StudioPersistenceError)
async def handle_persistence_error(
    _request: Request,
    _exc: StudioPersistenceError,
) -> JSONResponse:
    """Return a stable message without leaking database paths or SQL details."""
    return JSONResponse(
        status_code=503,
        content={"detail": "Studio storage is temporarily unavailable. Please retry."},
    )


@app.middleware("http")
async def apply_api_guardrails(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    started_at = time.perf_counter()
    METRICS.request_started()
    request_id = AUDIT.request_id(request.headers.get("X-Request-ID"))
    request.state.request_id = request_id
    auth_outcome: AuditAuthOutcome = "not_checked"
    audit_workspace_id: str | None = None
    if request.method != "OPTIONS":
        limit = (
            RATE_LIMIT_UPLOAD_REQUESTS
            if request.url.path == "/api/traces/upload"
            else RATE_LIMIT_REQUESTS
        )
        allowed, retry_after = await RATE_LIMITER.allow(
            _rate_limit_key(request),
            limit=limit,
            window_seconds=RATE_LIMIT_WINDOW_SECONDS,
        )
        if not allowed:
            limited_response = JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please wait before retrying."},
                headers={"Retry-After": str(retry_after)},
            )
            return _finalize_audited_response(
                request=request,
                response=limited_response,
                started_at=started_at,
                request_id=request_id,
                auth_outcome=auth_outcome,
                workspace_id=audit_workspace_id,
            )

        if request.url.path == METRICS_API_PATH:
            metrics_authorized = METRICS_ACCESS.authorizes(
                request.headers.get("Authorization"),
                auth_required=AUTH_CONFIG.required,
            )
            if not metrics_authorized:
                auth_outcome = "rejected"
                status_code = 401 if METRICS_ACCESS.token_configured else 503
                detail = (
                    "A valid Studio metrics bearer token is required."
                    if METRICS_ACCESS.token_configured
                    else "Studio metrics access is not configured for secured mode."
                )
                metrics_denied_response = JSONResponse(
                    status_code=status_code,
                    content={"detail": detail},
                    headers={"WWW-Authenticate": "Bearer"} if status_code == 401 else None,
                )
                return _finalize_audited_response(
                    request=request,
                    response=metrics_denied_response,
                    started_at=started_at,
                    request_id=request_id,
                    auth_outcome=auth_outcome,
                    workspace_id=None,
                )
            request.state.workspace_id = STORE.workspace_id
            request.state.workspace_role = "admin"
            auth_outcome = "authenticated" if METRICS_ACCESS.token_configured else "not_required"
        elif AUTH_CONFIG.required and request.url.path not in PUBLIC_API_PATHS:
            authorization = request.headers.get("Authorization")
            session_token = request.cookies.get(BROWSER_SESSION_COOKIE_NAME)
            principal = (
                AUTH_CONFIG.principal_for_authorization(authorization)
                if authorization is not None
                else AUTH_CONFIG.principal_for_browser_session(session_token)
            )
            if principal is None:
                auth_outcome = "rejected"
                unauthorized_response = JSONResponse(
                    status_code=401,
                    content={
                        "detail": (
                            "A valid Studio browser session or workspace API key is required."
                        )
                    },
                    headers={"WWW-Authenticate": "Bearer"},
                )
                if authorization is None and session_token is not None:
                    _delete_browser_session_cookie(unauthorized_response)
                return _finalize_audited_response(
                    request=request,
                    response=unauthorized_response,
                    started_at=started_at,
                    request_id=request_id,
                    auth_outcome=auth_outcome,
                    workspace_id=audit_workspace_id,
                )
            request.state.workspace_id = principal.workspace_id
            request.state.workspace_role = principal.role
            request.state.key_id = principal.key_id
            request.state.auth_kind = principal.auth_kind
            request.state.session_id = principal.session_id
            request.state.session_expires_at = principal.expires_at
            request.state.user_id = principal.user_id
            request.state.user_email = principal.email
            request.state.user_display_name = principal.display_name
            auth_outcome = "authenticated"
            audit_workspace_id = principal.workspace_id
            if (
                principal.auth_kind in {"browser_session", "identity_session"}
                and request.method not in {"GET", "HEAD", "OPTIONS"}
                and (
                    not _origin_is_allowed(request.headers.get("Origin"))
                    or request.headers.get(BROWSER_CSRF_HEADER) != BROWSER_CSRF_VALUE
                )
            ):
                forbidden_origin_response = JSONResponse(
                    status_code=403,
                    content={"detail": "This browser request failed Studio's origin protection."},
                )
                return _finalize_audited_response(
                    request=request,
                    response=forbidden_origin_response,
                    started_at=started_at,
                    request_id=request_id,
                    auth_outcome=auth_outcome,
                    workspace_id=audit_workspace_id,
                )
            if not _role_allows_request(
                role=principal.role,
                method=request.method,
                path=request.url.path,
            ):
                forbidden_response = JSONResponse(
                    status_code=403,
                    content={
                        "detail": _role_denied_detail(
                            principal.role,
                            path=request.url.path,
                        )
                    },
                )
                return _finalize_audited_response(
                    request=request,
                    response=forbidden_response,
                    started_at=started_at,
                    request_id=request_id,
                    auth_outcome=auth_outcome,
                    workspace_id=audit_workspace_id,
                )
        else:
            request.state.workspace_id = STORE.workspace_id
            request.state.workspace_role = "admin"
            request.state.key_id = None
            request.state.auth_kind = "open_local"
            if AUTH_CONFIG.required:
                auth_outcome = "public"
            else:
                auth_outcome = "not_required"
                audit_workspace_id = STORE.workspace_id

    try:
        response = await call_next(request)
    except Exception as exc:
        ERROR_REPORTER.emit_unhandled(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            workspace_id=audit_workspace_id,
            error=exc,
        )
        failure_response = JSONResponse(
            status_code=500,
            content={
                "detail": "Studio hit an unexpected error. Share the request ID with support.",
                "request_id": request_id,
            },
        )
        return _finalize_audited_response(
            request=request,
            response=failure_response,
            started_at=started_at,
            request_id=request_id,
            auth_outcome=auth_outcome,
            workspace_id=audit_workspace_id,
        )
    return _finalize_audited_response(
        request=request,
        response=response,
        started_at=started_at,
        request_id=request_id,
        auth_outcome=auth_outcome,
        workspace_id=audit_workspace_id,
    )


def _finalize_audited_response(
    *,
    request: Request,
    response: Response,
    started_at: float,
    request_id: str,
    auth_outcome: AuditAuthOutcome,
    workspace_id: str | None,
) -> Response:
    duration_seconds = time.perf_counter() - started_at
    response.headers["X-Request-ID"] = request_id
    _apply_security_headers(response)
    AUDIT.emit_request(
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_seconds * 1000,
        auth_outcome=auth_outcome,
        workspace_id=workspace_id,
    )
    METRICS.request_finished(
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_seconds=duration_seconds,
        auth_outcome=auth_outcome,
    )
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", response_model=None)
def health() -> JsonObject:
    storage_ok = STORE.check_health()
    if not storage_ok:
        runtime = _unavailable_runtime_status()
    else:
        try:
            runtime = STORE.runtime_status()
        except StudioPersistenceError:
            storage_ok = False
            runtime = _unavailable_runtime_status()
    return {
        "ok": storage_ok,
        "product": "TraceBisect Studio",
        "auth": cast(
            JsonValue,
            {
                **AUTH_CONFIG.runtime_status(),
                "browser_session_cookie_secure": BROWSER_SESSION_COOKIE_SECURE,
            },
        ),
        "audit": {
            "enabled": AUDIT.enabled,
            "format": "json",
            "request_id_header": "X-Request-ID",
        },
        "errors": cast(JsonValue, ERROR_REPORTER.runtime_status()),
        "email": cast(JsonValue, EMAIL_DELIVERY.runtime_status()),
        "metrics": {
            "format": "prometheus_text_0.0.4",
            "path": METRICS_API_PATH,
            "access": METRICS_ACCESS.access_mode(auth_required=AUTH_CONFIG.required),
            "scope": "process",
            "resets_on_restart": True,
        },
        "runtime": _public_runtime_status(runtime),
        "readiness": _production_readiness(storage_ok=storage_ok, runtime=runtime),
        "limits": {
            "max_upload_bytes": MAX_UPLOAD_BYTES,
            "max_stored_traces": STORE.max_traces,
            "max_stored_reports": STORE.max_reports,
            "max_stored_cases": STORE.max_cases,
            "rate_limit_window_seconds": RATE_LIMIT_WINDOW_SECONDS,
            "rate_limit_requests": RATE_LIMIT_REQUESTS,
            "rate_limit_upload_requests": RATE_LIMIT_UPLOAD_REQUESTS,
            "max_active_workspace_keys": MAX_ACTIVE_STUDIO_API_KEYS_PER_WORKSPACE,
            "max_workspace_members": MAX_ACTIVE_MEMBERS_PER_WORKSPACE,
            "max_pending_workspace_invitations": MAX_ACTIVE_INVITATIONS_PER_WORKSPACE,
            "max_stored_workspace_invitations": MAX_STORED_INVITATIONS_PER_WORKSPACE,
            "identity_rate_limit_requests": IDENTITY_RATE_LIMIT_REQUESTS,
            "identity_recovery_rate_limit_requests": IDENTITY_RECOVERY_RATE_LIMIT_REQUESTS,
            "max_email_delivery_attempts": (
                MAX_EMAIL_DELIVERY_ATTEMPTS if EMAIL_DELIVERY.enabled else 0
            ),
            "max_stored_email_messages_per_workspace": (
                MAX_STORED_EMAIL_MESSAGES_PER_WORKSPACE if EMAIL_DELIVERY.enabled else 0
            ),
            "max_stored_email_webhook_events": (
                MAX_STORED_WEBHOOK_EVENTS
                if EMAIL_DELIVERY.config.webhooks_enabled
                else 0
            ),
        },
    }


@app.get("/api/ready", response_model=None)
def ready(response: Response) -> JsonObject:
    """Report whether this API process and its configured store can serve traffic."""
    storage_ok = STORE.check_health()
    if not storage_ok:
        response.status_code = 503
    return {
        "ready": storage_ok,
        "checks": {"storage": "ok" if storage_ok else "unavailable"},
    }


@app.post(RESEND_WEBHOOK_API_PATH, response_model=None, include_in_schema=False)
async def receive_resend_webhook(request: Request) -> JsonObject:
    """Verify and reconcile a raw Resend/Svix delivery event."""
    if not EMAIL_DELIVERY.config.webhooks_enabled:
        raise HTTPException(status_code=404, detail="Webhook endpoint is not configured.")
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="Webhook content type must be JSON.")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_WEBHOOK_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Webhook body is too large.")
        chunks.append(chunk)
    try:
        result = EMAIL_DELIVERY.process_webhook(b"".join(chunks), request.headers)
    except StudioEmailWebhookInvalid as exc:
        raise HTTPException(
            status_code=400,
            detail="Webhook signature or payload is invalid.",
        ) from exc
    except StudioEmailDeliveryError as exc:
        raise HTTPException(
            status_code=503,
            detail="Studio could not record the email delivery event.",
        ) from exc
    return {
        "received": True,
        "duplicate": result.duplicate,
        "matched": result.matched,
    }


@app.get(METRICS_API_PATH, response_model=None, include_in_schema=False)
def metrics() -> Response:
    """Expose low-cardinality service metrics without workspace or resource labels."""
    return Response(
        content=METRICS.render_prometheus(storage_ready=STORE.check_health()),
        media_type=PROMETHEUS_CONTENT_TYPE,
    )


@app.get("/api/session", response_model=None)
def session(request: Request, store: StudioStoreDependency) -> JsonObject:
    """Return the authenticated workspace and its actual storage behavior."""
    return _session_payload(request, store)


@app.post(BROWSER_SESSION_EXCHANGE_PATH, response_model=None)
def create_browser_session(
    request: Request,
    response: Response,
    store: StudioStoreDependency,
) -> JsonObject:
    """Exchange a managed workspace key for a short-lived HttpOnly session."""
    origin = request.headers.get("Origin")
    if origin is not None and not _origin_is_allowed(origin):
        raise HTTPException(
            status_code=403,
            detail="This sign-in request did not come from an allowed Studio origin.",
        )
    if not AUTH_CONFIG.browser_sessions_enabled:
        raise HTTPException(
            status_code=409,
            detail="Managed workspace keys are required for browser sessions.",
        )
    try:
        issued = AUTH_CONFIG.issue_browser_session(request.headers.get("Authorization"))
    except StudioBrowserSessionError as exc:
        raise HTTPException(
            status_code=503,
            detail="Studio could not create a browser session. Please retry.",
        ) from exc
    if issued is None:
        raise HTTPException(status_code=401, detail="A current workspace key is required.")
    max_age = _session_cookie_max_age(issued.principal.expires_at)
    _set_browser_session_cookie(
        response,
        session_token=issued.session_token,
        max_age=max_age,
    )
    payload = _session_payload(request, store)
    payload["access_mode"] = "browser_session"
    payload["expires_at"] = issued.principal.expires_at
    return payload


@app.post(BROWSER_SESSION_LOGOUT_PATH, response_model=None)
def logout_browser_session(request: Request) -> Response:
    """Revoke the current browser session and clear its HttpOnly cookie."""
    if (
        not _origin_is_allowed(request.headers.get("Origin"))
        or request.headers.get(BROWSER_CSRF_HEADER) != BROWSER_CSRF_VALUE
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "This sign-out request did not come from an allowed Studio origin."},
        )
    session_token = request.cookies.get(BROWSER_SESSION_COOKIE_NAME)
    try:
        AUTH_CONFIG.revoke_browser_session(session_token)
    except StudioBrowserSessionError:
        return JSONResponse(
            status_code=503,
            content={"detail": "Studio could not close this browser session. Please retry."},
        )
    response = Response(status_code=204)
    _delete_browser_session_cookie(response)
    return response


@app.post(IDENTITY_LOGIN_PATH, response_model=None)
async def login_identity(
    payload: IdentityLoginRequest, request: Request, response: Response
) -> Response | JsonObject:
    """Sign a person into one workspace without exposing membership to invalid credentials."""
    _require_identity()
    _require_identity_origin(request, action="sign-in")
    await _require_identity_rate_limit(
        request,
        subject=payload.email,
        limit=IDENTITY_RATE_LIMIT_REQUESTS,
    )
    try:
        result = AUTH_CONFIG.login_identity(
            email=payload.email,
            password=payload.password,
            workspace_id=payload.workspace_id,
        )
    except StudioIdentityInvalidCredentials as exc:
        raise HTTPException(
            status_code=401,
            detail="The email, password, or workspace was not accepted.",
        ) from exc
    except (StudioIdentityError, StudioApiKeyError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Studio could not sign you in. Please retry.",
        ) from exc
    if result.session is None:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Choose the workspace you want to open.",
                "workspaces": [
                    {"workspace_id": membership.workspace_id, "role": membership.role}
                    for membership in result.workspaces
                ],
            },
        )
    principal = result.session.principal
    _set_browser_session_cookie(
        response,
        session_token=result.session.session_token,
        max_age=_session_cookie_max_age(principal.expires_at),
    )
    request.state.workspace_id = principal.workspace_id
    request.state.workspace_role = principal.role
    request.state.key_id = None
    request.state.auth_kind = "identity_session"
    request.state.session_id = principal.session_id
    request.state.session_expires_at = principal.expires_at
    request.state.user_id = principal.user_id
    request.state.user_email = principal.email
    request.state.user_display_name = principal.display_name
    return _session_payload(request, STORE_REGISTRY.get(principal.workspace_id))


@app.post(IDENTITY_INVITATION_PREVIEW_PATH, response_model=None)
async def preview_identity_invitation(
    payload: InvitationPreviewRequest,
    request: Request,
) -> JsonObject:
    """Show invitation metadata while keeping the secret token out of URLs and logs."""
    _require_identity()
    await _require_identity_rate_limit(
        request,
        subject=payload.invitation_token,
        limit=IDENTITY_RATE_LIMIT_REQUESTS,
    )
    try:
        record = AUTH_CONFIG.preview_invitation(payload.invitation_token)
    except StudioIdentityInvalidCredentials as exc:
        raise HTTPException(
            status_code=404, detail="This invitation is invalid or no longer active."
        ) from exc
    except (StudioIdentityError, StudioApiKeyError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Studio could not load this invitation. Please retry.",
        ) from exc
    return {"invitation": _invitation_payload(record)}


@app.post(IDENTITY_INVITATION_ACCEPT_PATH, response_model=None, status_code=201)
async def accept_identity_invitation(
    payload: InvitationAcceptRequest,
    request: Request,
) -> JsonObject:
    """Create or extend an account; never create a session implicitly."""
    _require_identity()
    _require_identity_origin(request, action="invitation")
    await _require_identity_rate_limit(
        request,
        subject=payload.invitation_token,
        limit=IDENTITY_RATE_LIMIT_REQUESTS,
    )
    try:
        accepted = AUTH_CONFIG.accept_invitation(
            invitation_token=payload.invitation_token,
            display_name=payload.display_name,
            password=payload.password,
        )
    except StudioIdentityInvalidCredentials as exc:
        raise HTTPException(
            status_code=404, detail="This invitation is invalid or no longer active."
        ) from exc
    except StudioIdentityConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (StudioIdentityError, StudioApiKeyError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Studio could not accept this invitation. Please retry.",
        ) from exc
    return {
        "accepted": True,
        "workspace_id": accepted.membership.workspace_id,
        "email": accepted.user.email,
        "recovery_codes": list(accepted.recovery_codes),
        "sign_in_required": True,
    }


@app.post(IDENTITY_RECOVERY_PATH, response_model=None)
async def recover_identity_account(
    payload: IdentityRecoveryRequest,
    request: Request,
) -> JsonObject:
    """Use one saved recovery code, revoke all sessions, and require a fresh sign-in."""
    _require_identity()
    _require_identity_origin(request, action="recovery")
    await _require_identity_rate_limit(
        request,
        subject=payload.email,
        limit=IDENTITY_RECOVERY_RATE_LIMIT_REQUESTS,
    )
    try:
        result = AUTH_CONFIG.recover_identity(
            email=payload.email,
            recovery_code=payload.recovery_code,
            new_password=payload.new_password,
        )
    except (StudioIdentityError, StudioApiKeyError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Studio could not process account recovery. Please retry.",
        ) from exc
    return {
        "accepted": result.accepted,
        "message": (
            "Password changed. Save the replacement recovery codes, then sign in again."
            if result.accepted
            else "If the email and recovery code were valid, the password was changed."
        ),
        "recovery_codes": list(result.recovery_codes),
        "sign_in_required": True,
    }


@app.get(TEAM_MEMBERS_API_PATH, response_model=None)
def list_workspace_members(request: Request) -> JsonObject:
    workspace_id, _current_key_id = _admin_access_context(request)
    _require_identity()
    try:
        members = AUTH_CONFIG.list_workspace_members(workspace_id)
    except (StudioIdentityError, StudioApiKeyError) as exc:
        raise HTTPException(
            status_code=503, detail="Studio could not load workspace members."
        ) from exc
    return {
        "members": cast(JsonValue, [_membership_payload(member) for member in members]),
        "current_user_id": _request_user_id(request),
    }


@app.patch(f"{TEAM_MEMBERS_API_PATH}/{{user_id}}", response_model=None)
def update_workspace_member(
    user_id: str,
    payload: MembershipUpdateRequest,
    request: Request,
) -> JsonObject:
    workspace_id, _current_key_id = _admin_access_context(request)
    _require_identity()
    try:
        member = AUTH_CONFIG.update_workspace_member(
            workspace_id=workspace_id,
            user_id=user_id,
            role=payload.role,
        )
    except StudioIdentityNotFound as exc:
        raise HTTPException(status_code=404, detail="Workspace member not found.") from exc
    except StudioIdentityConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (StudioIdentityError, StudioApiKeyError) as exc:
        raise HTTPException(status_code=503, detail="Studio could not update this member.") from exc
    return {"member": _membership_payload(member)}


@app.delete(f"{TEAM_MEMBERS_API_PATH}/{{user_id}}", response_model=None)
def remove_workspace_member(user_id: str, request: Request) -> JsonObject:
    workspace_id, _current_key_id = _admin_access_context(request)
    _require_identity()
    if user_id == _request_user_id(request):
        raise HTTPException(
            status_code=409,
            detail="You cannot remove your current account from this workspace.",
        )
    try:
        member = AUTH_CONFIG.remove_workspace_member(
            workspace_id=workspace_id,
            user_id=user_id,
        )
    except StudioIdentityNotFound as exc:
        raise HTTPException(status_code=404, detail="Workspace member not found.") from exc
    except StudioIdentityConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (StudioIdentityError, StudioApiKeyError) as exc:
        raise HTTPException(status_code=503, detail="Studio could not remove this member.") from exc
    return {"member": _membership_payload(member)}


@app.get(TEAM_INVITATIONS_API_PATH, response_model=None)
def list_workspace_invitations(request: Request) -> JsonObject:
    workspace_id, _current_key_id = _admin_access_context(request)
    _require_identity()
    try:
        invitations = AUTH_CONFIG.list_workspace_invitations(workspace_id)
        delivery_records = EMAIL_DELIVERY.latest_for_workspace(workspace_id)
    except (StudioIdentityError, StudioApiKeyError, StudioEmailDeliveryError) as exc:
        raise HTTPException(status_code=503, detail="Studio could not load invitations.") from exc
    return {
        "invitations": cast(
            JsonValue,
            [
                {
                    **_invitation_payload(item),
                    "delivery": _email_delivery_payload(
                        delivery_records.get(item.invitation_id)
                    ),
                }
                for item in invitations
            ],
        )
    }


@app.post(TEAM_INVITATIONS_API_PATH, response_model=None, status_code=201)
def create_workspace_invitation(
    payload: InvitationCreateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> JsonObject:
    workspace_id, _current_key_id = _admin_access_context(request)
    _require_identity()
    try:
        issued = AUTH_CONFIG.create_workspace_invitation(
            workspace_id=workspace_id,
            email=payload.email,
            role=payload.role,
            expires_in_days=payload.expires_in_days,
        )
    except StudioIdentityConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StudioIdentityError as exc:
        if str(exc).startswith("enter a valid email"):
            raise HTTPException(status_code=400, detail="Enter a valid email address.") from exc
        raise HTTPException(
            status_code=503, detail="Studio could not create the invitation."
        ) from exc
    except StudioApiKeyError as exc:
        raise HTTPException(
            status_code=503, detail="Studio could not create the invitation."
        ) from exc
    delivery: StudioEmailDeliveryRecord | None = None
    if EMAIL_DELIVERY.enabled:
        try:
            delivery = EMAIL_DELIVERY.queue_invitation(issued)
        except StudioEmailDeliveryError:
            delivery = None
        else:
            background_tasks.add_task(_deliver_invitation_email, delivery.message_id)
    return {
        "invitation_token": None if delivery is not None else issued.invitation_token,
        "invitation": {
            **_invitation_payload(issued.record),
            "delivery": _email_delivery_payload(delivery),
        },
        "delivery": _email_delivery_payload(delivery),
    }


@app.post(
    f"{TEAM_INVITATIONS_API_PATH}/{{invitation_id}}/resend",
    response_model=None,
)
def resend_workspace_invitation(
    invitation_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> JsonObject:
    workspace_id, _current_key_id = _admin_access_context(request)
    _require_identity()
    if not EMAIL_DELIVERY.enabled:
        raise HTTPException(
            status_code=409,
            detail="Automatic invitation email is not configured for this Studio.",
        )
    try:
        delivery = EMAIL_DELIVERY.requeue_invitation(
            workspace_id=workspace_id,
            invitation_id=invitation_id,
        )
    except StudioEmailDeliveryNotFound as exc:
        raise HTTPException(
            status_code=409,
            detail="This invitation only has a manual link. Revoke it and create a new invite.",
        ) from exc
    except StudioEmailDeliveryConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StudioEmailDeliveryError as exc:
        raise HTTPException(
            status_code=503,
            detail="Studio could not queue another invitation email.",
        ) from exc
    background_tasks.add_task(_deliver_invitation_email, delivery.message_id)
    return {"delivery": _email_delivery_payload(delivery)}


@app.delete(f"{TEAM_INVITATIONS_API_PATH}/{{invitation_id}}", response_model=None)
def revoke_workspace_invitation(invitation_id: str, request: Request) -> JsonObject:
    workspace_id, _current_key_id = _admin_access_context(request)
    _require_identity()
    try:
        invitation = AUTH_CONFIG.revoke_workspace_invitation(
            workspace_id=workspace_id,
            invitation_id=invitation_id,
        )
    except StudioIdentityNotFound as exc:
        raise HTTPException(status_code=404, detail="Workspace invitation not found.") from exc
    except StudioIdentityConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (StudioIdentityError, StudioApiKeyError) as exc:
        raise HTTPException(
            status_code=503, detail="Studio could not revoke this invitation."
        ) from exc
    return {"invitation": _invitation_payload(invitation)}


def _session_payload(request: Request, store: StudioStore) -> JsonObject:
    workspace_id = getattr(request.state, "workspace_id", store.workspace_id)
    if not isinstance(workspace_id, str):
        raise StudioPersistenceError("request workspace context is invalid")
    workspace_role = getattr(request.state, "workspace_role", "admin")
    if workspace_role not in {"viewer", "editor", "admin"}:
        raise StudioPersistenceError("request workspace role is invalid")
    access_mode = getattr(
        request.state,
        "auth_kind",
        "api_key" if AUTH_CONFIG.required else "open_local",
    )
    if access_mode not in {"open_local", "api_key", "browser_session", "identity_session"}:
        raise StudioPersistenceError("request authentication context is invalid")
    session_expires_at = getattr(request.state, "session_expires_at", None)
    if session_expires_at is not None and not isinstance(session_expires_at, str):
        raise StudioPersistenceError("request session expiry is invalid")
    user_id = getattr(request.state, "user_id", None)
    user_email = getattr(request.state, "user_email", None)
    user_display_name = getattr(request.state, "user_display_name", None)
    if any(
        value is not None and not isinstance(value, str)
        for value in (user_id, user_email, user_display_name)
    ):
        raise StudioPersistenceError("request user context is invalid")
    user: JsonObject | None = None
    if (
        isinstance(user_id, str)
        and isinstance(user_email, str)
        and isinstance(user_display_name, str)
    ):
        user = {
            "user_id": user_id,
            "email": user_email,
            "display_name": user_display_name,
        }
    return {
        "authenticated": AUTH_CONFIG.required,
        "workspace_id": workspace_id,
        "role": workspace_role,
        "access_mode": access_mode,
        "expires_at": session_expires_at,
        "user": user,
        "runtime": store.runtime_status(),
    }


@app.get(ACCESS_KEYS_API_PATH, response_model=None)
def list_workspace_access_keys(request: Request) -> JsonObject:
    """List only non-secret access metadata for the authenticated workspace."""
    workspace_id, current_key_id = _admin_access_context(request)
    _require_managed_access()
    try:
        records = AUTH_CONFIG.list_workspace_access_keys(workspace_id)
    except StudioApiKeyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Studio could not load workspace access. Please retry.",
        ) from exc
    return {
        "keys": cast(JsonValue, [_access_key_payload(record) for record in records]),
        "current_key_id": current_key_id,
    }


@app.post(ACCESS_KEYS_API_PATH, response_model=None, status_code=201)
def create_workspace_access_key(
    payload: AccessKeyCreateRequest,
    request: Request,
) -> JsonObject:
    """Issue one role-based key and return its plaintext value exactly once."""
    workspace_id, current_key_id = _admin_access_context(request)
    _require_managed_access()
    try:
        issued = AUTH_CONFIG.create_workspace_access_key(
            workspace_id=workspace_id,
            role=payload.role,
            label=payload.label,
            expires_in_days=payload.expires_in_days,
        )
    except StudioApiKeyError as exc:
        if str(exc).startswith("workspace already has the maximum"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This workspace has reached its active access-key limit. "
                    "Revoke an unused key before creating another."
                ),
            ) from exc
        raise HTTPException(
            status_code=503,
            detail="Studio could not create workspace access. Please retry.",
        ) from exc
    return {
        "api_key": issued.api_key,
        "record": _access_key_payload(issued.record),
        "current_key_id": current_key_id,
    }


@app.delete(f"{ACCESS_KEYS_API_PATH}/{{key_id}}", response_model=None)
def revoke_workspace_access_key(key_id: str, request: Request) -> JsonObject:
    """Revoke one workspace key while protecting the credential in current use."""
    workspace_id, current_key_id = _admin_access_context(request)
    _require_managed_access()
    if key_id == current_key_id:
        raise HTTPException(
            status_code=409,
            detail=(
                "This key opened your current session. Create a replacement, lock the workspace, "
                "sign in with the replacement, then revoke this key."
            ),
        )
    try:
        record = AUTH_CONFIG.revoke_workspace_access_key(
            workspace_id=workspace_id,
            key_id=key_id,
        )
    except StudioApiKeyError as exc:
        message = str(exc)
        if message.startswith("key ID must"):
            raise HTTPException(status_code=400, detail=message) from exc
        if message.startswith("no workspace access key"):
            raise HTTPException(status_code=404, detail="Workspace access key not found.") from exc
        raise HTTPException(
            status_code=503,
            detail="Studio could not revoke workspace access. Please retry.",
        ) from exc
    return {"key": _access_key_payload(record)}


@app.get("/api/demo-report", response_model=None)
def demo_report(store: StudioStoreDependency) -> JsonObject:
    return seed_demo_report(store)


@app.get("/api/traces", response_model=None)
def list_traces(store: StudioStoreDependency) -> JsonObject:
    return {"traces": cast(JsonValue, store.list_traces())}


@app.get("/api/runs", response_model=None)
def list_runs(
    store: StudioStoreDependency,
    q: Annotated[str | None, Query(max_length=120)] = None,
    status: Annotated[str | None, Query(max_length=32)] = None,
    severity: Annotated[str | None, Query(max_length=32)] = None,
    source_convention: Annotated[str | None, Query(max_length=32)] = None,
    divergence_type: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query()] = 50,
) -> JsonObject:
    _validate_run_filter("status", status, VALID_RUN_STATUSES)
    _validate_run_filter("severity", severity, VALID_RUN_SEVERITIES)
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100.")
    runs = _filtered_run_summaries(
        store.list_report_summaries(),
        q=q,
        status=status,
        severity=severity,
        source_convention=source_convention,
        divergence_type=divergence_type,
    )
    return {
        "runs": cast(JsonValue, runs[:limit]),
        "page": {"limit": limit, "next_cursor": None},
    }


@app.get("/api/runs/{report_id}", response_model=None)
def get_run_report(report_id: str, store: StudioStoreDependency) -> JsonObject:
    try:
        return store.get_report(report_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc


@app.post("/api/traces/upload", response_model=None)
async def upload_trace(
    file: Annotated[UploadFile, File()],
    store: StudioStoreDependency,
) -> JsonObject:
    filename = _safe_display_filename(file.filename)
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        await file.close()
        raise HTTPException(
            status_code=415,
            detail="Unsupported trace file type. Upload a .tbtrace or .json file.",
        )
    try:
        content = await _read_limited_upload(file)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            temp_path = Path(tmp.name)
        try:
            trace = load_trace_from_path(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)
    except (OSError, TraceBisectSchemaError) as exc:
        raise HTTPException(status_code=400, detail=_safe_error_message(exc)) from exc
    except StudioStoreFullError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    finally:
        await file.close()

    try:
        trace_key = store.add_trace(trace, name=filename)
    except StudioStoreFullError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    summary = next(item for item in store.list_traces() if item["id"] == trace_key)
    return {"trace": summary}


@app.post("/api/compare", response_model=None)
def compare_traces(request: CompareRequest, store: StudioStoreDependency) -> JsonObject:
    scenario = _validated_scenario_cmd(request.scenario_cmd)
    if request.baseline_trace_id == request.candidate_trace_id:
        raise HTTPException(
            status_code=400,
            detail="Baseline and candidate traces must be different.",
        )
    try:
        baseline = store.get_trace(request.baseline_trace_id)
        candidate = store.get_trace(request.candidate_trace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc

    report = build_comparison_report(
        baseline,
        candidate,
        baseline_name=store.get_trace_name(request.baseline_trace_id),
        candidate_name=store.get_trace_name(request.candidate_trace_id),
        scenario_cmd=scenario,
    )
    store.add_report(report)
    return report


@app.get("/api/regression-cases", response_model=None)
def list_regression_cases(store: StudioStoreDependency) -> JsonObject:
    return {"cases": cast(JsonValue, store.list_cases())}


@app.post("/api/regression-cases", response_model=None)
def create_regression_case(
    request: RegressionCaseCreateRequest,
    store: StudioStoreDependency,
) -> JsonObject:
    scenario = _validated_scenario_cmd(request.scenario_cmd)
    _validate_distinct_trace_ids(request.baseline_trace_id, request.candidate_trace_id)
    try:
        case = store.add_case_from_report(
            name=request.name,
            description=request.description,
            tags=_validated_string_items(request.tags, field_name="tags"),
            baseline_trace_id=request.baseline_trace_id,
            candidate_trace_id=request.candidate_trace_id,
            scenario_cmd=scenario,
            assertions=_validated_string_items(request.assertions, field_name="assertions"),
            cost_threshold=request.cost_threshold,
        )
    except StudioStoreFullError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    return {"case": case}


@app.get("/api/regression-cases/{case_id}", response_model=None)
def get_regression_case(case_id: str, store: StudioStoreDependency) -> JsonObject:
    try:
        return {"case": store.get_case(case_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc


@app.post("/api/regression-cases/{case_id}/run", response_model=None)
def run_regression_case(
    case_id: str,
    request: RegressionCaseRunRequest,
    store: StudioStoreDependency,
) -> JsonObject:
    scenario = _validated_scenario_cmd(request.scenario_cmd)
    try:
        existing = store.get_case(case_id)
        baseline_trace_id = _json_string(existing["baseline_trace_id"])
        _validate_distinct_trace_ids(baseline_trace_id, request.candidate_trace_id)
        case, report = store.run_case(
            case_id,
            candidate_trace_id=request.candidate_trace_id,
            scenario_cmd=scenario,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    return {"case": case, "report": report}


async def _read_limited_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Trace upload is too large. Maximum size is {MAX_UPLOAD_BYTES} bytes.",
            )
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status_code=400, detail="Uploaded trace file is empty.")
    return b"".join(chunks)


def _safe_display_filename(filename: str | None) -> str:
    name = Path(filename or "uploaded-trace").name.strip() or "uploaded-trace"
    if len(name) > MAX_FILENAME_LENGTH:
        suffix = Path(name).suffix
        stem_length = MAX_FILENAME_LENGTH - len(suffix)
        name = f"{Path(name).stem[:stem_length]}{suffix}"
    return name


def _validated_scenario_cmd(scenario_cmd: list[str] | None) -> list[str]:
    if scenario_cmd is None:
        return DEFAULT_SCENARIO_CMD
    if not scenario_cmd:
        raise HTTPException(
            status_code=400,
            detail="scenario_cmd must contain at least one command item.",
        )
    if len(scenario_cmd) > MAX_SCENARIO_CMD_ITEMS:
        raise HTTPException(status_code=400, detail="scenario_cmd contains too many command items.")
    for item in scenario_cmd:
        if not item or len(item) > MAX_SCENARIO_CMD_ITEM_LENGTH:
            raise HTTPException(
                status_code=400,
                detail="scenario_cmd contains an invalid command item.",
            )
    return scenario_cmd


def _validate_distinct_trace_ids(baseline_trace_id: str, candidate_trace_id: str) -> None:
    if baseline_trace_id == candidate_trace_id:
        raise HTTPException(
            status_code=400,
            detail="Baseline and candidate traces must be different.",
        )


def _validated_string_items(values: list[str], *, field_name: str) -> list[str]:
    for value in values:
        if not value.strip():
            raise HTTPException(status_code=400, detail=f"{field_name} contains an empty item.")
    return values


def _filtered_run_summaries(
    runs: list[JsonObject],
    *,
    q: str | None,
    status: str | None,
    severity: str | None,
    source_convention: str | None,
    divergence_type: str | None,
) -> list[JsonObject]:
    normalized_query = q.strip().lower() if q is not None else ""
    filtered: list[JsonObject] = []
    for run in runs:
        if status is not None and _json_string(run["status"]) != status:
            continue
        if severity is not None and _optional_json_string(run["severity"]) != severity:
            continue
        if (
            source_convention is not None
            and _json_string(run["source_convention"]) != source_convention
        ):
            continue
        if (
            divergence_type is not None
            and _optional_json_string(run["first_divergence_type"]) != divergence_type
        ):
            continue
        if normalized_query and normalized_query not in _run_search_blob(run):
            continue
        filtered.append(run)
    return filtered


def _validate_run_filter(name: str, value: str | None, allowed: frozenset[str]) -> None:
    if value is not None and value not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise HTTPException(status_code=400, detail=f"{name} must be one of: {allowed_values}.")


def _role_allows_request(*, role: str, method: str, path: str) -> bool:
    """Keep viewer credentials read-only, including the demo's seeding GET route."""
    if path == BROWSER_SESSION_EXCHANGE_PATH:
        return True
    if path == ACCESS_KEYS_API_PATH or path.startswith(f"{ACCESS_KEYS_API_PATH}/"):
        return role == "admin"
    if path == TEAM_MEMBERS_API_PATH or path.startswith(f"{TEAM_MEMBERS_API_PATH}/"):
        return role == "admin"
    if path == TEAM_INVITATIONS_API_PATH or path.startswith(f"{TEAM_INVITATIONS_API_PATH}/"):
        return role == "admin"
    if role != "viewer":
        return True
    return method.upper() in {"GET", "HEAD"} and path not in VIEWER_BLOCKED_GET_PATHS


def _role_denied_detail(role: str, *, path: str) -> str:
    if path == ACCESS_KEYS_API_PATH or path.startswith(f"{ACCESS_KEYS_API_PATH}/"):
        return "Workspace admin access is required to manage access keys."
    if path.startswith("/api/team/"):
        return "Workspace admin access is required to manage people and invitations."
    if role == "viewer":
        return (
            "Your current workspace has viewer access and is read-only. "
            "Editor or admin access is required to change workspace data."
        )
    return "This workspace key is not authorized for this operation."


def _admin_access_context(request: Request) -> tuple[str, str | None]:
    workspace_id = getattr(request.state, "workspace_id", None)
    workspace_role = getattr(request.state, "workspace_role", None)
    current_key_id = getattr(request.state, "key_id", None)
    if workspace_role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Workspace admin access is required to manage access keys.",
        )
    if not isinstance(workspace_id, str):
        raise StudioPersistenceError("request workspace context is invalid")
    if current_key_id is not None and not isinstance(current_key_id, str):
        raise StudioPersistenceError("request access-key context is invalid")
    return workspace_id, current_key_id


def _require_managed_access() -> None:
    if not AUTH_CONFIG.access_management_enabled:
        raise HTTPException(
            status_code=409,
            detail="Self-service access management requires managed workspace keys.",
        )


def _require_identity() -> None:
    if not AUTH_CONFIG.identity_enabled:
        raise HTTPException(
            status_code=409,
            detail="Managed human accounts are not configured for this Studio deployment.",
        )


def _require_identity_origin(request: Request, *, action: str) -> None:
    if (
        not _origin_is_allowed(request.headers.get("Origin"))
        or request.headers.get(BROWSER_CSRF_HEADER) != BROWSER_CSRF_VALUE
    ):
        raise HTTPException(
            status_code=403,
            detail=f"This {action} request did not come from an allowed Studio origin.",
        )


async def _require_identity_rate_limit(
    request: Request,
    *,
    subject: str,
    limit: int,
) -> None:
    host = request.client.host if request.client is not None else "unknown"
    subject_hash = hashlib.sha256(subject.strip().casefold().encode("utf-8")).hexdigest()[:24]
    allowed, retry_after = await RATE_LIMITER.allow(
        f"identity:{host}:{request.url.path}:{subject_hash}",
        limit=limit,
        window_seconds=RATE_LIMIT_WINDOW_SECONDS,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many account attempts. Please wait before retrying.",
            headers={"Retry-After": str(retry_after)},
        )


def _request_user_id(request: Request) -> str | None:
    user_id = getattr(request.state, "user_id", None)
    if user_id is not None and not isinstance(user_id, str):
        raise StudioPersistenceError("request user context is invalid")
    return user_id


def _membership_payload(record: StudioMembershipRecord) -> JsonObject:
    return {
        "user_id": record.user_id,
        "workspace_id": record.workspace_id,
        "email": record.email,
        "display_name": record.display_name,
        "role": record.role,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _invitation_payload(record: StudioInvitationRecord) -> JsonObject:
    return {
        "invitation_id": record.invitation_id,
        "workspace_id": record.workspace_id,
        "email": record.email,
        "role": record.role,
        "created_at": record.created_at,
        "expires_at": record.expires_at,
        "accepted_at": record.accepted_at,
        "revoked_at": record.revoked_at,
        "status": record.status,
    }


def _email_delivery_payload(record: StudioEmailDeliveryRecord | None) -> JsonObject:
    if record is None:
        return {
            "mode": "manual",
            "status": "not_queued",
            "attempt_count": 0,
            "last_error_code": None,
            "provider_status": None,
            "provider_event_at": None,
        }
    return {
        "mode": "automatic",
        "status": record.status,
        "attempt_count": record.attempt_count,
        "last_error_code": record.last_error_code,
        "provider_status": record.provider_status,
        "provider_event_at": record.provider_event_at,
    }


def _deliver_invitation_email(message_id: str) -> None:
    """Let the durable worker own failures instead of surfacing background errors."""
    try:
        EMAIL_DELIVERY.deliver_message(message_id)
    except StudioEmailDeliveryError:
        return


def _access_key_payload(record: StudioApiKeyRecord) -> JsonObject:
    return {
        "key_id": record.key_id,
        "workspace_id": record.workspace_id,
        "role": record.role,
        "label": record.label,
        "created_at": record.created_at,
        "expires_at": record.expires_at,
        "revoked_at": record.revoked_at,
        "status": record.status,
    }


def _production_readiness(
    *,
    storage_ok: bool,
    runtime: JsonObject | None = None,
) -> JsonObject:
    runtime = STORE.runtime_status() if runtime is None else runtime
    durable = runtime["durable"] is True
    storage_kind = runtime["kind"]
    completed: list[str] = ["API storage health check"] if storage_ok else []
    blockers = [
        "scheduled encrypted off-site backups and recovery drills",
        "managed user accounts, recovery, and team membership administration",
        "hosted deployment observability",
    ]
    if AUTH_CONFIG.required:
        completed.extend(
            [
                "bearer API-key authentication",
                "request-scoped workspace authorization",
            ]
        )
        if AUTH_CONFIG.credential_source == "managed":
            completed.extend(
                [
                    "hashed expiring workspace keys with admin issuance and revocation",
                    "admin self-service access-key management",
                    "viewer, editor, and admin request authorization",
                    "short-lived revocable HttpOnly browser sessions",
                ]
            )
            if BROWSER_SESSION_COOKIE_SECURE:
                completed.append("Secure browser session cookies")
            else:
                blockers.insert(0, "TLS-backed Secure browser session cookies")
            if AUTH_CONFIG.identity_enabled:
                completed.extend(
                    [
                        "Argon2id human accounts with invitation-only enrollment",
                        "single-use saved recovery codes with full session revocation",
                        "workspace-scoped team membership administration",
                    ]
                )
                blockers.remove(
                    "managed user accounts, recovery, and team membership administration"
                )
                if EMAIL_DELIVERY.enabled:
                    completed.append(
                        "encrypted transactional invitation email with bounded retries"
                    )
                    if EMAIL_DELIVERY.config.webhooks_enabled:
                        completed.append(
                            "authenticated idempotent email delivery and bounce reconciliation"
                        )
                        blockers.insert(
                            0,
                            "sender-domain monitoring and email suppression operations",
                        )
                    else:
                        blockers.insert(
                            0,
                            "email delivery webhooks and bounce handling",
                        )
                else:
                    blockers.insert(0, "transactional invitation email delivery")
        else:
            blockers.insert(0, "hashed API-key issuance, expiry, and revocation")
    else:
        blockers[:0] = [
            "authentication and authorization",
            "request-scoped workspace isolation",
        ]
    if AUDIT.enabled:
        completed.append("secret-safe structured request audit logs")
    else:
        blockers.insert(0, "structured request audit logs")
    if ERROR_REPORTER.enabled:
        completed.append("secret-safe structured server error events")
    else:
        blockers.insert(0, "structured server error events")
    metrics_access = METRICS_ACCESS.access_mode(auth_required=AUTH_CONFIG.required)
    if metrics_access == "bearer_token":
        completed.extend(
            [
                "low-cardinality Prometheus metrics with dedicated scrape access",
                "vendor-neutral alert rules and beginner incident runbook",
            ]
        )
        blockers[blockers.index("hosted deployment observability")] = (
            "deployment wiring for metrics collection, alert delivery, and error-event retention"
        )
    else:
        blockers.insert(0, "dedicated production metrics scrape access")
    if durable:
        completed.append("restart-safe workspace storage")
        if storage_kind == "sqlite":
            completed.append("verified local backup and non-destructive restore tooling")
        elif storage_kind == "postgres":
            completed.append("pooled multi-instance PostgreSQL core workspace storage")
            blockers.insert(
                0,
                "PostgreSQL repositories for managed identity and invitation delivery",
            )
    else:
        blockers.insert(0, "restart-safe durable storage")
    return {
        "api_ready": storage_ok,
        "production_saas_ready": False,
        "completed": cast(JsonValue, completed),
        "blockers": cast(JsonValue, blockers),
    }


def _public_runtime_status(runtime: JsonObject | None = None) -> JsonObject:
    runtime = STORE.runtime_status() if runtime is None else runtime
    if not AUTH_CONFIG.required:
        return runtime
    return {
        **runtime,
        "workspace_id": "protected",
        "trace_count": 0,
        "report_count": 0,
        "case_count": 0,
    }


def _unavailable_runtime_status() -> JsonObject:
    """Describe configured storage without retrying an unavailable backend."""
    return {
        "kind": STORE.runtime_kind,
        "durable": STORE.runtime_durable,
        "workspace_id": STORE.workspace_id,
        "trace_count": 0,
        "report_count": 0,
        "case_count": 0,
    }


def _run_search_blob(run: JsonObject) -> str:
    baseline = _json_object(run["baseline"])
    candidate = _json_object(run["candidate"])
    values = [
        _json_string(run["report_id"]),
        _json_string(run["created_at"]),
        _json_string(run["status"]),
        _json_string(run["source_convention"]),
        _json_string(baseline["display_name"]),
        _json_string(baseline["id"]),
        _json_string(baseline["source_convention"]),
        _json_string(candidate["display_name"]),
        _json_string(candidate["id"]),
        _json_string(candidate["source_convention"]),
        _optional_json_string(run["severity"]) or "",
        _optional_json_string(run["first_divergence_type"]) or "",
    ]
    return " ".join(values).lower()


def _json_object(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise HTTPException(status_code=500, detail="Stored run history is invalid.")
    return value


def _json_string(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=500, detail="Stored Studio data is invalid.")
    return value


def _optional_json_string(value: JsonValue) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=500, detail="Stored Studio data is invalid.")
    return value


def _key_error_detail(exc: KeyError) -> str:
    if exc.args and isinstance(exc.args[0], str):
        return exc.args[0]
    return str(exc)


def _rate_limit_key(request: Request) -> str:
    host = request.client.host if request.client is not None else "unknown"
    return f"{host}:{request.method}:{request.url.path}"


def _origin_is_allowed(origin: str | None) -> bool:
    return origin is not None and origin in ALLOWED_ORIGINS


def _session_cookie_max_age(expires_at: str) -> int:
    try:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StudioPersistenceError("browser session expiry is invalid") from exc
    if expires.tzinfo is None:
        raise StudioPersistenceError("browser session expiry has no timezone")
    remaining = int((expires.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
    return max(1, remaining)


def _delete_browser_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=BROWSER_SESSION_COOKIE_NAME,
        httponly=True,
        secure=BROWSER_SESSION_COOKIE_SECURE,
        samesite="strict",
        path="/",
    )


def _set_browser_session_cookie(
    response: Response,
    *,
    session_token: str,
    max_age: int,
) -> None:
    response.set_cookie(
        key=BROWSER_SESSION_COOKIE_NAME,
        value=session_token,
        max_age=max_age,
        httponly=True,
        secure=BROWSER_SESSION_COOKIE_SECURE,
        samesite="strict",
        path="/",
    )


def _apply_security_headers(response: Response) -> None:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=()",
    )
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")


def _safe_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return "Trace could not be parsed."
    return message[:500]
