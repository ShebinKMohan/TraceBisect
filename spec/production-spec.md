# TraceBisect — Production Specification v1.3

**Status**: Implementation-ready (locked, re-synced with prompt pack v1.4)
**Working name**: TraceBisect
**Tagline**: *Git bisect for AI agent traces*
**License (planned)**: MIT
**Target audience**: AI engineers, ML platform teams, anyone running LLM agents in production

**Changelog v1.3** (re-sync with implementation prompts — no scope changes, only consistency fixes):
- **Section 3 ingestion claim corrected**: V1 ingests OTel/OpenInference and native `.tbtrace`; Langfuse/Phoenix/MLflow source-specific importers are V2. The competitive table now reflects this.
- **Section 4.2 record CLI updated** to the locked signature: `tracebisect record --output <path> [--side-effects stub|live] [--allow-live] -- <command>`. `TRACEBISECT_OUTPUT` environment variable propagates the output path to the subprocess.
- **Section 4.2 assertion dimensions fixed**: `retrieval` removed from V1 (it's V2). V1 dimensions are now `tool_args, final_output, cost, branch, error_absence`.
- **Section 4.5 export-pytest contract clarified**: `--scenario` is REQUIRED for V1 export-pytest. Static trace-vs-trace mode is recategorized as a debugging aid available via the `tracebisect diff` command (which already does this), not via `export-pytest`. Live capture is the only V1 export-pytest mode.
- **Section 5.1 schema**: added `Trace.source_convention: Literal["native", "genai", "openinference", "mixed", "unknown"]`. Also corrected the Event `source_format` field to enumerate only V1 values: `"otel" | "native"`.
- **Section 5.3 wording fixed**: "V1 ships with nine divergence types" → "The full taxonomy has nine divergence types; V1 implements five."
- **Section 7 side-effect honesty**: explicit statement that stub/live applies only to SDK calls (via `patch_sdks()`) and `@wrap_tool`-decorated functions; unwrapped external side effects remain the user's responsibility regardless of mode.
- **Section 12 demo story** export-pytest line updated to the locked CLI signature with `--scenario`.
- **Section 13 security posture**: features that are not implemented by the V1 prompt pack (PII redaction hooks, secret detection, `.tracebisectignore`, opt-in raw payload capture, cassette write warnings) moved from V1 commitments to "V1.1 / V2 roadmap" with explicit deferral reasoning. Local-first remains a V1 commitment because the architecture is local-first by construction.

**Changelog v1.2** (earlier — defined `export-pytest` semantics, see prior history)
**Changelog v1.1** (earlier — corrected determinism modes and other issues)

---

## 0. One-Page TL;DR

Your AI agent worked yesterday and fails today. Traditional observability tells you *what* happened in each run; it does not tell you *what changed between them*. TraceBisect closes that gap.

TraceBisect is a local-first command-line tool that ingests two agent execution traces, aligns them event-by-event, identifies the first meaningful behavioral divergence, renders a clear terminal diff, and exports a pytest regression test that prevents the same failure from returning.

V1 ingests OpenTelemetry/OpenInference traces and TraceBisect's own native `.tbtrace` JSONL format. V2 will add source-specific importers for Langfuse, Phoenix, and MLflow — though most users of those tools can already export to OTel and use the V1 path.

The four primitive operations are: **ingest, align, detect, export.** Everything else in the product is a refinement of those four.

The strategic positioning is deliberately narrow: TraceBisect is not an observability platform, not a generic recorder, not a dashboard, and not an agent governance tool. It is a comparison engine and a regression-test generator. It plugs into the tools that already exist; it does not compete with them.

V1 ships in approximately six weeks and proves the four primitives end-to-end. V2 introduces true cross-commit bisection — given a known-good and known-bad commit, run the agent at intermediate commits and binary-search to the breaking change. That capability is the long-term moat.

---

## 1. Problem Statement

### 1.1 The user pain

Engineers building production LLM agents repeatedly report the same five problems:

**Agents fail non-deterministically.** The same input produces different outputs across runs. Temperature settings, model version drift, sampling, retrieval order, and tool-call ordering all introduce variation. Standard debugging instincts break down because the failure cannot be reliably reproduced.

**Failures are expensive to reproduce.** Re-running a failed agent in a development environment hits paid LLM APIs, paid tools, production databases, and customer-facing systems. A single debugging session can cost dollars; debugging a flaky test suite can cost hundreds.

**Traces tell you what happened, not how to fix it.** Existing observability platforms (Langfuse, Phoenix, LangSmith, MLflow, Datadog LLM Observability) render the timeline of a single run beautifully. They do not tell you what changed between two runs. The engineer is left to scroll through two timelines side-by-side and spot the difference manually.

**Production failures don't become tests.** A bug fixed today recurs in a different form three weeks later because there's no codified regression check. The institutional memory of "the agent got into an infinite loop when the user said X" lives in Slack threads, not in CI.

**Agent systems cross too many boundaries.** A single agent run touches LLMs, vector databases, tool APIs, MCP servers, file systems, message queues, and human approval steps. Any one of these can be the source of a behavioral change. Without a way to pinpoint *which* boundary caused the divergence, debugging is guesswork.

### 1.2 Market evidence

The need is widely acknowledged in current industry literature: LangChain's State of Agent Engineering report calls observability and evals the central production-readiness challenge; Deloitte's 2026 State of AI flags governance for autonomous agents as a major maturity gap; Microsoft's Cyber Pulse and the Cloud Security Alliance both identify reproducibility and audit as enterprise blockers for agent rollout.

What's missing in the existing tooling response is the specific primitive that turns these reports into actionable engineering: a way to compare two agent runs and produce a regression test from the difference. Every observability vendor records traces. Almost none compare them in a way that engineers can act on.

### 1.3 Why this gap exists

Two reasons. First, observability platforms are optimized for *single-run debugging in dashboards*; cross-run comparison is a graph problem on tree-structured data that doesn't fit the dashboard paradigm. Second, the market has been chasing the bigger prizes — full observability platforms, agent governance suites — and the small, sharp tool that does one thing well has been overlooked.

TraceBisect addresses exactly this gap.

---

## 2. Product Definition

### 2.1 What it is

TraceBisect is a local-first, framework-agnostic command-line tool that performs four operations on agent execution traces:

1. **Ingest** traces from multiple source formats into a canonical internal representation
2. **Align** events across two traces using a tiered matching strategy
3. **Detect** the first meaningful behavioral divergence and classify it by type and severity
4. **Export** the divergence as either a human-readable terminal diff or an executable pytest regression test

### 2.2 What it is not

The non-goals are as important as the goals and are listed explicitly to prevent scope creep:

- **Not an observability platform.** It does not replace Langfuse, Phoenix, LangSmith, or Datadog. It consumes their output.
- **Not a recorder.** A small built-in recorder exists for demos and onboarding, but the core product is comparison, not capture.
- **Not a dashboard.** The interface is a CLI and an HTML report (V2). There is no hosted UI.
- **Not a SaaS.** No cloud component, no telemetry to a server, no account required. Everything runs locally.
- **Not an evaluation platform.** It does not evaluate output quality with LLM-as-judge or compute metrics like BLEU, ROUGE, etc. It compares execution structure, not output goodness.
- **Not an agent framework.** It does not orchestrate, route, or govern agents. It observes their traces.

### 2.3 The one-liner

> *Your agent worked yesterday and fails today. TraceBisect compares the two runs, finds the first behavioral change, and exports a regression test so it doesn't happen again.*

### 2.4 Mental model: git bisect

The closest analog in existing developer tooling is `git bisect`. When a regression appears in a codebase, `git bisect` binary-searches the commit history to identify the breaking commit. TraceBisect does the analogous operation for agent runs: given two trace executions, it identifies the first event where behavior diverged, in a way that maps to a fixable code change.

This analogy is intentional and load-bearing. It signals to the target audience (Python developers familiar with git) the exact mental model they should bring. It positions the tool's algorithmic depth as the headline feature, not an afterthought.

---

## 3. Competitive Landscape

The space is more crowded than it appears at first glance. The position must be precise.

### 3.1 Adjacent products and their positioning

| Product | Category | What it does | TraceBisect's relationship |
|---|---|---|---|
| **Langfuse** | OSS LLM observability platform | Traces, prompts, evals, datasets, experiments | Integration target — Langfuse users export to OTel and TraceBisect ingests that. Source-specific Langfuse importer is V2.
| **Phoenix (Arize)** | OTel-native LLM observability | OpenInference-based tracing, evaluation | Integration target — same role |
| **MLflow Tracing** | OTel-compatible LLM observability | Traces across all major frameworks | Integration target — same role |
| **LangSmith** | LangChain-native dashboard | Trace viewing, evals, prompt iteration | Integration target — uses LangChain framework hooks |
| **Datadog LLM Observability** | Enterprise APM with LLM extensions | Production monitoring | Out of scope — TraceBisect targets developers, not SREs |
| **agent-replay** (ManasVardhan, GitHub) | OSS recorder/replay/diff | JSONL traces, severity-tagged divergences | Closest direct overlap — TraceBisect differentiates on cross-source ingestion and bisection |
| **GhostTrace** (PyPI) | Agent decision recorder | JSON exports, phantom branch tracking | Adjacent — focuses on decision recording, not comparison |
| **AgentOps** | Hosted agent observability dashboard | Time-travel session replay | Adjacent — dashboard-shaped, not CLI/CI shaped |
| **TraceOps** (PyPI) | Record/replay for regression testing | Framework-agnostic, pytest-native | Closest functional overlap — TraceBisect differentiates on alignment algorithm and bisection |
| **traceAI** (future-agi) | OSS OTel-based tracing | Apache 2.0 framework | Integration target |

### 3.2 What makes TraceBisect different

Three concrete differentiators that, taken together, are not present in any single existing product:

**Cross-source trace ingestion via OTel.** Most existing tools record in their own format and only diff within that format. TraceBisect's V1 ingestion is OpenTelemetry/OpenInference + its own JSONL — and since Langfuse, Phoenix, MLflow, and most modern instrumentation libraries can export to OTel, the V1 path covers most users out of the box. V2 will add source-specific importers (Langfuse, Phoenix, MLflow native exports) for cases where OTel export isn't available or loses fidelity. The canonical schema and alignment algorithm are designed to make adding importers cheap.

**First-divergence algorithm with severity classification.** Two traces that differ in a thousand small ways are useless to debug. TraceBisect identifies the *first meaningful* divergence by walking the aligned event streams, classifying each difference by type and severity, and surfacing the earliest critical event. This is the algorithmic core and it is genuinely hard.

**Pytest export and CI integration.** The output is not a report; it is an executable test. The fix loop is: divergence found → test exported → committed → CI catches the regression next time. This is the production-engineering story that the dashboard-based competitors structurally cannot match.

**(V2) True cross-commit bisection.** Given a known-good commit, a known-bad commit, and a test scenario, TraceBisect runs the agent at intermediate commits and binary-searches to the breaking change. This is the feature that earns the name and that no competitor offers.

---

## 4. Core Workflow

### 4.1 The user journey

The intended day-to-day workflow has six steps:

1. A production or staging agent run fails. The trace is captured by an existing observability tool (Langfuse, Phoenix, OTel-instrumented agent, or TraceBisect's own recorder).
2. The engineer suspects a recent code, prompt, or model change is responsible. They run the same scenario against the suspect new version, producing a second trace.
3. The engineer invokes TraceBisect with both traces. TraceBisect ingests, normalizes, aligns, and analyzes.
4. TraceBisect renders a terminal diff showing the first meaningful divergence with full context (event type, expected vs actual values, downstream impact, severity).
5. The engineer fixes the underlying bug.
6. The engineer exports a pytest regression test from the failing trace and commits it. CI now blocks the regression from returning.

### 4.2 CLI surface

V1 ships with five subcommands:

```
tracebisect ingest <source> <output.tbtrace>
    Convert source trace into canonical .tbtrace format.
    V1 supports: OTel/OpenInference JSON, native .tbtrace passthrough.
    V2 will add: Langfuse exports, Phoenix exports, MLflow traces.
    (Most Langfuse/Phoenix/MLflow users can already export to OTel
    and use that path in V1.)

tracebisect record --output <output.tbtrace> [--side-effects stub|live] [--allow-live] -- <command>
    Run a command with the built-in recorder, output canonical trace.
    --output is REQUIRED. The parent process passes the path to the
    child via the TRACEBISECT_OUTPUT environment variable.
    --side-effects defaults to stub. live requires --allow-live.
    (Demo/onboarding only — production users use existing observability tools)

tracebisect diff <baseline.tbtrace> <candidate.tbtrace>
    Align two traces, detect divergences, render terminal diff.
    (Static comparison — useful for debugging an existing failure
    or comparing two pre-recorded traces. NOT the regression-test path.)

tracebisect export-pytest <baseline.tbtrace> <output.py>
    --scenario <command>          Command to invoke in CI to produce
                                  a fresh candidate trace (REQUIRED in V1)
    --assert <dimensions>         Comma-separated list of what to check.
                                  V1 dimensions: tool_args, final_output,
                                  cost, branch, error_absence
    --cost-threshold <ratio>      Default 1.5x baseline cost
    Generate a pytest regression test. The test runs <scenario> in CI,
    captures a fresh candidate trace, and asserts behavior matches
    baseline on the specified dimensions. See Section 4.5.

tracebisect demo
    Run a built-in demo scenario showing all four primitives end-to-end
```

### 4.3 Worked example

```bash
# Engineer suspects last week's prompt update broke the refund agent.
# Both runs were instrumented with OpenTelemetry; traces exported as JSON.

$ tracebisect ingest baseline.otel.json baseline.tbtrace
$ tracebisect ingest candidate.otel.json candidate.tbtrace
$ tracebisect diff baseline.tbtrace candidate.tbtrace

✗ First divergence at event 7: tool_call.search_database
  Severity: CRITICAL
  Type:     changed_tool_args

  Expected:
    query = "users WHERE active = true"

  Actual:
    query = "users WHERE active = true AND deleted = false"
                                    ++++++++++++++++++++

  Direct effects:
    tool output changed
    final answer changed
    cost increased 18.4%
    no errors introduced

  Source metadata:
    baseline trace recorded at commit a3f9c1d
    candidate trace recorded at commit b71e442
    prompt template `refund_search` differs between runs

# Now generate a CI regression test that re-runs the scenario fresh
# each time and asserts behavior matches baseline:

$ tracebisect export-pytest baseline.tbtrace \
    --scenario "python examples/refund_agent.py --case refund_042" \
    --assert tool_args,final_output,cost \
    tests/test_refund_agent_regression_042.py

Exported test: tests/test_refund_agent_regression_042.py
  Scenario:    python examples/refund_agent.py --case refund_042
  Assertions:  tool_args matches baseline
               final_output matches baseline
               cost within 1.5x baseline
  Baseline:    tests/fixtures/baseline_refund_042.tbtrace
```

This output is the product. Everything in the spec exists to produce this output reliably. Note that all claims in the diff are *structurally verifiable* from the trace pair alone — TraceBisect does not infer domain meaning ("12 fewer users returned") because that requires understanding the agent's domain, which is out of scope for V1.

### 4.4 Why this loop prevents regressions

Critical to understand: the `tracebisect diff` step is *retrospective debugging* — it explains a failure that already happened. The `export-pytest` step is *prospective prevention* — the generated test runs the scenario fresh in CI on every commit and catches the regression *before* it ships. These are two different products glued together by one shared comparison engine, and both are essential to the value proposition.

### 4.5 The export-pytest contract

The semantic difference between a static comparison and a live regression test matters enough to spell out explicitly. V1 separates these by command:

- **Static comparison** is what `tracebisect diff` does. It takes two already-recorded traces and shows you what changed between them. Useful for retrospective debugging — "what happened in the failing run?" — but it does nothing to prevent the failure from recurring with new code tomorrow.
- **Live regression test** is what `tracebisect export-pytest` produces. The exported test runs the scenario command in CI on every invocation, captures a fresh trace, and asserts behavior matches the baseline. This is what gates the future.

In V1, `--scenario` is **required** for `export-pytest`. There is no static-only export mode. If a user wants static trace-vs-trace comparison, they use `tracebisect diff` directly. This separation keeps the regression-prevention promise honest and prevents users from accidentally generating tests that only document the past.

**Live regression test (the only V1 export-pytest output shape)**:
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
    """Regression test for refund_042 case.
    Generated 2026-05-09 from divergence at event 7
    (changed_tool_args, CRITICAL).
    """
    baseline = load_baseline(BASELINE_PATH)
    candidate = capture_trace(SCENARIO_CMD)  # runs scenario fresh

    assert_aligned(
        baseline=baseline,
        candidate=candidate,
        assertions=["tool_args", "final_output", "cost"],
        cost_threshold=1.5,
        mode="ci",
    )
```

The exported test runs the scenario command in a subprocess on every CI invocation, captures a fresh trace via TraceBisect's recorder (or by reading OTel output if the scenario is OTel-instrumented), and aligns it against the baseline using the same algorithm `tracebisect diff` uses. If any asserted dimension diverges beyond tolerance, the test fails.

**This is the loop that makes "prevents regressions" honest**: divergence found via `tracebisect diff` → test exported with a live scenario via `tracebisect export-pytest` → committed → CI runs the scenario at HEAD → fresh trace compared against baseline → red build catches the regression next time it's introduced.

**The V1 `assert` dimensions** map onto divergence types and direct checks:

| Assertion | V1 implementation |
|---|---|
| `tool_args` | check for `changed_tool_args` divergence (CRITICAL severity) |
| `final_output` | direct equality check on RUN_END payload (V1 direct check, not a divergence type) |
| `cost` | check ImpactAnalysis.cost_delta_ratio against `--cost-threshold` |
| `branch` | check for `branch_changed` divergence |
| `error_absence` | scan candidate for ERROR events not present in baseline |

`retrieval` is a V2 assertion dimension and is not available in V1. The user opts into specific dimensions to avoid brittle tests — a test that asserts on every dimension would fail on irrelevant LLM variation; a test that asserts only on what mattered for the original failure stays robust.

**Cost considerations**: live scenarios in CI consume LLM API budget. Two mitigations, both V1: (1) the recorder's `--side-effects stub` mode (default) records LLM responses to a per-scenario fixture file and replays them on subsequent runs, turning the live test into a semi-deterministic test that only hits APIs during initial baseline capture; (2) tests are tagged so CI can run a subset on every PR and the full suite nightly.

---

## 5. The Technical Core

This section specifies the four parts of the system that must be designed in writing before any code is written. These are the load-bearing algorithms; everything else is plumbing.

### 5.1 Canonical Trace Schema

All ingested traces, regardless of source format, are normalized into a typed canonical representation.

A **trace** is an ordered tree of **events**. The top-level `Trace` and individual `Event` shapes:

```python
@dataclass(frozen=True, slots=True)
class Trace:
    schema_version: int               # See SCHEMA_VERSION constant
    trace_id: str                     # Globally unique
    created_at: datetime
    root_event: Event                 # The RUN_START event at the top
    events: list[Event]               # Flat list for fast iteration
    source_convention: Literal[
        "native",                     # Produced by TraceBisect's own recorder
        "genai",                      # OTel GenAI semantic conventions (gen_ai.*)
        "openinference",              # OpenInference conventions (openinference.*)
        "mixed",                      # Spans use both genai and openinference
        "unknown",                    # Couldn't determine convention
    ]


@dataclass(frozen=True, slots=True)
class Event:
    id: str                           # Globally unique within the trace
    parent_id: Optional[str]          # Tree structure
    sequence_index: int               # Position in depth-first traversal
    type: EventType                   # Enum (see below)
    semantic_name: str                # Free-text descriptive name
    timestamp: datetime
    duration_ms: Optional[float]

    # Type-specific payload (typed via discriminated union)
    payload: Union[
        LLMCallPayload,
        ToolCallPayload,
        MCPCallPayload,
        RetrievalPayload,
        StateTransitionPayload,
        BranchDecisionPayload,
        ErrorPayload,
        HumanInputPayload,
    ]

    # Provenance
    source_format: Literal["otel", "native"]   # V1 values only
    source_event_id: str              # Original ID in source system

    # Replay metadata
    model_version: Optional[str]
    prompt_version: Optional[str]
    code_sha: Optional[str]
    sampling_params: Optional[dict]
```

The `EventType` enum has ten values:

```
RUN_START, RUN_END, LLM_CALL, TOOL_CALL, MCP_CALL,
RETRIEVAL, STATE_TRANSITION, BRANCH_DECISION, ERROR, HUMAN_INPUT
```

Each payload type has its own typed schema. For example:

```python
@dataclass
class LLMCallPayload:
    model: str
    provider: str
    messages: list[dict]          # OpenAI-style messages
    response_text: str
    response_tool_calls: list[dict]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    temperature: float
    seed: Optional[int]
```

The schema is versioned (`schema_version: 1`). All ingested traces are converted to the current schema version on ingest; future versions ship with migration utilities.

**Open question (resolve during V1)**: should events carry hashes of input/output for fast equality checks? Probably yes — adds ~32 bytes per event, saves repeated structural comparison.

### 5.2 Alignment Algorithm

The alignment problem: given two trace trees, produce a sequence of `(baseline_event, candidate_event)` pairs where each pair represents either matched events, an unmatched baseline event (deletion), or an unmatched candidate event (insertion).

This is tree-edit-distance with semantic node matching. It is genuinely hard CS and the V1 implementation commits to a tiered matching strategy:

**Tier 1: Stable ID match.** If both traces share an event ID (e.g. because they were derived from the same instrumented session at different code versions, or because both reference a deterministic identifier like a user ID + step), match directly. This is the cheapest and most reliable signal.

**Tier 2: Structural match.** If no stable ID, match by `(parent_match, type, semantic_name)`. Two events match structurally if their parents have already been matched, their event types are identical, and their semantic names are identical or near-identical (Levenshtein distance ≤ 2). This handles the common case of "same agent code, same instrumentation, different runs."

**Tier 3: Positional fallback.** If structural matching fails, match by sequence position within the parent. This handles cases where event ordering changed but events are otherwise identical.

**Unmatched events** become divergences of type `missing_event` (in baseline only) or `extra_event` (in candidate only).

**Algorithm sketch (pseudocode):**

```
function align(baseline_root, candidate_root):
    matches = []
    queue = [(baseline_root, candidate_root)]

    while queue not empty:
        (b, c) = queue.pop()

        if stable_id_match(b, c):
            matches.append((b, c, "id_match"))
        elif structural_match(b, c):
            matches.append((b, c, "structural_match"))
        else:
            matches.append((b, None, "deletion"))
            matches.append((None, c, "insertion"))
            continue

        # Recurse into children with positional fallback
        b_children = b.children
        c_children = c.children
        child_pairs = pair_children(b_children, c_children)
        queue.extend(child_pairs)

    return matches
```

The `pair_children` function uses Tier 2 for as many pairs as possible, then Tier 3 for the remainder.

**Edge cases that must be handled:**

- Empty traces (one or both)
- Traces with cycles (shouldn't happen, but assert and fail loudly)
- Traces with mismatched root types (one starts with RUN_START, other doesn't)
- Very deep traces (>1000 events) — must terminate in reasonable time; tested target is <5 seconds for 10,000-event traces

### 5.3 Divergence Taxonomy

A **divergence** is a typed difference detected during alignment. The full taxonomy has nine divergence types; V1 implements five. The nine types together (V1 implementation set marked):

| Type | Description | Default Severity | Ships in |
|---|---|---|---|
| `missing_event` | Event present in baseline, absent in candidate | HIGH | V1 |
| `extra_event` | Event present in candidate, absent in baseline | HIGH | V1 |
| `changed_input` | Matched events but input payload differs | varies | V2 |
| `changed_output` | Matched events but output payload differs | varies | V2 |
| `changed_tool_args` | Tool call matched but the called tool or its arguments differ | CRITICAL | V1 |
| `branch_changed` | Branch decision differs at matched node | CRITICAL | V1 |
| `retrieval_changed` | RAG retrieval returned different chunks | HIGH | V2 |
| `state_changed` | State transition reached different value | HIGH | V2 |
| `cost_regression` | Total or per-event cost increased >threshold | MEDIUM | V1 |

Each divergence carries:

```python
@dataclass
class Divergence:
    type: DivergenceType
    severity: Severity
    baseline_event: Optional[Event]
    candidate_event: Optional[Event]
    description: str              # Human-readable summary
    expected: Any                 # Baseline value
    actual: Any                   # Candidate value
    impact: ImpactAnalysis        # See below
    suggested_fix: Optional[str]
```

The `ImpactAnalysis` is computed by walking forward from the divergence point to determine *structurally observable* downstream effects: token count change, cost delta, final-output change (verbatim string match, not semantic), error introduction or removal, and number of subsequent events affected. This is what allows TraceBisect to say "tool output changed, final answer changed, cost increased 18.4%" rather than just "tool args changed." Domain-specific impact statements ("12 fewer users returned") are explicitly out of scope for V1 — they require understanding what the data means, which TraceBisect doesn't have. V2 will add custom impact-analysis hooks where users can plug in domain logic.

### 5.4 Severity Rules

Severity determines what gets surfaced as the "first divergence" and what gets ignored. The classification logic:

**CRITICAL** — Divergence affects final outcome or violates correctness contract.
Triggers: `branch_changed`, `changed_tool_args` (when result differs), `missing_event` of type RUN_END (run didn't complete), `error` introduced where none existed.

**HIGH** — Divergence likely affects outcome but downstream impact uncertain.
Triggers: `missing_event` or `extra_event` of types LLM_CALL/TOOL_CALL, `retrieval_changed`, `state_changed`.

**MEDIUM** — Divergence affects metrics but not necessarily correctness.
Triggers: `cost_regression` >20%, `changed_input` to LLM_CALL where output is semantically equivalent.

**LOW** — Divergence is likely cosmetic.
Triggers: token-level differences in LLM responses where structured parse is identical, latency differences, timestamp drift.

**INFO** — Divergence is informational.
Triggers: model version changes (unless behavior also diverged), code SHA changes (unless behavior also diverged).

The "first divergence" surfaced in the diff output is the earliest event with severity ≥ HIGH. CRITICAL takes precedence over HIGH at the same event index.

---

## 6. Determinism Modes

TraceBisect operates in one of three modes, set per-invocation via `--mode`. The critical design constraint: **the primary use case is comparing old code against new code**, so the default modes must permit code/prompt/model drift while flagging it explicitly.

**`permissive` mode** (development default): all drift is allowed. Code SHA, prompt versions, model versions, and trace schema can all differ between baseline and candidate. Drift is recorded as INFO-level metadata in the diff output but does not block analysis. This is the mode for "I changed the prompt — what behavior changed?" workflows. Use when actively investigating.

**`strict` mode**: fails the run only on conditions that would make comparison meaningless — incompatible trace schemas, missing required event types, or trace-shape incompatibility (e.g. one trace is missing RUN_END). Code, prompt, and model drift are reported as explicit metadata in the output, not as failures, unless the user passes `--require-same-code`, `--require-same-prompt`, or `--require-same-model`. Use when you need confidence that the comparison is structurally valid but want to permit deliberate change.

**`ci` mode**: enforces the exact expectations encoded in an exported pytest test. The test file specifies what must match (e.g. "tool args must equal X", "final answer must equal Y") and CI mode treats any unmet expectation as a failure. This is the mode used by exported regression tests when they run in continuous integration. Use when locking known-good behavior.

The default mode for the CLI is `permissive`. The default mode for exported pytest tests is `ci`. `strict` is opt-in via flag.

A separate `counterfactual` mode is on the V2 roadmap for bias and fairness testing. V1 does not implement it; the data model supports it.

---

## 7. Side-Effect Policy

V1 is not a general replay product — it does not re-run arbitrary captured agent runs. The side-effect question still arises in two specific contexts: the demo recorder, and the live trace capture inside CI tests generated by `export-pytest`. The policy applies to both.

When TraceBisect's demo recorder or CI-test runner captures a fresh trace by running the user's scenario command, that command may make tool calls with external side effects (database writes, emails, API calls to production systems). The policy options:

**`stub` mode** (V1 default): tool calls execute against a per-scenario fixtures directory of recorded responses, *not* live external systems. The scenario's tool wrapper checks the fixture first and falls back to live execution only if no fixture exists. This makes CI tests reproducible and budget-safe but requires fixtures to be recorded once.

**`live` mode**: tool calls execute against real systems. Used during initial baseline capture and when the user explicitly wants to test against current external state. Requires `--allow-live` flag plus a `--budget-limit` to cap cost.

**`skip` mode** (V2): tool call is not invoked at all and returns `None`. Forces the agent code to handle the absence — useful for testing error paths.

V1 ships `stub` and `live` modes only.

**Important honest scope**: stub/live behavior only applies to (a) LLM calls made through SDKs patched by `patch_sdks()` and (b) `@wrap_tool`-decorated functions that TraceBisect can see. Unwrapped external side effects — direct HTTP calls, raw subprocess invocations, file writes, database access through unwrapped clients — bypass the SDK patches and remain the user's responsibility regardless of mode. The recorder's docstring documents this limitation explicitly so users don't assume blanket protection.

For traces ingested from external sources (OTel exports, Langfuse, Phoenix, MLflow), the side-effect question is moot — the trace already happened, TraceBisect is just reading the record.

**Important honest framing**: TraceBisect is not designed to replay arbitrary historical agent runs deterministically. That problem is harder than V1 attempts to solve and is occupied by tools like agent-replay and AgentRR. TraceBisect's narrower commitment is: capture a *fresh* trace from a *current* scenario, compare against a stored baseline, fail if behavior diverged. That commitment is achievable and useful.

---

## 8. V1 Scope

### 8.0 V1 Acceptance Criterion

The single test that determines whether V1 has shipped:

> Given two real `.tbtrace` files generated from the same agent scenario before and after a change, TraceBisect finds the first meaningful divergence and exports a pytest test that fails on the bad behavior.

Everything in V1 supports this criterion. Anything that does not contribute to it is V2 work.

### 8.1 Must ship

The following are required for V1 release:

- Canonical trace schema with versioning
- OpenTelemetry / OpenInference span importer (one source, working end-to-end on real production traces)
- Tiny demo recorder (200 lines, OpenAI + Anthropic SDK wrappers, single JSONL output)
- Tiered alignment algorithm (Tiers 1–3)
- Five of nine divergence types: `missing_event`, `extra_event`, `changed_tool_args`, `branch_changed`, `cost_regression`
- Severity classification rules
- Terminal diff renderer (the money-shot output)
- **pytest export, with both pieces working**: a generator that writes a regression test file *and* the `tracebisect.testing` runtime module (`capture_trace`, `load_baseline`, `assert_aligned`) that the generated test imports and uses to capture fresh traces in CI
- `permissive`, `strict`, and `ci` determinism modes
- `stub` and `live` side-effect modes
- One end-to-end demo scenario with CI integration (the test must actually run the scenario fresh in CI, not just compare static fixtures)
- README, install via PyPI, basic docs site
- 90-second demo video

### 8.2 Defer to V2

- Source-specific importers (Langfuse, Phoenix, MLflow native formats — most cases are covered by OTel ingestion)
- Remaining four divergence types (`changed_input`, `changed_output`, `retrieval_changed`, `state_changed`)
- True cross-commit bisection
- HTML report viewer
- GitHub Action
- `counterfactual` determinism mode
- `skip` side-effect mode
- MCP replay
- TypeScript/JavaScript adapter

### 8.3 Skip entirely

- Hosted SaaS dashboard
- Authentication, RBAC, billing
- Team collaboration features
- Prompt management
- Full evaluation platform
- Enterprise governance dashboard
- Ten framework adapters
- Any feature that would require ongoing infrastructure to maintain

---

## 9. The Bisect Vision (V1 → V2 Path)

V1 delivers two-trace comparison. The name promises more, and V2 delivers it.

**True cross-commit bisection** works as follows: given a known-good commit (where the agent worked), a known-bad commit (where it fails), and a reproducible test scenario, TraceBisect:

1. Checks out the known-good commit, runs the test scenario, captures a baseline trace
2. Checks out the known-bad commit, runs the test scenario, captures a failing trace
3. Confirms baseline passes the divergence check and bad commit fails it
4. Binary-searches the commit history between known-good and known-bad
5. At each test commit, runs the test scenario, compares the trace against the baseline using the V1 alignment+divergence pipeline
6. Classifies each commit as good (no critical divergence) or bad (critical divergence present)
7. Converges on the breaking commit and reports it

This is the feature that earns the name and that no competitor offers. V1 must design toward it: the trace format must support reliable comparability across commits, the divergence classifier must produce stable good/bad signals, and the test scenario format must be commit-portable.

V1 does not ship bisection. V1 ships the foundation. V2 ships bisection on top.

The README in V1 must clearly state this scope. Suggested wording: *"V1 compares two traces. V2 will run that comparison automatically across commit history to find the exact change that broke your agent."*

---

## 10. Event Format and Storage

V1 stores traces as append-only JSONL with one event per line. File extension `.tbtrace`. Schema version is the first line.

```jsonl
{"schema_version": 1, "trace_id": "...", "created_at": "..."}
{"id": "evt_001", "type": "RUN_START", ...}
{"id": "evt_002", "parent_id": "evt_001", "type": "LLM_CALL", ...}
...
{"id": "evt_999", "type": "RUN_END", ...}
```

Why JSONL: streamable, append-only, line-diffable with standard tools, inspectable with `cat` and `jq`, no binary format lock-in.

V2 may add a DuckDB-backed local store for fast querying across many traces. ClickHouse-style backends are out of scope for the OSS V1.

---

## 11. The Money-Shot Terminal Output

The README's hero image is a screencap of this exact output, rendered in a terminal with ANSI colors:

```
✗ First divergence at event 7: tool_call.search_database
  Severity: CRITICAL
  Type:     changed_tool_args

  Expected:
    query = "users WHERE active = true"

  Actual:
    query = "users WHERE active = true AND deleted = false"
                                    ++++++++++++++++++++

  Direct effects:
    tool output changed
    final answer changed
    cost increased 18.4%
    no errors introduced

  Source metadata:
    baseline trace recorded at commit a3f9c1d
    candidate trace recorded at commit b71e442
    prompt template `refund_search` differs between runs

Exported test:
  tests/test_refund_agent_regression_042.py
```

A few notes on what this output deliberately does *not* claim:

- No domain-specific impact statements like "12 fewer users returned." That requires understanding what `users WHERE active=true` means in the agent's domain. V1 stays inside what it can verify from trace structure alone.
- No "likely root cause" attribution as a confident claim. Source metadata is reported only when the trace contains the relevant fields (commit SHA, prompt version, model version). When metadata is absent, those lines are omitted entirely. TraceBisect does not guess.
- No "suggested fix" recommendations. The diff describes what changed; the engineer decides what to do about it.

Color scheme: red for "actual" diverging value, green for "expected" baseline value, yellow for direct effects, dim white for source metadata and context. Standard `colorama` palette.

The visual density and information richness of this output is the product to a first approximation. Engineering effort spent making this output excellent has higher leverage than any other single thing in V1. Custom impact-analysis hooks (where users can plug in domain logic to translate "tool output changed" into "12 fewer users returned") are V2.

---

## 12. Demo Story

The 90-second demo video tells one complete production story:

**Setup (10s)**: A refund agent that worked yesterday now fails to find eligible users.

**Trace capture (15s)**: The agent is already OTel-instrumented for normal observability. Engineer exports the OTel traces from the working baseline run and the failing candidate run as JSON.

**Comparison (20s)**: `tracebisect ingest baseline.otel.json baseline.tbtrace`, same for candidate, then `tracebisect diff baseline.tbtrace candidate.tbtrace`. The terminal output appears, identifying the divergence at event 7.

**Root cause (15s)**: The output's source metadata shows the candidate trace came from commit b71e442, with a different prompt template than baseline. Engineer opens the prompt file and sees the regression.

**Fix and test (20s)**: Engineer fixes the prompt. `tracebisect export-pytest baseline.tbtrace tests/test_refund_regression.py --scenario "python examples/refund_agent/agent_v2.py" --assert tool_args,final_output,cost`. The test file appears.

**CI integration (10s)**: Engineer pushes to GitHub. CI runs the exported test in `ci` mode. Green build. Cut to "the bug stays fixed."

This story compresses the entire production-engineering value proposition into 90 seconds: real failure, real reproduction, real fix, real regression prevention. The flow uses only V1-shipped capabilities (OTel ingestion, diff, pytest export, CI mode) — no dependency on tools whose importers are deferred.

---

## 13. Security and Privacy

### 13.1 V1 commitments (architectural — true by construction)

- **Local-first**: no cloud component, no telemetry, no account, no automatic data exfiltration. The architecture has no remote endpoints; this isn't a policy promise that could be violated, it's a property of the build.
- **No third-party data collection**: TraceBisect never phones home and never includes analytics. Verifiable by inspecting the source.

### 13.2 V1.1 / V2 roadmap (features promised but not yet implemented)

The following posture features are commonly expected by users in regulated sectors (UK BFSI, EU healthcare, India DPDP) but are deferred until V1.1 or V2 because the implementation prompt pack does not cover them:

- **PII redaction hooks**: configurable per-event redaction functions
- **Built-in secret detectors**: OpenAI keys, Anthropic keys, AWS credentials, Bearer tokens
- **`.tracebisectignore` file**: per-project exclusions for sensitive event types
- **Opt-in raw payload capture**: by default, large payloads hashed; full capture requires explicit flag
- **Warning before exporting cassettes**: CLI prompt before any operation that writes outside the project directory

The README must be honest about this — V1 is local-first by architecture but does not actively redact or detect secrets. Users handling sensitive data should treat traces as sensitive artifacts and apply their own redaction until V1.1 lands. This honest framing is more defensible than over-promising security features and quietly missing them.

### 13.3 Why deferred

Each of these features adds non-trivial implementation effort and edge-case risk. Shipping them half-correctly would be worse than shipping them later: a "secret detector" with false negatives gives users a false sense of safety. V1.1 will tackle these as a coherent block once the V1 schema, ingestion, alignment, and export-pytest paths are battle-tested.

---

## 14. Distribution Plan

### 14.1 Launch surfaces

- GitHub repository (public from day one)
- PyPI package (`pip install tracebisect`)
- Documentation site (Mintlify or Docusaurus, modest)
- Show HN: "Show HN: TraceBisect — git bisect for AI agent traces"
- Reddit: r/LangChain, r/AI_Agents, r/LocalLLaMA, r/Python
- Hacker News (Show HN, optimal Tuesday/Wednesday morning Pacific)
- LangChain Discord
- MLOps Community Slack
- Dev.to and Medium technical articles
- LinkedIn portfolio post
- Conference talk submissions: AI Engineer World's Fair, MLOps World, PyCon UK

### 14.2 Naming and branding

- PyPI: `tracebisect`
- GitHub: `tracebisect/tracebisect` (verify org availability) or `<user>/tracebisect`
- Domain: `tracebisect.dev` (verify availability before commit)
- Package name verified clear on PyPI as of spec date

---

## 15. Portfolio and Adoption Success Criteria

### 15.1 Minimum viable shipped

- Public GitHub repository
- Clean README with terminal screenshot hero image
- Architecture diagram (one image)
- `pip install tracebisect` works
- All five CLI subcommands functional
- pytest test file export working (a pytest *plugin* is V2; V1 ships file generation only)
- LangChain or LangGraph example
- GitHub Actions CI badge passing
- 90-second demo video
- One technical blog post explaining the alignment algorithm

### 15.2 Strong adoption signal

- 1,000+ GitHub stars
- 5,000+ PyPI downloads
- 5+ external issues opened by real users
- 1+ external contributor
- 1+ public quote from a user (Twitter/HN/blog post)
- Documentation site indexed by Google

### 15.3 Excellent

- 10,000+ stars
- Sustained PR throughput (1+ external PR/month)
- Conference talk delivered
- Mention in a major newsletter (Latent Space, Pragmatic Engineer, MLOps Community)

---

## 16. Build Phasing

Six weeks to V1 ship. Four weeks is achievable for a demo; six is realistic for portfolio-quality OSS that doesn't embarrass the author.

### Week 1: Foundation and namespace claims

- **PyPI**: publish a thin real `tracebisect` 0.0.1-alpha — CLI shell with `--version`, `tracebisect demo` (prints "alpha — see github.com/.../tracebisect"), README, LICENSE, explicit alpha disclaimer in the long description. **Do not upload an empty placeholder package**; PyPI policy treats those as name squatting and they can be removed.
- Reserve the GitHub namespace and `tracebisect.dev` domain (verify availability)
- Write the four design-doc pages (canonical schema, alignment algorithm, divergence taxonomy, severity rules) — these become `spec/` in the repo
- Set up project skeleton: `src/`, `tests/`, `docs/`, CI workflows, type-checking, linting
- Implement canonical trace schema with full type definitions and schema versioning

### Week 2: Ingestion

- One importer: OTel/OpenInference via OTLP JSON. End-to-end test: read OTel trace, hydrate into canonical model, dump to JSON, verify round-trip.
- Test against actual exports from at least three sources: LangChain native instrumentation, OpenInference auto-instrumentation, and manual OTel spans. Real-world traces are messier than synthetic; budget time for this.
- Recorder for demo/onboarding: ~200 lines wrapping OpenAI and Anthropic SDK calls, outputting canonical JSONL.

### Weeks 3–4: Alignment

- Implement Tier 1 (stable ID) and Tier 2 (structural) matching
- Implement positional fallback (Tier 3)
- Test against handwritten trace pairs covering edge cases: empty trace, deeply nested, branch divergence, mismatched root types, very long sequences
- Performance test: align 10,000-event trace pair in <5 seconds
- Plan two weeks here, not one — alignment is the load-bearing algorithm and tree-edit-distance with semantic matching is genuinely hard. Schedule pessimistically.

### Week 5: Divergence detection and rendering

- Implement five divergence types (`missing_event`, `extra_event`, `changed_tool_args`, `branch_changed`, `cost_regression`)
- Implement severity classification rules
- Implement terminal diff renderer with ANSI colors — this is the money shot, polish until it's beautiful
- **`tracebisect.testing` runtime module**: `capture_trace()` (runs scenario in subprocess, collects fresh trace), `load_baseline()`, `assert_aligned()`. This module is what generated tests import; it's part of the package surface, not just internal code.
- `export-pytest` generator: writes test file that uses the runtime module to capture-and-compare in CI, *not* just load two static traces
- Determinism mode handling (`permissive`, `strict`, `ci`)

### Week 6: Polish and ship

- End-to-end demo scenario (the refund agent story) — script, recorded run, both traces saved as fixtures
- 90-second demo video recording and editing
- README with hero image and demo GIF
- Documentation site (single page, scrollable, Mintlify or Docusaurus minimal)
- PyPI release 0.1.0 (the real first release, replacing the alpha shell)
- Show HN post, scheduled for optimal Tuesday or Wednesday morning Pacific
- Conference talk submissions

Total: 6 weeks. Stretch is 8 weeks if real-world OTel trace variation proves harder than budgeted. Either way, V1 is shippable, not perfect — perfection is iteration after launch.

---

## 17. Risks and Mitigations

**Risk: Alignment algorithm is harder than spec assumes.**
Mitigation: The tiered approach has graceful degradation — Tier 3 always produces *some* alignment, even if imperfect. Imperfect alignment with clear severity scoring is still useful. The 6-week build plan already allocates two weeks (Weeks 3–4) to alignment specifically. If it overruns, ship V1 with Tier 1 and Tier 2 only and document Tier 3 as a known limitation.

**Risk: OTel trace format variation across vendors breaks ingestion.**
Mitigation: Test against actual exports from at least three sources (LangChain native, OpenInference auto-instrumentation, and manual OTel spans) during Week 2 ingestion work. Real traces from real frameworks tend to be messier than the spec assumes. Allocate buffer for parser fixes.

**Risk: Crowded market means low signal even if tool is good.**
Mitigation: The "git bisect" framing is genuinely sharper than any competitor's positioning. Hero output (Section 11) is more polished than competitor outputs. Demo story (Section 12) is more concrete. These are differentiators in a crowded market.

**Risk: Name conflict surfaces post-launch.**
Mitigation: Verify on PyPI, npm, GitHub, and trademark databases before committing. Reserve all package namespaces in Week 1.

**Risk: V2 bisection is much harder than V1 implies.**
Mitigation: V1 explicitly does not promise bisection. README is honest about scope. V2 ships when V2 is ready, not on a deadline.

**Risk: Solo maintainer burnout post-launch.**
Mitigation: Set issue-response SLA at one week, not 24 hours. Document explicitly what's in-scope for community contribution. Decline scope expansion that doesn't fit core mission.

---

## 18. Open Questions

These remain open and should be resolved during implementation, not before:

- Should events carry input/output hashes for fast equality checks? (Probable yes)
- What's the right severity threshold for `cost_regression` — 20%? 50%? Configurable?
- Does the pytest export generate one test per divergence or one test per failing run?
- Should the recorder be a separate package (`tracebisect-record`) or part of the main install?
- For OTel ingestion: support OTLP gRPC, OTLP HTTP, JSON file dumps, or all three?
- Does the demo scenario use OpenAI or Anthropic SDK in the demo video? (Probably both, alternating, to signal framework-agnosticism)

---

## 19. Glossary

**Trace** — A complete record of one agent execution, structured as a tree of events.

**Event** — A single operation within a trace: an LLM call, a tool call, a state transition, etc.

**Alignment** — The process of pairing events between two traces to identify what matched and what didn't.

**Divergence** — A typed difference between two aligned traces. Has a type (e.g. `changed_tool_args`) and a severity.

**First divergence** — The earliest event in trace order where a divergence with severity ≥ HIGH occurred. The headline finding TraceBisect surfaces.

**Cassette** — Term inherited from VCR.py for a stored trace file. Used informally; the formal term is "trace file."

**Determinism mode** — `permissive` (default), `strict`, or `ci`. Controls how version drift is handled during diff. A `counterfactual` mode is on the V2 roadmap.

**Side-effect policy** — `stub` (default) or `live`; controls how external side effects are handled during fresh trace capture (in CI tests and the demo recorder). A `skip` mode is V2.

---

## End of Specification v1.3

*This specification is locked and re-synced with TraceBisect_ClaudeCode_Prompts.md v1.4. Both documents describe the same product. Implementation should track against this spec; the prompt pack is the executable plan that produces it.*

*Next deliverables (in order): four design-doc pages (Section 5 expanded — canonical schema, alignment algorithm, divergence taxonomy, severity rules), project skeleton (already done), canonical schema implementation, OTel importer, alignment, divergence detection + renderer, testing runtime, export-pytest generator, demo, V1 release.*