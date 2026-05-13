"""OTel / OpenInference JSON importer for canonical TraceBisect traces.

Implements Phase 3 Step 3.1 of the V1 plan: read OTLP-style JSON exports
(OpenInference and OTel GenAI semantic conventions, optionally mixed) and
hydrate canonical :class:`tracebisect.schema.Trace` objects per the
source-format mapping in ``spec/canonical-trace-schema.md`` §8 and
``spec/production-spec.md`` §5.1.

This module is ingestion-only. Alignment, divergence detection, rendering,
recording, and CLI wiring live elsewhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast

from tracebisect.schema import (
    SCHEMA_VERSION,
    ErrorPayload,
    Event,
    EventPayload,
    EventType,
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
)

__all__ = ["import_otel_json"]


SourceConvention = Literal["native", "genai", "openinference", "mixed", "unknown"]


@dataclass
class _SourceSpan:
    """Internal lossy projection of a single OTel span."""

    span_id: str
    parent_span_id: str | None
    trace_id: str
    name: str
    start_time: datetime
    end_time: datetime | None
    attributes: JsonObject
    exception_events: list[JsonObject] = field(default_factory=list)
    status_code: str | None = None  # "OK", "ERROR", "UNSET", or None


# ----------------------------- Public API ----------------------------- #


def import_otel_json(path: str | Path) -> Trace:
    """Read an OTLP-style JSON file and return a canonical :class:`Trace`."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise MalformedTraceError(f"OTel JSON at {p}: {exc}") from exc
    try:
        raw_obj: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MalformedTraceError(f"OTel JSON at {p}: invalid JSON: {exc.msg}") from exc
    if not isinstance(raw_obj, dict):
        raise MalformedTraceError(f"OTel JSON at {p}: top-level must be an object")
    raw: JsonObject = cast(JsonObject, raw_obj)

    spans = _extract_spans(raw, path=p)
    return _build_trace(spans, path=p)


# ----------------------------- OTel attribute decoding ----------------------------- #


def _decode_otel_value(value: object, *, context: str) -> JsonValue:
    """Decode one OTel ``AnyValue`` JSON variant into a plain ``JsonValue``."""
    if not isinstance(value, dict):
        raise MalformedTraceError(f"{context}: attribute value must be an OTel value object")
    if "stringValue" in value:
        v = value["stringValue"]
        if not isinstance(v, str):
            raise MalformedTraceError(f"{context}: stringValue must be a string")
        return v
    if "intValue" in value:
        v = value["intValue"]
        if isinstance(v, bool):
            raise MalformedTraceError(f"{context}: intValue must not be a boolean")
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            try:
                return int(v)
            except ValueError as exc:
                raise MalformedTraceError(
                    f"{context}: intValue {v!r} is not a valid integer"
                ) from exc
        raise MalformedTraceError(f"{context}: intValue must be an integer or numeric string")
    if "doubleValue" in value:
        v = value["doubleValue"]
        if isinstance(v, bool) or not isinstance(v, int | float):
            raise MalformedTraceError(f"{context}: doubleValue must be a number")
        return float(v)
    if "boolValue" in value:
        v = value["boolValue"]
        if not isinstance(v, bool):
            raise MalformedTraceError(f"{context}: boolValue must be a boolean")
        return v
    if "arrayValue" in value:
        arr_obj = value["arrayValue"]
        if not isinstance(arr_obj, dict):
            raise MalformedTraceError(f"{context}: arrayValue must be an object")
        values = arr_obj.get("values", [])
        if not isinstance(values, list):
            raise MalformedTraceError(f"{context}: arrayValue.values must be a list")
        decoded: list[JsonValue] = []
        for i, item in enumerate(values):
            decoded.append(_decode_otel_value(item, context=f"{context}[{i}]"))
        return decoded
    if "kvlistValue" in value:
        kvl_obj = value["kvlistValue"]
        if not isinstance(kvl_obj, dict):
            raise MalformedTraceError(f"{context}: kvlistValue must be an object")
        values = kvl_obj.get("values", [])
        if not isinstance(values, list):
            raise MalformedTraceError(f"{context}: kvlistValue.values must be a list")
        out: JsonObject = {}
        for i, kv in enumerate(values):
            if not isinstance(kv, dict):
                raise MalformedTraceError(f"{context}: kvlistValue[{i}] must be a key/value object")
            k = kv.get("key")
            inner = kv.get("value")
            if not isinstance(k, str):
                raise MalformedTraceError(f"{context}: kvlistValue[{i}].key must be a string")
            out[k] = _decode_otel_value(inner, context=f"{context}.{k}")
        return out
    raise MalformedTraceError(
        f"{context}: unrecognized OTel value variant; keys={sorted(value.keys())}"
    )


