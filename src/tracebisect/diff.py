"""V1 divergence detection for aligned TraceBisect traces."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from typing import Literal, TypeAlias

from tracebisect.align import Match
from tracebisect.schema import (
    BranchDecisionPayload,
    ErrorPayload,
    Event,
    EventType,
    JsonObject,
    JsonValue,
    LLMCallPayload,
    MCPCallPayload,
    RunEndPayload,
    ToolCallPayload,
    Trace,
)

DivergenceType: TypeAlias = Literal[
    "missing_event",
    "extra_event",
    "changed_tool_args",
    "branch_changed",
    "cost_regression",
]

Severity: TypeAlias = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]

_PairedKind: TypeAlias = Literal["ID_MATCH", "STRUCTURAL_MATCH", "POSITIONAL_MATCH"]

COST_SEVERITY_THRESHOLD: float = 1.20

__all__ = [
    "COST_SEVERITY_THRESHOLD",
    "Divergence",
    "DivergenceType",
    "ImpactAnalysis",
    "Severity",
    "detect_divergences",
    "first_divergence",
]


@dataclass(frozen=True, slots=True)
class ImpactAnalysis:
    tokens_delta: int
    cost_delta_ratio: float
    final_output_changed: bool
    errors_introduced: list[JsonObject]
    affected_event_count: int


@dataclass(frozen=True, slots=True)
class Divergence:
    type: DivergenceType
    severity: Severity
    baseline_event: Event | None
    candidate_event: Event | None
    description: str
    expected: JsonValue
    actual: JsonValue
    impact: ImpactAnalysis
    source_metadata: JsonObject


def detect_divergences(
    matches: list[Match],
    baseline: Trace,
    candidate: Trace,
    *,
    cost_severity_threshold: float = COST_SEVERITY_THRESHOLD,
) -> list[Divergence]:
    """Detect the five V1 divergence types from an alignment result."""
    divergences: list[Divergence] = []
    for index, match in enumerate(matches):
        if match.kind == "DELETION":
            if match.baseline_event is None:
                raise AssertionError("DELETION match must carry a baseline event")
            divergences.append(
                _missing_event(match.baseline_event, matches, baseline, candidate, index)
            )
        elif match.kind == "INSERTION":
            if match.candidate_event is None:
                raise AssertionError("INSERTION match must carry a candidate event")
            divergences.append(
                _extra_event(match.candidate_event, matches, baseline, candidate, index)
            )
        elif match.kind in ("ID_MATCH", "STRUCTURAL_MATCH", "POSITIONAL_MATCH"):
            baseline_event = match.baseline_event
            candidate_event = match.candidate_event
            if baseline_event is None or candidate_event is None:
                raise AssertionError("paired match must carry both events")
            paired_kind: _PairedKind = match.kind
            tool_args = _changed_tool_args(
                baseline_event,
                candidate_event,
                paired_kind,
                matches,
                baseline,
                candidate,
                index,
            )
            if tool_args is not None:
                divergences.append(tool_args)
            branch = _branch_changed(
                baseline_event,
                candidate_event,
                paired_kind,
                matches,
                baseline,
                candidate,
                index,
            )
            if branch is not None:
                divergences.append(branch)
            event_cost = _per_event_cost_regression(
                baseline_event,
                candidate_event,
                paired_kind,
                matches,
                baseline,
                candidate,
                index,
                cost_severity_threshold,
            )
            if event_cost is not None:
                divergences.append(event_cost)

    trace_cost = _trace_cost_regression(matches, baseline, candidate, cost_severity_threshold)
    if trace_cost is not None:
        divergences.append(trace_cost)
    return divergences


def first_divergence(divergences: list[Divergence]) -> Divergence | None:
    """Return the earliest HIGH-or-worse divergence, with same-event CRITICAL precedence."""
    eligible = [
        divergence
        for divergence in divergences
        if _severity_rank(divergence.severity) >= _severity_rank("HIGH")
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda divergence: (
            _match_index(divergence),
            -_severity_rank(divergence.severity),
            str(divergence.source_metadata.get("detector", "")),
        ),
    )


def _missing_event(
    event: Event,
    matches: list[Match],
    baseline: Trace,
    candidate: Trace,
    match_index: int,
) -> Divergence:
    severity: Severity = "CRITICAL" if event.type is EventType.RUN_END else "HIGH"
    description = (
        f"{event.type.value} {event.semantic_name} "
        f"(baseline #{event.sequence_index}) absent in candidate"
    )
    return Divergence(
        type="missing_event",
        severity=severity,
        baseline_event=event,
        candidate_event=None,
        description=description,
        expected=_event_summary(event),
        actual=None,
        impact=_impact(matches, baseline, candidate, match_index),
        source_metadata={"detector": "missing_event", "match_index": match_index},
    )


def _extra_event(
    event: Event,
    matches: list[Match],
    baseline: Trace,
    candidate: Trace,
    match_index: int,
) -> Divergence:
    severity: Severity = "CRITICAL" if event.type is EventType.ERROR else "HIGH"
    description = (
        f"{event.type.value} {event.semantic_name} "
        f"(candidate #{event.sequence_index}) absent in baseline"
    )
    return Divergence(
        type="extra_event",
        severity=severity,
        baseline_event=None,
        candidate_event=event,
        description=description,
        expected=None,
        actual=_event_summary(event),
        impact=_impact(matches, baseline, candidate, match_index),
        source_metadata={"detector": "extra_event", "match_index": match_index},
    )


def _changed_tool_args(
    baseline_event: Event,
    candidate_event: Event,
    alignment_kind: _PairedKind,
    matches: list[Match],
    baseline: Trace,
    candidate: Trace,
    match_index: int,
) -> Divergence | None:
    baseline_args = _tool_arguments(baseline_event)
    candidate_args = _tool_arguments(candidate_event)
    if baseline_args is None or candidate_args is None:
        return None
    if _canonical_json(baseline_args) == _canonical_json(candidate_args):
        return None
    return Divergence(
        type="changed_tool_args",
        severity="CRITICAL",
        baseline_event=baseline_event,
        candidate_event=candidate_event,
        description=f"{baseline_event.type.value} {baseline_event.semantic_name} arguments differ",
        expected=baseline_args,
        actual=candidate_args,
        impact=_impact(matches, baseline, candidate, match_index),
        source_metadata={
            "detector": "changed_tool_args",
            "alignment_kind": alignment_kind,
            "match_index": match_index,
        },
    )


def _branch_changed(
    baseline_event: Event,
    candidate_event: Event,
    alignment_kind: _PairedKind,
    matches: list[Match],
    baseline: Trace,
    candidate: Trace,
    match_index: int,
) -> Divergence | None:
    if not isinstance(baseline_event.payload, BranchDecisionPayload) or not isinstance(
        candidate_event.payload,
        BranchDecisionPayload,
    ):
        return None
    if baseline_event.payload.chosen_branch == candidate_event.payload.chosen_branch:
        return None
    return Divergence(
        type="branch_changed",
        severity="CRITICAL",
        baseline_event=baseline_event,
        candidate_event=candidate_event,
        description=f"BRANCH_DECISION {baseline_event.payload.branch_name} took different branch",
        expected=baseline_event.payload.chosen_branch,
        actual=candidate_event.payload.chosen_branch,
        impact=_impact(matches, baseline, candidate, match_index),
        source_metadata={
            "detector": "branch_changed",
            "alignment_kind": alignment_kind,
            "branch_name": baseline_event.payload.branch_name,
            "match_index": match_index,
        },
    )


def _per_event_cost_regression(
    baseline_event: Event,
    candidate_event: Event,
    alignment_kind: _PairedKind,
    matches: list[Match],
    baseline: Trace,
    candidate: Trace,
    match_index: int,
    threshold: float,
) -> Divergence | None:
    if not isinstance(baseline_event.payload, LLMCallPayload) or not isinstance(
        candidate_event.payload,
        LLMCallPayload,
    ):
        return None
    ratio = _cost_ratio(candidate_event.payload.cost_usd, baseline_event.payload.cost_usd)
    if ratio <= threshold:
        return None
    return Divergence(
        type="cost_regression",
        severity="MEDIUM",
        baseline_event=baseline_event,
        candidate_event=candidate_event,
        description=(
            f"LLM_CALL {baseline_event.semantic_name} cost rose "
            f"{_format_ratio(ratio)}x (severity threshold {threshold:g})"
        ),
        expected=baseline_event.payload.cost_usd,
        actual=candidate_event.payload.cost_usd,
        impact=_impact(matches, baseline, candidate, match_index),
        source_metadata={
            "detector": "cost_regression",
            "alignment_kind": alignment_kind,
            "scope": "event",
            "match_index": match_index,
        },
    )


def _trace_cost_regression(
    matches: list[Match],
    baseline: Trace,
    candidate: Trace,
    threshold: float,
) -> Divergence | None:
    baseline_cost = _trace_cost(baseline)
    candidate_cost = _trace_cost(candidate)
    ratio = _cost_ratio(candidate_cost, baseline_cost)
    if ratio <= threshold:
        return None
    return Divergence(
        type="cost_regression",
        severity="MEDIUM",
        baseline_event=None,
        candidate_event=None,
        description=f"Total cost rose {_format_ratio(ratio)}x (severity threshold {threshold:g})",
        expected=baseline_cost,
        actual=candidate_cost,
        impact=_impact(matches, baseline, candidate, 0),
        source_metadata={
            "detector": "cost_regression",
            "scope": "trace",
            "match_index": len(matches),
        },
    )


def _impact(
    matches: list[Match],
    baseline: Trace,
    candidate: Trace,
    match_index: int,
) -> ImpactAnalysis:
    window = matches[match_index:]
    baseline_events = [match.baseline_event for match in window if match.baseline_event is not None]
    candidate_events = [
        match.candidate_event for match in window if match.candidate_event is not None
    ]
    return ImpactAnalysis(
        tokens_delta=_token_total(candidate_events) - _token_total(baseline_events),
        cost_delta_ratio=_cost_ratio(_cost_total(candidate_events), _cost_total(baseline_events)),
        final_output_changed=_final_output(baseline) != _final_output(candidate),
        errors_introduced=_errors_introduced(window),
        affected_event_count=sum(1 for match in window if match.kind != "ID_MATCH"),
    )


def _tool_arguments(event: Event) -> JsonObject | None:
    if isinstance(event.payload, ToolCallPayload | MCPCallPayload):
        return event.payload.arguments
    return None


def _event_summary(event: Event) -> JsonObject:
    return {
        "event_type": event.type.value,
        "semantic_name": event.semantic_name,
        "sequence_index": event.sequence_index,
    }


def _errors_introduced(matches: list[Match]) -> list[JsonObject]:
    errors: list[JsonObject] = []
    for match in matches:
        event = match.candidate_event
        if event is None or event.type is not EventType.ERROR:
            continue
        if match.baseline_event is not None and match.baseline_event.type is EventType.ERROR:
            continue
        if not isinstance(event.payload, ErrorPayload):
            continue
        errors.append(
            {
                "error_type": event.payload.error_type,
                "message": event.payload.message,
                "sequence_index": event.sequence_index,
            }
        )
    return errors


def _token_total(events: list[Event]) -> int:
    total = 0
    for event in events:
        if isinstance(event.payload, LLMCallPayload):
            total += event.payload.input_tokens + event.payload.output_tokens
    if total > 0:
        return total
    for event in reversed(events):
        if isinstance(event.payload, RunEndPayload):
            return event.payload.total_input_tokens + event.payload.total_output_tokens
    return total


def _cost_total(events: list[Event]) -> float:
    total = 0.0
    for event in events:
        if isinstance(event.payload, LLMCallPayload):
            total += event.payload.cost_usd
    if total > 0.0:
        return total
    for event in reversed(events):
        if isinstance(event.payload, RunEndPayload):
            return event.payload.total_cost_usd
    return total


def _trace_cost(trace: Trace) -> float:
    run_end = _run_end(trace)
    if run_end is not None:
        return run_end.total_cost_usd
    return _cost_total(trace.events)


def _final_output(trace: Trace) -> str | None:
    run_end = _run_end(trace)
    if run_end is None:
        return None
    return run_end.final_output


def _run_end(trace: Trace) -> RunEndPayload | None:
    for event in reversed(trace.events):
        if isinstance(event.payload, RunEndPayload):
            return event.payload
    return None


def _cost_ratio(candidate_cost: float, baseline_cost: float) -> float:
    epsilon = 1e-12
    return candidate_cost / max(baseline_cost, epsilon)


def _format_ratio(ratio: float) -> str:
    return f"{ratio:.2f}".rstrip("0").rstrip(".")


def _severity_rank(severity: Severity) -> int:
    return {
        "INFO": 0,
        "LOW": 1,
        "MEDIUM": 2,
        "HIGH": 3,
        "CRITICAL": 4,
    }[severity]


def _match_index(divergence: Divergence) -> int:
    raw_index = divergence.source_metadata.get("match_index")
    if isinstance(raw_index, int):
        return raw_index
    return 10**12


def _canonical_json(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 9)
    if isinstance(value, list):
        return [_canonical_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical_json(value[key]) for key in sorted(value)}
    return value
