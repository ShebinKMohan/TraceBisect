# Divergence Taxonomy — Design Document

> Status: Design (Phase 1 V1)
> Companion to: `spec/production-spec.md` §4.5 (export-pytest contract), §5.3 (taxonomy), §5.4 (severity)
> Builds on: `spec/canonical-trace-schema.md` §2–§5; `spec/alignment-algorithm.md` (consumes its `Match` output)
> Implementation target: `src/tracebisect/diff.py` (not yet written)

---

## 1. Definition

A **divergence** is a typed difference detected during alignment — produced *after* the alignment pass of `spec/alignment-algorithm.md` has paired baseline and candidate events, and *before* the rendering layer turns the result into the money-shot output (production-spec §11). It is the smallest unit of "something changed" that TraceBisect commits to in its public surface.

Divergence detection is downstream of alignment. `detect_divergences(matches, baseline, candidate) -> list[Divergence]` consumes the alignment's `list[Match]` and the two `Trace`s. Detectors never re-pair — they inspect events alignment already paired (for "changed-X" types) or events alignment marked `DELETION` / `INSERTION` (for missing / extra types). One pass over the match list; each detector fires on its applicable subset.

Two boundaries are load-bearing:

- **Divergence types** (§3) are emitted by `detect_divergences`. Each one is a structural fact about the trace pair.
- **Assertion dimensions** (§6) are the user-facing knobs exposed via `tracebisect.testing.assert_aligned(..., assertions=[...])`. Some are backed by divergence types (`tool_args` → `changed_tool_args`); others are direct checks that bypass `detect_divergences` (`final_output`, `error_absence`).

---

## 2. The `Divergence` Dataclass

```python
# V1 implementation type — exactly the five divergence kinds that
# detect_divergences() may emit and that assert_aligned() may react to.
# The §3 prose discusses the full nine-type taxonomy; the V1 *type*
# narrows that to what V1 actually ships.
DivergenceType: TypeAlias = Literal[
    "missing_event",
    "extra_event",
    "changed_tool_args",
    "branch_changed",
    "cost_regression",
]

Severity: TypeAlias = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]


@dataclass(frozen=True, slots=True)
class Divergence:
    type: DivergenceType
    severity: Severity
    baseline_event: (
        Event | None
    )  # None for extra_event and trace-level cost_regression; set otherwise.
    candidate_event: (
        Event | None
    )  # None for missing_event and trace-level cost_regression; set otherwise.
    description: str  # One-line human summary (used in rendering)
    expected: JsonValue  # Baseline-side value; shape varies by `type` (§3)
    actual: JsonValue  # Candidate-side value; shape varies by `type` (§3)
    impact: "ImpactAnalysis"  # See §4
    source_metadata: JsonObject  # Provenance: detector name, alignment kind, etc.
```

`JsonValue` and `JsonObject` are as defined in `spec/canonical-trace-schema.md` §2. `expected` / `actual` are JSON values so the divergence is trivially serializable; their concrete shape per `type` is fixed by the detector (§3).

`source_metadata` carries detector-level provenance — at minimum `{"detector": "<name>", "alignment_kind": "<MatchKind>"}`. It is the diagnostic escape hatch and is not interpreted by `assert_aligned`. One exception for rendering: Studio reads `tool_changed`, `baseline_tool`, and `candidate_tool` on `changed_tool_args` (§3.5) to describe a replaced tool, because a swap's `{"tool_name", "arguments"}` expected / actual shape is otherwise indistinguishable from argument drift on a generic tool-dispatch call.

**Forward compatibility.** V2 will widen `DivergenceType` to include `changed_input`, `changed_output`, `retrieval_changed`, and `state_changed` once those detectors ship (§3 describes them). V1 baselines never contain those values, so a V2 reader handling a V1 trace will only ever observe the five values above — no unknown-tag handling needed.

---

## 3. The Nine Divergence Types

Each subsection gives: **detector logic**, **expected / actual extraction**, **default severity** (per production-spec §5.4), **JSON representation**, and **pytest assertion template** (V1 types; V2 types note deferral). All detectors iterate the `list[Match]` from alignment; no detector revisits the raw `events` list except to follow `Event.parent_id` if needed for impact analysis.

