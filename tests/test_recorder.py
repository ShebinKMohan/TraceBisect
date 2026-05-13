from __future__ import annotations

from pathlib import Path

import pytest

import tracebisect
from tracebisect.jsonl import read_trace
from tracebisect.recorder import record_trace, wrap_tool
from tracebisect.schema import EventType, LLMCallPayload, RunEndPayload, ToolCallPayload


@wrap_tool
def search_database(query: str) -> list[dict[str, str | int]]:
    return [{"id": 1, "name": "alice", "query": query}]


def test_record_trace_writes_native_trace_with_wrapped_tool(tmp_path: Path) -> None:
    output = tmp_path / "recorded.tbtrace"

    with record_trace(
        user_input="Find active users.",
        output_path=output,
        trace_id="trc_native_recorder",
        agent_name="refund-agent",
        agent_version="1.0",
        prompt_version="refund_search:v3",
        code_sha="a3f9c1d",
    ) as recorder:
        with recorder.llm_call(
            model="gpt-4o-mini",
            provider="openai",
            messages=[{"role": "user", "content": "Find active users."}],
            response_text="I will query the database.",
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
        ):
            rows = search_database(query="users WHERE active = true")
        recorder.finish(
            final_output="Found 1 active user: alice.",
            total_input_tokens=84,
            total_output_tokens=22,
            total_cost_usd=0.00021,
        )

    trace = read_trace(output)

    assert trace.trace_id == "trc_native_recorder"
    assert trace.source_convention == "native"
    assert [event.type for event in trace.events] == [
        EventType.RUN_START,
        EventType.LLM_CALL,
        EventType.TOOL_CALL,
        EventType.RUN_END,
    ]
    assert rows == [{"id": 1, "name": "alice", "query": "users WHERE active = true"}]

    llm = trace.events[1]
    tool = trace.events[2]
    assert llm.parent_id == trace.root_event.id
    assert tool.parent_id == llm.id
    assert isinstance(llm.payload, LLMCallPayload)
    assert llm.payload.model == "gpt-4o-mini"
    assert isinstance(tool.payload, ToolCallPayload)
    assert tool.payload.tool_name == "search_database"
    assert tool.payload.arguments == {"query": "users WHERE active = true"}
    assert '"alice"' in (tool.payload.result or "")


def test_record_trace_uses_tracebisect_output_when_output_path_is_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "env-output.tbtrace"
    monkeypatch.setenv("TRACEBISECT_OUTPUT", str(output))

    with record_trace(user_input="hello", trace_id="trc_env") as recorder:
        recorder.finish(final_output="done")

    assert read_trace(output).trace_id == "trc_env"


def test_wrap_tool_calls_function_normally_without_active_recorder() -> None:
    assert search_database(query="users WHERE active = true")[0]["name"] == "alice"


def test_recorder_api_is_available_from_package_top_level() -> None:
    assert tracebisect.record_trace is record_trace
    assert tracebisect.wrap_tool is wrap_tool


def test_tool_exception_is_recorded_then_reraised(tmp_path: Path) -> None:
    output = tmp_path / "error.tbtrace"

    @wrap_tool
    def explode() -> None:
        raise RuntimeError("database unavailable")

    with (
        pytest.raises(RuntimeError, match="database unavailable"),
        record_trace(user_input="fail", output_path=output, trace_id="trc_error"),
    ):
        explode()

    trace = read_trace(output)
    assert [event.type for event in trace.events] == [
        EventType.RUN_START,
        EventType.TOOL_CALL,
        EventType.ERROR,
        EventType.RUN_END,
    ]
    tool = trace.events[1]
    end = trace.events[-1]
    assert isinstance(tool.payload, ToolCallPayload)
    assert tool.payload.error == "database unavailable"
    assert isinstance(end.payload, RunEndPayload)
    assert end.payload.success is False
