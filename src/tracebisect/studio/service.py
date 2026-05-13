"""Service layer for TraceBisect Studio.

The Studio service wraps the existing CLI/core engine and returns JSON-shaped
objects that a web UI can render directly.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from tracebisect.align import align
from tracebisect.cli import _pytest_template
from tracebisect.demo import build_refund_baseline_trace, build_refund_candidate_trace
from tracebisect.diff import Divergence, detect_divergences, first_divergence
from tracebisect.jsonl import read_trace
from tracebisect.otel import import_otel_json
from tracebisect.schema import Event, EventPayload, JsonObject, JsonValue, Trace

DEFAULT_SCENARIO_CMD = ["python", "examples/refund_agent.py", "--case", "refund_042"]
DEFAULT_ASSERTIONS = ["tool_args", "final_output", "cost"]


@dataclass(slots=True)
class StudioStore:
    """In-memory Studio store for the first SaaS-style MVP slice."""

    traces: dict[str, Trace] = field(default_factory=dict)
    trace_names: dict[str, str] = field(default_factory=dict)
    reports: dict[str, JsonObject] = field(default_factory=dict)

    def add_trace(self, trace: Trace, *, name: str | None = None) -> str:
        trace_key = trace.trace_id or f"trace-{uuid.uuid4().hex[:12]}"
        if trace_key in self.traces:
            trace_key = f"{trace_key}-{uuid.uuid4().hex[:8]}"
        self.traces[trace_key] = trace
        self.trace_names[trace_key] = name or trace.trace_id
        return trace_key

    def get_trace(self, trace_key: str) -> Trace:
        try:
            return self.traces[trace_key]
        except KeyError as exc:
            raise KeyError(f"unknown trace id: {trace_key}") from exc

    def add_report(self, report: JsonObject) -> str:
        report_id = _string_value(report["report_id"])
        self.reports[report_id] = report
        return report_id

    def list_traces(self) -> list[JsonObject]:
        return [
            _trace_summary(trace, trace_key=trace_key, display_name=self.trace_names[trace_key])
            for trace_key, trace in sorted(self.traces.items())
        ]


def build_demo_report() -> JsonObject:
    """Build the seeded refund-agent comparison report shown on first load."""
    return build_comparison_report(
        build_refund_baseline_trace(),
        build_refund_candidate_trace(),
        baseline_name="Refund baseline",
        candidate_name="Refund regression",
        scenario_cmd=DEFAULT_SCENARIO_CMD,
    )


def load_trace_from_path(path: str | Path) -> Trace:
    """Load either canonical `.tbtrace` JSONL or OTel/OpenInference JSON."""
    source = Path(path)
    if source.suffix == ".tbtrace":
        return read_trace(source)
    return import_otel_json(source)


def build_comparison_report(
    baseline: Trace,
    candidate: Trace,
    *,
    baseline_name: str = "Baseline",
    candidate_name: str = "Candidate",
    scenario_cmd: list[str] | None = None,
) -> JsonObject:
    """Compare two traces and return a JSON-ready Studio report."""
    matches = align(baseline, candidate)
    divergences = detect_divergences(matches, baseline, candidate)
    first = first_divergence(divergences)
    report_id = f"rpt_{uuid.uuid4().hex[:12]}"
    scenario = scenario_cmd or DEFAULT_SCENARIO_CMD
    pytest_source = _pytest_template(
        baseline_path=f"./baselines/{baseline.trace_id}.tbtrace",
        scenario_cmd=scenario,
        assertions=DEFAULT_ASSERTIONS,
        cost_threshold=1.5,
    )
    return {
        "report_id": report_id,
        "created_at": _format_datetime(datetime.now(timezone.utc)),
        "baseline": _trace_summary(
            baseline,
            trace_key=baseline.trace_id,
            display_name=baseline_name,
        ),
        "candidate": _trace_summary(
            candidate,
            trace_key=candidate.trace_id,
            display_name=candidate_name,
        ),
        "events": {
            "baseline": [_event_view(event) for event in baseline.events],
            "candidate": [_event_view(event) for event in candidate.events],
        },
        "divergence_count": len(divergences),
        "first_divergence": _divergence_view(first) if first is not None else None,
        "divergences": [_divergence_view(divergence) for divergence in divergences],
        "pytest": {
            "filename": "test_tracebisect_regression.py",
            "source": pytest_source,
        },
        "integrations": [
            {
                "name": "OpenTelemetry / OpenInference",
                "status": "available",
                "description": "Upload OTLP-style JSON exports directly.",
            },
            {
                "name": "Langfuse",
                "status": "via OpenTelemetry",
                "description": "Use OTel-compatible trace export paths for the Studio MVP.",
            },
            {
                "name": "LangSmith",
                "status": "via OpenTelemetry",
                "description": "Direct API import is planned after the visual MVP.",
            },
        ],
    }


def _trace_summary(trace: Trace, *, trace_key: str, display_name: str) -> JsonObject:
    return {
        "id": trace_key,
        "trace_id": trace.trace_id,
        "display_name": display_name,
        "source_convention": trace.source_convention,
        "created_at": _format_datetime(trace.created_at),
        "event_count": len(trace.events),
        "root_event": trace.root_event.semantic_name,
    }


def _event_view(event: Event) -> JsonObject:
    return {
        "id": event.id,
        "parent_id": event.parent_id,
        "sequence_index": event.sequence_index,
        "type": event.type.value,
        "semantic_name": event.semantic_name,
        "timestamp": _format_datetime(event.timestamp),
        "duration_ms": event.duration_ms,
        "payload": _payload_view(event.payload),
        "source_format": event.source_format,
        "source_event_id": event.source_event_id,
        "model_version": event.model_version,
        "prompt_version": event.prompt_version,
        "code_sha": event.code_sha,
        "sampling_params": event.sampling_params,
    }


def _divergence_view(divergence: Divergence) -> JsonObject:
    return {
        "type": divergence.type,
        "severity": divergence.severity,
        "baseline_event_id": (
            divergence.baseline_event.id if divergence.baseline_event is not None else None
        ),
        "candidate_event_id": (
            divergence.candidate_event.id if divergence.candidate_event is not None else None
        ),
        "description": divergence.description,
        "expected": divergence.expected,
        "actual": divergence.actual,
        "impact": {
            "tokens_delta": divergence.impact.tokens_delta,
            "cost_delta_ratio": divergence.impact.cost_delta_ratio,
            "final_output_changed": divergence.impact.final_output_changed,
            "errors_introduced": cast(JsonValue, divergence.impact.errors_introduced),
            "affected_event_count": divergence.impact.affected_event_count,
        },
        "source_metadata": divergence.source_metadata,
    }


def _payload_view(payload: EventPayload) -> JsonObject:
    return cast(JsonObject, asdict(payload))


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _string_value(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise TypeError("expected string JSON value")
    return value
