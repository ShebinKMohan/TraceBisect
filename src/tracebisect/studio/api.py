"""FastAPI application for TraceBisect Studio."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated, cast

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from tracebisect.schema import JsonObject, JsonValue, TraceBisectSchemaError
from tracebisect.studio.service import (
    DEFAULT_SCENARIO_CMD,
    StudioStore,
    build_comparison_report,
    build_demo_report,
    load_trace_from_path,
)

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
    baseline_trace_id: str = Field(min_length=1)
    candidate_trace_id: str = Field(min_length=1)
    scenario_cmd: list[str] | None = None


@app.get("/api/health", response_model=None)
def health() -> JsonObject:
    return {"ok": True, "product": "TraceBisect Studio"}


@app.get("/api/demo-report", response_model=None)
def demo_report() -> JsonObject:
    return build_demo_report()


@app.get("/api/traces", response_model=None)
def list_traces() -> JsonObject:
    return {"traces": cast(JsonValue, STORE.list_traces())}


@app.post("/api/traces/upload", response_model=None)
async def upload_trace(file: Annotated[UploadFile, File()]) -> JsonObject:
    filename = file.filename or "uploaded-trace"
    suffix = Path(filename).suffix or ".json"
    try:
        content = await file.read()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            temp_path = Path(tmp.name)
        try:
            trace = load_trace_from_path(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)
    except (OSError, TraceBisectSchemaError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    trace_key = STORE.add_trace(trace, name=filename)
    summary = next(item for item in STORE.list_traces() if item["id"] == trace_key)
    return {"trace": summary}


@app.post("/api/compare", response_model=None)
def compare_traces(request: CompareRequest) -> JsonObject:
    try:
        baseline = STORE.get_trace(request.baseline_trace_id)
        candidate = STORE.get_trace(request.candidate_trace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    scenario = request.scenario_cmd or DEFAULT_SCENARIO_CMD
    report = build_comparison_report(
        baseline,
        candidate,
        baseline_name=STORE.trace_names[request.baseline_trace_id],
        candidate_name=STORE.trace_names[request.candidate_trace_id],
        scenario_cmd=scenario,
    )
    STORE.add_report(report)
    return report
