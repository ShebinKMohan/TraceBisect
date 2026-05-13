"""Built-in refund-search demo traces for TraceBisect."""

from __future__ import annotations

from datetime import datetime, timezone

from tracebisect.schema import (
    SCHEMA_VERSION,
    Event,
    EventType,
    LLMCallPayload,
    RunEndPayload,
    RunStartPayload,
    ToolCallPayload,
    Trace,
)

__all__ = ["build_refund_baseline_trace", "build_refund_candidate_trace"]

_UTC = timezone.utc
_T0 = datetime(2026, 5, 11, 6, 52, 1, tzinfo=_UTC)
_T1 = datetime(2026, 5, 11, 6, 52, 2, tzinfo=_UTC)


def build_refund_baseline_trace() -> Trace:
    """Build the canonical baseline trace used by ``tracebisect demo``."""
    return _refund_trace(
        trace_id="trc_demo_refund_baseline",
        query="users WHERE active = true",
        result=(
            '[{"id": 1, "name": "alice"}, {"id": 2, "name": "carol"}, {"id": 3, "name": "ravi"}]'
        ),
        final_output="Found 3 active users eligible for refund follow-up: alice, carol, ravi.",
        total_cost_usd=0.00021,
    )


def build_refund_candidate_trace() -> Trace:
    """Build the candidate trace with the intentional tool-argument regression."""
    return _refund_trace(
        trace_id="trc_demo_refund_candidate",
        query="users WHERE active = true AND deleted = false",
        result='[{"id": 1, "name": "alice"}, {"id": 3, "name": "ravi"}]',
        final_output="Found 2 active non-deleted users eligible for refund follow-up: alice, ravi.",
        total_cost_usd=0.00028,
    )


def _refund_trace(
    *,
    trace_id: str,
    query: str,
    result: str,
    final_output: str,
    total_cost_usd: float,
) -> Trace:
    root = Event(
        id="evt_001",
        parent_id=None,
        sequence_index=0,
        type=EventType.RUN_START,
        semantic_name="refund_search",
        timestamp=_T0,
        duration_ms=None,
        payload=RunStartPayload(
            user_input="Find active users eligible for refund follow-up.",
            agent_name="refund-agent",
            agent_version="1.0",
            run_metadata={"demo": True, "case": "refund_search"},
        ),
        source_format="native",
        source_event_id="evt_001",
        model_version=None,
        prompt_version="refund_search:v3",
        code_sha="a3f9c1d",
        sampling_params=None,
    )
    llm = Event(
        id="evt_002",
        parent_id="evt_001",
        sequence_index=1,
        type=EventType.LLM_CALL,
        semantic_name="gpt-4o-mini",
        timestamp=_T0,
        duration_ms=412.3,
        payload=LLMCallPayload(
            model="gpt-4o-mini",
            provider="openai",
            messages=[
                {
                    "role": "user",
                    "content": "Find active users eligible for refund follow-up.",
                }
            ],
            response_text="I will query the user database.",
            response_tool_calls=[
                {
                    "name": "search_database",
                    "arguments": {"query": query},
                }
            ],
            input_tokens=84,
            output_tokens=22,
            cost_usd=total_cost_usd,
            temperature=0.0,
            seed=42,
        ),
        source_format="native",
        source_event_id="evt_002",
        model_version="gpt-4o-2024-08-06",
        prompt_version="refund_search:v3",
        code_sha="a3f9c1d",
        sampling_params={"temperature": 0.0, "seed": 42},
    )
    tool = Event(
        id="evt_003",
        parent_id="evt_002",
        sequence_index=2,
        type=EventType.TOOL_CALL,
        semantic_name="search_database",
        timestamp=_T0,
        duration_ms=18.7,
        payload=ToolCallPayload(
            tool_name="search_database",
            arguments={"query": query},
            result=result,
            error=None,
        ),
        source_format="native",
        source_event_id="evt_003",
        model_version=None,
        prompt_version=None,
        code_sha="a3f9c1d",
        sampling_params=None,
    )
    end = Event(
        id="evt_004",
        parent_id="evt_001",
        sequence_index=3,
        type=EventType.RUN_END,
        semantic_name="refund_search",
        timestamp=_T1,
        duration_ms=None,
        payload=RunEndPayload(
            final_output=final_output,
            total_input_tokens=84,
            total_output_tokens=22,
            total_cost_usd=total_cost_usd,
            success=True,
        ),
        source_format="native",
        source_event_id="evt_004",
        model_version=None,
        prompt_version="refund_search:v3",
        code_sha="a3f9c1d",
        sampling_params=None,
    )
    return Trace(
        schema_version=SCHEMA_VERSION,
        trace_id=trace_id,
        created_at=_T0,
        root_event=root,
        events=[root, llm, tool, end],
        source_convention="native",
    )
