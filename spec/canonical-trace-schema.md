# Canonical Trace Schema — Design Document

> Status: Design (Phase 1 V1)
> Companion to: `spec/production-spec.md` §5.1, §5.4, §10, §11
> Implementation target: `src/tracebisect/schema.py` (not yet written)

---

## 1. Purpose

TraceBisect ingests traces from heterogeneous sources: its own native recorder, OpenTelemetry GenAI conventions (`gen_ai.*`), OpenInference conventions (`openinference.*`), and — over time — vendor-specific exporters. Every downstream component (alignment, divergence detection, rendering, export-pytest) must operate on a **single typed representation**. Alignment cannot meaningfully diff a `langfuse.Generation` against an `openinference.LLM` span; it diffs two `LLMCallPayload` objects whose shape is identical regardless of origin.

The canonical schema exists to:

1. **Decouple ingestion from analysis.** New source formats become adapters; alignment and divergence never learn about them.
2. **Make divergences typeable.** The severity rules in production-spec §5.4 key off `EventType` and payload fields, which is only meaningful against a closed, typed vocabulary.
3. **Provide a stable on-disk format.** `.tbtrace` files written by V1 must be readable by V1.1+ via migrations (§7).
4. **Keep ambiguity localized.** Source-format quirks live in adapters and provenance fields, not in the data model.

The schema is opinionated and narrow — not a generic observability schema. It models exactly what TraceBisect needs to bisect a regression.

---

## 2. Trace Structure

The top-level container is `Trace`: one event tree, identified by a globally unique `trace_id`. Events are stored both as a tree (via `root_event` + `parent_id` links) and as a flat list (`events`) in depth-first `sequence_index` order. The flat list exists because alignment, rendering, and the JSONL writer all iterate sequentially far more often than they traverse the tree.

Top-level fields:

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `int` | Matches the `SCHEMA_VERSION` module constant. Drives migration dispatch on load. |
| `trace_id` | `str` | Globally unique. Native recorder generates opaque stable IDs; OTel ingest reuses the trace ID. |
| `created_at` | `datetime` | UTC, timezone-aware. The wall-clock time the run started, not the time the file was written. |
| `root_event` | `Event` | Always a `RUN_START` event. Invariant enforced at construction. |
| `events` | `list[Event]` | Depth-first ordered. Includes `root_event` at index 0. Last event is `RUN_END` or `ERROR` for crashed runs. |
| `source_convention` | `Literal["native", "genai", "openinference", "mixed", "unknown"]` | Records which semantic convention the source spans followed. `mixed` is legitimate (some agents emit both). `unknown` is a soft fallback — ingest never hard-fails on it. |

```python
SCHEMA_VERSION: int = 1

# JSON type aliases used throughout the schema. `JsonValue` is a recursive
# alias for any JSON-serializable value; `JsonObject` is a JSON object.
# Use these everywhere a payload field is "raw JSON we will not introspect
# beyond structural diff" — never bare `dict` or `list[dict]`.
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class Trace:
    schema_version: int
    trace_id: str
    created_at: datetime
    root_event: "Event"
    events: list["Event"]
    source_convention: Literal["native", "genai", "openinference", "mixed", "unknown"]
```

Invariants (enforced in a `__post_init__` or a `validate()` helper — to be decided):

- `events[0] is root_event`
- `root_event.type is EventType.RUN_START`
- `all(e.sequence_index == i for i, e in enumerate(events))`
- `schema_version == SCHEMA_VERSION` on construction (older versions are upgraded before instantiation)

---

## 3. Event Model

`Event` is the unit of comparison for alignment. Every field is either (a) needed for alignment, (b) needed for rendering the money-shot output (production-spec §11), or (c) provenance for export-pytest. Nothing else.

