"""JSONL serialization for canonical TraceBisect traces.

V1 stores `.tbtrace` files as line-oriented JSONL per
``spec/canonical-trace-schema.md`` §6 and ``spec/production-spec.md`` §10:
the first line is a trace header, every subsequent line is one Event in
``sequence_index`` order.

Datetimes serialize as ISO 8601 with the ``Z`` suffix. Optional fields whose
value is ``None`` are written as JSON ``null`` rather than omitted, so the
on-disk layout is keyed identically for every event.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast

from tracebisect.schema import (
    SCHEMA_VERSION,
    BranchDecisionPayload,
    BrokenParentChainError,
    ErrorPayload,
    Event,
    EventPayload,
    EventType,
    HumanInputPayload,
    JsonObject,
    JsonValue,
    LLMCallPayload,
    MalformedTraceError,
    MCPCallPayload,
    RetrievalPayload,
    RunEndPayload,
    RunStartPayload,
    StateTransitionPayload,
    ToolCallPayload,
    Trace,
    TraceBisectSchemaError,
    UnknownSchemaVersionError,
)

__all__ = ["read_trace", "write_trace"]


SourceConvention = Literal["native", "genai", "openinference", "mixed", "unknown"]
SourceFormat = Literal["otel", "native"]

_VALID_SOURCE_CONVENTIONS: frozenset[str] = frozenset(
    {"native", "genai", "openinference", "mixed", "unknown"}
)
_VALID_SOURCE_FORMATS: frozenset[str] = frozenset({"otel", "native"})


# ----------------------------- Public API ----------------------------- #


def write_trace(trace: Trace, path: str | Path) -> None:
    """Serialize ``trace`` to ``path`` as JSONL (header line + one Event per line)."""
    target = Path(path)
    lines: list[str] = [json.dumps(_header_to_dict(trace))]
    for event in trace.events:
        lines.append(json.dumps(_event_to_dict(event)))
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_trace(path: str | Path) -> Trace:
    """Parse a JSONL ``.tbtrace`` file at ``path`` and reconstruct a :class:`Trace`."""
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    raw_lines = [ln for ln in text.split("\n") if ln.strip()]
    if not raw_lines:
        raise MalformedTraceError("trace file is empty")

    parsed: list[JsonObject] = []
    for lineno, raw in enumerate(raw_lines, start=1):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MalformedTraceError(f"line {lineno}: invalid JSON: {exc.msg}") from exc
        if not isinstance(obj, dict):
            raise MalformedTraceError(f"line {lineno}: not a JSON object")
        parsed.append(cast(JsonObject, obj))

    header = parsed[0]
    trace_id, created_at, source_convention = _header_from_dict(header)

    events: list[Event] = []
    for idx, raw_event in enumerate(parsed[1:]):
        events.append(_event_from_dict(raw_event, line_number=idx + 2))

    if not events:
        raise MalformedTraceError("trace contains no events; expected at least a RUN_START root")

    _validate_tree(events)

    return Trace(
        schema_version=SCHEMA_VERSION,
        trace_id=trace_id,
        created_at=created_at,
        root_event=events[0],
        events=events,
        source_convention=source_convention,
    )


# ----------------------------- Header I/O ----------------------------- #


def _header_to_dict(trace: Trace) -> JsonObject:
    return {
        "schema_version": trace.schema_version,
        "trace_id": trace.trace_id,
        "created_at": _format_datetime(trace.created_at),
        "source_convention": trace.source_convention,
    }


def _header_from_dict(header: JsonObject) -> tuple[str, datetime, SourceConvention]:
    if "schema_version" not in header:
        raise MalformedTraceError("header is missing 'schema_version'")
    schema_version_raw = header["schema_version"]
    if not isinstance(schema_version_raw, int) or isinstance(schema_version_raw, bool):
        raise MalformedTraceError(
            f"header 'schema_version' must be an integer, got {type(schema_version_raw).__name__}"
        )
    if schema_version_raw != SCHEMA_VERSION:
        raise UnknownSchemaVersionError(
            f"unknown schema_version {schema_version_raw}; this build supports {SCHEMA_VERSION}"
        )

    trace_id = _required_str(header, "trace_id", context="header")

    created_at_raw = _required_str(header, "created_at", context="header")
    try:
        created_at = _parse_datetime(created_at_raw)
    except ValueError as exc:
        raise MalformedTraceError(f"header 'created_at' is not a valid datetime: {exc}") from exc

    convention_raw = _required_str(header, "source_convention", context="header")
    if convention_raw not in _VALID_SOURCE_CONVENTIONS:
        raise MalformedTraceError(
            f"header 'source_convention' must be one of {sorted(_VALID_SOURCE_CONVENTIONS)}; "
            f"got {convention_raw!r}"
        )
    return trace_id, created_at, cast(SourceConvention, convention_raw)


# ----------------------------- Event I/O ----------------------------- #


def _event_to_dict(event: Event) -> JsonObject:
    return {
        "id": event.id,
        "parent_id": event.parent_id,
        "sequence_index": event.sequence_index,
        "type": event.type.value,
        "semantic_name": event.semantic_name,
        "timestamp": _format_datetime(event.timestamp),
        "duration_ms": event.duration_ms,
        "payload": _payload_to_dict(event.payload),
        "source_format": event.source_format,
        "source_event_id": event.source_event_id,
        "model_version": event.model_version,
        "prompt_version": event.prompt_version,
        "code_sha": event.code_sha,
        "sampling_params": event.sampling_params,
    }


def _event_from_dict(d: JsonObject, *, line_number: int) -> Event:
    ctx = f"event on line {line_number}"
    event_id = _required_str(d, "id", context=ctx)
    parent_id = _optional_str(d, "parent_id", context=ctx)
    sequence_index = _required_int(d, "sequence_index", context=ctx)
    type_raw = _required_str(d, "type", context=ctx)
    try:
        event_type = EventType(type_raw)
    except ValueError as exc:
        raise MalformedTraceError(f"{ctx}: unknown event type {type_raw!r}") from exc
    semantic_name = _required_str(d, "semantic_name", context=ctx)

    timestamp_raw = _required_str(d, "timestamp", context=ctx)
    try:
        timestamp = _parse_datetime(timestamp_raw)
    except ValueError as exc:
        raise MalformedTraceError(f"{ctx}: 'timestamp' is not a valid datetime: {exc}") from exc

    duration_ms = _optional_float(d, "duration_ms", context=ctx)

    payload_raw = _required_json_object(d, "payload", context=ctx)
    payload = _payload_from_dict(event_type, payload_raw, context=ctx)

    source_format_raw = _required_str(d, "source_format", context=ctx)
    if source_format_raw not in _VALID_SOURCE_FORMATS:
        raise MalformedTraceError(
            f"{ctx}: 'source_format' must be one of {sorted(_VALID_SOURCE_FORMATS)}; "
            f"got {source_format_raw!r}"
        )
    source_format = cast(SourceFormat, source_format_raw)

    source_event_id = _required_str(d, "source_event_id", context=ctx)
    model_version = _optional_str(d, "model_version", context=ctx)
    prompt_version = _optional_str(d, "prompt_version", context=ctx)
    code_sha = _optional_str(d, "code_sha", context=ctx)
    sampling_params = _optional_json_object(d, "sampling_params", context=ctx)

    return Event(
        id=event_id,
        parent_id=parent_id,
        sequence_index=sequence_index,
        type=event_type,
        semantic_name=semantic_name,
        timestamp=timestamp,
        duration_ms=duration_ms,
        payload=payload,
        source_format=source_format,
        source_event_id=source_event_id,
        model_version=model_version,
        prompt_version=prompt_version,
        code_sha=code_sha,
        sampling_params=sampling_params,
    )


# ----------------------------- Payload I/O ----------------------------- #


def _payload_to_dict(payload: EventPayload) -> JsonObject:
    # Payload dataclasses contain only JSON-native scalars, JsonObject, or lists
    # thereof — no datetimes — so `asdict` produces a value suitable for json.dumps.
    return cast(JsonObject, asdict(payload))


def _payload_from_dict(event_type: EventType, d: JsonObject, *, context: str) -> EventPayload:
    if event_type is EventType.RUN_START:
        return RunStartPayload(
            user_input=_required_str(d, "user_input", context=context),
            agent_name=_optional_str(d, "agent_name", context=context),
            agent_version=_optional_str(d, "agent_version", context=context),
            run_metadata=_required_json_object(d, "run_metadata", context=context),
        )
    if event_type is EventType.RUN_END:
        return RunEndPayload(
            final_output=_required_str(d, "final_output", context=context),
            total_input_tokens=_required_int(d, "total_input_tokens", context=context),
            total_output_tokens=_required_int(d, "total_output_tokens", context=context),
            total_cost_usd=_required_float(d, "total_cost_usd", context=context),
            success=_required_bool(d, "success", context=context),
        )
    if event_type is EventType.LLM_CALL:
        return LLMCallPayload(
            model=_required_str(d, "model", context=context),
            provider=_required_str(d, "provider", context=context),
            messages=_required_json_object_list(d, "messages", context=context),
            response_text=_required_str(d, "response_text", context=context),
            response_tool_calls=_required_json_object_list(
                d, "response_tool_calls", context=context
            ),
            input_tokens=_required_int(d, "input_tokens", context=context),
            output_tokens=_required_int(d, "output_tokens", context=context),
            cost_usd=_required_float(d, "cost_usd", context=context),
            temperature=_required_float(d, "temperature", context=context),
            seed=_optional_int(d, "seed", context=context),
        )
    if event_type is EventType.TOOL_CALL:
        return ToolCallPayload(
            tool_name=_required_str(d, "tool_name", context=context),
            arguments=_required_json_object(d, "arguments", context=context),
            result=_optional_str(d, "result", context=context),
            error=_optional_str(d, "error", context=context),
        )
    if event_type is EventType.MCP_CALL:
        return MCPCallPayload(
            server_name=_required_str(d, "server_name", context=context),
            tool_name=_required_str(d, "tool_name", context=context),
            arguments=_required_json_object(d, "arguments", context=context),
            result=_optional_str(d, "result", context=context),
            error=_optional_str(d, "error", context=context),
            protocol_version=_optional_str(d, "protocol_version", context=context),
        )
    if event_type is EventType.RETRIEVAL:
        return RetrievalPayload(
            query=_required_str(d, "query", context=context),
            retriever=_required_str(d, "retriever", context=context),
            top_k=_required_int(d, "top_k", context=context),
            document_ids=_required_str_list(d, "document_ids", context=context),
            scores=_required_float_list(d, "scores", context=context),
        )
    if event_type is EventType.STATE_TRANSITION:
        return StateTransitionPayload(
            from_state=_optional_str(d, "from_state", context=context),
            to_state=_required_str(d, "to_state", context=context),
            trigger=_optional_str(d, "trigger", context=context),
        )
    if event_type is EventType.BRANCH_DECISION:
        return BranchDecisionPayload(
            branch_name=_required_str(d, "branch_name", context=context),
            chosen_branch=_required_str(d, "chosen_branch", context=context),
            available_branches=_required_str_list(d, "available_branches", context=context),
            reasoning=_optional_str(d, "reasoning", context=context),
        )
    if event_type is EventType.ERROR:
        return ErrorPayload(
            error_type=_required_str(d, "error_type", context=context),
            message=_required_str(d, "message", context=context),
            stack_trace=_optional_str(d, "stack_trace", context=context),
            recovered=_required_bool(d, "recovered", context=context),
        )
    if event_type is EventType.HUMAN_INPUT:
        return HumanInputPayload(
            prompt_shown=_required_str(d, "prompt_shown", context=context),
            response=_required_str(d, "response", context=context),
            responder_id=_optional_str(d, "responder_id", context=context),
        )
    # Exhaustive — EventType has exactly 10 members; keep mypy happy.
    raise MalformedTraceError(f"{context}: unhandled event type {event_type!r}")


# ----------------------------- Tree validation ----------------------------- #


def _validate_tree(events: list[Event]) -> None:
    root = events[0]
    if root.type is not EventType.RUN_START:
        raise MalformedTraceError(
            f"expected events[0] to be a RUN_START root event; got {root.type.value}"
        )
    if root.parent_id is not None:
        raise MalformedTraceError(
            f"root event {root.id!r} has non-null parent_id={root.parent_id!r}"
        )

    id_to_index: dict[str, int] = {}
    for i, event in enumerate(events):
        if event.sequence_index != i:
            raise MalformedTraceError(
                f"event {event.id!r}: sequence_index {event.sequence_index} "
                f"does not match position {i}"
            )
        if event.id in id_to_index:
            raise MalformedTraceError(f"duplicate event id {event.id!r}")
        id_to_index[event.id] = i

    for i, event in enumerate(events[1:], start=1):
        if event.parent_id is None:
            raise MalformedTraceError(
                f"non-root event {event.id!r} has parent_id=None; only the "
                "RUN_START root may omit a parent"
            )
        if event.parent_id not in id_to_index:
            raise BrokenParentChainError(
                f"event {event.id!r} references unknown parent_id={event.parent_id!r}"
            )
        parent_index = id_to_index[event.parent_id]
        if parent_index >= i:
            raise MalformedTraceError(
                f"event {event.id!r} at index {i} has parent_id={event.parent_id!r} "
                f"at index {parent_index}; parent must appear before child in DFS "
                "sequence_index order"
            )


# ----------------------------- Datetime helpers ----------------------------- #


def _format_datetime(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise TraceBisectSchemaError(
            "canonical datetimes must be timezone-aware (UTC); got naive datetime"
        )
    utc = dt.astimezone(timezone.utc)
    iso = utc.isoformat()
    if iso.endswith("+00:00"):
        iso = iso[:-6] + "Z"
    return iso


def _parse_datetime(value: str) -> datetime:
    candidate = value
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"{value!r} is not ISO 8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{value!r} is missing a timezone offset")
    return parsed.astimezone(timezone.utc)


# ----------------------------- Field extractors ----------------------------- #


def _missing(field: str, *, context: str) -> MalformedTraceError:
    return MalformedTraceError(f"{context}: missing required field {field!r}")


def _wrong_type(
    field: str, *, context: str, expected: str, value: JsonValue
) -> MalformedTraceError:
    return MalformedTraceError(
        f"{context}: field {field!r} must be {expected}; got {type(value).__name__}"
    )


def _required_str(d: JsonObject, field: str, *, context: str) -> str:
    if field not in d:
        raise _missing(field, context=context)
    value = d[field]
    if not isinstance(value, str):
        raise _wrong_type(field, context=context, expected="a string", value=value)
    return value


def _optional_str(d: JsonObject, field: str, *, context: str) -> str | None:
    if field not in d:
        raise _missing(field, context=context)
    value = d[field]
    if value is None:
        return None
    if not isinstance(value, str):
        raise _wrong_type(field, context=context, expected="a string or null", value=value)
    return value


def _required_int(d: JsonObject, field: str, *, context: str) -> int:
    if field not in d:
        raise _missing(field, context=context)
    value = d[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise _wrong_type(field, context=context, expected="an integer", value=value)
    return value


def _optional_int(d: JsonObject, field: str, *, context: str) -> int | None:
    if field not in d:
        raise _missing(field, context=context)
    value = d[field]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _wrong_type(field, context=context, expected="an integer or null", value=value)
    return value


def _required_float(d: JsonObject, field: str, *, context: str) -> float:
    if field not in d:
        raise _missing(field, context=context)
    value = d[field]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _wrong_type(field, context=context, expected="a number", value=value)
    return float(value)


def _optional_float(d: JsonObject, field: str, *, context: str) -> float | None:
    if field not in d:
        raise _missing(field, context=context)
    value = d[field]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _wrong_type(field, context=context, expected="a number or null", value=value)
    return float(value)


def _required_bool(d: JsonObject, field: str, *, context: str) -> bool:
    if field not in d:
        raise _missing(field, context=context)
    value = d[field]
    if not isinstance(value, bool):
        raise _wrong_type(field, context=context, expected="a boolean", value=value)
    return value


def _required_json_object(d: JsonObject, field: str, *, context: str) -> JsonObject:
    if field not in d:
        raise _missing(field, context=context)
    value = d[field]
    if not isinstance(value, dict):
        raise _wrong_type(field, context=context, expected="a JSON object", value=value)
    return value


def _optional_json_object(d: JsonObject, field: str, *, context: str) -> JsonObject | None:
    if field not in d:
        raise _missing(field, context=context)
    value = d[field]
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _wrong_type(field, context=context, expected="a JSON object or null", value=value)
    return value


def _required_str_list(d: JsonObject, field: str, *, context: str) -> list[str]:
    if field not in d:
        raise _missing(field, context=context)
    value = d[field]
    if not isinstance(value, list):
        raise _wrong_type(field, context=context, expected="a list of strings", value=value)
    result: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise MalformedTraceError(
                f"{context}: field {field!r}[{i}] must be a string; got {type(item).__name__}"
            )
        result.append(item)
    return result


def _required_float_list(d: JsonObject, field: str, *, context: str) -> list[float]:
    if field not in d:
        raise _missing(field, context=context)
    value = d[field]
    if not isinstance(value, list):
        raise _wrong_type(field, context=context, expected="a list of numbers", value=value)
    result: list[float] = []
    for i, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, int | float):
            raise MalformedTraceError(
                f"{context}: field {field!r}[{i}] must be a number; got {type(item).__name__}"
            )
        result.append(float(item))
    return result


def _required_json_object_list(d: JsonObject, field: str, *, context: str) -> list[JsonObject]:
    if field not in d:
        raise _missing(field, context=context)
    value = d[field]
    if not isinstance(value, list):
        raise _wrong_type(field, context=context, expected="a list of JSON objects", value=value)
    result: list[JsonObject] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise MalformedTraceError(
                f"{context}: field {field!r}[{i}] must be a JSON object; got {type(item).__name__}"
            )
        result.append(item)
    return result