def _decode_otel_attributes(attrs: object, *, context: str) -> JsonObject:
    """Decode an OTel ``KeyValue`` attribute list into a flat ``JsonObject``."""
    if not isinstance(attrs, list):
        raise MalformedTraceError(f"{context}: attributes must be a list")
    out: JsonObject = {}
    for i, kv in enumerate(attrs):
        if not isinstance(kv, dict):
            raise MalformedTraceError(f"{context}: attribute[{i}] must be a key/value object")
        k = kv.get("key")
        inner = kv.get("value")
        if not isinstance(k, str):
            raise MalformedTraceError(f"{context}: attribute[{i}].key must be a string")
        out[k] = _decode_otel_value(inner, context=f"{context}.{k}")
    return out


# ----------------------------- Span extraction ----------------------------- #


def _parse_unix_nano(value: object, *, context: str) -> datetime:
    if isinstance(value, bool):
        raise MalformedTraceError(f"{context}: must be int or numeric string")
    if isinstance(value, int):
        nano = value
    elif isinstance(value, str):
        try:
            nano = int(value)
        except ValueError as exc:
            raise MalformedTraceError(
                f"{context}: {value!r} is not a valid unix-nano integer"
            ) from exc
    else:
        raise MalformedTraceError(f"{context}: must be int or numeric string")
    seconds = nano / 1_000_000_000
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _parse_status(status: object) -> str | None:
    if not isinstance(status, dict):
        return None
    code = status.get("code")
    if isinstance(code, str):
        return code[len("STATUS_CODE_") :] if code.startswith("STATUS_CODE_") else code
    if isinstance(code, int) and not isinstance(code, bool):
        return {0: "UNSET", 1: "OK", 2: "ERROR"}.get(code)
    return None


def _extract_one_span(sp: object, *, path: Path) -> _SourceSpan:
    if not isinstance(sp, dict):
        raise MalformedTraceError(f"{path}: each span must be a JSON object")

    span_id = sp.get("spanId")
    if not isinstance(span_id, str) or not span_id:
        raise MalformedTraceError(f"{path}: span is missing 'spanId'")

    trace_id = sp.get("traceId")
    if not isinstance(trace_id, str) or not trace_id:
        raise MalformedTraceError(f"{path}: span {span_id!r} is missing 'traceId'")

    parent_raw = sp.get("parentSpanId")
    if parent_raw is not None and not isinstance(parent_raw, str):
        raise MalformedTraceError(f"{path}: span {span_id!r} parentSpanId must be a string or null")
    parent_span_id: str | None = parent_raw if isinstance(parent_raw, str) and parent_raw else None

    name = sp.get("name", "")
    if not isinstance(name, str):
        raise MalformedTraceError(f"{path}: span {span_id!r} name must be a string")

    start_raw = sp.get("startTimeUnixNano")
    if start_raw is None:
        raise MalformedTraceError(f"{path}: span {span_id!r} is missing startTimeUnixNano")
    start_time = _parse_unix_nano(start_raw, context=f"span {span_id!r} startTimeUnixNano")

    end_raw = sp.get("endTimeUnixNano")
    end_time: datetime | None = None
    if end_raw is not None:
        end_time = _parse_unix_nano(end_raw, context=f"span {span_id!r} endTimeUnixNano")

    raw_attrs = sp.get("attributes", [])
    attributes = _decode_otel_attributes(raw_attrs, context=f"span {span_id!r} attributes")

    raw_events = sp.get("events", [])
    exception_events: list[JsonObject] = []
    if isinstance(raw_events, list):
        for i, ev in enumerate(raw_events):
            if not isinstance(ev, dict):
                continue
            ev_name = ev.get("name", "")
            ev_attrs_raw = ev.get("attributes", [])
            ev_attrs = _decode_otel_attributes(
                ev_attrs_raw,
                context=f"span {span_id!r} events[{i}] attributes",
            )
            if ev_name == "exception":
                exception_events.append(ev_attrs)

    status_code = _parse_status(sp.get("status"))

    return _SourceSpan(
        span_id=span_id,
        parent_span_id=parent_span_id,
        trace_id=trace_id,
        name=name,
        start_time=start_time,
        end_time=end_time,
        attributes=attributes,
        exception_events=exception_events,
        status_code=status_code,
    )


