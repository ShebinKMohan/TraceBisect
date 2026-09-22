"""Tests for V1 divergence detection."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tracebisect.align import align
from tracebisect.diff import (
    Divergence,
    DivergenceType,
    ImpactAnalysis,
    detect_divergences,
    first_divergence,
)
from tracebisect.jsonl import read_trace
from tracebisect.schema import (
    SCHEMA_VERSION,
    BranchDecisionPayload,
    Event,
    EventType,
    JsonObject,
    MCPCallPayload,
    RunEndPayload,
    RunStartPayload,
    ToolCallPayload,
    Trace,
)

UTC = timezone.utc
T0 = datetime(2026, 5, 11, 6, 52, 1, tzinfo=UTC)
T1 = datetime(2026, 5, 11, 6, 52, 2, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures"


def _run_start(event_id: str = "root") -> Event:
    return Event(
        id=event_id,
        parent_id=None,
        sequence_index=0,
        type=EventType.RUN_START,
        semantic_name="run",
        timestamp=T0,
        duration_ms=None,
        payload=RunStartPayload(
            user_input="input",
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


def _run_end(event_id: str, parent_id: str, sequence_index: int, *, cost: float) -> Event:
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
            total_input_tokens=10,
            total_output_tokens=5,
            total_cost_usd=cost,
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
    query: str,
    tool_name: str = "search_database",
    semantic_name: str | None = None,
    arguments: JsonObject | None = None,
) -> Event:
    return Event(
        id=event_id,
        parent_id=parent_id,
        sequence_index=sequence_index,
        type=EventType.TOOL_CALL,
        semantic_name=semantic_name or tool_name,
        timestamp=T0,
        duration_ms=10.0,
        payload=ToolCallPayload(
            tool_name=tool_name,
            arguments={"query": query} if arguments is None else arguments,
            result="[]",
            error=None,
        ),
        source_format="native",
        source_event_id=event_id,
        model_version=None,
        prompt_version=None,
        code_sha="abc123",
        sampling_params=None,
    )


def _branch(
    event_id: str,
    parent_id: str,
    sequence_index: int,
    *,
    chosen_branch: str,
) -> Event:
    return Event(
        id=event_id,
        parent_id=parent_id,
        sequence_index=sequence_index,
        type=EventType.BRANCH_DECISION,
        semantic_name="refund_route",
        timestamp=T0,
        duration_ms=1.0,
        payload=BranchDecisionPayload(
            branch_name="refund_route",
            chosen_branch=chosen_branch,
            available_branches=["approve", "deny"],
            reasoning=None,
        ),
        source_format="native",
        source_event_id=event_id,
        model_version=None,
        prompt_version=None,
        code_sha="abc123",
        sampling_params=None,
    )


def _mcp(
    event_id: str,
    parent_id: str,
    sequence_index: int,
    *,
    server_name: str,
    tool_name: str = "read_file",
) -> Event:
    return Event(
        id=event_id,
        parent_id=parent_id,
        sequence_index=sequence_index,
        type=EventType.MCP_CALL,
        semantic_name=tool_name,
        timestamp=T0,
        duration_ms=10.0,
        payload=MCPCallPayload(
            server_name=server_name,
            tool_name=tool_name,
            arguments={"path": "notes.txt"},
            result="ok",
            error=None,
            protocol_version=None,
        ),
        source_format="native",
        source_event_id=event_id,
        model_version=None,
        prompt_version=None,
        code_sha="abc123",
        sampling_params=None,
    )


def _trace(events: list[Event], trace_id: str = "trc_diff") -> Trace:
    return Trace(
        schema_version=SCHEMA_VERSION,
        trace_id=trace_id,
        created_at=T0,
        root_event=events[0],
        events=events,
        source_convention="native",
    )


def _by_type(divergences: list[Divergence]) -> dict[DivergenceType, Divergence]:
    return {divergence.type: divergence for divergence in divergences}


def test_changed_tool_args_and_cost_regression_from_refund_fixtures() -> None:
    baseline = read_trace(FIXTURES / "refund_search_baseline.tbtrace")
    candidate = read_trace(FIXTURES / "refund_search_candidate_changed_tool_args.tbtrace")

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)

    by_type = _by_type(divergences)
    changed_tool_args = by_type["changed_tool_args"]
    assert changed_tool_args.severity == "CRITICAL"
    assert changed_tool_args.baseline_event is not None
    assert changed_tool_args.baseline_event.id == "evt_003"
    assert changed_tool_args.expected == {"query": "users WHERE active = true"}
    assert changed_tool_args.actual == {"query": "users WHERE active = true AND deleted = false"}
    assert changed_tool_args.source_metadata["alignment_kind"] == "ID_MATCH"

    cost = by_type["cost_regression"]
    assert cost.severity == "MEDIUM"
    assert cost.baseline_event is None
    assert cost.candidate_event is None
    assert cost.impact.cost_delta_ratio == pytest.approx(0.00028 / 0.00021)


def test_extra_error_event_escalates_to_critical() -> None:
    baseline = read_trace(FIXTURES / "refund_search_baseline.tbtrace")
    candidate = read_trace(FIXTURES / "refund_search_candidate_extra_error.tbtrace")

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)

    extra = _by_type(divergences)["extra_event"]
    assert extra.severity == "CRITICAL"
    assert extra.baseline_event is None
    assert extra.candidate_event is not None
    assert extra.candidate_event.type is EventType.ERROR
    assert extra.actual == {
        "event_type": "ERROR",
        "semantic_name": "database_retry_error",
        "sequence_index": 3,
    }


def test_missing_run_end_escalates_to_critical() -> None:
    b_root = _run_start("b_root")
    c_root = _run_start("c_root")
    baseline = _trace([b_root, _run_end("b_end", "b_root", 1, cost=0.01)])
    candidate = _trace([c_root])

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)

    missing = _by_type(divergences)["missing_event"]
    assert missing.severity == "CRITICAL"
    assert missing.baseline_event is not None
    assert missing.baseline_event.type is EventType.RUN_END
    assert missing.actual is None


def test_branch_changed_is_critical() -> None:
    b_root = _run_start("b_root")
    c_root = _run_start("c_root")
    baseline = _trace([b_root, _branch("b_branch", "b_root", 1, chosen_branch="approve")])
    candidate = _trace([c_root, _branch("c_branch", "c_root", 1, chosen_branch="deny")])

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)

    branch = _by_type(divergences)["branch_changed"]
    assert branch.severity == "CRITICAL"
    assert branch.expected == "approve"
    assert branch.actual == "deny"
    assert branch.source_metadata["branch_name"] == "refund_route"


def test_cost_regression_uses_fixed_twenty_percent_severity_threshold() -> None:
    b_root = _run_start("b_root")
    c_root = _run_start("c_root")
    baseline = _trace([b_root, _run_end("b_end", "b_root", 1, cost=1.0)])
    candidate_under = _trace([c_root, _run_end("c_end", "c_root", 1, cost=1.2)])
    candidate_over = _trace([c_root, _run_end("c_end", "c_root", 1, cost=1.21)])

    assert detect_divergences(align(baseline, candidate_under), baseline, candidate_under) == []

    divergences = detect_divergences(align(baseline, candidate_over), baseline, candidate_over)

    assert _by_type(divergences)["cost_regression"].impact.cost_delta_ratio == pytest.approx(1.21)


def test_first_divergence_prefers_earliest_high_or_critical_in_match_order() -> None:
    b_root = _run_start("b_root")
    c_root = _run_start("c_root")
    baseline = _trace(
        [
            b_root,
            _tool("b_tool", "b_root", 1, query="users WHERE active = true"),
            _branch("b_branch", "b_root", 2, chosen_branch="approve"),
        ]
    )
    candidate = _trace(
        [
            c_root,
            _tool("c_tool", "c_root", 1, query="users WHERE active = true AND deleted = false"),
            _branch("c_branch", "c_root", 2, chosen_branch="deny"),
        ]
    )

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)

    first = first_divergence(divergences)
    assert first is not None
    assert first.type == "changed_tool_args"


def test_impact_analysis_fields_are_json_ready() -> None:
    impact = ImpactAnalysis(
        tokens_delta=0,
        cost_delta_ratio=1.0,
        final_output_changed=False,
        errors_introduced=[],
        affected_event_count=0,
    )

    assert impact.errors_introduced == []


def test_detector_never_emits_v2_divergence_types() -> None:
    baseline = read_trace(FIXTURES / "refund_search_baseline.tbtrace")
    candidate = read_trace(FIXTURES / "refund_search_candidate_changed_tool_args.tbtrace")

    emitted = {
        divergence.type
        for divergence in detect_divergences(align(baseline, candidate), baseline, candidate)
    }

    assert emitted <= {
        "missing_event",
        "extra_event",
        "changed_tool_args",
        "branch_changed",
        "cost_regression",
    }


def test_cost_regression_tie_does_not_hide_critical_first_divergence() -> None:
    cost_divergence = Divergence(
        type="cost_regression",
        severity="MEDIUM",
        baseline_event=None,
        candidate_event=None,
        description="Cost rose",
        expected=1.0,
        actual=1.3,
        impact=ImpactAnalysis(
            tokens_delta=0,
            cost_delta_ratio=1.3,
            final_output_changed=False,
            errors_introduced=[],
            affected_event_count=0,
        ),
        source_metadata={"detector": "cost_regression", "scope": "trace"},
    )
    high_divergence = Divergence(
        type="missing_event",
        severity="HIGH",
        baseline_event=_run_end("b_end", "root", 1, cost=0.0),
        candidate_event=None,
        description="Missing RUN_END",
        expected={"event_type": "RUN_END", "semantic_name": "run", "sequence_index": 1},
        actual=None,
        impact=cost_divergence.impact,
        source_metadata={"detector": "missing_event", "match_index": 4},
    )
    critical_divergence = Divergence(
        type="changed_tool_args",
        severity="CRITICAL",
        baseline_event=_tool("b_tool", "root", 2, query="a"),
        candidate_event=_tool("c_tool", "root", 2, query="b"),
        description="Tool args changed",
        expected={"query": "a"},
        actual={"query": "b"},
        impact=cost_divergence.impact,
        source_metadata={"detector": "changed_tool_args", "match_index": 4},
    )

    assert (
        first_divergence([cost_divergence, high_divergence, critical_divergence])
        is critical_divergence
    )


def test_changed_tool_args_flags_swapped_tool_with_shared_event_ids() -> None:
    query = "order 123"
    baseline = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query=query, tool_name="lookup_order"),
        ]
    )
    candidate = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query=query, tool_name="cancel_order"),
        ]
    )

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)

    assert [divergence.type for divergence in divergences] == ["changed_tool_args"]
    swap = divergences[0]
    assert swap.severity == "CRITICAL"
    assert swap.description == "TOOL_CALL lookup_order replaced by cancel_order"
    assert swap.expected == {"tool_name": "lookup_order", "arguments": {"query": query}}
    assert swap.actual == {"tool_name": "cancel_order", "arguments": {"query": query}}
    assert swap.source_metadata["alignment_kind"] == "ID_MATCH"
    assert swap.source_metadata["tool_changed"] is True
    assert first_divergence(divergences) is swap


def test_changed_tool_args_flags_swapped_tool_paired_by_position() -> None:
    query = "order 123"
    baseline = _trace(
        [
            _run_start("b_root"),
            _tool("b_tool", "b_root", 1, query=query, tool_name="lookup_order"),
        ]
    )
    candidate = _trace(
        [
            _run_start("c_root"),
            _tool("c_tool", "c_root", 1, query=query, tool_name="cancel_order"),
        ]
    )

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)

    assert [divergence.type for divergence in divergences] == ["changed_tool_args"]
    assert divergences[0].source_metadata["alignment_kind"] == "POSITIONAL_MATCH"
    assert divergences[0].source_metadata["baseline_tool"] == "lookup_order"
    assert divergences[0].source_metadata["candidate_tool"] == "cancel_order"


def test_tool_name_whitespace_drift_is_not_reported_as_swap() -> None:
    query = "users WHERE active = true"
    baseline = _trace(
        [
            _run_start("b_root"),
            _tool("b_tool", "b_root", 1, query=query, tool_name="search_database"),
        ]
    )
    candidate = _trace(
        [
            _run_start("c_root"),
            _tool("c_tool", "c_root", 1, query=query, tool_name="search_database "),
        ]
    )

    matches = align(baseline, candidate)

    assert matches[1].kind == "STRUCTURAL_MATCH"
    assert detect_divergences(matches, baseline, candidate) == []


def test_changed_tool_args_flags_mcp_tool_change_on_same_server() -> None:
    baseline = _trace([_run_start("evt_001"), _mcp("evt_002", "evt_001", 1, server_name="files")])
    candidate = _trace(
        [
            _run_start("evt_001"),
            _mcp("evt_002", "evt_001", 1, server_name="files", tool_name="write_file"),
        ]
    )

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)

    assert [divergence.type for divergence in divergences] == ["changed_tool_args"]
    assert divergences[0].description == "MCP_CALL files/read_file replaced by files/write_file"


def test_changed_tool_args_ignores_placeholder_mcp_server_name() -> None:
    # otel.py emits server_name="unknown" when mcp.server.name is absent; that is
    # missing instrumentation, not a different server.
    baseline = _trace([_run_start("evt_001"), _mcp("evt_002", "evt_001", 1, server_name="files")])
    candidate = _trace(
        [_run_start("evt_001"), _mcp("evt_002", "evt_001", 1, server_name="unknown")]
    )

    assert detect_divergences(align(baseline, candidate), baseline, candidate) == []


def test_changed_tool_args_still_flags_tool_change_behind_placeholder_server() -> None:
    baseline = _trace([_run_start("evt_001"), _mcp("evt_002", "evt_001", 1, server_name="files")])
    candidate = _trace(
        [
            _run_start("evt_001"),
            _mcp("evt_002", "evt_001", 1, server_name="unknown", tool_name="write_file"),
        ]
    )

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)

    assert [divergence.type for divergence in divergences] == ["changed_tool_args"]
    assert divergences[0].description == "MCP_CALL files/read_file replaced by unknown/write_file"


def test_changed_tool_args_ignores_placeholder_tool_name() -> None:
    # otel.py emits tool_name="unknown_tool" when neither tool.name nor span.name exists.
    baseline = _trace([_run_start("evt_001"), _tool("evt_002", "evt_001", 1, query="q")])
    candidate = _trace(
        [_run_start("evt_001"), _tool("evt_002", "evt_001", 1, query="q", tool_name="unknown_tool")]
    )

    assert detect_divergences(align(baseline, candidate), baseline, candidate) == []


def test_changed_tool_args_flags_mcp_server_change() -> None:
    baseline = _trace(
        [
            _run_start("evt_001"),
            _mcp("evt_002", "evt_001", 1, server_name="files"),
        ]
    )
    candidate = _trace(
        [
            _run_start("evt_001"),
            _mcp("evt_002", "evt_001", 1, server_name="shell"),
        ]
    )

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)

    assert [divergence.type for divergence in divergences] == ["changed_tool_args"]
    assert divergences[0].description == "MCP_CALL files/read_file replaced by shell/read_file"
    assert divergences[0].expected == {
        "server_name": "files",
        "tool_name": "read_file",
        "arguments": {"path": "notes.txt"},
    }


@pytest.mark.parametrize(
    ("baseline_name", "candidate_name"),
    [
        ("café", "café"),
        ("search_database", "search​_database"),
        ("search_database", "  search_database"),
        ("café", "cafe​́"),
    ],
)
def test_tool_identity_ignores_normalization_invisible_characters_and_padding(
    baseline_name: str,
    candidate_name: str,
) -> None:
    baseline = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query="q", tool_name=baseline_name),
        ]
    )
    candidate = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query="q", tool_name=candidate_name),
        ]
    )

    assert detect_divergences(align(baseline, candidate), baseline, candidate) == []


def test_case_only_tool_name_change_is_reported() -> None:
    baseline = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query="q", tool_name="Search"),
        ]
    )
    candidate = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query="q", tool_name="search"),
        ]
    )

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)

    assert [divergence.description for divergence in divergences] == [
        "TOOL_CALL Search replaced by search",
    ]


def test_swap_with_changed_arguments_is_described_as_replacement() -> None:
    baseline = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query="order 123", tool_name="lookup_order"),
        ]
    )
    candidate = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query="order 999", tool_name="cancel_order"),
        ]
    )

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)

    assert [divergence.description for divergence in divergences] == [
        "TOOL_CALL lookup_order replaced by cancel_order",
    ]
    assert divergences[0].actual == {
        "tool_name": "cancel_order",
        "arguments": {"query": "order 999"},
    }


def test_first_divergence_prefers_earlier_swap_over_later_argument_drift() -> None:
    baseline = _trace(
        [
            _run_start("b_root"),
            _tool("b_first", "b_root", 1, query="q1", tool_name="lookup_order"),
            _tool("b_second", "b_root", 2, query="q2", tool_name="search_database"),
        ]
    )
    candidate = _trace(
        [
            _run_start("c_root"),
            _tool("c_first", "c_root", 1, query="q1", tool_name="cancel_order"),
            _tool("c_second", "c_root", 2, query="q3", tool_name="search_database"),
        ]
    )

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)
    first = first_divergence(divergences)

    assert sorted(divergence.description for divergence in divergences) == [
        "TOOL_CALL lookup_order replaced by cancel_order",
        "TOOL_CALL search_database arguments differ",
    ]
    assert first is not None
    assert first.description == "TOOL_CALL lookup_order replaced by cancel_order"


def test_tool_identity_uses_tool_name_not_semantic_name() -> None:
    baseline = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query="q", tool_name="lookup", semantic_name="tool"),
            _tool("evt_003", "evt_001", 2, query="q", tool_name="search", semantic_name="step_a"),
        ]
    )
    candidate = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query="q", tool_name="cancel", semantic_name="tool"),
            _tool("evt_003", "evt_001", 2, query="q", tool_name="search", semantic_name="step_b"),
        ]
    )

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)

    assert [divergence.description for divergence in divergences] == [
        "TOOL_CALL lookup replaced by cancel",
    ]


@pytest.mark.parametrize("arguments", [{"order_id": "123"}, {}])
def test_positional_ids_on_reordered_tools_are_not_a_swap(arguments: JsonObject) -> None:
    baseline = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query="", tool_name="get_status", arguments=arguments),
            _tool("evt_003", "evt_001", 2, query="", tool_name="get_shipping", arguments=arguments),
        ]
    )
    candidate = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query="", tool_name="get_shipping", arguments=arguments),
            _tool("evt_003", "evt_001", 2, query="", tool_name="get_status", arguments=arguments),
        ]
    )

    assert detect_divergences(align(baseline, candidate), baseline, candidate) == []


def test_positional_ids_after_an_inserted_tool_report_the_extra_step() -> None:
    baseline = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query="order 123", tool_name="lookup_order"),
            _tool("evt_003", "evt_001", 2, query="order 123", tool_name="refund_order"),
        ]
    )
    candidate = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query="order 123", tool_name="lookup_order"),
            _tool("evt_003", "evt_001", 2, query="order 123", tool_name="check_fraud"),
            _tool("evt_004", "evt_001", 3, query="order 123", tool_name="refund_order"),
        ]
    )

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)

    assert [divergence.type for divergence in divergences] == ["extra_event"]
    assert divergences[0].candidate_event is not None
    assert divergences[0].candidate_event.semantic_name == "check_fraud"


def test_positional_ids_after_a_removed_tool_report_the_missing_step() -> None:
    baseline = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query="order 123", tool_name="lookup_order"),
            _tool("evt_003", "evt_001", 2, query="order 123", tool_name="verify_identity"),
            _tool("evt_004", "evt_001", 3, query="order 123", tool_name="refund_order"),
        ]
    )
    candidate = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query="order 123", tool_name="lookup_order"),
            _tool("evt_003", "evt_001", 2, query="order 123", tool_name="refund_order"),
        ]
    )

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)

    assert [divergence.type for divergence in divergences] == ["missing_event"]
    assert divergences[0].baseline_event is not None
    assert divergences[0].baseline_event.semantic_name == "verify_identity"


@pytest.mark.parametrize(
    ("baseline_servers", "candidate_servers"),
    [
        (("files", "files"), ("unknown", "unknown")),
        (("unknown", "unknown"), ("unknown", "unknown")),
        (("", ""), ("", "")),
    ],
)
def test_positional_ids_on_reordered_mcp_calls_with_placeholder_servers_are_not_a_swap(
    baseline_servers: tuple[str, str],
    candidate_servers: tuple[str, str],
) -> None:
    baseline = _trace(
        [
            _run_start("evt_001"),
            _mcp("evt_002", "evt_001", 1, server_name=baseline_servers[0], tool_name="read_file"),
            _mcp("evt_003", "evt_001", 2, server_name=baseline_servers[1], tool_name="write_file"),
        ]
    )
    candidate = _trace(
        [
            _run_start("evt_001"),
            _mcp("evt_002", "evt_001", 1, server_name=candidate_servers[0], tool_name="write_file"),
            _mcp("evt_003", "evt_001", 2, server_name=candidate_servers[1], tool_name="read_file"),
        ]
    )

    assert detect_divergences(align(baseline, candidate), baseline, candidate) == []


def test_placeholder_server_insertion_reports_only_the_extra_step() -> None:
    baseline = _trace(
        [
            _run_start("evt_001"),
            _mcp("evt_002", "evt_001", 1, server_name="files", tool_name="read_file"),
            _mcp("evt_003", "evt_001", 2, server_name="files", tool_name="write_file"),
        ]
    )
    candidate = _trace(
        [
            _run_start("evt_001"),
            _mcp("evt_002", "evt_001", 1, server_name="unknown", tool_name="read_file"),
            _mcp("evt_003", "evt_001", 2, server_name="unknown", tool_name="list_dir"),
            _mcp("evt_004", "evt_001", 3, server_name="unknown", tool_name="write_file"),
        ]
    )

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)

    assert [divergence.type for divergence in divergences] == ["extra_event"]
    assert divergences[0].candidate_event is not None
    assert divergences[0].candidate_event.semantic_name == "list_dir"


def test_positional_ids_on_reordered_same_named_mcp_tools_keep_their_servers() -> None:
    baseline = _trace(
        [
            _run_start("evt_001"),
            _mcp("evt_002", "evt_001", 1, server_name="files", tool_name="read_file"),
            _mcp("evt_003", "evt_001", 2, server_name="shell", tool_name="read_file"),
        ]
    )
    candidate = _trace(
        [
            _run_start("evt_001"),
            _mcp("evt_002", "evt_001", 1, server_name="shell", tool_name="read_file"),
            _mcp("evt_003", "evt_001", 2, server_name="files", tool_name="read_file"),
        ]
    )

    assert detect_divergences(align(baseline, candidate), baseline, candidate) == []


def test_swap_description_uses_normalized_tool_labels() -> None:
    baseline = _trace(
        [
            _run_start("evt_001"),
            _tool(
                "evt_002", "evt_001", 1, query="q", tool_name="lookup_order", semantic_name="step"
            ),
        ]
    )
    candidate = _trace(
        [
            _run_start("evt_001"),
            _tool(
                "evt_002",
                "evt_001",
                1,
                query="q",
                tool_name=" cancel_order​",
                semantic_name="step",
            ),
        ]
    )

    divergences = detect_divergences(align(baseline, candidate), baseline, candidate)

    assert [divergence.description for divergence in divergences] == [
        "TOOL_CALL lookup_order replaced by cancel_order",
    ]
    assert divergences[0].source_metadata["baseline_tool"] == "lookup_order"
    assert divergences[0].source_metadata["candidate_tool"] == "cancel_order"
    assert divergences[0].actual == {
        "tool_name": " cancel_order​",
        "arguments": {"query": "q"},
    }


def test_empty_names_and_placeholder_tools_match_any_tool() -> None:
    tool_baseline = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query="q", tool_name="search_database"),
        ]
    )
    tool_candidate = _trace(
        [
            _run_start("evt_001"),
            _tool("evt_002", "evt_001", 1, query="q", tool_name=""),
        ]
    )
    mcp_baseline = _trace(
        [
            _run_start("evt_001"),
            _mcp("evt_002", "evt_001", 1, server_name="files", tool_name="read_file"),
        ]
    )
    mcp_candidates = [
        _trace(
            [
                _run_start("evt_001"),
                _mcp("evt_002", "evt_001", 1, server_name="", tool_name="read_file"),
            ]
        ),
        _trace(
            [
                _run_start("evt_001"),
                _mcp("evt_002", "evt_001", 1, server_name="files", tool_name="unknown_tool"),
            ]
        ),
    ]

    tool_matches = align(tool_baseline, tool_candidate)
    assert detect_divergences(tool_matches, tool_baseline, tool_candidate) == []
    for candidate in mcp_candidates:
        assert detect_divergences(align(mcp_baseline, candidate), mcp_baseline, candidate) == []
