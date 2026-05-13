"""Tests for the canonical V1 trace schema (`tracebisect.schema`).

Mirrors the structure defined in ``spec/canonical-trace-schema.md`` §3–§5.
These tests pin shape, immutability, and the EventType <-> payload pairing
that downstream alignment and divergence detection rely on.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone

import pytest

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

UTC = timezone.utc
T0 = datetime(2026, 5, 11, 6, 52, 1, tzinfo=UTC)
T1 = datetime(2026, 5, 11, 6, 52, 2, tzinfo=UTC)


# ----------------------------- Helpers ----------------------------- #


def _run_start_event(event_id: str = "evt_001", code_sha: str | None = "a30ecca") -> Event:
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
        code_sha=code_sha,
        sampling_params=None,
    )


def _llm_call_event(event_id: str, parent_id: str, sequence_index: int) -> Event:
    return Event(
        id=event_id,
        parent_id=parent_id,
        sequence_index=sequence_index,
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
        source_event_id=event_id,
        model_version="gpt-4o-2024-08-06",
        prompt_version="v3",
        code_sha="a30ecca",
        sampling_params={"temperature": 0.0, "seed": 42},
    )


def _tool_call_event(event_id: str, parent_id: str, sequence_index: int) -> Event:
    return Event(
        id=event_id,
        parent_id=parent_id,
        sequence_index=sequence_index,
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
        source_event_id=event_id,
        model_version=None,
        prompt_version=None,
        code_sha="a30ecca",
        sampling_params=None,
    )


def _run_end_event(event_id: str, parent_id: str, sequence_index: int) -> Event:
    return Event(
        id=event_id,
        parent_id=parent_id,
        sequence_index=sequence_index,
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
        source_event_id=event_id,
        model_version=None,
        prompt_version=None,
        code_sha="a30ecca",
        sampling_params=None,
    )


# ----------------------------- Module constant ----------------------------- #


def test_schema_version_is_one() -> None:
    assert SCHEMA_VERSION == 1


# ----------------------------- EventType ----------------------------- #


def test_event_type_has_exactly_ten_values() -> None:
    members = list(EventType)
    assert len(members) == 10


def test_event_type_string_values_match_names() -> None:
    for member in EventType:
        assert member.value == member.name


def test_event_type_member_set_is_closed() -> None:
    expected = {
        "RUN_START",
        "RUN_END",
        "LLM_CALL",
        "TOOL_CALL",
        "MCP_CALL",
        "RETRIEVAL",
        "STATE_TRANSITION",
        "BRANCH_DECISION",
        "ERROR",
        "HUMAN_INPUT",
    }
    assert {m.value for m in EventType} == expected


# ----------------------------- Payload construction ----------------------------- #


def test_run_start_payload_constructs() -> None:
    payload = RunStartPayload(
        user_input="hello",
        agent_name="bot",
        agent_version="1.0",
        run_metadata={"trace_session": "abc"},
    )
    assert payload.user_input == "hello"
    assert payload.run_metadata == {"trace_session": "abc"}


def test_run_end_payload_constructs() -> None:
    payload = RunEndPayload(
        final_output="done",
        total_input_tokens=100,
        total_output_tokens=50,
        total_cost_usd=0.0042,
        success=True,
    )
    assert payload.success is True
    assert payload.total_cost_usd == pytest.approx(0.0042)


def test_llm_call_payload_constructs() -> None:
    payload = LLMCallPayload(
        model="gpt-4o-mini",
        provider="openai",
        messages=[{"role": "user", "content": "hi"}],
        response_text="hello",
        response_tool_calls=[],
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0001,
        temperature=0.0,
        seed=None,
    )
    assert payload.seed is None
    assert payload.messages[0]["role"] == "user"


def test_tool_call_payload_constructs() -> None:
    payload = ToolCallPayload(
        tool_name="search",
        arguments={"q": "users"},
        result="[]",
        error=None,
    )
    assert payload.tool_name == "search"


def test_mcp_call_payload_constructs() -> None:
    payload = MCPCallPayload(
        server_name="local-mcp",
        tool_name="lookup",
        arguments={"id": "u1"},
        result=None,
        error="timeout",
        protocol_version="2024-11-05",
    )
    assert payload.error == "timeout"


def test_retrieval_payload_constructs() -> None:
    payload = RetrievalPayload(
        query="active users",
        retriever="bm25:docs",
        top_k=3,
        document_ids=["d1", "d2", "d3"],
        scores=[0.9, 0.8, 0.7],
    )
    assert payload.top_k == 3
    assert payload.document_ids[0] == "d1"


def test_state_transition_payload_constructs() -> None:
    payload = StateTransitionPayload(from_state=None, to_state="planning", trigger="user_message")
    assert payload.from_state is None
    assert payload.to_state == "planning"


def test_branch_decision_payload_constructs() -> None:
    payload = BranchDecisionPayload(
        branch_name="route_query",
        chosen_branch="sql_path",
        available_branches=["sql_path", "rag_path"],
        reasoning="user mentioned a table name",
    )
    assert payload.chosen_branch == "sql_path"


def test_error_payload_constructs() -> None:
    payload = ErrorPayload(
        error_type="TimeoutError",
        message="connection timed out",
        stack_trace=None,
        recovered=False,
    )
    assert payload.recovered is False


def test_human_input_payload_constructs() -> None:
    payload = HumanInputPayload(
        prompt_shown="approve refund?",
        response="yes",
        responder_id="reviewer-7",
    )
    assert payload.response == "yes"


# ----------------------------- Frozen / slots behaviour ----------------------------- #


def test_event_is_frozen() -> None:
    event = _run_start_event()
    with pytest.raises(FrozenInstanceError):
        event.semantic_name = "mutated"  # type: ignore[misc]


def test_trace_is_frozen() -> None:
    root = _run_start_event()
    trace = Trace(
        schema_version=SCHEMA_VERSION,
        trace_id="trc_test",
        created_at=T0,
        root_event=root,
        events=[root],
        source_convention="native",
    )
    with pytest.raises(FrozenInstanceError):
        trace.trace_id = "other"  # type: ignore[misc]


def test_event_has_no_dict() -> None:
    event = _run_start_event()
    assert not hasattr(event, "__dict__")


def test_trace_has_no_dict() -> None:
    root = _run_start_event()
    trace = Trace(
        schema_version=SCHEMA_VERSION,
        trace_id="trc_test",
        created_at=T0,
        root_event=root,
        events=[root],
        source_convention="native",
    )
    assert not hasattr(trace, "__dict__")


def test_event_rejects_unknown_attribute() -> None:
    event = _run_start_event()
    # `frozen=True` + `slots=True` rejects undeclared attributes; the exact
    # exception type depends on which guard the interpreter trips first.
    with pytest.raises((AttributeError, TypeError)):
        event.bogus = 1  # type: ignore[attr-defined]


def test_trace_rejects_unknown_attribute() -> None:
    root = _run_start_event()
    trace = Trace(
        schema_version=SCHEMA_VERSION,
        trace_id="trc_test",
        created_at=T0,
        root_event=root,
        events=[root],
        source_convention="native",
    )
    with pytest.raises((AttributeError, TypeError)):
        trace.bogus = 1  # type: ignore[attr-defined]


# ----------------------------- Full Trace construction ----------------------------- #


def test_four_event_trace_matches_design_doc_example() -> None:
    root = _run_start_event("evt_001")
    llm = _llm_call_event("evt_002", parent_id=root.id, sequence_index=1)
    tool = _tool_call_event("evt_003", parent_id=root.id, sequence_index=2)
    end = _run_end_event("evt_004", parent_id=root.id, sequence_index=3)
    events = [root, llm, tool, end]

    trace = Trace(
        schema_version=SCHEMA_VERSION,
        trace_id="trc_01HG",
        created_at=T0,
        root_event=root,
        events=events,
        source_convention="native",
    )

    assert trace.events[0] is trace.root_event
    assert len(trace.events) == 4
    for i, event in enumerate(trace.events):
        assert event.sequence_index == i
    assert trace.events[0].parent_id is None
    assert trace.events[1].parent_id == root.id
    assert trace.events[2].parent_id == root.id
    assert trace.events[3].parent_id == root.id

    assert trace.created_at.tzinfo is not None
    assert trace.created_at.utcoffset() == timezone.utc.utcoffset(trace.created_at)
    for event in trace.events:
        assert event.timestamp.tzinfo is not None
        assert event.timestamp.utcoffset() == timezone.utc.utcoffset(event.timestamp)


# ----------------------------- EventType x payload pairing ----------------------------- #


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        (
            EventType.RUN_START,
            RunStartPayload(
                user_input="x",
                agent_name=None,
                agent_version=None,
                run_metadata={},
            ),
        ),
        (
            EventType.RUN_END,
            RunEndPayload(
                final_output="x",
                total_input_tokens=0,
                total_output_tokens=0,
                total_cost_usd=0.0,
                success=True,
            ),
        ),
        (
            EventType.LLM_CALL,
            LLMCallPayload(
                model="m",
                provider="p",
                messages=[],
                response_text="",
                response_tool_calls=[],
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                temperature=0.0,
                seed=None,
            ),
        ),
        (
            EventType.TOOL_CALL,
            ToolCallPayload(tool_name="t", arguments={}, result=None, error=None),
        ),
        (
            EventType.MCP_CALL,
            MCPCallPayload(
                server_name="s",
                tool_name="t",
                arguments={},
                result=None,
                error=None,
                protocol_version=None,
            ),
        ),
        (
            EventType.RETRIEVAL,
            RetrievalPayload(query="q", retriever="r", top_k=0, document_ids=[], scores=[]),
        ),
        (
            EventType.STATE_TRANSITION,
            StateTransitionPayload(from_state=None, to_state="a", trigger=None),
        ),
        (
            EventType.BRANCH_DECISION,
            BranchDecisionPayload(
                branch_name="b",
                chosen_branch="a",
                available_branches=["a"],
                reasoning=None,
            ),
        ),
        (
            EventType.ERROR,
            ErrorPayload(error_type="E", message="x", stack_trace=None, recovered=False),
        ),
        (
            EventType.HUMAN_INPUT,
            HumanInputPayload(prompt_shown="?", response="ok", responder_id=None),
        ),
    ],
)
def test_each_event_type_pairs_with_its_payload(
    event_type: EventType, payload: EventPayload
) -> None:
    event = Event(
        id=f"evt_{event_type.value.lower()}",
        parent_id=None if event_type is EventType.RUN_START else "evt_001",
        sequence_index=0 if event_type is EventType.RUN_START else 1,
        type=event_type,
        semantic_name="test",
        timestamp=T0,
        duration_ms=None,
        payload=payload,
        source_format="native",
        source_event_id="src",
        model_version=None,
        prompt_version=None,
        code_sha=None,
        sampling_params=None,
    )
    assert event.type is event_type
    assert isinstance(event.payload, type(payload))


# ----------------------------- source_convention shapes ----------------------------- #


@pytest.mark.parametrize(
    "source_convention",
    ["native", "genai", "openinference", "mixed", "unknown"],
)
def test_trace_accepts_every_source_convention(source_convention: str) -> None:
    root = _run_start_event()
    trace = Trace(
        schema_version=SCHEMA_VERSION,
        trace_id="trc",
        created_at=T0,
        root_event=root,
        events=[root],
        source_convention=source_convention,  # type: ignore[arg-type]
    )
    assert trace.source_convention == source_convention


# ----------------------------- Error class hierarchy ----------------------------- #


def test_schema_error_subclasses() -> None:
    assert issubclass(TraceBisectSchemaError, ValueError)
    assert issubclass(UnknownSchemaVersionError, TraceBisectSchemaError)
    assert issubclass(MalformedTraceError, TraceBisectSchemaError)
    assert issubclass(BrokenParentChainError, TraceBisectSchemaError)


def test_schema_errors_can_be_raised_and_caught_as_value_error() -> None:
    with pytest.raises(ValueError):
        raise MalformedTraceError("boom")
    with pytest.raises(TraceBisectSchemaError):
        raise BrokenParentChainError("chain")
    with pytest.raises(TraceBisectSchemaError):
        raise UnknownSchemaVersionError("v")


# ----------------------------- JsonObject usage ----------------------------- #


def test_json_object_accepts_nested_structures() -> None:
    nested: JsonObject = {
        "scalar": "value",
        "number": 1,
        "flag": True,
        "null": None,
        "list": [1, 2, {"inner": "x"}],
        "dict": {"k": [None, 1.5, "x"]},
    }
    payload = RunStartPayload(
        user_input="x", agent_name=None, agent_version=None, run_metadata=nested
    )
    assert payload.run_metadata["dict"] == {"k": [None, 1.5, "x"]}


# ----------------------------- Field-count regression locks ----------------------------- #


def test_event_dataclass_has_fourteen_fields() -> None:
    assert len(fields(Event)) == 14


def test_trace_dataclass_has_six_fields() -> None:
    assert len(fields(Trace)) == 6