def _extract_spans(raw: JsonObject, *, path: Path) -> list[_SourceSpan]:
    if "resourceSpans" not in raw:
        raise MalformedTraceError(f"{path}: missing 'resourceSpans' at top level")
    resource_spans = raw["resourceSpans"]
    if not isinstance(resource_spans, list):
        raise MalformedTraceError(f"{path}: 'resourceSpans' must be a list")
    if not resource_spans:
        raise MalformedTraceError(f"{path}: 'resourceSpans' is empty")

    spans: list[_SourceSpan] = []
    for r_idx, rs in enumerate(resource_spans):
        if not isinstance(rs, dict):
            raise MalformedTraceError(f"{path}: resourceSpans[{r_idx}] must be an object")
        scope_spans = rs.get("scopeSpans", [])
        if not isinstance(scope_spans, list):
            raise MalformedTraceError(f"{path}: resourceSpans[{r_idx}].scopeSpans must be a list")
        for s_idx, ss in enumerate(scope_spans):
            if not isinstance(ss, dict):
                raise MalformedTraceError(f"{path}: scopeSpans[{s_idx}] must be an object")
            span_list = ss.get("spans", [])
            if not isinstance(span_list, list):
                raise MalformedTraceError(f"{path}: scopeSpans[{s_idx}].spans must be a list")
            for sp in span_list:
                spans.append(_extract_one_span(sp, path=path))
    if not spans:
        raise MalformedTraceError(f"{path}: no spans found anywhere in resourceSpans")
    return spans


# ----------------------------- Classification ----------------------------- #


def _oi_kind(span: _SourceSpan) -> str | None:
    v = span.attributes.get("openinference.span.kind")
    if isinstance(v, str):
        return v
    return None


def _is_genai_llm(span: _SourceSpan) -> bool:
    op = span.attributes.get("gen_ai.operation.name")
    if isinstance(op, str) and op in {"chat", "completion"}:
        return True
    return span.name in {"gen_ai.chat", "gen_ai.completion"}


def _is_mcp_call(span: _SourceSpan) -> bool:
    if span.name == "mcp.tool.call":
        return True
    return "mcp.tool.name" in span.attributes


def _span_event_type(span: _SourceSpan) -> EventType | None:
    """Map a source span to a canonical :class:`EventType`, or ``None`` if non-semantic."""
    kind = _oi_kind(span)
    if kind == "LLM":
        return EventType.LLM_CALL
    if kind == "TOOL":
        return EventType.TOOL_CALL
    if kind == "RETRIEVER":
        return EventType.RETRIEVAL
    if kind == "CHAIN":
        return EventType.STATE_TRANSITION
    if _is_mcp_call(span):
        return EventType.MCP_CALL
    if _is_genai_llm(span):
        return EventType.LLM_CALL
    return None


def _detect_convention(spans: list[_SourceSpan]) -> SourceConvention:
    has_oi = False
    has_genai = False
    for span in spans:
        attrs = span.attributes
        if _oi_kind(span) is not None or any(k.startswith("openinference.") for k in attrs):
            has_oi = True
        if _is_genai_llm(span) or any(k.startswith("gen_ai.") for k in attrs):
            has_genai = True
    if has_oi and has_genai:
        return "mixed"
    if has_oi:
        return "openinference"
    if has_genai:
        return "genai"
    return "unknown"


# ----------------------------- Attribute helpers ----------------------------- #


def _attr_str(attrs: JsonObject, key: str) -> str | None:
    v = attrs.get(key)
    return v if isinstance(v, str) else None


def _attr_int(attrs: JsonObject, key: str) -> int | None:
    v = attrs.get(key)
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v)
        except ValueError:
            return None
    return None


def _attr_float(attrs: JsonObject, key: str) -> float | None:
    v = attrs.get(key)
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _attr_object(attrs: JsonObject, key: str) -> JsonObject | None:
    v = attrs.get(key)
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return cast(JsonObject, parsed)
    return None


def _attr_object_list(attrs: JsonObject, key: str) -> list[JsonObject] | None:
    v = attrs.get(key)
    if isinstance(v, list):
        return [item for item in v if isinstance(item, dict)]
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, list):
            return [cast(JsonObject, item) for item in parsed if isinstance(item, dict)]
    return None