```python
@dataclass(frozen=True, slots=True)
class Event:
    # Identity and tree structure
    id: str  # Globally unique within the trace; opaque stable string in native traces.
    parent_id: Optional[str]  # None only for the root RUN_START event.
    sequence_index: int  # Position in depth-first traversal; 0 for root.

    # Semantic identity — what this event "is"
    type: EventType  # Discriminator for `payload`.
    semantic_name: str  # Human-readable label, e.g. "search_database", "gpt-4o-mini".
    # Used in alignment's semantic-match tier and in rendered output.

    # Temporal
    timestamp: datetime  # UTC, timezone-aware. Start time of the event.
    duration_ms: Optional[float]  # None for instantaneous events (e.g. BRANCH_DECISION).

    # Type-specific data
    payload: "EventPayload"  # Discriminated union; see §5. Shape MUST match `type`.

    # Provenance — where this event came from
    source_format: Literal["otel", "native"]  # V1 values only. Vendor adapters land in V1.1+.
    source_event_id: str  # Original ID in the source system (OTel span ID, etc.).

    # Replay metadata — needed for export-pytest reproduction
    model_version: Optional[str]  # e.g. "gpt-4o-2024-08-06". Only meaningful for LLM_CALL.
    prompt_version: Optional[str]  # User-supplied prompt template version; opaque to TraceBisect.
    code_sha: Optional[str]  # git SHA of the agent code at run time, if available.
    sampling_params: Optional[
        JsonObject
    ]  # temperature/top_p/seed snapshot for replay. Opaque escape hatch.
```

**On `sampling_params: Optional[JsonObject]`.** One of two intentional escape hatches in the schema (the other is `run_metadata`). Providers add sampling knobs constantly — logit bias, response format, structured outputs, reasoning effort. A typed shape would either omit fields or churn quarterly. Contract: opaque to alignment and divergence; exists solely so export-pytest can re-issue the call faithfully.

**Why not `Any` elsewhere.** Alignment and divergence read every other field. `Any` there would defeat type checking on the hottest paths.

---

## 4. EventType Enum

```python
class EventType(StrEnum):
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
```

| Value | Purpose |
|---|---|
| `RUN_START` | Marks the beginning of an agent run; carries the initial user input and run-level metadata. Exactly one per trace, always at `sequence_index=0`. |
| `RUN_END` | Marks normal completion; carries the final output, total token usage, and total cost. Absent on crashed runs (which end in `ERROR`). |
| `LLM_CALL` | A single completion request to a language model; the most common divergence site and the densest payload. |
| `TOOL_CALL` | Invocation of an in-process tool/function the agent exposed (search, calculator, custom callables). |
| `MCP_CALL` | Invocation of a tool over the Model Context Protocol; distinguished from `TOOL_CALL` because the wire surface and failure modes differ. |
| `RETRIEVAL` | A vector/keyword/hybrid search returning ranked documents; payload captures query and retrieved IDs for alignment. |
| `STATE_TRANSITION` | A change in agent state machine / graph node (LangGraph node entry, agent role switch); marks structural progress. |
| `BRANCH_DECISION` | An explicit conditional in agent control flow (router decisions, "should I use tool X?"); captured for divergence on logic, not just output. |
| `ERROR` | An exception or failure within the run; may be terminal or recovered. Severity rules in §5.4 of the production spec promote unhandled errors to CRITICAL. |
| `HUMAN_INPUT` | A point at which the agent paused for human input (HITL approval, clarification). Treated as deterministic input on replay. |

These ten values are closed for V1. Adding a new event type is a schema-version bump.

---

## 5. Payload Types

Each `EventType` maps to exactly one payload dataclass. All payloads are `frozen=True, slots=True`.

