"""Service layer for TraceBisect Studio.

The Studio service wraps the existing CLI/core engine and returns JSON-shaped
objects that a web UI can render directly.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import cast

from tracebisect.align import align
from tracebisect.cli import _pytest_template
from tracebisect.demo import build_refund_baseline_trace, build_refund_candidate_trace
from tracebisect.diff import Divergence, detect_divergences, first_divergence
from tracebisect.jsonl import read_trace
from tracebisect.otel import import_otel_json
from tracebisect.schema import (
    Event,
    EventPayload,
    JsonObject,
    JsonValue,
    LLMCallPayload,
    RunEndPayload,
    RunStartPayload,
    Trace,
)

DEFAULT_SCENARIO_CMD = ["python", "examples/refund_agent.py", "--case", "refund_042"]
DEFAULT_ASSERTIONS = ["tool_args", "final_output", "cost"]
DEFAULT_MAX_STORED_TRACES = 100
DEFAULT_MAX_STORED_REPORTS = 100
DEFAULT_MAX_STORED_CASES = 100


class StudioStoreFullError(RuntimeError):
    """Raised when the Studio store reaches its configured capacity."""


@dataclass(slots=True)
class StudioStore:
    """In-memory Studio store used by the zero-configuration local mode."""

    traces: dict[str, Trace] = field(default_factory=dict)
    trace_names: dict[str, str] = field(default_factory=dict)
    reports: dict[str, JsonObject] = field(default_factory=dict)
    cases: dict[str, JsonObject] = field(default_factory=dict)
    demo_report_id: str | None = None
    workspace_id: str = "local"
    max_traces: int = DEFAULT_MAX_STORED_TRACES
    max_reports: int = DEFAULT_MAX_STORED_REPORTS
    max_cases: int = DEFAULT_MAX_STORED_CASES
    _lock: RLock = field(default_factory=RLock, repr=False, compare=False)

    def add_trace(self, trace: Trace, *, name: str | None = None) -> str:
        with self._lock:
            trace_key = trace.trace_id or f"trace-{uuid.uuid4().hex[:12]}"
            if trace_key not in self.traces and len(self.traces) >= self.max_traces:
                raise StudioStoreFullError(
                    "trace store is full; increase TRACEBISECT_STUDIO_MAX_STORED_TRACES"
                )
            self.traces[trace_key] = trace
            self.trace_names[trace_key] = name or trace.trace_id
            return trace_key

    def get_trace(self, trace_key: str) -> Trace:
        with self._lock:
            try:
                return self.traces[trace_key]
            except KeyError as exc:
                raise KeyError(f"unknown trace id: {trace_key}") from exc

    def add_report(self, report: JsonObject) -> str:
        with self._lock:
            report_id = _string_value(report["report_id"])
            if report_id not in self.reports and len(self.reports) >= self.max_reports:
                oldest_report_id = next(iter(self.reports))
                del self.reports[oldest_report_id]
            self.reports[report_id] = report
            return report_id

    def get_report(self, report_id: str) -> JsonObject:
        with self._lock:
            try:
                return self.reports[report_id]
            except KeyError as exc:
                raise KeyError(f"unknown run id: {report_id}") from exc

    def list_report_summaries(self) -> list[JsonObject]:
        with self._lock:
            return [
                _report_summary(report)
                for report in sorted(
                    self.reports.values(),
                    key=lambda item: _string_value(item["created_at"]),
                    reverse=True,
                )
            ]

    def add_case_from_report(
        self,
        *,
        name: str,
        description: str,
        tags: list[str],
        baseline_trace_id: str,
        candidate_trace_id: str,
        scenario_cmd: list[str],
        assertions: list[str],
        cost_threshold: float,
    ) -> JsonObject:
        with self._lock:
            if len(self.cases) >= self.max_cases:
                raise StudioStoreFullError(
                    "regression case store is full; increase TRACEBISECT_STUDIO_MAX_STORED_CASES"
                )
            baseline = self.get_trace(baseline_trace_id)
            candidate = self.get_trace(candidate_trace_id)
            report = build_comparison_report(
                baseline,
                candidate,
                baseline_name=self.trace_names[baseline_trace_id],
                candidate_name=self.trace_names[candidate_trace_id],
                scenario_cmd=scenario_cmd,
                assertions=assertions,
                cost_threshold=cost_threshold,
            )
            self.add_report(report)
            case_id = f"case_{uuid.uuid4().hex[:12]}"
            now = _format_datetime(datetime.now(timezone.utc))
            case = _case_from_report(
                case_id=case_id,
                name=name,
                description=description,
                created_at=now,
                updated_at=now,
                tags=tags,
                baseline_trace_id=baseline_trace_id,
                candidate_trace_id=candidate_trace_id,
                scenario_cmd=scenario_cmd,
                assertions=assertions,
                cost_threshold=cost_threshold,
                report=report,
            )
            self.cases[case_id] = case
            return case

    def get_case(self, case_id: str) -> JsonObject:
        with self._lock:
            try:
                return self.cases[case_id]
            except KeyError as exc:
                raise KeyError(f"unknown regression case id: {case_id}") from exc

    def list_cases(self) -> list[JsonObject]:
        with self._lock:
            return list(self.cases.values())

    def run_case(
        self,
        case_id: str,
        *,
        candidate_trace_id: str,
        scenario_cmd: list[str],
    ) -> tuple[JsonObject, JsonObject]:
        with self._lock:
            existing = self.get_case(case_id)
            baseline_trace_id = _string_value(existing["baseline_trace_id"])
            baseline = self.get_trace(baseline_trace_id)
            candidate = self.get_trace(candidate_trace_id)
            assertions = _string_list(existing["assertions"])
            cost_threshold = _float_value(existing["cost_threshold"])
            report = build_comparison_report(
                baseline,
                candidate,
                baseline_name=self.trace_names[baseline_trace_id],
                candidate_name=self.trace_names[candidate_trace_id],
                scenario_cmd=scenario_cmd,
                assertions=assertions,
                cost_threshold=cost_threshold,
            )
            self.add_report(report)
            updated_at = _format_datetime(datetime.now(timezone.utc))
            updated = _case_from_report(
                case_id=case_id,
                name=_string_value(existing["name"]),
                description=_string_value(existing["description"]),
                created_at=_string_value(existing["created_at"]),
                updated_at=updated_at,
                tags=_string_list(existing["tags"]),
                baseline_trace_id=baseline_trace_id,
                candidate_trace_id=candidate_trace_id,
                scenario_cmd=scenario_cmd,
                assertions=assertions,
                cost_threshold=cost_threshold,
                report=report,
            )
            self.cases[case_id] = updated
            return updated, report

    def list_traces(self) -> list[JsonObject]:
        with self._lock:
            return [
                _trace_summary(trace, trace_key=trace_key, display_name=self.trace_names[trace_key])
                for trace_key, trace in sorted(self.traces.items())
            ]

    def clear(self) -> None:
        with self._lock:
            self.traces.clear()
            self.trace_names.clear()
            self.reports.clear()
            self.cases.clear()
            self.demo_report_id = None

    def set_demo_report_id(self, report_id: str | None) -> None:
        """Remember which report powers the idempotent built-in demo."""
        with self._lock:
            self.demo_report_id = report_id

    def check_health(self) -> bool:
        """Return whether the backing store can currently serve requests."""
        return True

    def runtime_status(self) -> JsonObject:
        """Describe storage behavior without exposing secrets or filesystem paths."""
        with self._lock:
            return {
                "kind": "memory",
                "durable": False,
                "workspace_id": self.workspace_id,
                "trace_count": len(self.traces),
                "report_count": len(self.reports),
                "case_count": len(self.cases),
            }


def build_demo_report() -> JsonObject:
    """Build the seeded refund-agent comparison report shown on first load."""
    return build_comparison_report(
        build_refund_baseline_trace(),
        build_refund_candidate_trace(),
        baseline_name="Refund baseline",
        candidate_name="Refund regression",
        scenario_cmd=DEFAULT_SCENARIO_CMD,
    )


def seed_demo_report(store: StudioStore) -> JsonObject:
    """Seed demo traces and report into the store idempotently."""
    with store._lock:
        if store.demo_report_id is not None:
            try:
                return store.get_report(store.demo_report_id)
            except KeyError:
                store.set_demo_report_id(None)

        baseline = build_refund_baseline_trace()
        candidate = build_refund_candidate_trace()
        baseline_id = store.add_trace(baseline, name="Refund baseline")
        candidate_id = store.add_trace(candidate, name="Refund regression")
        report = build_comparison_report(
            store.get_trace(baseline_id),
            store.get_trace(candidate_id),
            baseline_name=store.trace_names[baseline_id],
            candidate_name=store.trace_names[candidate_id],
            scenario_cmd=DEFAULT_SCENARIO_CMD,
        )
        store.set_demo_report_id(store.add_report(report))
        return report


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
    assertions: list[str] | None = None,
    cost_threshold: float = 1.5,
) -> JsonObject:
    """Compare two traces and return a JSON-ready Studio report."""
    matches = align(baseline, candidate)
    divergences = detect_divergences(matches, baseline, candidate)
    first = first_divergence(divergences)
    report_id = f"rpt_{uuid.uuid4().hex[:12]}"
    scenario = scenario_cmd or DEFAULT_SCENARIO_CMD
    pytest_assertions = assertions or DEFAULT_ASSERTIONS
    pytest_source = _pytest_template(
        baseline_path=f"./baselines/{baseline.trace_id}.tbtrace",
        scenario_cmd=scenario,
        assertions=pytest_assertions,
        cost_threshold=cost_threshold,
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


def _case_from_report(
    *,
    case_id: str,
    name: str,
    description: str,
    created_at: str,
    updated_at: str,
    tags: list[str],
    baseline_trace_id: str,
    candidate_trace_id: str,
    scenario_cmd: list[str],
    assertions: list[str],
    cost_threshold: float,
    report: JsonObject,
) -> JsonObject:
    divergence_count = _int_value(report["divergence_count"])
    first = report["first_divergence"]
    severity = _divergence_severity(first)
    report_id = _string_value(report["report_id"])
    return {
        "case_id": case_id,
        "name": name,
        "description": description,
        "created_at": created_at,
        "updated_at": updated_at,
        "tags": cast(JsonValue, tags),
        "source_report_id": report_id,
        "baseline_trace_id": baseline_trace_id,
        "candidate_trace_id": candidate_trace_id,
        "baseline": report["baseline"],
        "candidate": report["candidate"],
        "first_divergence": first,
        "divergence_count": divergence_count,
        "assertions": cast(JsonValue, assertions),
        "cost_threshold": cost_threshold,
        "scenario_cmd": cast(JsonValue, scenario_cmd),
        "pytest": report["pytest"],
        "last_result": {
            "status": "failing" if divergence_count > 0 else "passing",
            "report_id": report_id,
            "divergence_count": divergence_count,
            "severity": severity,
            "checked_at": updated_at,
        },
    }


def _report_summary(report: JsonObject) -> JsonObject:
    baseline = _json_object(report["baseline"])
    candidate = _json_object(report["candidate"])
    first = report["first_divergence"]
    divergence_count = _int_value(report["divergence_count"])
    severity = _divergence_severity(first)
    first_type = _divergence_type(first)
    return {
        "report_id": report["report_id"],
        "created_at": report["created_at"],
        "baseline": {
            "id": baseline["id"],
            "display_name": baseline["display_name"],
            "source_convention": baseline["source_convention"],
            "event_count": baseline["event_count"],
        },
        "candidate": {
            "id": candidate["id"],
            "display_name": candidate["display_name"],
            "source_convention": candidate["source_convention"],
            "event_count": candidate["event_count"],
        },
        "source_convention": _combined_source_convention(
            _string_value(baseline["source_convention"]),
            _string_value(candidate["source_convention"]),
        ),
        "divergence_count": divergence_count,
        "status": "failing" if divergence_count > 0 else "passing",
        "severity": severity,
        "first_divergence_type": first_type,
        "event_count": _int_value(baseline["event_count"]) + _int_value(candidate["event_count"]),
    }


def _trace_summary(trace: Trace, *, trace_key: str, display_name: str) -> JsonObject:
    profile = _trace_profile(trace)
    return {
        "id": trace_key,
        "trace_id": trace.trace_id,
        "display_name": display_name,
        "source_convention": trace.source_convention,
        "created_at": _format_datetime(trace.created_at),
        "event_count": len(trace.events),
        "root_event": trace.root_event.semantic_name,
        **profile,
    }


def _trace_profile(trace: Trace) -> JsonObject:
    root_payload = trace.root_event.payload
    llm_payloads = [
        event.payload for event in trace.events if isinstance(event.payload, LLMCallPayload)
    ]
    end_payload = _run_end_payload(trace)
    root_start = root_payload if isinstance(root_payload, RunStartPayload) else None
    total_input_tokens = (
        end_payload.total_input_tokens
        if end_payload is not None
        else sum(payload.input_tokens for payload in llm_payloads)
    )
    total_output_tokens = (
        end_payload.total_output_tokens
        if end_payload is not None
        else sum(payload.output_tokens for payload in llm_payloads)
    )
    total_cost_usd = (
        end_payload.total_cost_usd
        if end_payload is not None
        else sum(payload.cost_usd for payload in llm_payloads)
    )
    first_llm = llm_payloads[0] if llm_payloads else None
    return {
        "status": _trace_status(end_payload),
        "duration_ms": _trace_duration_ms(trace),
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
        "total_cost_usd": total_cost_usd,
        "model": first_llm.model if first_llm is not None else None,
        "model_version": _first_model_version(trace),
        "prompt_version": _first_prompt_version(trace),
        "code_sha": _first_code_sha(trace),
        "agent_name": root_start.agent_name if root_start is not None else None,
        "agent_version": root_start.agent_version if root_start is not None else None,
        "session_id": _trace_session_id(root_start),
    }


def _run_end_payload(trace: Trace) -> RunEndPayload | None:
    for event in reversed(trace.events):
        if isinstance(event.payload, RunEndPayload):
            return event.payload
    return None


def _trace_status(end_payload: RunEndPayload | None) -> str:
    if end_payload is None:
        return "unknown"
    return "success" if end_payload.success else "error"


def _trace_duration_ms(trace: Trace) -> float:
    if not trace.events:
        return 0.0
    first_ts = min(event.timestamp for event in trace.events)
    last_ts = max(event.timestamp for event in trace.events)
    elapsed_ms = (last_ts - first_ts).total_seconds() * 1000
    if elapsed_ms > 0:
        return round(elapsed_ms, 3)
    return round(sum(event.duration_ms or 0.0 for event in trace.events), 3)


def _first_model_version(trace: Trace) -> str | None:
    for event in trace.events:
        if event.model_version:
            return event.model_version
    return None


def _first_prompt_version(trace: Trace) -> str | None:
    for event in trace.events:
        if event.prompt_version:
            return event.prompt_version
    return None


def _first_code_sha(trace: Trace) -> str | None:
    for event in trace.events:
        if event.code_sha:
            return event.code_sha
    return None


def _trace_session_id(root_payload: RunStartPayload | None) -> str | None:
    if root_payload is None:
        return None
    for key in ("session_id", "thread_id", "conversation_id", "case_id", "case", "scenario"):
        value = root_payload.run_metadata.get(key)
        text = _metadata_text(value)
        if text:
            return text
    return root_payload.user_input[:80] if root_payload.user_input else None


def _metadata_text(value: JsonValue | None) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return None


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


def _json_object(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError("expected JSON object")
    return value


def _string_list(value: JsonValue) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError("expected string list JSON value")
    return cast(list[str], value)


def _int_value(value: JsonValue) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("expected integer JSON value")
    return value


def _float_value(value: JsonValue) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError("expected numeric JSON value")
    return float(value)


def _divergence_severity(value: JsonValue) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError("expected divergence JSON object")
    severity = value.get("severity")
    if severity is not None and not isinstance(severity, str):
        raise TypeError("expected divergence severity string")
    return severity


def _divergence_type(value: JsonValue) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError("expected divergence JSON object")
    divergence_type = value.get("type")
    if divergence_type is not None and not isinstance(divergence_type, str):
        raise TypeError("expected divergence type string")
    return divergence_type


def _combined_source_convention(baseline: str, candidate: str) -> str:
    return baseline if baseline == candidate else "mixed"