def _coalesce_str(attrs: JsonObject, *keys: str) -> str | None:
    for k in keys:
        v = _attr_str(attrs, k)
        if v is not None:
            return v
    return None


def _coalesce_int(attrs: JsonObject, *keys: str) -> int | None:
    for k in keys:
        v = _attr_int(attrs, k)
        if v is not None:
            return v
    return None


def _coalesce_float(attrs: JsonObject, *keys: str) -> float | None:
    for k in keys:
        v = _attr_float(attrs, k)
        if v is not None:
            return v
    return None


# ----------------------------- Payload builders ----------------------------- #


def _build_llm_payload(span: _SourceSpan) -> LLMCallPayload:
    attrs = span.attributes
    model = (
        _coalesce_str(
            attrs,
            "llm.model_name",
            "gen_ai.response.model",
            "gen_ai.request.model",
        )
        or "unknown"
    )
    provider = _coalesce_str(attrs, "llm.provider", "gen_ai.system") or "unknown"

    messages = _attr_object_list(attrs, "llm.input_messages") or []
    if not messages:
        user_input = _attr_str(attrs, "input.value") or _attr_str(attrs, "input")
        if user_input:
            messages = [{"role": "user", "content": user_input}]

    response_text = _attr_str(attrs, "output.value") or ""
    if not response_text:
        out_msgs = _attr_str(attrs, "llm.output_messages")
        if out_msgs:
            response_text = out_msgs

    tool_calls = (
        _attr_object_list(attrs, "llm.tool_calls")
        or _attr_object_list(attrs, "response.tool_calls")
        or []
    )

    input_tokens = _coalesce_int(attrs, "llm.token_count.prompt", "gen_ai.usage.input_tokens")
    if input_tokens is None:
        input_tokens = 0
    output_tokens = _coalesce_int(attrs, "llm.token_count.completion", "gen_ai.usage.output_tokens")
    if output_tokens is None:
        output_tokens = 0
    cost = _coalesce_float(attrs, "llm.cost.usd", "gen_ai.usage.cost_usd")
    if cost is None:
        cost = 0.0
    temperature = _coalesce_float(
        attrs,
        "llm.invocation_parameters.temperature",
        "gen_ai.request.temperature",
    )
    if temperature is None:
        temperature = 0.0
    seed = _coalesce_int(
        attrs,
        "llm.invocation_parameters.seed",
        "gen_ai.request.seed",
    )

    return LLMCallPayload(
        model=model,
        provider=provider,
        messages=messages,
        response_text=response_text,
        response_tool_calls=tool_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        temperature=temperature,
        seed=seed,
    )


def _build_tool_payload(span: _SourceSpan) -> ToolCallPayload:
    attrs = span.attributes
    tool_name = _attr_str(attrs, "tool.name") or span.name or "unknown_tool"
    arguments = (
        _attr_object(attrs, "tool.parameters")
        or _attr_object(attrs, "tool.arguments")
        or _attr_object(attrs, "input.value")
        or {}
    )
    result = _attr_str(attrs, "output.value")

    error: str | None = None
    if span.status_code == "ERROR":
        error = (
            _attr_str(attrs, "exception.message")
            or _attr_str(attrs, "status.message")
            or "tool call failed"
        )
    elif span.exception_events:
        error = _attr_str(span.exception_events[0], "exception.message") or "exception"

    return ToolCallPayload(
        tool_name=tool_name,
        arguments=arguments,
        result=result,
        error=error,
    )


def _build_mcp_payload(span: _SourceSpan) -> MCPCallPayload:
    attrs = span.attributes
    server_name = _attr_str(attrs, "mcp.server.name") or "unknown"
    tool_name = _attr_str(attrs, "mcp.tool.name") or span.name
    arguments = (
        _attr_object(attrs, "mcp.tool.arguments") or _attr_object(attrs, "input.value") or {}
    )
    result = _attr_str(attrs, "output.value")
    protocol_version = _attr_str(attrs, "mcp.protocol.version")

    error: str | None = None
    if span.status_code == "ERROR":
        error = (
            _attr_str(attrs, "exception.message")
            or _attr_str(attrs, "status.message")
            or "mcp call failed"
        )
    elif span.exception_events:
        error = _attr_str(span.exception_events[0], "exception.message") or "exception"

    return MCPCallPayload(
        server_name=server_name,
        tool_name=tool_name,
        arguments=arguments,
        result=result,
        error=error,
        protocol_version=protocol_version,
    )


