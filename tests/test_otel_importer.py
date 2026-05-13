"""Tests for the OTel / OpenInference JSON importer (`tracebisect.otel`).

Pins the canonical shape produced by ``import_otel_json`` for OpenInference,
GenAI, and mixed-convention input, the error surface for malformed input, and
the JSONL round-trip guarantee against the Step 2.1 reader/writer.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from tracebisect.jsonl import read_trace, write_trace
from tracebisect.otel import import_otel_json
from tracebisect.schema import (
    ErrorPayload,
    EventType,
    LLMCallPayload,
    MalformedTraceError,
    StateTransitionPayload,
    ToolCallPayload,
    Trace,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "otel"
OI_PATH = FIXTURES_DIR / "openinference_refund_search.json"
GENAI_PATH = FIXTURES_DIR / "genai_refund_search.json"
MIXED_PATH = FIXTURES_DIR / "mixed_convention_trace.json"
FRAMEWORK_CONTEXT_PATH = FIXTURES_DIR / "openinference_with_framework_context.json"

ALL_FIXTURE_PATHS = [OI_PATH, GENAI_PATH, MIXED_PATH, FRAMEWORK_CONTEXT_PATH]


# ----------------------------- 1. Fixture existence ----------------------------- #


def test_all_otel_fixture_files_exist() -> None:
    for path in ALL_FIXTURE_PATHS:
        assert path.exists(), f"missing OTel fixture {path}"


# ----------------------------- 2. OpenInference refund import ----------------------------- #


def test_openinference_refund_imports_into_canonical_shape() -> None:
    trace = import_otel_json(OI_PATH)
    assert trace.source_convention == "openinference"

    types = [e.type for e in trace.events]
    assert types == [
        EventType.RUN_START,
        EventType.LLM_CALL,
        EventType.TOOL_CALL,
        EventType.RUN_END,
    ]

    run_start, llm_call, tool_call, run_end = trace.events
    assert run_start.parent_id is None
    assert llm_call.parent_id == run_start.id
    assert tool_call.parent_id == llm_call.id
    assert run_end.parent_id == run_start.id

    assert isinstance(tool_call.payload, ToolCallPayload)
    assert tool_call.payload.arguments == {"query": "users WHERE active = true"}

    for event in trace.events:
        assert event.source_format == "otel"
        assert event.source_event_id
        # bookends use ":start"/":end" derived ids; other events use raw span ids.
        if event.type in {EventType.RUN_START, EventType.RUN_END}:
            assert event.source_event_id.endswith(":start") or event.source_event_id.endswith(
                ":end"
            )
        else:
            assert ":" not in event.source_event_id


def test_openinference_agent_subtree_excludes_framework_context() -> None:
    trace = import_otel_json(FRAMEWORK_CONTEXT_PATH)
    assert trace.source_convention == "openinference"
    assert len(trace.events) == 4

    assert [e.type for e in trace.events] == [
        EventType.RUN_START,
        EventType.LLM_CALL,
        EventType.TOOL_CALL,
        EventType.RUN_END,
    ]
    assert not any(e.type is EventType.STATE_TRANSITION for e in trace.events)

    leaked_ids = {
        "http_root",
        "auth_middleware",
        "response_serialization",
        "logging_child",
    }
    emitted_ids = {e.id for e in trace.events} | {e.source_event_id for e in trace.events}
    assert emitted_ids.isdisjoint(leaked_ids)

    run_start, llm_call, tool_call, run_end = trace.events
    assert llm_call.parent_id == run_start.id
    assert tool_call.parent_id == llm_call.id
    assert run_end.parent_id == run_start.id

    assert isinstance(tool_call.payload, ToolCallPayload)
    assert tool_call.payload.arguments == {"query": "users WHERE active = true"}


# ----------------------------- 3. GenAI import with synthetic root ----------------------------- #


def test_genai_refund_imports_with_synthetic_bookends_and_elided_envelope() -> None:
    trace = import_otel_json(GENAI_PATH)
    assert trace.source_convention == "genai"

    types = [e.type for e in trace.events]
    assert types == [
        EventType.RUN_START,
        EventType.LLM_CALL,
        EventType.RUN_END,
    ]

    # The non-semantic HTTP envelope must not show up as a STATE_TRANSITION.
    state_transition_ids = [
        e.source_event_id for e in trace.events if e.type is EventType.STATE_TRANSITION
    ]
    assert "envelope_001" not in state_transition_ids
    # And it must not appear under any canonical id.
    assert "envelope_001" not in {e.id for e in trace.events}

    # Its child (the GenAI LLM span) is reparented under RUN_START.
    run_start = trace.events[0]
    llm_event = next(e for e in trace.events if e.type is EventType.LLM_CALL)
    assert llm_event.parent_id == run_start.id

    assert isinstance(llm_event.payload, LLMCallPayload)
    # response.model wins over request.model per the importer fallback chain.
    assert llm_event.payload.model == "gpt-4o-2024-08-06"
    assert llm_event.payload.provider == "openai"
    assert llm_event.payload.input_tokens == 84
    assert llm_event.payload.output_tokens == 22
    assert llm_event.payload.temperature == 0.0
    assert llm_event.payload.seed == 42

    for event in trace.events:
        assert event.source_format == "otel"


# ----------------------------- 4. Mixed convention import ----------------------------- #


def test_mixed_convention_imports_with_both_llm_and_tool() -> None:
    trace = import_otel_json(MIXED_PATH)
    assert trace.source_convention == "mixed"

    types = [e.type for e in trace.events]
    assert EventType.LLM_CALL in types
    assert EventType.TOOL_CALL in types
    assert types[0] is EventType.RUN_START
    assert types[-1] is EventType.RUN_END

    for event in trace.events:
        assert event.source_format == "otel"


# ----------------------------- 5. JSONL round-trip ----------------------------- #


@pytest.mark.parametrize("path", ALL_FIXTURE_PATHS, ids=[p.name for p in ALL_FIXTURE_PATHS])
def test_imported_trace_round_trips_through_jsonl(path: Path, tmp_path: Path) -> None:
    trace = import_otel_json(path)
    out = tmp_path / "imported.tbtrace"
    write_trace(trace, out)
    restored = read_trace(out)
    assert restored == trace


# ----------------------------- 6. Event invariants ----------------------------- #


@pytest.fixture(scope="module")
def all_imported_traces() -> list[Trace]:
    return [import_otel_json(p) for p in ALL_FIXTURE_PATHS]


@pytest.mark.parametrize("fixture_path", ALL_FIXTURE_PATHS, ids=[p.name for p in ALL_FIXTURE_PATHS])
def test_imported_trace_event_invariants(fixture_path: Path) -> None:
    trace = import_otel_json(fixture_path)
    assert trace.events[0] is trace.root_event
    assert trace.root_event.type is EventType.RUN_START

    id_to_index: dict[str, int] = {e.id: i for i, e in enumerate(trace.events)}
    for i, event in enumerate(trace.events):
        assert event.sequence_index == i
        if i > 0:
            assert event.parent_id is not None
            assert event.parent_id in id_to_index
            assert id_to_index[event.parent_id] < i

    assert isinstance(trace.created_at, datetime)
    assert trace.created_at.tzinfo is not None
    assert trace.created_at.utcoffset() == timezone.utc.utcoffset(trace.created_at)
    for event in trace.events:
        assert isinstance(event.timestamp, datetime)
        assert event.timestamp.tzinfo is not None
        assert event.timestamp.utcoffset() == timezone.utc.utcoffset(event.timestamp)


# ----------------------------- 7. Error handling ----------------------------- #


def _valid_oi_blob() -> dict[str, Any]:
    """A loadable OTel JSON dict that callers can mutate in-place to break."""
    return cast(dict[str, Any], json.loads(OI_PATH.read_text(encoding="utf-8")))


def _write_blob(tmp_path: Path, blob: object, name: str = "case.json") -> Path:
    out = tmp_path / name
    out.write_text(json.dumps(blob), encoding="utf-8")
    return out


def test_invalid_json_raises_malformed(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(MalformedTraceError):
        import_otel_json(bad)


def test_missing_resource_spans_raises_malformed(tmp_path: Path) -> None:
    out = _write_blob(tmp_path, {"foo": "bar"})
    with pytest.raises(MalformedTraceError):
        import_otel_json(out)


def test_empty_resource_spans_raises_malformed(tmp_path: Path) -> None:
    out = _write_blob(tmp_path, {"resourceSpans": []})
    with pytest.raises(MalformedTraceError):
        import_otel_json(out)


def test_no_spans_in_resource_spans_raises_malformed(tmp_path: Path) -> None:
    out = _write_blob(tmp_path, {"resourceSpans": [{"scopeSpans": [{"spans": []}]}]})
    with pytest.raises(MalformedTraceError):
        import_otel_json(out)


def test_multiple_agent_root_spans_raises_malformed(tmp_path: Path) -> None:
    blob = _valid_oi_blob()
    spans = blob["resourceSpans"][0]["scopeSpans"][0]["spans"]
    duplicate_agent = copy.deepcopy(spans[0])
    duplicate_agent["spanId"] = "agent_002"
    duplicate_agent["parentSpanId"] = None
    spans.append(duplicate_agent)
    out = _write_blob(tmp_path, blob)
    with pytest.raises(MalformedTraceError):
        import_otel_json(out)


def test_multiple_source_root_spans_without_agent_raises_malformed(
    tmp_path: Path,
) -> None:
    blob: dict[str, Any] = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "trc_x",
                                "spanId": "root_a",
                                "parentSpanId": None,
                                "name": "gen_ai.chat",
                                "startTimeUnixNano": "1000000000000000000",
                                "endTimeUnixNano": "1000000000100000000",
                                "attributes": [
                                    {
                                        "key": "gen_ai.operation.name",
                                        "value": {"stringValue": "chat"},
                                    },
                                    {
                                        "key": "gen_ai.system",
                                        "value": {"stringValue": "openai"},
                                    },
                                ],
                            },
                            {
                                "traceId": "trc_x",
                                "spanId": "root_b",
                                "parentSpanId": None,
                                "name": "gen_ai.chat",
                                "startTimeUnixNano": "1000000000200000000",
                                "endTimeUnixNano": "1000000000300000000",
                                "attributes": [
                                    {
                                        "key": "gen_ai.operation.name",
                                        "value": {"stringValue": "chat"},
                                    },
                                    {
                                        "key": "gen_ai.system",
                                        "value": {"stringValue": "openai"},
                                    },
                                ],
                            },
                        ]
                    }
                ]
            }
        ]
    }
    out = _write_blob(tmp_path, blob)
    with pytest.raises(MalformedTraceError):
        import_otel_json(out)


def test_span_missing_span_id_raises_malformed(tmp_path: Path) -> None:
    blob = _valid_oi_blob()
    del blob["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["spanId"]
    out = _write_blob(tmp_path, blob)
    with pytest.raises(MalformedTraceError):
        import_otel_json(out)


def test_span_missing_trace_id_raises_malformed(tmp_path: Path) -> None:
    blob = _valid_oi_blob()
    del blob["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"]
    out = _write_blob(tmp_path, blob)
    with pytest.raises(MalformedTraceError):
        import_otel_json(out)


# ----------------------------- 8. Exception event mapping ----------------------------- #


def test_exception_event_becomes_error_child(tmp_path: Path) -> None:
    blob = _valid_oi_blob()
    tool_span = blob["resourceSpans"][0]["scopeSpans"][0]["spans"][2]
    assert tool_span["spanId"] == "tool_001"
    tool_span["events"] = [
        {
            "timeUnixNano": "1778932321610000000",
            "name": "exception",
            "attributes": [
                {
                    "key": "exception.type",
                    "value": {"stringValue": "TimeoutError"},
                },
                {
                    "key": "exception.message",
                    "value": {"stringValue": "Database query timed out after 2 retries"},
                },
                {
                    "key": "exception.stacktrace",
                    "value": {"stringValue": "Traceback: ..."},
                },
            ],
        }
    ]
    out = _write_blob(tmp_path, blob)
    trace = import_otel_json(out)

    error_events = [e for e in trace.events if e.type is EventType.ERROR]
    assert len(error_events) == 1
    err = error_events[0]
    assert err.parent_id == "tool_001"

    payload = err.payload
    assert isinstance(payload, ErrorPayload)
    assert payload.error_type == "TimeoutError"
    assert payload.message == "Database query timed out after 2 retries"
    assert payload.stack_trace == "Traceback: ..."
    # Parent tool span status is OK, so the error is treated as recovered.
    assert payload.recovered is True


# ----------------------------- 9. Non-root non-semantic span ----------------------------- #


def test_non_root_non_semantic_span_maps_to_state_transition(tmp_path: Path) -> None:
    blob = _valid_oi_blob()
    spans = blob["resourceSpans"][0]["scopeSpans"][0]["spans"]
    # Insert an HTTP-style envelope between AGENT and LLM by reparenting LLM
    # under a new non-semantic span.
    envelope = {
        "traceId": "trc_oi_refund",
        "spanId": "envelope_inner",
        "parentSpanId": "agent_001",
        "name": "http.client.request",
        "kind": "SPAN_KIND_CLIENT",
        "startTimeUnixNano": "1778932321050000000",
        "endTimeUnixNano": "1778932321600000000",
        "attributes": [
            {"key": "http.method", "value": {"stringValue": "POST"}},
            {"key": "http.url", "value": {"stringValue": "https://api.openai.com/v1/chat"}},
        ],
        "status": {"code": "STATUS_CODE_OK"},
    }
    # Reparent LLM under the envelope.
    for s in spans:
        if s["spanId"] == "llm_001":
            s["parentSpanId"] = "envelope_inner"
            break
    spans.append(envelope)

    out = _write_blob(tmp_path, blob)
    trace = import_otel_json(out)

    envelope_event = next((e for e in trace.events if e.id == "envelope_inner"), None)
    assert envelope_event is not None
    assert envelope_event.type is EventType.STATE_TRANSITION
    assert isinstance(envelope_event.payload, StateTransitionPayload)
    assert envelope_event.payload.to_state == "http.client.request"

    llm_event = next(e for e in trace.events if e.id == "llm_001")
    assert llm_event.parent_id == envelope_event.id
