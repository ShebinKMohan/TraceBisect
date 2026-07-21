"""Tests for the canonical JSONL serializer (`tracebisect.jsonl`).

Pin the on-disk contract defined in ``spec/canonical-trace-schema.md`` §6 and
``spec/production-spec.md`` §10: header line + one Event per line, ISO 8601 Z
datetimes, explicit JSON ``null`` for optional fields, deterministic order.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest

from tracebisect.jsonl import dumps_trace, loads_trace, read_trace, write_trace
from tracebisect.schema import (
    SCHEMA_VERSION,
    BranchDecisionPayload,
    BrokenParentChainError,
    ErrorPayload,
    Event,
    EventPayload,
    EventType,
    HumanInputPayload,
    LLMCallPayload,
    MalformedTraceError,
    MCPCallPayload,
    RetrievalPayload,
    RunEndPayload,
    RunStartPayload,
    StateTransitionPayload,
    ToolCallPayload,
    Trace,
    UnknownSchemaVersionError,
)

UTC = timezone.utc
T0 = datetime(2026, 5, 11, 6, 52, 1, tzinfo=UTC)
T1 = datetime(2026, 5, 11, 6, 52, 2, tzinfo=UTC)


# ----------------------------- Builders ----------------------------- #


def _root(event_id: str = "evt_001") -> Event:
    return Event(
        id=event_id,
        parent_id=None,
        sequence_index=0,
        type=EventType.RUN_START,
        semantic_name="run",
        timestamp=T0,
        duration_ms=None,
        payload=RunStartPayload(
            user_input="find active users",
            agent_name="support-bot",
            agent_version="1.4.0",
            run_metadata={},
        ),
        source_format="native",
        source_event_id=event_id,
        model_version=None,
        prompt_version=None,
        code_sha="a30ecca",
        sampling_params=None,
    )


def _wrap_single_event_trace(payload: EventPayload, event_type: EventType) -> Trace:
    """Build a minimal valid trace (root + one event + RUN_END) for `payload`."""
    root = _root()
    middle = Event(
        id="evt_002",
        parent_id="evt_001",
        sequence_index=1,
        type=event_type,
        semantic_name="middle",
        timestamp=T0,
        duration_ms=1.5,
        payload=payload,
        source_format="native",
        source_event_id="evt_002",
        model_version=None,
        prompt_version=None,
        code_sha=None,
        sampling_params=None,
    )
    end = Event(
        id="evt_003",
        parent_id="evt_001",
        sequence_index=2,
        type=EventType.RUN_END,
        semantic_name="run",
        timestamp=T1,
        duration_ms=None,
        payload=RunEndPayload(
            final_output="ok",
            total_input_tokens=0,
            total_output_tokens=0,
            total_cost_usd=0.0,
            success=True,
        ),
        source_format="native",
        source_event_id="evt_003",
        model_version=None,
        prompt_version=None,
        code_sha=None,
        sampling_params=None,
    )
    return Trace(
        schema_version=SCHEMA_VERSION,
        trace_id="trc_test",
        created_at=T0,
        root_event=root,
        events=[root, middle, end],
        source_convention="native",
    )


def _four_event_trace() -> Trace:
    root = _root()
    llm = Event(
        id="evt_002",
        parent_id="evt_001",
        sequence_index=1,
        type=EventType.LLM_CALL,
        semantic_name="gpt-4o-mini",
        timestamp=T0,
        duration_ms=412.3,
        payload=LLMCallPayload(
            model="gpt-4o-mini",
            provider="openai",
            messages=[{"role": "user", "content": "find active users"}],
            response_text="",
            response_tool_calls=[
                {
                    "name": "search_database",
                    "arguments": {"query": "users WHERE active = true"},
                }
            ],
            input_tokens=84,
            output_tokens=22,
            cost_usd=0.00021,
            temperature=0.0,
            seed=42,
        ),
        source_format="native",
        source_event_id="evt_002",
        model_version="gpt-4o-2024-08-06",
        prompt_version="v3",
        code_sha="a30ecca",
        sampling_params={"temperature": 0.0, "seed": 42},
    )
    tool = Event(
        id="evt_003",
        parent_id="evt_001",
        sequence_index=2,
        type=EventType.TOOL_CALL,
        semantic_name="search_database",
        timestamp=T0,
        duration_ms=18.7,
        payload=ToolCallPayload(
            tool_name="search_database",
            arguments={"query": "users WHERE active = true"},
            result='[{"id": 1, "name": "alice"}]',
            error=None,
        ),
        source_format="native",
        source_event_id="evt_003",
        model_version=None,
        prompt_version=None,
        code_sha="a30ecca",
        sampling_params=None,
    )
    end = Event(
        id="evt_004",
        parent_id="evt_001",
        sequence_index=3,
        type=EventType.RUN_END,
        semantic_name="run",
        timestamp=T1,
        duration_ms=None,
        payload=RunEndPayload(
            final_output="found 12 active users",
            total_input_tokens=84,
            total_output_tokens=22,
            total_cost_usd=0.00021,
            success=True,
        ),
        source_format="native",
        source_event_id="evt_004",
        model_version=None,
        prompt_version=None,
        code_sha="a30ecca",
        sampling_params=None,
    )
    return Trace(
        schema_version=SCHEMA_VERSION,
        trace_id="trc_01HG",
        created_at=T0,
        root_event=root,
        events=[root, llm, tool, end],
        source_convention="native",
    )


def _write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ----------------------------- Round-trip happy paths ----------------------------- #


def test_round_trip_four_event_trace(tmp_path: Path) -> None:
    original = _four_event_trace()
    path = tmp_path / "trace.tbtrace"
    write_trace(original, path)
    restored = read_trace(path)
    assert restored == original

    assert isinstance(restored.created_at, datetime)
    assert restored.created_at.tzinfo == timezone.utc
    for event in restored.events:
        assert isinstance(event.timestamp, datetime)
        assert event.timestamp.tzinfo == timezone.utc


def test_round_trip_canonical_jsonl_text_without_filesystem() -> None:
    original = _four_event_trace()

    encoded = dumps_trace(original)
    restored = loads_trace(encoded)

    assert encoded.endswith("\n")
    assert restored == original


def test_round_trip_preserves_none_for_optional_event_fields(tmp_path: Path) -> None:
    original = _four_event_trace()
    path = tmp_path / "trace.tbtrace"
    write_trace(original, path)
    restored = read_trace(path)

    end_event = restored.events[3]
    assert end_event.duration_ms is None
    assert end_event.model_version is None
    assert end_event.prompt_version is None
    assert end_event.sampling_params is None

    root = restored.events[0]
    assert root.parent_id is None
    assert root.sampling_params is None

    tool = restored.events[2]
    assert isinstance(tool.payload, ToolCallPayload)
    assert tool.payload.error is None


# ----------------------------- Per-payload coverage ----------------------------- #


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        (
            EventType.LLM_CALL,
            LLMCallPayload(
                model="m",
                provider="p",
                messages=[{"role": "system", "content": "be helpful"}],
                response_text="hi",
                response_tool_calls=[],
                input_tokens=1,
                output_tokens=2,
                cost_usd=0.01,
                temperature=0.7,
                seed=None,
            ),
        ),
        (
            EventType.TOOL_CALL,
            ToolCallPayload(
                tool_name="search",
                arguments={"q": "alice"},
                result="rows",
                error=None,
            ),
        ),
        (
            EventType.MCP_CALL,
            MCPCallPayload(
                server_name="srv",
                tool_name="lookup",
                arguments={"id": 1},
                result=None,
                error="timeout",
                protocol_version="2024-11-05",
            ),
        ),
        (
            EventType.RETRIEVAL,
            RetrievalPayload(
                query="active users",
                retriever="bm25:docs",
                top_k=3,
                document_ids=["d1", "d2", "d3"],
                scores=[0.9, 0.8, 0.7],
            ),
        ),
        (
            EventType.STATE_TRANSITION,
            StateTransitionPayload(from_state="idle", to_state="thinking", trigger="user_msg"),
        ),
        (
            EventType.BRANCH_DECISION,
            BranchDecisionPayload(
                branch_name="route",
                chosen_branch="sql",
                available_branches=["sql", "rag"],
                reasoning="user mentioned table",
            ),
        ),
        (
            EventType.ERROR,
            ErrorPayload(
                error_type="ValueError",
                message="bad input",
                stack_trace="Traceback...",
                recovered=True,
            ),
        ),
        (
            EventType.HUMAN_INPUT,
            HumanInputPayload(
                prompt_shown="approve?",
                response="yes",
                responder_id=None,
            ),
        ),
    ],
)
def test_round_trip_each_payload_type(
    tmp_path: Path, event_type: EventType, payload: EventPayload
) -> None:
    trace = _wrap_single_event_trace(payload, event_type)
    path = tmp_path / "trace.tbtrace"
    write_trace(trace, path)
    restored = read_trace(path)
    assert restored == trace
    assert isinstance(restored.events[1].payload, type(payload))


def test_round_trip_run_start_payload(tmp_path: Path) -> None:
    root = Event(
        id="evt_001",
        parent_id=None,
        sequence_index=0,
        type=EventType.RUN_START,
        semantic_name="run",
        timestamp=T0,
        duration_ms=None,
        payload=RunStartPayload(
            user_input="hello",
            agent_name="bot",
            agent_version="1.0",
            run_metadata={"k": "v"},
        ),
        source_format="native",
        source_event_id="evt_001",
        model_version=None,
        prompt_version=None,
        code_sha=None,
        sampling_params=None,
    )
    end = Event(
        id="evt_002",
        parent_id="evt_001",
        sequence_index=1,
        type=EventType.RUN_END,
        semantic_name="run",
        timestamp=T1,
        duration_ms=None,
        payload=RunEndPayload(
            final_output="done",
            total_input_tokens=1,
            total_output_tokens=2,
            total_cost_usd=0.0001,
            success=True,
        ),
        source_format="native",
        source_event_id="evt_002",
        model_version=None,
        prompt_version=None,
        code_sha=None,
        sampling_params=None,
    )
    trace = Trace(
        schema_version=SCHEMA_VERSION,
        trace_id="trc",
        created_at=T0,
        root_event=root,
        events=[root, end],
        source_convention="native",
    )
    path = tmp_path / "trace.tbtrace"
    write_trace(trace, path)
    restored = read_trace(path)
    assert restored == trace


# ----------------------------- Header validation ----------------------------- #


def test_unknown_schema_version_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.tbtrace"
    _write_lines(
        path,
        [
            json.dumps(
                {
                    "schema_version": 9999,
                    "trace_id": "trc",
                    "created_at": "2026-05-11T06:52:01Z",
                    "source_convention": "native",
                }
            ),
        ],
    )
    with pytest.raises(UnknownSchemaVersionError):
        read_trace(path)


def test_missing_schema_version_is_malformed(tmp_path: Path) -> None:
    path = tmp_path / "bad.tbtrace"
    _write_lines(
        path,
        [
            json.dumps(
                {
                    "trace_id": "trc",
                    "created_at": "2026-05-11T06:52:01Z",
                    "source_convention": "native",
                }
            ),
        ],
    )
    with pytest.raises(MalformedTraceError):
        read_trace(path)


@pytest.mark.parametrize("missing_field", ["trace_id", "created_at", "source_convention"])
def test_missing_header_field_is_malformed(tmp_path: Path, missing_field: str) -> None:
    header = {
        "schema_version": SCHEMA_VERSION,
        "trace_id": "trc",
        "created_at": "2026-05-11T06:52:01Z",
        "source_convention": "native",
    }
    header.pop(missing_field)
    path = tmp_path / "bad.tbtrace"
    _write_lines(path, [json.dumps(header)])
    with pytest.raises(MalformedTraceError):
        read_trace(path)


def test_invalid_created_at_is_malformed(tmp_path: Path) -> None:
    path = tmp_path / "bad.tbtrace"
    _write_lines(
        path,
        [
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "trace_id": "trc",
                    "created_at": "not-a-date",
                    "source_convention": "native",
                }
            ),
        ],
    )
    with pytest.raises(MalformedTraceError):
        read_trace(path)


def test_unknown_source_convention_is_malformed(tmp_path: Path) -> None:
    path = tmp_path / "bad.tbtrace"
    _write_lines(
        path,
        [
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "trace_id": "trc",
                    "created_at": "2026-05-11T06:52:01Z",
                    "source_convention": "bogus",
                }
            ),
        ],
    )
    with pytest.raises(MalformedTraceError):
        read_trace(path)


def test_invalid_json_is_malformed(tmp_path: Path) -> None:
    path = tmp_path / "bad.tbtrace"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(MalformedTraceError):
        read_trace(path)


# ----------------------------- Event validation ----------------------------- #


def _header_line() -> str:
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "trace_id": "trc",
            "created_at": "2026-05-11T06:52:01Z",
            "source_convention": "native",
        }
    )


def _valid_root_event_dict() -> dict[str, object]:
    return {
        "id": "evt_001",
        "parent_id": None,
        "sequence_index": 0,
        "type": "RUN_START",
        "semantic_name": "run",
        "timestamp": "2026-05-11T06:52:01Z",
        "duration_ms": None,
        "payload": {
            "user_input": "hi",
            "agent_name": None,
            "agent_version": None,
            "run_metadata": {},
        },
        "source_format": "native",
        "source_event_id": "evt_001",
        "model_version": None,
        "prompt_version": None,
        "code_sha": None,
        "sampling_params": None,
    }


def test_unknown_event_type_is_malformed(tmp_path: Path) -> None:
    bad_event = _valid_root_event_dict()
    bad_event["type"] = "NOT_A_TYPE"
    path = tmp_path / "bad.tbtrace"
    _write_lines(path, [_header_line(), json.dumps(bad_event)])
    with pytest.raises(MalformedTraceError):
        read_trace(path)


def test_missing_event_field_is_malformed(tmp_path: Path) -> None:
    bad_event = _valid_root_event_dict()
    bad_event.pop("semantic_name")
    path = tmp_path / "bad.tbtrace"
    _write_lines(path, [_header_line(), json.dumps(bad_event)])
    with pytest.raises(MalformedTraceError):
        read_trace(path)


def test_invalid_event_timestamp_is_malformed(tmp_path: Path) -> None:
    bad_event = _valid_root_event_dict()
    bad_event["timestamp"] = "yesterday"
    path = tmp_path / "bad.tbtrace"
    _write_lines(path, [_header_line(), json.dumps(bad_event)])
    with pytest.raises(MalformedTraceError):
        read_trace(path)


def test_payload_incompatible_with_event_type_is_malformed(tmp_path: Path) -> None:
    bad_event = _valid_root_event_dict()
    # RUN_START expects user_input/agent_name/agent_version/run_metadata, not RUN_END fields.
    bad_event["payload"] = {
        "final_output": "x",
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost_usd": 0.0,
        "success": True,
    }
    path = tmp_path / "bad.tbtrace"
    _write_lines(path, [_header_line(), json.dumps(bad_event)])
    with pytest.raises(MalformedTraceError):
        read_trace(path)


def test_invalid_source_format_is_malformed(tmp_path: Path) -> None:
    bad_event = _valid_root_event_dict()
    bad_event["source_format"] = "bogus"
    path = tmp_path / "bad.tbtrace"
    _write_lines(path, [_header_line(), json.dumps(bad_event)])
    with pytest.raises(MalformedTraceError):
        read_trace(path)


# ----------------------------- Tree validation ----------------------------- #


def test_missing_run_start_root_is_malformed(tmp_path: Path) -> None:
    path = tmp_path / "bad.tbtrace"
    _write_lines(path, [_header_line()])
    with pytest.raises(MalformedTraceError):
        read_trace(path)


def test_non_root_event_with_unknown_parent_breaks_chain(tmp_path: Path) -> None:
    root = _valid_root_event_dict()
    child = _valid_root_event_dict()
    child["id"] = "evt_002"
    child["parent_id"] = "evt_ghost"
    child["sequence_index"] = 1
    child["type"] = "RUN_END"
    child["payload"] = {
        "final_output": "x",
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost_usd": 0.0,
        "success": True,
    }
    path = tmp_path / "bad.tbtrace"
    _write_lines(path, [_header_line(), json.dumps(root), json.dumps(child)])
    with pytest.raises(BrokenParentChainError):
        read_trace(path)


def test_events_zero_must_be_run_start_root(tmp_path: Path) -> None:
    # First event is not RUN_START.
    first = _valid_root_event_dict()
    first["type"] = "TOOL_CALL"
    first["payload"] = {
        "tool_name": "t",
        "arguments": {},
        "result": None,
        "error": None,
    }
    path = tmp_path / "bad.tbtrace"
    _write_lines(path, [_header_line(), json.dumps(first)])
    with pytest.raises(MalformedTraceError):
        read_trace(path)


def test_sequence_index_mismatch_is_malformed(tmp_path: Path) -> None:
    root = _valid_root_event_dict()
    end = _valid_root_event_dict()
    end["id"] = "evt_002"
    end["parent_id"] = "evt_001"
    end["sequence_index"] = 5  # wrong; should be 1
    end["type"] = "RUN_END"
    end["payload"] = {
        "final_output": "x",
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost_usd": 0.0,
        "success": True,
    }
    path = tmp_path / "bad.tbtrace"
    _write_lines(path, [_header_line(), json.dumps(root), json.dumps(end)])
    with pytest.raises(MalformedTraceError):
        read_trace(path)


def test_non_root_with_null_parent_is_malformed(tmp_path: Path) -> None:
    root = _valid_root_event_dict()
    second_root = _valid_root_event_dict()
    second_root["id"] = "evt_002"
    second_root["sequence_index"] = 1
    second_root["type"] = "RUN_END"
    second_root["payload"] = {
        "final_output": "x",
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost_usd": 0.0,
        "success": True,
    }
    path = tmp_path / "bad.tbtrace"
    _write_lines(path, [_header_line(), json.dumps(root), json.dumps(second_root)])
    with pytest.raises(MalformedTraceError):
        read_trace(path)


def _tool_call_event_dict(
    event_id: str, parent_id: str | None, sequence_index: int
) -> dict[str, object]:
    return {
        "id": event_id,
        "parent_id": parent_id,
        "sequence_index": sequence_index,
        "type": "TOOL_CALL",
        "semantic_name": "t",
        "timestamp": "2026-05-11T06:52:01Z",
        "duration_ms": 1.0,
        "payload": {
            "tool_name": "t",
            "arguments": {},
            "result": None,
            "error": None,
        },
        "source_format": "native",
        "source_event_id": event_id,
        "model_version": None,
        "prompt_version": None,
        "code_sha": None,
        "sampling_params": None,
    }


def test_forward_parent_reference_is_malformed(tmp_path: Path) -> None:
    # evt_002 (idx 1) references evt_003 (idx 2), which comes later. Trace.events
    # is defined as DFS sequence_index order, so a child preceding its parent
    # violates the canonical contract.
    root = _valid_root_event_dict()
    evt2 = _tool_call_event_dict("evt_002", parent_id="evt_003", sequence_index=1)
    evt3 = _tool_call_event_dict("evt_003", parent_id="evt_001", sequence_index=2)
    path = tmp_path / "bad.tbtrace"
    _write_lines(
        path,
        [_header_line(), json.dumps(root), json.dumps(evt2), json.dumps(evt3)],
    )
    with pytest.raises(MalformedTraceError):
        read_trace(path)


def test_non_root_cycle_is_malformed(tmp_path: Path) -> None:
    # Two non-root events pointing at each other (evt_002 -> evt_003 -> evt_002).
    # Both parent links violate DFS parent-before-child; the validator must reject
    # the trace rather than admit a cycle.
    root = _valid_root_event_dict()
    evt2 = _tool_call_event_dict("evt_002", parent_id="evt_003", sequence_index=1)
    evt3 = _tool_call_event_dict("evt_003", parent_id="evt_002", sequence_index=2)
    path = tmp_path / "bad.tbtrace"
    _write_lines(
        path,
        [_header_line(), json.dumps(root), json.dumps(evt2), json.dumps(evt3)],
    )
    with pytest.raises(MalformedTraceError):
        read_trace(path)


# ----------------------------- source_convention round-trip ----------------------------- #


@pytest.mark.parametrize(
    "source_convention",
    ["native", "genai", "openinference", "mixed", "unknown"],
)
def test_source_convention_round_trips(tmp_path: Path, source_convention: str) -> None:
    base = _four_event_trace()
    trace = Trace(
        schema_version=base.schema_version,
        trace_id=base.trace_id,
        created_at=base.created_at,
        root_event=base.root_event,
        events=base.events,
        source_convention=cast(
            "object",
            source_convention,  # type: ignore[arg-type]
        ),  # type: ignore[arg-type]
    )
    path = tmp_path / "trace.tbtrace"
    write_trace(trace, path)
    restored = read_trace(path)
    assert restored.source_convention == source_convention


# ----------------------------- Shape / determinism ----------------------------- #


def test_optional_none_fields_serialize_as_json_null(tmp_path: Path) -> None:
    trace = _four_event_trace()
    path = tmp_path / "trace.tbtrace"
    write_trace(trace, path)

    lines = path.read_text(encoding="utf-8").splitlines()
    root_line = json.loads(lines[1])
    # parent_id, duration_ms, model_version, prompt_version, sampling_params
    # all None on root.
    assert root_line["parent_id"] is None
    assert root_line["duration_ms"] is None
    assert root_line["model_version"] is None
    assert root_line["prompt_version"] is None
    assert root_line["sampling_params"] is None
    # Keys are present, not omitted.
    for key in (
        "parent_id",
        "duration_ms",
        "model_version",
        "prompt_version",
        "code_sha",
        "sampling_params",
    ):
        assert key in root_line


def test_datetime_serializes_with_z_suffix(tmp_path: Path) -> None:
    trace = _four_event_trace()
    path = tmp_path / "trace.tbtrace"
    write_trace(trace, path)
    lines = path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    assert isinstance(header["created_at"], str)
    assert header["created_at"].endswith("Z")
    for raw in lines[1:]:
        event = json.loads(raw)
        assert isinstance(event["timestamp"], str)
        assert event["timestamp"].endswith("Z")


def test_header_is_on_first_line(tmp_path: Path) -> None:
    trace = _four_event_trace()
    path = tmp_path / "trace.tbtrace"
    write_trace(trace, path)
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    parsed = json.loads(first_line)
    assert parsed["schema_version"] == SCHEMA_VERSION
    assert parsed["trace_id"] == trace.trace_id
    assert "events" not in parsed


def test_event_order_is_preserved(tmp_path: Path) -> None:
    trace = _four_event_trace()
    path = tmp_path / "trace.tbtrace"
    write_trace(trace, path)
    restored = read_trace(path)
    assert [e.id for e in restored.events] == [
        "evt_001",
        "evt_002",
        "evt_003",
        "evt_004",
    ]