```python
@dataclass(frozen=True, slots=True)
class RunStartPayload:
    user_input: str  # The initial prompt or task description.
    agent_name: Optional[str]  # User-supplied identifier for the agent under test.
    agent_version: Optional[str]  # User-supplied version string.
    run_metadata: JsonObject  # Free-form metadata; opaque escape hatch #2.


@dataclass(frozen=True, slots=True)
class RunEndPayload:
    final_output: str  # The agent's final answer/result.
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    success: bool  # False if the run completed but failed acceptance.


@dataclass(frozen=True, slots=True)
class LLMCallPayload:
    model: str  # e.g. "gpt-4o-mini". Used in semantic-match tier.
    provider: str  # "openai", "anthropic", "google", etc.
    messages: list[
        JsonObject
    ]  # OpenAI-style messages. Shape stable across providers post-normalization.
    response_text: str
    response_tool_calls: list[JsonObject]  # OpenAI tool-call shape, post-normalization.
    input_tokens: int
    output_tokens: int
    cost_usd: float
    temperature: float
    seed: Optional[int]  # Critical for determinism-mode replay.


@dataclass(frozen=True, slots=True)
class ToolCallPayload:
    tool_name: str
    arguments: JsonObject  # Normalized JSON-serializable args.
    result: Optional[str]  # Stringified result; None if the call raised.
    error: Optional[str]  # Exception summary; None on success.


@dataclass(frozen=True, slots=True)
class MCPCallPayload:
    server_name: str
    tool_name: str
    arguments: JsonObject
    result: Optional[str]
    error: Optional[str]
    protocol_version: Optional[
        str
    ]  # MCP version; included because wire compatibility matters for replay.


@dataclass(frozen=True, slots=True)
class RetrievalPayload:
    query: str
    retriever: str  # e.g. "pinecone:prod-index-v3", "bm25:docs".
    top_k: int
    document_ids: list[str]  # The IDs returned, in rank order. Used for set-diff in divergence.
    scores: list[float]  # Aligned with document_ids; empty if retriever doesn't expose scores.


@dataclass(frozen=True, slots=True)
class StateTransitionPayload:
    from_state: Optional[str]  # None on initial transition.
    to_state: str
    trigger: Optional[str]  # What caused the transition (event name, condition).


@dataclass(frozen=True, slots=True)
class BranchDecisionPayload:
    branch_name: str  # Identifier for the decision point.
    chosen_branch: str  # The branch label that was taken.
    available_branches: list[
        str
    ]  # All branches considered; lets divergence flag "could have but didn't".
    reasoning: Optional[str]  # Model-emitted rationale, if captured.


@dataclass(frozen=True, slots=True)
class ErrorPayload:
    error_type: str  # Exception class name.
    message: str
    stack_trace: Optional[str]
    recovered: bool  # True if the run continued past this error.


@dataclass(frozen=True, slots=True)
class HumanInputPayload:
    prompt_shown: str  # What the human was asked.
    response: str  # What the human entered.
    responder_id: Optional[str]  # Optional identity tag for multi-reviewer setups.


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
```

The `messages`, `response_tool_calls`, `arguments`, and `run_metadata` fields are typed via `JsonObject` / `list[JsonObject]` rather than nested dataclasses. Deliberate: provider-message shapes are already a de facto standard (OpenAI), and a TraceBisect-specific nested type would add a second normalization layer with no analysis benefit. Divergence treats these as JSON values via structural diff.

---

## 6. JSONL Serialization Format

Per production-spec §10, V1 stores traces as append-only JSONL with extension `.tbtrace`. The format is line-oriented and streamable — a tail can be parsed before a run completes.

**Layout:**
1. **Line 1 — header.** A single JSON object with `schema_version`, `trace_id`, `created_at`, `source_convention`. No event shape on this line.
2. **Lines 2…N — events.** One serialized `Event` per line, in `sequence_index` order.

Datetimes serialize as ISO 8601 with `Z` suffix. Enums serialize as their string value. The reader dispatches on `type` to pick the right payload dataclass.

**Example trace (four-event run — RUN_START → LLM_CALL requesting a tool → TOOL_CALL executing it → RUN_END):**

