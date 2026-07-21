"""FastAPI application for TraceBisect Studio."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, cast

from fastapi import FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from tracebisect.schema import JsonObject, JsonValue, TraceBisectSchemaError
from tracebisect.studio.service import (
    DEFAULT_SCENARIO_CMD,
    StudioStore,
    StudioStoreFullError,
    build_comparison_report,
    load_trace_from_path,
    seed_demo_report,
)
from tracebisect.studio.storage import (
    StudioPersistenceError,
    create_studio_store,
)

MAX_UPLOAD_BYTES = int(os.getenv("TRACEBISECT_STUDIO_MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
MAX_FILENAME_LENGTH = 120
MAX_SCENARIO_CMD_ITEMS = 16
MAX_SCENARIO_CMD_ITEM_LENGTH = 240
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("TRACEBISECT_STUDIO_RATE_LIMIT_WINDOW_SECONDS", "60"))
RATE_LIMIT_REQUESTS = int(os.getenv("TRACEBISECT_STUDIO_RATE_LIMIT_REQUESTS", "180"))
RATE_LIMIT_UPLOAD_REQUESTS = int(os.getenv("TRACEBISECT_STUDIO_UPLOAD_RATE_LIMIT_REQUESTS", "30"))
ALLOWED_UPLOAD_SUFFIXES = frozenset({".tbtrace", ".json"})
UPLOAD_CHUNK_BYTES = 1024 * 1024
VALID_RUN_STATUSES = frozenset({"passing", "failing"})
VALID_RUN_SEVERITIES = frozenset({"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"})

app = FastAPI(
    title="TraceBisect Studio API",
    version="0.1.0",
    description="SaaS-style API around the TraceBisect trace regression engine.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

STORE: StudioStore = create_studio_store()


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
            _apply_security_headers(limited_response)
            return limited_response

    response = await call_next(request)
    _apply_security_headers(response)
    return response


@app.get("/api/health", response_model=None)
def health() -> JsonObject:
    storage_ok = STORE.check_health()
    return {
        "ok": storage_ok,
        "product": "TraceBisect Studio",
        "runtime": STORE.runtime_status(),
        "readiness": _production_readiness(storage_ok=storage_ok),
        "limits": {
            "max_upload_bytes": MAX_UPLOAD_BYTES,
            "max_stored_traces": STORE.max_traces,
            "max_stored_reports": STORE.max_reports,
            "max_stored_cases": STORE.max_cases,
            "rate_limit_window_seconds": RATE_LIMIT_WINDOW_SECONDS,
            "rate_limit_requests": RATE_LIMIT_REQUESTS,
            "rate_limit_upload_requests": RATE_LIMIT_UPLOAD_REQUESTS,
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


@app.get("/api/demo-report", response_model=None)
def demo_report() -> JsonObject:
    return seed_demo_report(STORE)


@app.get("/api/traces", response_model=None)
def list_traces() -> JsonObject:
    return {"traces": cast(JsonValue, STORE.list_traces())}


@app.get("/api/runs", response_model=None)
def list_runs(
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
        STORE.list_report_summaries(),
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
def get_run_report(report_id: str) -> JsonObject:
    try:
        return STORE.get_report(report_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc


@app.post("/api/traces/upload", response_model=None)
async def upload_trace(file: Annotated[UploadFile, File()]) -> JsonObject:
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
        trace_key = STORE.add_trace(trace, name=filename)
    except StudioStoreFullError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    summary = next(item for item in STORE.list_traces() if item["id"] == trace_key)
    return {"trace": summary}


@app.post("/api/compare", response_model=None)
def compare_traces(request: CompareRequest) -> JsonObject:
    scenario = _validated_scenario_cmd(request.scenario_cmd)
    if request.baseline_trace_id == request.candidate_trace_id:
        raise HTTPException(
            status_code=400,
            detail="Baseline and candidate traces must be different.",
        )
    try:
        baseline = STORE.get_trace(request.baseline_trace_id)
        candidate = STORE.get_trace(request.candidate_trace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc

    report = build_comparison_report(
        baseline,
        candidate,
        baseline_name=STORE.trace_names[request.baseline_trace_id],
        candidate_name=STORE.trace_names[request.candidate_trace_id],
        scenario_cmd=scenario,
    )
    STORE.add_report(report)
    return report


@app.get("/api/regression-cases", response_model=None)
def list_regression_cases() -> JsonObject:
    return {"cases": cast(JsonValue, STORE.list_cases())}


@app.post("/api/regression-cases", response_model=None)
def create_regression_case(request: RegressionCaseCreateRequest) -> JsonObject:
    scenario = _validated_scenario_cmd(request.scenario_cmd)
    _validate_distinct_trace_ids(request.baseline_trace_id, request.candidate_trace_id)
    try:
        case = STORE.add_case_from_report(
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
def get_regression_case(case_id: str) -> JsonObject:
    try:
        return {"case": STORE.get_case(case_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc


@app.post("/api/regression-cases/{case_id}/run", response_model=None)
def run_regression_case(case_id: str, request: RegressionCaseRunRequest) -> JsonObject:
    scenario = _validated_scenario_cmd(request.scenario_cmd)
    try:
        existing = STORE.get_case(case_id)
        baseline_trace_id = _json_string(existing["baseline_trace_id"])
        _validate_distinct_trace_ids(baseline_trace_id, request.candidate_trace_id)
        case, report = STORE.run_case(
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


def _production_readiness(*, storage_ok: bool) -> JsonObject:
    runtime = STORE.runtime_status()
    durable = runtime["durable"] is True
    completed: list[str] = ["API storage health check"] if storage_ok else []
    blockers = [
        "authentication and authorization",
        "request-scoped workspace isolation",
        "managed backups and restore testing",
    ]
    if durable:
        completed.append("restart-safe workspace storage")
    else:
        blockers.insert(0, "restart-safe durable storage")
    return {
        "api_ready": storage_ok,
        "production_saas_ready": False,
        "completed": cast(JsonValue, completed),
        "blockers": cast(JsonValue, blockers),
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