def _build_retrieval_payload(span: _SourceSpan) -> RetrievalPayload:
    attrs = span.attributes
    query = _attr_str(attrs, "input.value") or _attr_str(attrs, "retrieval.query") or ""
    retriever = _attr_str(attrs, "retriever.name") or span.name or "unknown"

    document_ids: list[str] = []
    raw_docs = attrs.get("retrieval.documents")
    if isinstance(raw_docs, list):
        for d in raw_docs:
            if isinstance(d, dict):
                inner = d.get("id") or d.get("document.id")
                if isinstance(inner, str):
                    document_ids.append(inner)
            elif isinstance(d, str):
                document_ids.append(d)

    scores: list[float] = []
    raw_scores = attrs.get("retrieval.scores")
    if isinstance(raw_scores, list):
        for s in raw_scores:
            if not isinstance(s, bool) and isinstance(s, int | float):
                scores.append(float(s))

    top_k = _attr_int(attrs, "retrieval.top_k")
    if top_k is None:
        top_k = len(document_ids)

    return RetrievalPayload(
        query=query,
        retriever=retriever,
        top_k=top_k,
        document_ids=document_ids,
        scores=scores,
    )


def _build_state_transition_payload(span: _SourceSpan) -> StateTransitionPayload:
    return StateTransitionPayload(
        from_state=None,
        to_state=span.name,
        trigger=_attr_str(span.attributes, "input.value"),
    )


def _build_run_start_payload(
    root_span: _SourceSpan, *, trace_id: str, root_is_agent: bool
) -> RunStartPayload:
    attrs = root_span.attributes
    user_input = _attr_str(attrs, "input.value") or _attr_str(attrs, "input") or ""
    agent_name = (
        _attr_str(attrs, "openinference.agent.name")
        or _attr_str(attrs, "service.name")
        or root_span.name
    )
    agent_version = _attr_str(attrs, "service.version")

    run_metadata: JsonObject = {
        "trace_id": trace_id,
        "source_root_span_id": root_span.span_id,
        "source_root_span_name": root_span.name,
        "source_root_is_agent": root_is_agent,
    }
    return RunStartPayload(
        user_input=user_input,
        agent_name=agent_name,
        agent_version=agent_version,
        run_metadata=run_metadata,
    )


def _build_run_end_payload(root_span: _SourceSpan, events_so_far: list[Event]) -> RunEndPayload:
    attrs = root_span.attributes
    final_output = _attr_str(attrs, "output.value") or ""

    total_in_root = _coalesce_int(
        attrs,
        "llm.token_count.prompt",
        "gen_ai.usage.input_tokens",
    )
    total_out_root = _coalesce_int(
        attrs,
        "llm.token_count.completion",
        "gen_ai.usage.output_tokens",
    )
    cost_root = _coalesce_float(attrs, "llm.cost.usd", "gen_ai.usage.cost_usd")

    if total_in_root is None and total_out_root is None and cost_root is None:
        total_in = 0
        total_out = 0
        cost: float = 0.0
        for event in events_so_far:
            if isinstance(event.payload, LLMCallPayload):
                total_in += event.payload.input_tokens
                total_out += event.payload.output_tokens
                cost += event.payload.cost_usd
    else:
        total_in = total_in_root or 0
        total_out = total_out_root or 0
        cost = cost_root if cost_root is not None else 0.0

    success = root_span.status_code != "ERROR"

    return RunEndPayload(
        final_output=final_output,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_cost_usd=cost,
        success=success,
    )


# ----------------------------- Event emission ----------------------------- #


def _duration_ms(span: _SourceSpan) -> float | None:
    if span.end_time is None:
        return None
    return (span.end_time - span.start_time).total_seconds() * 1000.0


