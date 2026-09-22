"""Tests for TraceBisect's parent-local trace alignment engine."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tracebisect.align import (
    EmptyTraceError,
    Match,
    RootTypeMismatch,
    SchemaVersionMismatch,
    TraceCycleError,
    align,
)
from tracebisect.jsonl import read_trace
from tracebisect.schema import (
    SCHEMA_VERSION,
    Event,
    EventType,
    RunEndPayload,
    RunStartPayload,
    StateTransitionPayload,
    ToolCallPayload,
    Trace,
)

UTC = timezone.utc
T0 = datetime(2026, 5, 11, 6, 52, 1, tzinfo=UTC)
T1 = datetime(2026, 5, 11, 6, 52, 2, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures"


def _run_start(event_id: str = "root", *, trace_label: str = "baseline") -> Event:
    return Event(
        id=event_id,
        parent_id=None,
        sequence_index=0,
        type=EventType.RUN_START,
        semantic_name="run",
        timestamp=T0,
        duration_ms=None,
        payload=RunStartPayload(
            user_input=f"{trace_label} input",
            agent_name="support-bot",
            agent_version="1.0",
            run_metadata={},
        ),
        source_format="native",
        source_event_id=event_id,
        model_version=None,
        prompt_version=None,
        code_sha="abc123",
        sampling_params=None,
    )


def _run_end(event_id: str, parent_id: str, sequence_index: int) -> Event:
    return Event(
        id=event_id,
        parent_id=parent_id,
        sequence_index=sequence_index,
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
        source_event_id=event_id,
        model_version=None,
        prompt_version=None,
        code_sha="abc123",
        sampling_params=None,
    )


def _tool(
    event_id: str,
    parent_id: str,
    sequence_index: int,
    *,
    source_event_id: str | None = None,
    tool_name: str = "search_database",
    query: str = "users WHERE active = true",
    semantic_name: str | None = None,
) -> Event:
    return Event(
        id=event_id,
        parent_id=parent_id,
        sequence_index=sequence_index,
        type=EventType.TOOL_CALL,
        semantic_name=semantic_name or tool_name,
        timestamp=T0,
        duration_ms=12.0,
        payload=ToolCallPayload(
            tool_name=tool_name,
            arguments={"query": query},
            result="[]",
            error=None,
        ),
        source_format="native",
        source_event_id=source_event_id or event_id,
        model_version=None,
        prompt_version=None,
        code_sha="abc123",
        sampling_params=None,
    )


def _state(
    event_id: str,
    parent_id: str,
    sequence_index: int,
    *,
    semantic_name: str,
    to_state: str,
    source_event_id: str | None = None,
) -> Event:
    return Event(
        id=event_id,
        parent_id=parent_id,
        sequence_index=sequence_index,
        type=EventType.STATE_TRANSITION,
        semantic_name=semantic_name,
        timestamp=T0,
        duration_ms=1.0,
        payload=StateTransitionPayload(
            from_state=None,
            to_state=to_state,
            trigger=None,
        ),
        source_format="native",
        source_event_id=source_event_id or event_id,
        model_version=None,
        prompt_version=None,
        code_sha="abc123",
        sampling_params=None,
    )


def _trace(events: list[Event], *, trace_id: str = "trc_align") -> Trace:
    return Trace(
        schema_version=SCHEMA_VERSION,
        trace_id=trace_id,
        created_at=T0,
        root_event=events[0],
        events=events,
        source_convention="native",
    )


def _ids(matches: list[Match]) -> list[tuple[str | None, str | None, str]]:
    rows: list[tuple[str | None, str | None, str]] = []
    for match in matches:
        baseline = match.baseline_event
        candidate = match.candidate_event
        kind = match.kind
        rows.append(
            (
                baseline.id if baseline is not None else None,
                candidate.id if candidate is not None else None,
                kind,
            )
        )
    return rows


def test_align_refund_fixtures_matches_existing_events_by_id() -> None:
    baseline = read_trace(FIXTURES / "refund_search_baseline.tbtrace")
    candidate = read_trace(FIXTURES / "refund_search_candidate_changed_tool_args.tbtrace")

    matches = align(baseline, candidate)

    assert _ids(matches) == [
        ("evt_001", "evt_001", "ID_MATCH"),
        ("evt_002", "evt_002", "ID_MATCH"),
        ("evt_003", "evt_003", "ID_MATCH"),
        ("evt_004", "evt_004", "ID_MATCH"),
    ]


def test_source_event_id_matches_when_event_ids_differ() -> None:
    b_root = _run_start("b_root")
    c_root = _run_start("c_root")
    baseline = _trace([b_root, _tool("b_tool", "b_root", 1, source_event_id="span-tool")])
    candidate = _trace([c_root, _tool("c_tool", "c_root", 1, source_event_id="span-tool")])

    matches = align(baseline, candidate)

    assert _ids(matches) == [
        ("b_root", "c_root", "ID_MATCH"),
        ("b_tool", "c_tool", "ID_MATCH"),
    ]


def test_structural_match_uses_same_type_and_near_semantic_name() -> None:
    b_root = _run_start("b_root")
    c_root = _run_start("c_root")
    baseline = _trace(
        [b_root, _state("b_state", "b_root", 1, semantic_name="route:v1", to_state="ready")]
    )
    candidate = _trace(
        [c_root, _state("c_state", "c_root", 1, semantic_name="route:v2", to_state="done")]
    )

    matches = align(baseline, candidate)

    assert _ids(matches) == [
        ("b_root", "c_root", "ID_MATCH"),
        ("b_state", "c_state", "STRUCTURAL_MATCH"),
    ]


def test_positional_match_is_type_gated_for_same_type_only() -> None:
    b_root = _run_start("b_root")
    c_root = _run_start("c_root")
    baseline = _trace(
        [b_root, _state("b_state", "b_root", 1, semantic_name="planner", to_state="ready")]
    )
    candidate = _trace(
        [c_root, _state("c_state", "c_root", 1, semantic_name="executor", to_state="done")]
    )

    matches = align(baseline, candidate)

    assert _ids(matches) == [
        ("b_root", "c_root", "ID_MATCH"),
        ("b_state", "c_state", "POSITIONAL_MATCH"),
    ]


def test_cross_type_fallback_slot_emits_delete_and_insert_not_positional() -> None:
    b_root = _run_start("b_root")
    c_root = _run_start("c_root")
    baseline = _trace([b_root, _tool("b_tool", "b_root", 1)])
    candidate = _trace(
        [c_root, _state("c_state", "c_root", 1, semantic_name="planner", to_state="ready")]
    )

    matches = align(baseline, candidate)

    assert "POSITIONAL_MATCH" not in [match.kind for match in matches]
    assert _ids(matches) == [
        ("b_root", "c_root", "ID_MATCH"),
        ("b_tool", None, "DELETION"),
        (None, "c_state", "INSERTION"),
    ]


def test_inserted_subtree_expands_depth_first_at_candidate_position() -> None:
    b_root = _run_start("b_root")
    c_root = _run_start("c_root")
    shared_b = _tool("b_tool", "b_root", 1, source_event_id="shared-tool")
    inserted_parent = _state(
        "c_insert_parent",
        "c_root",
        1,
        semantic_name="extra",
        to_state="extra",
    )
    inserted_child = _state(
        "c_insert_child",
        "c_insert_parent",
        2,
        semantic_name="extra_child",
        to_state="extra_child",
    )
    shared_c = _tool("c_tool", "c_root", 3, source_event_id="shared-tool")

    baseline = _trace([b_root, shared_b])
    candidate = _trace([c_root, inserted_parent, inserted_child, shared_c])

    assert _ids(align(baseline, candidate)) == [
        ("b_root", "c_root", "ID_MATCH"),
        (None, "c_insert_parent", "INSERTION"),
        (None, "c_insert_child", "INSERTION"),
        ("b_tool", "c_tool", "ID_MATCH"),
    ]


def test_deleted_subtree_expands_depth_first_at_baseline_position() -> None:
    b_root = _run_start("b_root")
    c_root = _run_start("c_root")
    deleted_parent = _state("b_deleted_parent", "b_root", 1, semantic_name="drop", to_state="drop")
    deleted_child = _state(
        "b_deleted_child",
        "b_deleted_parent",
        2,
        semantic_name="drop_child",
        to_state="drop_child",
    )
    shared_b = _tool("b_tool", "b_root", 3, source_event_id="shared-tool")
    shared_c = _tool("c_tool", "c_root", 1, source_event_id="shared-tool")

    baseline = _trace([b_root, deleted_parent, deleted_child, shared_b])
    candidate = _trace([c_root, shared_c])

    assert _ids(align(baseline, candidate)) == [
        ("b_root", "c_root", "ID_MATCH"),
        ("b_deleted_parent", None, "DELETION"),
        ("b_deleted_child", None, "DELETION"),
        ("b_tool", "c_tool", "ID_MATCH"),
    ]


def test_duplicate_stable_ids_pair_by_sibling_order() -> None:
    b_root = _run_start("b_root")
    c_root = _run_start("c_root")
    baseline = _trace(
        [
            b_root,
            _tool("b_tool_1", "b_root", 1, source_event_id="same-span"),
            _tool("b_tool_2", "b_root", 2, source_event_id="same-span"),
            _tool("b_tool_3", "b_root", 3, source_event_id="same-span"),
        ]
    )
    candidate = _trace(
        [
            c_root,
            _tool("c_tool_1", "c_root", 1, source_event_id="same-span"),
            _tool("c_tool_2", "c_root", 2, source_event_id="same-span"),
            _tool("c_tool_3", "c_root", 3, source_event_id="same-span"),
        ]
    )

    assert _ids(align(baseline, candidate)) == [
        ("b_root", "c_root", "ID_MATCH"),
        ("b_tool_1", "c_tool_1", "ID_MATCH"),
        ("b_tool_2", "c_tool_2", "ID_MATCH"),
        ("b_tool_3", "c_tool_3", "ID_MATCH"),
    ]


def test_deep_matching_uses_explicit_stack_not_python_recursion() -> None:
    depth = 1100
    baseline_events = [_run_start("b_root")]
    candidate_events = [_run_start("c_root")]
    b_parent = "b_root"
    c_parent = "c_root"
    for index in range(1, depth + 1):
        baseline_events.append(
            _state(
                f"b_state_{index}",
                b_parent,
                index,
                semantic_name=f"state_{index}",
                to_state=f"state_{index}",
                source_event_id=f"span_{index}",
            )
        )
        candidate_events.append(
            _state(
                f"c_state_{index}",
                c_parent,
                index,
                semantic_name=f"state_{index}",
                to_state=f"state_{index}",
                source_event_id=f"span_{index}",
            )
        )
        b_parent = f"b_state_{index}"
        c_parent = f"c_state_{index}"

    matches = align(_trace(baseline_events), _trace(candidate_events))

    assert len(matches) == depth + 1
    assert matches[-1].baseline_event is not None
    assert matches[-1].baseline_event.id == "b_state_1100"
    assert matches[-1].candidate_event is not None
    assert matches[-1].candidate_event.id == "c_state_1100"


def test_schema_version_mismatch_raises() -> None:
    root = _run_start("root")
    baseline = _trace([root])
    candidate = Trace(
        schema_version=SCHEMA_VERSION + 1,
        trace_id="trc_candidate",
        created_at=T0,
        root_event=root,
        events=[root],
        source_convention="native",
    )

    with pytest.raises(SchemaVersionMismatch):
        align(baseline, candidate)


def test_empty_trace_raises() -> None:
    root = _run_start("root")
    empty = Trace(
        schema_version=SCHEMA_VERSION,
        trace_id="trc_empty",
        created_at=T0,
        root_event=root,
        events=[],
        source_convention="native",
    )

    with pytest.raises(EmptyTraceError):
        align(empty, _trace([root]))


def test_root_type_mismatch_raises() -> None:
    bad_root = _tool("not_root", "missing_parent", 0)
    baseline = Trace(
        schema_version=SCHEMA_VERSION,
        trace_id="trc_bad",
        created_at=T0,
        root_event=bad_root,
        events=[bad_root],
        source_convention="native",
    )

    with pytest.raises(RootTypeMismatch):
        align(baseline, _trace([_run_start("root")]))


def test_cycle_raises() -> None:
    root = _run_start("root")
    a = _state("a", "b", 1, semantic_name="a", to_state="a")
    b = _state("b", "a", 2, semantic_name="b", to_state="b")
    trace = _trace([root, a, b])

    with pytest.raises(TraceCycleError):
        align(trace, _trace([_run_start("candidate_root")]))


def test_positional_raw_ids_do_not_pair_reordered_tools() -> None:
    baseline = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, tool_name="get_status"),
            _tool("evt_003", "evt_001", 2, tool_name="get_shipping"),
        ]
    )
    candidate = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, tool_name="get_shipping"),
            _tool("evt_003", "evt_001", 2, tool_name="get_status"),
        ]
    )

    assert _ids(align(baseline, candidate)) == [
        ("evt_001", "evt_001", "ID_MATCH"),
        ("evt_002", "evt_003", "ID_MATCH"),
        ("evt_003", "evt_002", "ID_MATCH"),
    ]


def test_shared_raw_id_still_pairs_a_tool_that_did_not_move() -> None:
    baseline = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, tool_name="lookup_order"),
        ]
    )
    candidate = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, tool_name="cancel_order"),
        ]
    )

    assert _ids(align(baseline, candidate)) == [
        ("evt_001", "evt_001", "ID_MATCH"),
        ("evt_002", "evt_002", "ID_MATCH"),
    ]


def test_raw_id_gate_compares_normalized_tool_names() -> None:
    baseline = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, tool_name="get_status"),
            _tool("evt_003", "evt_001", 2, tool_name="get_shipping"),
        ]
    )
    candidate = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, tool_name="get_shipping "),
            _tool("evt_003", "evt_001", 2, tool_name="get_status "),
        ]
    )

    assert _ids(align(baseline, candidate)) == [
        ("evt_001", "evt_001", "ID_MATCH"),
        ("evt_002", "evt_003", "STRUCTURAL_MATCH"),
        ("evt_003", "evt_002", "STRUCTURAL_MATCH"),
    ]


def test_raw_id_gate_uses_tool_names_not_semantic_names() -> None:
    baseline = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, tool_name="get_status", semantic_name="tool"),
            _tool("evt_003", "evt_001", 2, tool_name="get_shipping", semantic_name="tool"),
        ]
    )
    candidate = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, tool_name="get_shipping", semantic_name="tool"),
            _tool("evt_003", "evt_001", 2, tool_name="get_status", semantic_name="tool"),
        ]
    )

    assert _ids(align(baseline, candidate)) == [
        ("evt_001", "evt_001", "ID_MATCH"),
        ("evt_002", "evt_003", "ID_MATCH"),
        ("evt_003", "evt_002", "ID_MATCH"),
    ]