### 3.1 `missing_event` — V1, default `HIGH` (CRITICAL when type is `RUN_END`)

- **Detector:** for each `Match` with `kind == "DELETION"`, emit one `missing_event` divergence. The baseline-side subtree of that DELETION yields one divergence per event (one per `Match`), not one collapsed entry — consistent with the subtree-expansion rule in `spec/alignment-algorithm.md` §1.
- **Expected:** `{"event_type": <EventType>, "semantic_name": <str>, "sequence_index": <int>}` extracted from `baseline_event`.
- **Actual:** `null`.
- **Severity override:** if `baseline_event.type == "RUN_END"`, severity escalates to `CRITICAL` (the run didn't complete).
- **JSON:**
  ```json
  {"type": "missing_event", "severity": "HIGH",
   "baseline_event": "evt_007", "candidate_event": null,
   "description": "TOOL_CALL search_database (baseline #7) absent in candidate",
   "expected": {"event_type": "TOOL_CALL", "semantic_name": "search_database", "sequence_index": 7},
   "actual": null, "impact": {...}, "source_metadata": {"detector": "missing_event"}}
  ```
- **Pytest assertion (V1):** surfaced by `tracebisect diff`. `assert_aligned` does **not** fail on every `missing_event`; it fails on one only when the divergence violates a selected assertion dimension or breaks root / structural validity required for the comparison. Examples: `assertions=["final_output"]` fails if `RUN_END` is missing in the candidate; `assertions=["tool_args"]` fails when the specific `TOOL_CALL` it would gate on is absent.

### 3.2 `extra_event` — V1, default `HIGH`

- **Detector:** for each `Match` with `kind == "INSERTION"`, emit one `extra_event` divergence.
- **Expected:** `null`.
- **Actual:** `{"event_type": <EventType>, "semantic_name": <str>, "sequence_index": <int>}` from `candidate_event`.
- **Severity override:** if `candidate_event.type == "ERROR"` and there is no peer `ERROR` in baseline, severity escalates to `CRITICAL` (new error introduced).
- **JSON:** mirror of §3.1 with sides swapped.
- **Pytest assertion (V1):** surfaced by `tracebisect diff`. `assert_aligned` fails on an `extra_event` only when the divergence violates a selected assertion dimension. The targeted example: `assertions=["error_absence"]` fails on an unmatched candidate `ERROR` event. Other extra-event categories (an inserted `LLM_CALL`, an inserted `STATE_TRANSITION`) do not by themselves fail an exported test — they are observable via `tracebisect diff` but require an explicit assertion dimension to gate CI.

### 3.3 `changed_input` — V2

- **Detector:** for each `Match` with a paired kind (`ID_MATCH`, `STRUCTURAL_MATCH`, `POSITIONAL_MATCH`) whose payload has an "input" component (`LLMCallPayload.messages`, `ToolCallPayload.arguments`, `RetrievalPayload.query`), structural-diff the canonicalized input JSON. Emit one divergence per differing event.
- **Expected / Actual:** the canonicalized input JSON from baseline / candidate respectively.
- **Default severity:** varies — MEDIUM if the corresponding `changed_output` is semantically equivalent; HIGH otherwise.
- **JSON / Pytest template:** **Deferred to V2.** The V2 design must resolve "semantically equivalent output" before this detector can ship; in V1 the broader `changed_tool_args` covers the most actionable subset (§3.5).

### 3.4 `changed_output` — V2

- **Detector:** mirror of §3.3 on the output-bearing fields (`LLMCallPayload.response_text` and `response_tool_calls`, `ToolCallPayload.result`, `RetrievalPayload.document_ids`, etc.).
- **JSON / Pytest template:** **Deferred to V2.** V1 covers the high-signal case (`final_output` direct check; see §6) without needing per-event output diffs.

### 3.5 `changed_tool_args` — V1, default `CRITICAL`

- **Detector:** for each `Match` of a paired kind where `baseline_event.type == "TOOL_CALL"` (or `MCP_CALL`), compare canonicalized `arguments`. The canonicalization is the same JSON canonical form used by alignment Tier 1 (`spec/alignment-algorithm.md` §3).
- **Tool identity (checked first):** alignment can still pair two *different* tools — Tier 3 pairs any same-type siblings, and a shared raw ID pairs them when neither tool appears elsewhere among the siblings (`spec/alignment-algorithm.md` §3) — so the detector compares the called tool before the arguments. Identity is `tool_name` for `TOOL_CALL` and `(server_name, tool_name)` for `MCP_CALL`. Each name has Unicode format characters (category `Cf`, e.g. zero-width spaces) removed, is then NFC-normalized, and trimmed; comparison is otherwise exact and case-sensitive, so Tier 2's edit-distance tolerance (`-v1` → `-v2`, case changes) deliberately does **not** carry over. An empty name, or a placeholder name (`unknown` for an MCP server, `unknown_tool` for a tool), matches any value; the OTel importer emits `unknown` when `mcp.server.name` is absent and `unknown_tool` when a tool span has neither `tool.name` nor a span name. On a mismatch the detector emits `changed_tool_args` even when the arguments are identical: `expected` / `actual` carry `{"tool_name": …, "arguments": …}` (plus `server_name` for `MCP_CALL`), the description reads `TOOL_CALL <baseline_tool> replaced by <candidate_tool>` using the normalized names (MCP labels are `server/tool`), and `source_metadata` adds `"tool_changed": true`, `"baseline_tool"`, and `"candidate_tool"`. Same-tool argument drift keeps the arguments-only shape below. Known limit: when the OTel importer falls back to a span name because `tool.name` is absent on only one side (for example `search_database` versus `Tool: search_database`), the fallback is compared as a real name and reported as a swap.
- **Expected:** baseline `arguments` (a `JsonObject`).
- **Actual:** candidate `arguments` (a `JsonObject`).
- **Severity override:** stays CRITICAL even if `result` did not change — the args drift is itself the regression signal users want to gate on.
- **JSON:**
  ```json
  {"type": "changed_tool_args", "severity": "CRITICAL",
   "baseline_event": "evt_007", "candidate_event": "evt_007p",
   "description": "TOOL_CALL search_database arguments differ",
   "expected": {"query": "users WHERE active = true"},
   "actual":   {"query": "users WHERE active = true AND deleted = false"},
   "impact": {...}, "source_metadata": {"detector": "changed_tool_args", "alignment_kind": "ID_MATCH"}}
  ```
- **Pytest assertion (V1):**
  ```python
  from tracebisect.testing import capture_trace, load_baseline, assert_aligned


  def test_no_tool_arg_regression():
      assert_aligned(
          baseline=load_baseline(BASELINE_PATH),
          candidate=capture_trace(SCENARIO_CMD),
          assertions=["tool_args"],
          mode="ci",
      )
  ```

### 3.6 `branch_changed` — V1, default `CRITICAL`



- **Detector:** for each `Match` of a paired kind where `baseline_event.type == "BRANCH_DECISION"`, compare `BranchDecisionPayload.chosen_branch`. Alignment's Tier 1 stable ID for `BRANCH_DECISION` is `(branch_name,)` (`spec/alignment-algorithm.md` §3); the chosen branch is intentionally compared here, not at alignment time.
- **Expected:** baseline `chosen_branch`.
- **Actual:** candidate `chosen_branch`.
- **JSON:**
  ```json
  {"type": "branch_changed", "severity": "CRITICAL",
   "description": "BRANCH_DECISION refund_route took different branch",
   "expected": "approve", "actual": "deny", "impact": {...},
   "source_metadata": {"detector": "branch_changed", "branch_name": "refund_route"}}
  ```
- **Pytest assertion (V1):**
  ```python
  assert_aligned(
      baseline=baseline,
      candidate=candidate,
      assertions=["branch"],
      mode="ci",
  )
  ```

### 3.7 `retrieval_changed` — V2

- **Detector:** for each `Match` of a paired kind where `baseline_event.type == "RETRIEVAL"`, diff `RetrievalPayload.document_ids` (set-and-order diff).
- **JSON / Pytest template:** **Deferred to V2.** The `retrieval` assertion dimension is also V2 (production-spec §4.5).

### 3.8 `state_changed` — V2

- **Detector:** for each `Match` of a paired kind where `baseline_event.type == "STATE_TRANSITION"`, compare `StateTransitionPayload.to_state` (and optionally `from_state`).
- **JSON / Pytest template:** **Deferred to V2.**

### 3.9 `cost_regression` — V1, default `MEDIUM`

- **Detector:** compute `cost_delta_ratio = candidate_total_cost / max(baseline_total_cost, epsilon)`. Emit one trace-level `cost_regression` divergence whenever the ratio exceeds the user-supplied `cost_threshold` passed through `assert_aligned(cost_threshold=...)`. The exported-pytest default is `cost_threshold=1.5` (production-spec §4.5). The detector also runs per-`LLM_CALL` when both baseline and candidate carry per-event cost; per-event divergences inherit the same threshold.
- **Note — assertion default vs severity trigger are distinct.** The exported-test default `cost_threshold=1.5` controls *when the test fails*. Severity classification (production-spec §5.4) separately escalates a `cost_regression` to `MEDIUM` when the cost rises by more than 20 % (a separate threshold from the assertion default); that severity trigger affects *rendered prominence* in `tracebisect diff` output, not test pass/fail. Do not collapse the two: a regression at ratio 1.30 is reported MEDIUM by `tracebisect diff` but does **not** fail an exported test running the default `cost_threshold=1.5`.
- **Expected:** baseline cost (`float`, USD).
- **Actual:** candidate cost (`float`, USD).
- **`baseline_event` / `candidate_event`:** `None` for the trace-level emission; set to the matched `LLM_CALL` pair for per-event emissions.
- **JSON:**
  ```json
  {"type": "cost_regression", "severity": "MEDIUM",
   "baseline_event": null, "candidate_event": null,
   "description": "Total cost rose 1.62× (test threshold 1.5)",
   "expected": 0.00021, "actual": 0.00034,
   "impact": {"tokens_delta": 41, "cost_delta_ratio": 1.62, ...},
   "source_metadata": {"detector": "cost_regression", "scope": "trace"}}
  ```
- **Pytest assertion (V1):**
  ```python
  assert_aligned(
      baseline=baseline,
      candidate=candidate,
      assertions=["cost"],
      cost_threshold=1.5,
      mode="ci",
  )
  ```

---

## 4. The `ImpactAnalysis` Structure

Every `Divergence` carries an `ImpactAnalysis`. Per production-spec §5.3, impact captures *structurally observable* downstream effects — what changed in the trace after the divergence point — and nothing else.

```python
@dataclass(frozen=True, slots=True)
class ImpactAnalysis:
    tokens_delta: int  # candidate − baseline, summed over events
    # at or after the divergence point.
    cost_delta_ratio: float  # candidate / max(baseline, epsilon),
    # over the same forward window.
    final_output_changed: bool  # Verbatim string equality on RUN_END
    # payload.final_output — no semantic check.
    errors_introduced: list[JsonObject]  # One entry per ERROR event in candidate
    # that has no peer ERROR in baseline,
    # restricted to events at or after the
    # divergence point. Each entry shape:
    # {"error_type": str, "message": str,
    #  "sequence_index": int}.
    # Empty list when no new errors.
    affected_event_count: int  # Number of subsequent events whose
    # alignment kind is not ID_MATCH.
```

These five fields are the V1 closed set. **Domain-specific impact is explicitly out of scope:** TraceBisect cannot say "12 fewer users returned" — that requires understanding what `users WHERE active = true` *means*, which the structural layer never does. V2 will add custom impact-analysis hooks for domain logic; until then, rendering translates the five fields into phrasing like "final answer changed, cost rose 1.34×, 4 downstream events affected."

`final_output_changed` is a boolean here, not a divergence type. The `final_output` assertion dimension (§6) is what surfaces it as a test failure.

---

## 5. V1 Implementation Set

V1 ships **five of nine** divergence types. V2 ships the remaining four.

| Type | Ships in | Why this split |
|---|---|---|
| `missing_event` | **V1** | Direct read of alignment's DELETION output. |
| `extra_event` | **V1** | Mirror of the above. |
| `changed_tool_args` | **V1** | Highest-signal regression for agent code; opt-in via `assertions=["tool_args"]`. |
| `branch_changed` | **V1** | High-signal, cheap: single payload field comparison. |
| `cost_regression` | **V1** | Backs `assertions=["cost"]`; reads `ImpactAnalysis.cost_delta_ratio`. |
| `changed_input` | V2 | Needs the "semantically equivalent output" judgment to set severity safely. |
| `changed_output` | V2 | Same. V1 substitutes the `final_output` direct check (§6) for the highest-value subset. |
| `retrieval_changed` | V2 | The `retrieval` assertion dimension is V2 per production-spec §4.5. |
| `state_changed` | V2 | Pending STATE_TRANSITION collapse-policy (`spec/canonical-trace-schema.md` §9 OQ #5). |

The V1 set is exactly what the V1 `DivergenceType` literal admits (§2) and exactly what `detect_divergences()` emits. V2 widens `DivergenceType` to add the deferred four; V1 baselines never contain those values, so they read cleanly under either version.

---

## 6. Pytest Assertion Templates (V1 Surface)

The export-pytest generator emits exactly one `assert_aligned(...)` call per test, with a list of opt-in `assertions=[...]`. The locked V1 signature is **keyword-only**:

```python
def assert_aligned(
    *,
    baseline: Trace,
    candidate: Trace,
    assertions: list[str],
    cost_threshold: float = 1.5,
    mode: Literal["permissive", "strict", "ci"] = "ci",
) -> None: ...
```

Positional calls raise `TypeError` at runtime; every template below passes arguments by name. Five V1 assertion dimensions are available:

| Assertion | Backed by | Direct or detector? |
|---|---|---|
| `"tool_args"` | `changed_tool_args` divergence | Detector |
| `"final_output"` | Verbatim string equality on RUN_END payload | **Direct check** (not a divergence type) |
| `"cost"` | `cost_regression` divergence + `cost_threshold` | Detector |
| `"branch"` | `branch_changed` divergence | Detector |
| `"error_absence"` | Scan candidate for ERROR events with no peer in baseline | **Direct check** (not a divergence type) |

`final_output` and `error_absence` are deliberately direct checks: simple, high-signal, and want to fail loudly even when no `Divergence` is emitted.

The exact template the export-pytest generator emits, locked by production-spec §4.5:

```python
import subprocess
from tracebisect.testing import (
    capture_trace,
    load_baseline,
    assert_aligned,
)

BASELINE_PATH = "tests/fixtures/baseline_refund_042.tbtrace"
SCENARIO_CMD = ["python", "examples/refund_agent.py", "--case", "refund_042"]


def test_refund_agent_no_regression():
    """Generated from divergence at event 7 (changed_tool_args, CRITICAL)."""
    baseline = load_baseline(BASELINE_PATH)
    candidate = capture_trace(SCENARIO_CMD)

    assert_aligned(
        baseline=baseline,
        candidate=candidate,
        assertions=["tool_args", "final_output", "cost"],
        cost_threshold=1.5,
        mode="ci",
    )
```

Per-dimension minimal forms (each is a valid standalone test body; all calls use keyword arguments per the locked signature above):

```python
# tool_args only
assert_aligned(
    baseline=baseline,
    candidate=candidate,
    assertions=["tool_args"],
    mode="ci",
)

# final_output only — direct check, no divergence detection needed
assert_aligned(
    baseline=baseline,
    candidate=candidate,
    assertions=["final_output"],
    mode="ci",
)

# cost only
assert_aligned(
    baseline=baseline,
    candidate=candidate,
    assertions=["cost"],
    cost_threshold=1.5,
    mode="ci",
)

# branch only
assert_aligned(
    baseline=baseline,
    candidate=candidate,
    assertions=["branch"],
    mode="ci",
)

# error_absence only — direct check
assert_aligned(
    baseline=baseline,
    candidate=candidate,
    assertions=["error_absence"],
    mode="ci",
)
```

All templates use only the public `tracebisect.testing` surface (`capture_trace`, `load_baseline`, `assert_aligned`). The generator never imports `tracebisect.diff` — keeping the public API the only contract generated tests depend on.

`retrieval` is a V2 assertion dimension and is unavailable in V1 (production-spec §4.5).

---

> End of design document. Next deliverable: `spec/severity-rules.md`.