def _build_typed_event(
    span: _SourceSpan,
    event_type: EventType,
    *,
    parent_id: str,
    sequence_index: int,
) -> Event:
    payload: EventPayload
    semantic_name = span.name
    model_version: str | None = None

    if event_type is EventType.LLM_CALL:
        llm_payload = _build_llm_payload(span)
        payload = llm_payload
        if llm_payload.model and llm_payload.model != "unknown":
            semantic_name = llm_payload.model
            model_version = _attr_str(span.attributes, "gen_ai.response.model") or llm_payload.model
    elif event_type is EventType.TOOL_CALL:
        tool_payload = _build_tool_payload(span)
        payload = tool_payload
        if tool_payload.tool_name:
            semantic_name = tool_payload.tool_name
    elif event_type is EventType.MCP_CALL:
        mcp_payload = _build_mcp_payload(span)
        payload = mcp_payload
        if mcp_payload.tool_name:
            semantic_name = mcp_payload.tool_name
    elif event_type is EventType.RETRIEVAL:
        retrieval_payload = _build_retrieval_payload(span)
        payload = retrieval_payload
        if retrieval_payload.retriever:
            semantic_name = retrieval_payload.retriever
    elif event_type is EventType.STATE_TRANSITION:
        payload = _build_state_transition_payload(span)
    else:  # pragma: no cover - defensive; classifier never returns other types
        raise MalformedTraceError(f"span {span.span_id!r}: unexpected event type {event_type}")

    return Event(
        id=span.span_id,
        parent_id=parent_id,
        sequence_index=sequence_index,
        type=event_type,
        semantic_name=semantic_name,
        timestamp=span.start_time,
        duration_ms=_duration_ms(span),
        payload=payload,
        source_format="otel",
        source_event_id=span.span_id,
        model_version=model_version,
        prompt_version=None,
        code_sha=None,
        sampling_params=None,
    )


def _build_state_transition_event(
    span: _SourceSpan, *, parent_id: str, sequence_index: int
) -> Event:
    return Event(
        id=span.span_id,
        parent_id=parent_id,
        sequence_index=sequence_index,
        type=EventType.STATE_TRANSITION,
        semantic_name=span.name,
        timestamp=span.start_time,
        duration_ms=_duration_ms(span),
        payload=StateTransitionPayload(
            from_state=None,
            to_state=span.name,
            trigger=None,
        ),
        source_format="otel",
        source_event_id=span.span_id,
        model_version=None,
        prompt_version=None,
        code_sha=None,
        sampling_params=None,
    )


def _build_error_event(
    span: _SourceSpan,
    ex_attrs: JsonObject,
    *,
    parent_canonical_id: str,
    sequence_index: int,
    parent_status: str | None,
    occurrence: int,
) -> Event:
    error_type = _attr_str(ex_attrs, "exception.type") or "Error"
    message = _attr_str(ex_attrs, "exception.message") or ""
    stack = _attr_str(ex_attrs, "exception.stacktrace")
    recovered = parent_status != "ERROR"

    suffix = "" if occurrence == 0 else f":{occurrence}"
    err_id = f"{span.span_id}:error{suffix}"

    return Event(
        id=err_id,
        parent_id=parent_canonical_id,
        sequence_index=sequence_index,
        type=EventType.ERROR,
        semantic_name=error_type,
        timestamp=span.start_time,
        duration_ms=None,
        payload=ErrorPayload(
            error_type=error_type,
            message=message,
            stack_trace=stack,
            recovered=recovered,
        ),
        source_format="otel",
        source_event_id=err_id,
        model_version=None,
        prompt_version=None,
        code_sha=None,
        sampling_params=None,
    )


# ----------------------------- Trace assembly ----------------------------- #


