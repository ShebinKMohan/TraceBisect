"""Tiered alignment for canonical TraceBisect traces.

Implements the V1 alignment contract in ``spec/alignment-algorithm.md``:
parent-local stable-id matching, structural matching, positional fallback,
and deterministic DFS output with expanded insertion/deletion subtrees.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypeAlias

from tracebisect.schema import (
    BranchDecisionPayload,
    Event,
    EventType,
    LLMCallPayload,
    MCPCallPayload,
    RetrievalPayload,
    ToolCallPayload,
    Trace,
)

MatchKind: TypeAlias = Literal[
    "ID_MATCH",
    "STRUCTURAL_MATCH",
    "POSITIONAL_MATCH",
    "DELETION",
    "INSERTION",
]

StableID: TypeAlias = tuple[str, ...]

__all__ = [
    "EmptyTraceError",
    "Match",
    "MatchKind",
    "RootTypeMismatch",
    "SchemaVersionMismatch",
    "TraceCycleError",
    "align",
]


class AlignmentError(ValueError):
    """Base class for alignment precondition failures."""


class RootTypeMismatch(AlignmentError):
    """Raised when either trace does not have a RUN_START root."""


class TraceCycleError(AlignmentError):
    """Raised when parent links do not form a valid tree."""


class SchemaVersionMismatch(AlignmentError):
    """Raised when traces with different schema versions are aligned."""


class EmptyTraceError(AlignmentError):
    """Raised when a trace has no events."""


@dataclass(frozen=True, slots=True)
class Match:
    baseline_event: Event | None
    candidate_event: Event | None
    kind: MatchKind


@dataclass(frozen=True, slots=True)
class _PairRecord:
    baseline_event: Event | None
    candidate_event: Event | None
    kind: MatchKind


@dataclass(frozen=True, slots=True)
class _ExpandMatchedParent:
    baseline_parent: Event
    candidate_parent: Event


@dataclass(frozen=True, slots=True)
class _EmitMatchedPair:
    baseline_event: Event
    candidate_event: Event
    kind: Literal["ID_MATCH", "STRUCTURAL_MATCH", "POSITIONAL_MATCH"]


@dataclass(frozen=True, slots=True)
class _EmitUnmatchedSubtree:
    event: Event
    side: Literal["DELETION", "INSERTION"]


_Frame: TypeAlias = _ExpandMatchedParent | _EmitMatchedPair | _EmitUnmatchedSubtree


def align(baseline: Trace, candidate: Trace) -> list[Match]:
    """Align two canonical traces and return deterministic DFS-ordered matches."""
    _validate_pair(baseline, candidate)
    baseline_children = _children_by_parent(baseline)
    candidate_children = _children_by_parent(candidate)

    matches: list[Match] = [
        Match(baseline.root_event, candidate.root_event, "ID_MATCH"),
    ]
    stack: list[_Frame] = [_ExpandMatchedParent(baseline.root_event, candidate.root_event)]
    while stack:
        frame = stack.pop()
        if isinstance(frame, _ExpandMatchedParent):
            records = _pair_children(
                baseline_children.get(frame.baseline_parent.id, []),
                candidate_children.get(frame.candidate_parent.id, []),
            )
            for record in reversed(records):
                if record.kind in ("ID_MATCH", "STRUCTURAL_MATCH", "POSITIONAL_MATCH"):
                    baseline_event = record.baseline_event
                    candidate_event = record.candidate_event
                    if baseline_event is None or candidate_event is None:
                        raise AssertionError("matched records must have both events")
                    stack.append(
                        _EmitMatchedPair(
                            baseline_event,
                            candidate_event,
                            record.kind,
                        )
                    )
                elif record.kind == "DELETION":
                    if record.baseline_event is None:
                        raise AssertionError("deletion records must have a baseline event")
                    stack.append(_EmitUnmatchedSubtree(record.baseline_event, "DELETION"))
                elif record.kind == "INSERTION":
                    if record.candidate_event is None:
                        raise AssertionError("insertion records must have a candidate event")
                    stack.append(_EmitUnmatchedSubtree(record.candidate_event, "INSERTION"))
        elif isinstance(frame, _EmitMatchedPair):
            matches.append(Match(frame.baseline_event, frame.candidate_event, frame.kind))
            stack.append(_ExpandMatchedParent(frame.baseline_event, frame.candidate_event))
        else:
            if frame.side == "DELETION":
                matches.append(Match(frame.event, None, "DELETION"))
                children = baseline_children
            else:
                matches.append(Match(None, frame.event, "INSERTION"))
                children = candidate_children
            for child in reversed(children.get(frame.event.id, [])):
                stack.append(_EmitUnmatchedSubtree(child, frame.side))
    return matches


def _validate_pair(baseline: Trace, candidate: Trace) -> None:
    if baseline.schema_version != candidate.schema_version:
        raise SchemaVersionMismatch(
            f"schema versions differ: {baseline.schema_version} != {candidate.schema_version}"
        )
    _validate_trace(baseline, name="baseline")
    _validate_trace(candidate, name="candidate")


def _validate_trace(trace: Trace, *, name: str) -> None:
    if not trace.events:
        raise EmptyTraceError(f"{name} trace has no events")
    if (
        trace.root_event.type is not EventType.RUN_START
        or trace.events[0].type is not EventType.RUN_START
    ):
        raise RootTypeMismatch(f"{name} trace root must be RUN_START")
    if trace.events[0] is not trace.root_event:
        raise RootTypeMismatch(f"{name} trace events[0] must be root_event")

    by_id = {event.id: event for event in trace.events}
    if len(by_id) != len(trace.events):
        raise TraceCycleError(f"{name} trace contains duplicate event ids")

    for event in trace.events:
        seen: set[str] = {event.id}
        current = event
        while current.parent_id is not None:
            parent = by_id.get(current.parent_id)
            if parent is None:
                raise TraceCycleError(
                    f"{name} event {current.id!r} references unknown parent {current.parent_id!r}"
                )
            if parent.id in seen:
                raise TraceCycleError(f"{name} trace contains a parent cycle at {parent.id!r}")
            seen.add(parent.id)
            current = parent


def _children_by_parent(trace: Trace) -> dict[str, list[Event]]:
    children: dict[str, list[Event]] = {}
    for event in trace.events:
        if event.parent_id is not None:
            children.setdefault(event.parent_id, []).append(event)
    for child_list in children.values():
        child_list.sort(key=lambda event: event.sequence_index)
    return children


def _emit_matched_parent(
    baseline_parent: Event,
    candidate_parent: Event,
    *,
    baseline_children: dict[str, list[Event]],
    candidate_children: dict[str, list[Event]],
    matches: list[Match],
) -> None:
    records = _pair_children(
        baseline_children.get(baseline_parent.id, []),
        candidate_children.get(candidate_parent.id, []),
    )
    for record in records:
        if record.kind in ("ID_MATCH", "STRUCTURAL_MATCH", "POSITIONAL_MATCH"):
            baseline_event = record.baseline_event
            candidate_event = record.candidate_event
            if baseline_event is None or candidate_event is None:
                raise AssertionError("matched records must have both events")
            matches.append(Match(baseline_event, candidate_event, record.kind))
            _emit_matched_parent(
                baseline_event,
                candidate_event,
                baseline_children=baseline_children,
                candidate_children=candidate_children,
                matches=matches,
            )
        elif record.kind == "DELETION":
            if record.baseline_event is None:
                raise AssertionError("deletion records must have a baseline event")
            _emit_unmatched_subtree(
                record.baseline_event,
                side="DELETION",
                children=baseline_children,
                matches=matches,
            )
        elif record.kind == "INSERTION":
            if record.candidate_event is None:
                raise AssertionError("insertion records must have a candidate event")
            _emit_unmatched_subtree(
                record.candidate_event,
                side="INSERTION",
                children=candidate_children,
                matches=matches,
            )


def _emit_unmatched_subtree(
    event: Event,
    *,
    side: Literal["DELETION", "INSERTION"],
    children: dict[str, list[Event]],
    matches: list[Match],
) -> None:
    if side == "DELETION":
        matches.append(Match(event, None, "DELETION"))
    else:
        matches.append(Match(None, event, "INSERTION"))
    for child in children.get(event.id, []):
        _emit_unmatched_subtree(child, side=side, children=children, matches=matches)


def _pair_children(
    baseline_children: list[Event],
    candidate_children: list[Event],
) -> list[_PairRecord]:
    pair_by_baseline_id: dict[str, _PairRecord] = {}
    paired_baseline: set[str] = set()
    paired_candidate: set[str] = set()

    _pair_by_key(
        baseline_children,
        candidate_children,
        paired_baseline=paired_baseline,
        paired_candidate=paired_candidate,
        pair_by_baseline_id=pair_by_baseline_id,
        key_func=_event_id_key,
        kind="ID_MATCH",
    )
    _pair_by_key(
        baseline_children,
        candidate_children,
        paired_baseline=paired_baseline,
        paired_candidate=paired_candidate,
        pair_by_baseline_id=pair_by_baseline_id,
        key_func=_source_event_id_key,
        kind="ID_MATCH",
    )
    _pair_by_key(
        baseline_children,
        candidate_children,
        paired_baseline=paired_baseline,
        paired_candidate=paired_candidate,
        pair_by_baseline_id=pair_by_baseline_id,
        key_func=_computed_stable_key,
        kind="ID_MATCH",
    )
    _pair_structural(
        baseline_children,
        candidate_children,
        paired_baseline=paired_baseline,
        paired_candidate=paired_candidate,
        pair_by_baseline_id=pair_by_baseline_id,
    )
    _pair_positional(
        baseline_children,
        candidate_children,
        paired_baseline=paired_baseline,
        paired_candidate=paired_candidate,
        pair_by_baseline_id=pair_by_baseline_id,
    )
    return _merge_records(
        baseline_children,
        candidate_children,
        pair_by_baseline_id=pair_by_baseline_id,
        paired_candidate=paired_candidate,
    )


def _pair_by_key(
    baseline_children: list[Event],
    candidate_children: list[Event],
    *,
    paired_baseline: set[str],
    paired_candidate: set[str],
    pair_by_baseline_id: dict[str, _PairRecord],
    key_func: _KeyFunc,
    kind: Literal["ID_MATCH"],
) -> None:
    baseline_buckets = _bucket_by_key(baseline_children, paired_baseline, key_func)
    candidate_buckets = _bucket_by_key(candidate_children, paired_candidate, key_func)
    for key in sorted(baseline_buckets.keys() & candidate_buckets.keys()):
        b_bucket = baseline_buckets[key]
        c_bucket = candidate_buckets[key]
        for baseline_event, candidate_event in zip(b_bucket, c_bucket, strict=False):
            if baseline_event.type is not candidate_event.type:
                continue
            _record_pair(
                baseline_event,
                candidate_event,
                kind=kind,
                paired_baseline=paired_baseline,
                paired_candidate=paired_candidate,
                pair_by_baseline_id=pair_by_baseline_id,
            )


_KeyFunc: TypeAlias = Callable[[Event], StableID | None]


def _bucket_by_key(
    events: list[Event],
    paired: set[str],
    key_func: _KeyFunc,
) -> dict[StableID, list[Event]]:
    buckets: dict[StableID, list[Event]] = {}
    for event in events:
        if event.id in paired:
            continue
        key = key_func(event)
        if key is None:
            continue
        buckets.setdefault(key, []).append(event)
    for bucket in buckets.values():
        bucket.sort(key=lambda event: event.sequence_index)
    return buckets


def _pair_structural(
    baseline_children: list[Event],
    candidate_children: list[Event],
    *,
    paired_baseline: set[str],
    paired_candidate: set[str],
    pair_by_baseline_id: dict[str, _PairRecord],
) -> None:
    for baseline_event in baseline_children:
        if baseline_event.id in paired_baseline:
            continue
        best: Event | None = None
        best_distance: int | None = None
        for candidate_event in candidate_children:
            if candidate_event.id in paired_candidate:
                continue
            if candidate_event.type is not baseline_event.type:
                continue
            distance = _levenshtein(baseline_event.semantic_name, candidate_event.semantic_name)
            if distance > 2:
                continue
            if best is None or distance < (best_distance or 0):
                best = candidate_event
                best_distance = distance
        if best is not None:
            _record_pair(
                baseline_event,
                best,
                kind="STRUCTURAL_MATCH",
                paired_baseline=paired_baseline,
                paired_candidate=paired_candidate,
                pair_by_baseline_id=pair_by_baseline_id,
            )


def _pair_positional(
    baseline_children: list[Event],
    candidate_children: list[Event],
    *,
    paired_baseline: set[str],
    paired_candidate: set[str],
    pair_by_baseline_id: dict[str, _PairRecord],
) -> None:
    baseline_remaining = [e for e in baseline_children if e.id not in paired_baseline]
    candidate_remaining = [e for e in candidate_children if e.id not in paired_candidate]
    for baseline_event, candidate_event in zip(
        baseline_remaining,
        candidate_remaining,
        strict=False,
    ):
        if baseline_event.type is candidate_event.type:
            _record_pair(
                baseline_event,
                candidate_event,
                kind="POSITIONAL_MATCH",
                paired_baseline=paired_baseline,
                paired_candidate=paired_candidate,
                pair_by_baseline_id=pair_by_baseline_id,
            )


def _record_pair(
    baseline_event: Event,
    candidate_event: Event,
    *,
    kind: Literal["ID_MATCH", "STRUCTURAL_MATCH", "POSITIONAL_MATCH"],
    paired_baseline: set[str],
    paired_candidate: set[str],
    pair_by_baseline_id: dict[str, _PairRecord],
) -> None:
    paired_baseline.add(baseline_event.id)
    paired_candidate.add(candidate_event.id)
    pair_by_baseline_id[baseline_event.id] = _PairRecord(
        baseline_event,
        candidate_event,
        kind,
    )


def _merge_records(
    baseline_children: list[Event],
    candidate_children: list[Event],
    *,
    pair_by_baseline_id: dict[str, _PairRecord],
    paired_candidate: set[str],
) -> list[_PairRecord]:
    candidate_index = {event.id: i for i, event in enumerate(candidate_children)}
    records: list[_PairRecord] = []
    candidate_cursor = 0
    for baseline_event in baseline_children:
        pair = pair_by_baseline_id.get(baseline_event.id)
        if pair is None:
            records.append(_PairRecord(baseline_event, None, "DELETION"))
            continue
        candidate_event = pair.candidate_event
        if candidate_event is None:
            raise AssertionError("paired record missing candidate event")
        target_index = candidate_index[candidate_event.id]
        while candidate_cursor < target_index:
            candidate = candidate_children[candidate_cursor]
            if candidate.id not in paired_candidate:
                records.append(_PairRecord(None, candidate, "INSERTION"))
            candidate_cursor += 1
        records.append(pair)
        candidate_cursor = max(candidate_cursor, target_index + 1)

    while candidate_cursor < len(candidate_children):
        candidate = candidate_children[candidate_cursor]
        if candidate.id not in paired_candidate:
            records.append(_PairRecord(None, candidate, "INSERTION"))
        candidate_cursor += 1
    return records


def _event_id_key(event: Event) -> StableID:
    return ("event_id", event.id)


def _source_event_id_key(event: Event) -> StableID:
    return ("source_event_id", event.source_event_id)


def _computed_stable_key(event: Event) -> StableID | None:
    payload = event.payload
    if isinstance(payload, LLMCallPayload):
        if event.prompt_version is None:
            return None
        return (
            "llm",
            event.prompt_version,
            payload.model,
            _stable_hash(payload.messages),
        )
    if isinstance(payload, ToolCallPayload):
        return ("tool", payload.tool_name, _stable_hash(payload.arguments))
    if isinstance(payload, MCPCallPayload):
        return (
            "mcp",
            payload.server_name,
            payload.tool_name,
            _stable_hash(payload.arguments),
        )
    if isinstance(payload, RetrievalPayload):
        return ("retrieval", payload.retriever, _stable_hash(payload.query))
    if isinstance(payload, BranchDecisionPayload):
        return ("branch", payload.branch_name)
    return None


def _stable_hash(value: object) -> str:
    normalized = _normalize_json(value)
    data = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def _normalize_json(value: object) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return round(value, 9)
    if isinstance(value, list):
        return [_normalize_json(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _normalize_json(v) for k, v in sorted(value.items())}
    return value


def _levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            insertion = current[j - 1] + 1
            deletion = previous[j] + 1
            substitution = previous[j - 1] + (left_char != right_char)
            current.append(min(insertion, deletion, substitution))
        previous = current
    return previous[-1]
