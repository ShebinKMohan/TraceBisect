"""Contract tests for checked-in ``.tbtrace`` fixtures under tests/fixtures/.

These fixtures are durable artifacts that later phases (alignment, divergence
detection, renderer, export-pytest) build on, so this module pins their
structure, parent-chain shape, datetime/null contract, and key scenario fields.
The fixtures are static; tests read but never write them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracebisect.jsonl import read_trace
from tracebisect.schema import (
    SCHEMA_VERSION,
    ErrorPayload,
    EventType,
    RunEndPayload,
    ToolCallPayload,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

BASELINE_PATH = FIXTURES_DIR / "refund_search_baseline.tbtrace"
CHANGED_TOOL_ARGS_PATH = FIXTURES_DIR / "refund_search_candidate_changed_tool_args.tbtrace"
EXTRA_ERROR_PATH = FIXTURES_DIR / "refund_search_candidate_extra_error.tbtrace"
MIXED_PATH = FIXTURES_DIR / "mixed_convention_trace.tbtrace"

ALL_FIXTURES: list[Path] = [
    BASELINE_PATH,
    CHANGED_TOOL_ARGS_PATH,
    EXTRA_ERROR_PATH,
    MIXED_PATH,
]

EVENT_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "parent_id",
        "sequence_index",
        "type",
        "semantic_name",
        "timestamp",
        "duration_ms",
        "payload",
        "source_format",
        "source_event_id",
        "model_version",
        "prompt_version",
        "code_sha",
        "sampling_params",
    }
)

HEADER_FIELDS: frozenset[str] = frozenset(
    {"schema_version", "trace_id", "created_at", "source_convention"}
)


def _nonblank_lines(path: Path) -> list[str]:
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ----------------------------- 1. Discovery ----------------------------- #


def test_all_fixture_files_exist() -> None:
    for path in ALL_FIXTURES:
        assert path.exists(), f"missing fixture {path}"


def test_all_fixtures_have_tbtrace_suffix() -> None:
    for path in ALL_FIXTURES:
        assert path.suffix == ".tbtrace"


# ----------------------------- 2. Readability ----------------------------- #


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=[p.name for p in ALL_FIXTURES])
def test_fixture_is_readable_and_structurally_valid(path: Path) -> None:
    trace = read_trace(path)
    assert trace.schema_version == SCHEMA_VERSION
    assert trace.events[0] is trace.root_event
    assert trace.root_event.type is EventType.RUN_START
    for i, event in enumerate(trace.events):
        assert event.sequence_index == i

    id_to_index: dict[str, int] = {e.id: i for i, e in enumerate(trace.events)}
    for i, event in enumerate(trace.events[1:], start=1):
        assert event.parent_id is not None, (
            f"{path.name}: non-root event {event.id!r} has parent_id=None"
        )
        assert event.parent_id in id_to_index, (
            f"{path.name}: event {event.id!r} references unknown parent {event.parent_id!r}"
        )
        assert id_to_index[event.parent_id] < i, (
            f"{path.name}: event {event.id!r} parent appears at-or-after child"
        )


# ----------------------------- 3. JSONL shape ----------------------------- #


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=[p.name for p in ALL_FIXTURES])
def test_fixture_has_header_and_at_least_one_event_line(path: Path) -> None:
    lines = _nonblank_lines(path)
    assert len(lines) >= 2


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=[p.name for p in ALL_FIXTURES])
def test_fixture_header_has_required_fields_only(path: Path) -> None:
    lines = _nonblank_lines(path)
    header = json.loads(lines[0])
    assert isinstance(header, dict)
    for key in HEADER_FIELDS:
        assert key in header, f"{path.name}: header missing {key!r}"
    assert "events" not in header, (
        f"{path.name}: header should not contain an inline 'events' array"
    )


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=[p.name for p in ALL_FIXTURES])
def test_every_event_line_has_all_fourteen_event_fields(path: Path) -> None:
    lines = _nonblank_lines(path)
    for lineno, raw in enumerate(lines[1:], start=2):
        event = json.loads(raw)
        assert isinstance(event, dict), f"{path.name}:L{lineno} not a JSON object"
        missing = EVENT_FIELDS - event.keys()
        assert not missing, f"{path.name}:L{lineno} event missing fields: {sorted(missing)}"


# ----------------------------- 4. Datetime / null contract ----------------------------- #


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=[p.name for p in ALL_FIXTURES])
def test_header_and_event_datetimes_end_with_z(path: Path) -> None:
    lines = _nonblank_lines(path)
    header = json.loads(lines[0])
    created_at = header["created_at"]
    assert isinstance(created_at, str)
    assert created_at.endswith("Z"), f"{path.name}: created_at {created_at!r} must end with Z"
    for lineno, raw in enumerate(lines[1:], start=2):
        event = json.loads(raw)
        ts = event["timestamp"]
        assert isinstance(ts, str)
        assert ts.endswith("Z"), f"{path.name}:L{lineno} timestamp {ts!r} must end with Z"


def test_at_least_one_event_has_explicit_json_null_for_optional_field() -> None:
    optional_keys = {
        "parent_id",
        "duration_ms",
        "model_version",
        "prompt_version",
        "code_sha",
        "sampling_params",
    }
    found = False
    for path in ALL_FIXTURES:
        lines = _nonblank_lines(path)
        for raw in lines[1:]:
            # Reparse as raw text to be sure the key is *present* (not omitted)
            # before checking it parses to None.
            event = json.loads(raw)
            for key in optional_keys:
                if key in event and event[key] is None:
                    # Confirm the key actually appears in the on-disk text;
                    # json.loads can't fabricate a key, but assert anyway so a
                    # future hand-edit can't drop the key and still pass.
                    assert f'"{key}":' in raw, (
                        f"{path.name}: key {key!r} parsed but absent in raw text"
                    )
                    found = True
    assert found, "no fixture event had JSON null for any optional field"


# ----------------------------- 5. Baseline contract ----------------------------- #


def test_baseline_fixture_contract() -> None:
    trace = read_trace(BASELINE_PATH)
    assert trace.source_convention == "native"

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

    payload = tool_call.payload
    assert isinstance(payload, ToolCallPayload)
    assert payload.arguments == {"query": "users WHERE active = true"}


# ----------------------------- 6. changed_tool_args contract ----------------------------- #


def test_changed_tool_args_candidate_contract() -> None:
    baseline = read_trace(BASELINE_PATH)
    candidate = read_trace(CHANGED_TOOL_ARGS_PATH)

    baseline_tool = baseline.events[2]
    candidate_tool = candidate.events[2]

    for evt in (baseline_tool, candidate_tool):
        assert evt.type is EventType.TOOL_CALL
        assert evt.semantic_name == "search_database"
        assert isinstance(evt.payload, ToolCallPayload)
        assert evt.payload.tool_name == "search_database"

    assert [e.id for e in baseline.events] == [e.id for e in candidate.events]
    assert [e.parent_id for e in baseline.events] == [e.parent_id for e in candidate.events]

    baseline_args = baseline_tool.payload
    candidate_args = candidate_tool.payload
    assert isinstance(baseline_args, ToolCallPayload)
    assert isinstance(candidate_args, ToolCallPayload)
    assert baseline_args.arguments == {"query": "users WHERE active = true"}
    assert candidate_args.arguments == {"query": "users WHERE active = true AND deleted = false"}
    assert baseline_args.arguments != candidate_args.arguments

    baseline_end_payload = baseline.events[3].payload
    candidate_end_payload = candidate.events[3].payload
    assert isinstance(baseline_end_payload, RunEndPayload)
    assert isinstance(candidate_end_payload, RunEndPayload)
    assert baseline_end_payload.final_output != candidate_end_payload.final_output


# ----------------------------- 7. extra_error contract ----------------------------- #


def test_extra_error_candidate_contract() -> None:
    trace = read_trace(EXTRA_ERROR_PATH)
    types = [e.type for e in trace.events]
    assert types == [
        EventType.RUN_START,
        EventType.LLM_CALL,
        EventType.TOOL_CALL,
        EventType.ERROR,
        EventType.RUN_END,
    ]

    run_start, _, tool_call, error_event, run_end = trace.events
    assert error_event.type is EventType.ERROR
    assert error_event.parent_id == tool_call.id
    assert error_event.sequence_index == 3
    assert run_end.sequence_index == 4
    assert run_end.parent_id == run_start.id

    payload = error_event.payload
    assert isinstance(payload, ErrorPayload)
    assert isinstance(payload.recovered, bool)
    # Pin the fixture's recovered=True choice so a casual rewrite can't drift.
    assert payload.recovered is True

    assert error_event.sequence_index < run_end.sequence_index


# ----------------------------- 8. mixed convention contract ----------------------------- #


def test_mixed_convention_fixture_contract() -> None:
    trace = read_trace(MIXED_PATH)
    assert trace.source_convention == "mixed"

    source_formats = {e.source_format for e in trace.events}
    assert "otel" in source_formats
    assert "native" in source_formats

    allowed = {"otel", "native"}
    assert source_formats <= allowed, f"unexpected source_format values: {source_formats - allowed}"