def _build_trace(spans: list[_SourceSpan], *, path: Path) -> Trace:
    convention = _detect_convention(spans)
    if convention == "unknown":
        raise MalformedTraceError(
            f"{path}: no recognized OTel/OpenInference convention in any span"
        )

    agent_spans = [s for s in spans if _oi_kind(s) == "AGENT"]
    if len(agent_spans) > 1:
        raise MalformedTraceError(
            f"{path}: multiple OpenInference AGENT spans found; V1 expects exactly one"
        )
    agent_span: _SourceSpan | None = agent_spans[0] if agent_spans else None

    true_roots = [s for s in spans if s.parent_span_id is None]

    if agent_span is None:
        if len(true_roots) > 1:
            raise MalformedTraceError(f"{path}: multiple source root spans with no AGENT root")
        if not true_roots:
            raise MalformedTraceError(f"{path}: no AGENT root and no parent-less source root span")
        source_root: _SourceSpan = true_roots[0]
        scoped_spans = spans
    else:
        source_root = agent_span
        reachable_ids = _reachable_descendant_ids(agent_span, spans)
        scoped_spans = [s for s in spans if s.span_id in reachable_ids]

    root_is_agent = agent_span is not None
    root_event_type = _span_event_type(source_root)
    elide_root = root_is_agent or root_event_type is None

    trace_id = source_root.trace_id
    scoped_spans_by_id: dict[str, _SourceSpan] = {s.span_id: s for s in scoped_spans}

    # Build children map. Reparent orphans (parent_span_id points to a span we
    # never received) under source_root so we never crash on broken chains.
    children_of: dict[str, list[_SourceSpan]] = {}
    for s in scoped_spans:
        if s is source_root:
            continue
        if s.parent_span_id is not None and s.parent_span_id in scoped_spans_by_id:
            parent_id = s.parent_span_id
        else:
            parent_id = source_root.span_id
        children_of.setdefault(parent_id, []).append(s)
    for k in children_of:
        children_of[k].sort(key=lambda s: (s.start_time, s.span_id))

    events: list[Event] = []
    run_start_id = f"{source_root.span_id}:start"
    run_start = Event(
        id=run_start_id,
        parent_id=None,
        sequence_index=0,
        type=EventType.RUN_START,
        semantic_name=source_root.name or "run",
        timestamp=source_root.start_time,
        duration_ms=None,
        payload=_build_run_start_payload(
            source_root, trace_id=trace_id, root_is_agent=root_is_agent
        ),
        source_format="otel",
        source_event_id=run_start_id,
        model_version=None,
        prompt_version=None,
        code_sha=None,
        sampling_params=None,
    )
    events.append(run_start)

    if elide_root:
        for child in children_of.get(source_root.span_id, []):
            _emit_span_dfs(child, parent_id=run_start_id, events=events, children_of=children_of)
    else:
        _emit_span_dfs(source_root, parent_id=run_start_id, events=events, children_of=children_of)

    run_end_timestamp = source_root.end_time or source_root.start_time
    run_end_id = f"{source_root.span_id}:end"
    run_end = Event(
        id=run_end_id,
        parent_id=run_start_id,
        sequence_index=len(events),
        type=EventType.RUN_END,
        semantic_name=source_root.name or "run",
        timestamp=run_end_timestamp,
        duration_ms=None,
        payload=_build_run_end_payload(source_root, events),
        source_format="otel",
        source_event_id=run_end_id,
        model_version=None,
        prompt_version=None,
        code_sha=None,
        sampling_params=None,
    )
    events.append(run_end)

    return Trace(
        schema_version=SCHEMA_VERSION,
        trace_id=trace_id,
        created_at=source_root.start_time,
        root_event=run_start,
        events=events,
        source_convention=convention,
    )


def _reachable_descendant_ids(root: _SourceSpan, spans: list[_SourceSpan]) -> set[str]:
    children_by_parent: dict[str, list[_SourceSpan]] = {}
    for span in spans:
        if span.parent_span_id is not None:
            children_by_parent.setdefault(span.parent_span_id, []).append(span)

    reachable: set[str] = {root.span_id}
    stack: list[str] = [root.span_id]
    while stack:
        current = stack.pop()
        for child in children_by_parent.get(current, []):
            if child.span_id in reachable:
                continue
            reachable.add(child.span_id)
            stack.append(child.span_id)
    return reachable


def _emit_span_dfs(
    span: _SourceSpan,
    *,
    parent_id: str,
    events: list[Event],
    children_of: dict[str, list[_SourceSpan]],
) -> None:
    event_type = _span_event_type(span)
    if event_type is None:
        canonical_event = _build_state_transition_event(
            span, parent_id=parent_id, sequence_index=len(events)
        )
    else:
        canonical_event = _build_typed_event(
            span,
            event_type,
            parent_id=parent_id,
            sequence_index=len(events),
        )
    events.append(canonical_event)

    # Emit ERROR child(ren) immediately after their span's canonical event so
    # they precede the span's other children in DFS sequence_index order.
    for occurrence, ex_attrs in enumerate(span.exception_events):
        events.append(
            _build_error_event(
                span,
                ex_attrs,
                parent_canonical_id=canonical_event.id,
                sequence_index=len(events),
                parent_status=span.status_code,
                occurrence=occurrence,
            )
        )

    for child in children_of.get(span.span_id, []):
        _emit_span_dfs(
            child,
            parent_id=canonical_event.id,
            events=events,
            children_of=children_of,
        )
