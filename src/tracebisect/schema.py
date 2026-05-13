"""Canonical V1 trace schema for TraceBisect.

Implements the typed canonical event/trace data model defined in
``spec/canonical-trace-schema.md`` (Step 1.1) and referenced by
``spec/production-spec.md`` §5.1. Every downstream component
(alignment, divergence detection, rendering, export-pytest) operates on
this single representation regardless of the original source format.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias

# StrEnum is stdlib in 3.11+. The runtime target is 3.10+ per pyproject.toml,
# so fall back to a (str, Enum) subclass when running on 3.10 to preserve the
# `str`-valued behaviour the schema relies on.
if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # pragma: no cover - exercised only on Python 3.10
    from enum import Enum

    class StrEnum(str, Enum):
        """Backport of :class:`enum.StrEnum` for Python 3.10."""


SCHEMA_VERSION: int = 1

# Recursive JSON type aliases used throughout the schema. `JsonValue` is the
# union of every JSON-serializable Python value; `JsonObject` is a JSON
# object. Use these wherever a payload field is "raw JSON we will not
# introspect beyond structural diff" — never bare `dict` or `list[dict]`.
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class EventType(StrEnum):
    """Closed V1 vocabulary of event kinds (see schema §4)."""

    RUN_START = "RUN_START"
    RUN_END = "RUN_END"
    LLM_CALL = "LLM_CALL"
    TOOL_CALL = "TOOL_CALL"
    MCP_CALL = "MCP_CALL"
    RETRIEVAL = "RETRIEVAL"
    STATE_TRANSITION = "STATE_TRANSITION"
    BRANCH_DECISION = "BRANCH_DECISION"
    ERROR = "ERROR"
    HUMAN_INPUT = "HUMAN_INPUT"


@dataclass(frozen=True, slots=True)
class RunStartPayload:
    user_input: str
    agent_name: str | None
    agent_version: str | None
    run_metadata: JsonObject


@dataclass(frozen=True, slots=True)
class RunEndPayload:
    final_output: str
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    success: bool


@dataclass(frozen=True, slots=True)
class LLMCallPayload:
    model: str
    provider: str
    messages: list[JsonObject]
    response_text: str
    response_tool_calls: list[JsonObject]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    temperature: float
    seed: int | None


@dataclass(frozen=True, slots=True)
class ToolCallPayload:
    tool_name: str
    arguments: JsonObject
    result: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class MCPCallPayload:
    server_name: str
    tool_name: str
    arguments: JsonObject
    result: str | None
    error: str | None
    protocol_version: str | None


@dataclass(frozen=True, slots=True)
class RetrievalPayload:
    query: str
    retriever: str
    top_k: int
    document_ids: list[str]
    scores: list[float]


@dataclass(frozen=True, slots=True)
class StateTransitionPayload:
    from_state: str | None
    to_state: str
    trigger: str | None


@dataclass(frozen=True, slots=True)
class BranchDecisionPayload:
    branch_name: str
    chosen_branch: str
    available_branches: list[str]
    reasoning: str | None


@dataclass(frozen=True, slots=True)
class ErrorPayload:
    error_type: str
    message: str
    stack_trace: str | None
    recovered: bool


@dataclass(frozen=True, slots=True)
class HumanInputPayload:
    prompt_shown: str
    response: str
    responder_id: str | None


EventPayload: TypeAlias = (
    RunStartPayload
    | RunEndPayload
    | LLMCallPayload
    | ToolCallPayload
    | MCPCallPayload
    | RetrievalPayload
    | StateTransitionPayload
    | BranchDecisionPayload
    | ErrorPayload
    | HumanInputPayload
)


@dataclass(frozen=True, slots=True)
class Event:
    id: str
    parent_id: str | None
    sequence_index: int
    type: EventType
    semantic_name: str
    timestamp: datetime
    duration_ms: float | None
    payload: EventPayload
    source_format: Literal["otel", "native"]
    source_event_id: str
    model_version: str | None
    prompt_version: str | None
    code_sha: str | None
    sampling_params: JsonObject | None


@dataclass(frozen=True, slots=True)
class Trace:
    schema_version: int
    trace_id: str
    created_at: datetime
    root_event: Event
    events: list[Event]
    source_convention: Literal["native", "genai", "openinference", "mixed", "unknown"]


class TraceBisectSchemaError(ValueError):
    """Base class for canonical-schema / on-disk format errors."""


class UnknownSchemaVersionError(TraceBisectSchemaError):
    """Raised when a `.tbtrace` declares a schema_version this build cannot read."""


class MalformedTraceError(TraceBisectSchemaError):
    """Raised when a `.tbtrace` is structurally invalid for the current schema."""


class BrokenParentChainError(TraceBisectSchemaError):
    """Raised when an event references a parent_id that is not present in the trace."""