```jsonl
{"schema_version": 1, "trace_id": "trc_01HG...", "created_at": "2026-05-11T06:52:01Z", "source_convention": "native"}
{"id": "evt_001", "parent_id": null, "sequence_index": 0, "type": "RUN_START", "semantic_name": "run", "timestamp": "2026-05-11T06:52:01Z", "duration_ms": null, "payload": {"user_input": "find active users", "agent_name": "support-bot", "agent_version": "1.4.0", "run_metadata": {}}, "source_format": "native", "source_event_id": "evt_001", "model_version": null, "prompt_version": null, "code_sha": "a30ecca", "sampling_params": null}
{"id": "evt_002", "parent_id": "evt_001", "sequence_index": 1, "type": "LLM_CALL", "semantic_name": "gpt-4o-mini", "timestamp": "2026-05-11T06:52:01Z", "duration_ms": 412.3, "payload": {"model": "gpt-4o-mini", "provider": "openai", "messages": [{"role": "user", "content": "find active users"}], "response_text": "", "response_tool_calls": [{"name": "search_database", "arguments": {"query": "users WHERE active = true"}}], "input_tokens": 84, "output_tokens": 22, "cost_usd": 0.00021, "temperature": 0.0, "seed": 42}, "source_format": "native", "source_event_id": "evt_002", "model_version": "gpt-4o-2024-08-06", "prompt_version": "v3", "code_sha": "a30ecca", "sampling_params": {"temperature": 0.0, "seed": 42}}
{"id": "evt_003", "parent_id": "evt_001", "sequence_index": 2, "type": "TOOL_CALL", "semantic_name": "search_database", "timestamp": "2026-05-11T06:52:01Z", "duration_ms": 18.7, "payload": {"tool_name": "search_database", "arguments": {"query": "users WHERE active = true"}, "result": "[{\"id\": 1, \"name\": \"alice\"}, ...]", "error": null}, "source_format": "native", "source_event_id": "evt_003", "model_version": null, "prompt_version": null, "code_sha": "a30ecca", "sampling_params": null}
{"id": "evt_004", "parent_id": "evt_001", "sequence_index": 3, "type": "RUN_END", "semantic_name": "run", "timestamp": "2026-05-11T06:52:02Z", "duration_ms": null, "payload": {"final_output": "found 12 active users", "total_input_tokens": 84, "total_output_tokens": 22, "total_cost_usd": 0.00021, "success": true}, "source_format": "native", "source_event_id": "evt_004", "model_version": null, "prompt_version": null, "code_sha": "a30ecca", "sampling_params": null}
```

Why JSONL: streamable, append-only, line-diffable, inspectable with `cat`/`jq`, no binary lock-in. Repeated field names per line are acceptable at V1 scale (single-run files, hundreds of events typical).

---

## 7. Schema Versioning

`SCHEMA_VERSION` is a single module-level integer. V1 ships at `SCHEMA_VERSION = 1`.

**Migration model.** When the loader sees `schema_version != SCHEMA_VERSION`, it dispatches into a chain of `migrate_N_to_N+1` functions. Each migration takes a parsed dict and returns a parsed dict shaped for the next version. Only after the chain runs does the loader construct dataclasses — keeping migrations free of frozen-dataclass constraints.

**Version bump required for:** adding/removing/renaming an `EventType` value, removing/renaming any field, changing a field's type, adding a required field with no safe default.

**No bump for:** adding an optional field with a safe default, new keys inside `sampling_params` or `run_metadata` (opaque dicts), or new `source_convention` values older readers can treat as `"unknown"` (confirm during implementation).

V1 ships zero migrations. The first will be written when V1.1 introduces version 2; designing the framework now would be premature.

---

## 8. Source Format Mapping

Adapters at ingest map source spans onto `EventType` values. The mapping for common OTel GenAI and OpenInference span kinds:

| Source span | Convention | Maps to `EventType` | Notes |
|---|---|---|---|
| `gen_ai.chat` / `gen_ai.completion` | GenAI | `LLM_CALL` | Most common. Provider read from `gen_ai.system`. |
| `gen_ai.embeddings` | GenAI | (not modeled in V1) | Out of scope; embedding calls aren't a divergence axis V1 surfaces. |
| `openinference.LLM` | OpenInference | `LLM_CALL` | Messages read from `llm.input_messages` / `llm.output_messages`. |
| `openinference.TOOL` | OpenInference | `TOOL_CALL` | Distinguish from MCP by absence of MCP-specific attrs. |
| `openinference.RETRIEVER` | OpenInference | `RETRIEVAL` | `retrieval.documents` → `document_ids`. |
| `openinference.AGENT` | OpenInference | `RUN_START` / `RUN_END` | Outer agent span splits into bookends. |
| `openinference.CHAIN` | OpenInference | `STATE_TRANSITION` | Treated as a structural marker, not an LLM call. |
| `mcp.tool.call` (proposed) | OTel-MCP | `MCP_CALL` | Convention is still emerging; adapter must be tolerant. |
| Span with `exception` event | Any | `ERROR` | Recovered status inferred from parent span outcome. |
| Native `tracebisect.branch` | Native | `BRANCH_DECISION` | Only the native recorder emits these today. |
| Native `tracebisect.human_input` | Native | `HUMAN_INPUT` | HITL is native-only in V1. |

**Ingest rule for non-semantic spans.** A non-semantic span (no recognizable GenAI / OpenInference / native attributes) may be elided **only if** its children are reparented to its parent so the canonical tree remains valid. If reparenting would break tree invariants — or if dropping the span would lose structural information — the adapter must instead map the span to the nearest structural event (typically `STATE_TRANSITION`) and emit a warning in the ingest report. Silent drops that orphan children are not allowed.

**`source_convention="mixed"` covers two cases:**

1. **Different spans use different conventions.** Some spans in the trace are tagged with `gen_ai.*`, others with `openinference.*`. The adapter handles each span on its own terms; no reconciliation is needed.
2. **The same span carries both conventions.** A single span has both `gen_ai.*` and `openinference.*` attributes. In this case OpenInference is authoritative for **event-type classification** (the OpenInference span kind determines the `EventType`), and GenAI fills in **missing model/token/cost fields** (e.g. `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, and any cost attribute the GenAI convention defines). OpenInference values win on conflict for classification fields; GenAI values win only where OpenInference left a field unset.

---

## 9. Open Questions

Intentionally unresolved at design time; resolve during V1 implementation.

1. **Input/output hashes on events** (production-spec §5.1 open question). Adding `input_hash` / `output_hash: Optional[str]` to `Event` would let alignment skip structural comparison on identical payloads. Cost: ~32 bytes per event plus hashing on ingest. Tentative: yes, but defer until alignment profiling shows the win. Decision point: end of week 3 (alignment milestone).

2. **Canonical message normalization.** `LLMCallPayload.messages` is typed `list[JsonObject]` and assumed OpenAI-shaped. Anthropic and Google shapes differ (system prompts, content blocks, tool-result format). Normalize aggressively, or preserve provider shape? Probably normalize, but spell out the target shape before ingesting Anthropic-native traces.

3. **`source_convention="mixed"` semantics.** What does alignment do when one trace is `genai` and the other is `mixed`? Likely nothing special — alignment operates on canonical events, not the provenance tag. Confirm with a real mixed trace.

4. **Token cost authority.** `cost_usd` is recorded by the source. Trust it, or recompute from a price table? V1 trusts the source; V1.1 may add a re-cost step.

5. **`STATE_TRANSITION` granularity.** LangGraph-style frameworks emit transitions frequently. Collapse no-op transitions (`from_state == to_state`)? Alignment cost vs. fidelity tradeoff.

6. **Tree invariants enforcement.** Should `Trace.__post_init__` walk `events` for `parent_id` consistency, or is that the loader's job? Likely: separate `validate()` called by the loader and tests, keeping construction cheap.

7. **`source_format` future values.** V1 locks `Literal["otel", "native"]`. Adding LangSmith/LangFuse-native ingest in V1.1 widens this literal — a schema bump. Confirm V1.1 plan accepts that.

8. **JSONL stream tail behavior.** If a trace file is truncated (process killed mid-run), the loader has no policy. Options: (a) return a partial trace flagged `complete=False`, (b) raise. Affects how `bisect` handles in-flight runs.

---

> End of design document. Next deliverable: `spec/alignment-algorithm.md`.
