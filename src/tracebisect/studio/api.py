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

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from tracebisect.schema import JsonObject, JsonValue, TraceBisectSchemaError
from tracebisect.studio.service import (
    DEFAULT_MAX_STORED_TRACES,
    DEFAULT_SCENARIO_CMD,
    StudioStore,
    StudioStoreFullError,
    build_comparison_report,
    build_demo_report,
    load_trace_from_path,
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

STORE = StudioStore()


class CompareRequest(BaseModel):
    baseline_trace_id: str = Field(min_length=1, max_length=256)
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
    return {
        "ok": True,
        "product": "TraceBisect Studio",
        "limits": {
            "max_upload_bytes": MAX_UPLOAD_BYTES,
            "max_stored_traces": DEFAULT_MAX_STORED_TRACES,
            "rate_limit_window_seconds": RATE_LIMIT_WINDOW_SECONDS,
            "rate_limit_requests": RATE_LIMIT_REQUESTS,
            "rate_limit_upload_requests": RATE_LIMIT_UPLOAD_REQUESTS,
        },
    }


@app.get("/api/demo-report", response_model=None)
def demo_report() -> JsonObject:
    return build_demo_report()


@app.get("/api/traces", response_model=None)
def list_traces() -> JsonObject:
    return {"traces": cast(JsonValue, STORE.list_traces())}


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
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    report = build_comparison_report(
        baseline,
        candidate,
        baseline_name=STORE.trace_names[request.baseline_trace_id],
        candidate_name=STORE.trace_names[request.candidate_trace_id],
        scenario_cmd=scenario,
    )
    STORE.add_report(report)
    return report


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
