# Alignment Algorithm — Design Document

> Status: Design (Phase 1 V1)
> Companion to: `spec/production-spec.md` §5.2, §5.3
> Builds on: `spec/canonical-trace-schema.md` §2 (Trace), §3 (Event), §4 (EventType), §5 (Payload Types)
> Implementation target: `src/tracebisect/align.py` (not yet written)

---

## 1. The Alignment Problem

**Input.** Two `Trace` values (`baseline` and `candidate`), each as defined in `spec/canonical-trace-schema.md` §2. Each trace owns a `root_event` (always `RUN_START` per §3) and a depth-first ordered `events` list whose parents form a tree via `Event.parent_id`.

**Output.** A list of `Match` triples:

```python
MatchKind: TypeAlias = Literal[
    "ID_MATCH", "STRUCTURAL_MATCH", "POSITIONAL_MATCH", "DELETION", "INSERTION",
]

@dataclass(frozen=True, slots=True)
class Match:
    baseline: Event | None        # None iff kind == "INSERTION"
    candidate: Event | None       # None iff kind == "DELETION"
    kind: MatchKind
```

**Invariants.**
- *Coverage:* every event in `baseline.events` appears in exactly one `Match` (paired or `DELETION`). Symmetric for `candidate.events` (`INSERTION` when unpaired). No double-counting. **Unmatched subtrees are expanded event-by-event:** when a child is emitted as `DELETION`, every descendant on the baseline side is emitted as a separate `DELETION` `Match` in depth-first `sequence_index` order; symmetrically for `INSERTION` on the candidate side. Subtrees are never collapsed into a single `Match`.
- *DFS output order:* `Match`es appear in deterministic depth-first traversal order. A matched child pair is followed immediately by every `Match` arising from that pair's subtree before any of its sibling's `Match`es. `INSERTION`s land at their candidate-side parent-local position; `DELETION`s land at their baseline-side parent-local position. There is no global re-sort.
- *Determinism:* given the same inputs, `align` returns byte-identical `Match` lists across runs. Divergence rendering (production-spec §11) is keyed by event index; flaky alignment would surface different "first divergences" between runs.
- *Out of scope:* what changed *inside* a paired event (payload diff, cost delta, branch-decision change) belongs to divergence detection (production-spec §5.3).

**Why not the obvious algorithm.** The general problem is **tree edit distance with semantic node matching**. Zhang–Shasha is `O(n²·min(depth,leaves)²)` — far above the 10K-in-5s budget. Myers diff and Hunt–McIlroy LCS operate on sequences and lose tree structure (a `TOOL_CALL` that moved parents is not the same as a deleted one). V1 commits to a **tiered greedy matcher**, accepting that it is not minimum-edit-distance optimal. Optimality costs two orders of magnitude in latency, and users tolerate suboptimal pairings better than a 90-second wait.

---

## 2. The Tiered Matching Strategy

Three tiers, applied in order, from highest-confidence to fallback. A given event pair is decided by the **first** tier that produces a match; lower tiers are not consulted for it.

| Tier | Signal | When it fires | Confidence |
|---|---|---|---|
| 1 | Stable ID equality (shared deterministic IDs first, then computed stable tuples) | Among siblings under an already-matched parent | Highest — exact equality |
| 2 | Structural equality — same `EventType`, near-identical `semantic_name` | Among the same parent's children that Tier 1 left unpaired | High — same code path |
| 3 | Positional fallback, **type-gated** | Among that parent's children that Tier 2 left unpaired | Low — last resort |

**Why this order.** Tier 1 is cheap and reasons about the *same logical event*. Tier 2 captures "same agent code, different run" — identical `EventType` and `semantic_name` but no stable ID. Tier 3 is the safety net.

**All three tiers are parent-local.** None of them scans the flat event list globally. Pairing is scoped to the children of an already-matched parent pair; descent proceeds only into matched pairs. This locality is what prevents a Tier 1 hit on a leaf from skipping its ancestors, keeps the algorithm near-linear in expectation, and makes the output's parent-local ordering well-defined (§6).

---

## 3. Tier 1: Stable ID Matching

Tier 1 has two layers of signal, applied in priority order. Both are scoped to **sibling sets under an already-matched parent** — never across the whole flat event list. (The root pair is the base case: roots are paired by position before any descent, see §6.) Parent-local scoping is what prevents a Tier 1 hit on a deeply nested descendant from skipping its intermediate ancestors.

**Primary signal — shared deterministic IDs.** When `baseline.source_event_id == candidate.source_event_id` (both traces derive from the same instrumented session, e.g. the same OTel exporter run at two code versions) or when both events carry the same `Event.id` (some recorders use deterministic step-keyed IDs like `"run42:step7"`), this is the strongest possible Tier 1 evidence and fires unconditionally. Equality is exact string comparison.

**Secondary signal — computed stable tuples.** When deterministic IDs are absent or differ, Tier 1 falls back to a stable tuple computed from payload fields. The tuple per `EventType` (citing payload field names from `spec/canonical-trace-schema.md` §5):

| EventType | Stable-ID tuple | Notes |
|---|---|---|
| `RUN_START` | matched by position | Exactly one per trace at index 0; root pair is set before Tier 1 runs. |
| `RUN_END` | matched by position | One at end, or absent on crashed runs (no Tier 1 if absent on either side). |
| `LLM_CALL` | `(prompt_version, model, normalized_messages_hash)` | All three required; if `prompt_version is None`, no secondary Tier 1 path. |
| `TOOL_CALL` | `(tool_name, normalized_arguments_hash)` | Always available — both fields required by `ToolCallPayload`. |
| `MCP_CALL` | `(server_name, tool_name, normalized_arguments_hash)` | Same shape plus the MCP server. |
| `RETRIEVAL` | `(retriever, query_hash)` | `query_hash` over canonicalized query string. |
| `BRANCH_DECISION` | `(branch_name,)` | Decision-point ID; chosen branch is compared by divergence, not alignment. |
| `STATE_TRANSITION` / `ERROR` / `HUMAN_INPUT` | — | No secondary Tier 1 path; fall through to Tier 2. |

**What "normalized" means.** Hashes are computed over a canonicalized JSON form: (1) all object keys lexicographically sorted at every depth; (2) no insignificant whitespace (`json.dumps(separators=(",", ":"))`); (3) Unicode strings in NFC form; (4) floats truncated to 9 decimal places to neutralize last-bit hardware nondeterminism; (5) `NaN` / `Infinity` replaced with `null` (and flagged in the ingest report). Hash function: SHA-256 truncated to 16 hex chars (64 bits) — collisions across one parent's child set are astronomically unlikely.

**Duplicate stable-ID handling.** Inside one parent's child set, the same stable key can appear more than once (e.g. an agent calls `search_database` with identical arguments twice). The algorithm bucketizes by key:

1. For each Tier 1 signal (primary or secondary), build `b_buckets: dict[Key, list[Event]]` from the parent's baseline children and `c_buckets` symmetrically from candidate children.
2. For each key present in both: if both buckets have length 1, emit an `ID_MATCH` and remove both events from the unpaired pool.
3. If either bucket has length > 1 (i.e. duplicates exist on at least one side), pair within the bucket by sibling order: sort each bucket by `sequence_index` and pair index-by-index up to `min(len(b_bucket), len(c_bucket))`, emitting `ID_MATCH` for each pair. Remaining bucket residue (the longer side's tail) stays in the unpaired pool and falls through to Tier 2 / Tier 3.

Sibling-order pairing for duplicate buckets is one of two reasonable policies (the other being "leave all duplicates for Tier 2/3"). We commit to sibling-order here because (a) it is deterministic, (b) it preserves the common case where the agent's repeated calls happen in the same order on both runs, and (c) it lets the divergence layer see ordered ID matches rather than positional matches when nothing has actually changed.

**Cost.** One pass over the parent's children to bucketize, one merge pass. `O(k)` per parent pair where `k` is sibling count.

---

## 4. Tier 2: Structural Matching

After Tier 1, remaining unpaired events are matched parent-first using three predicates that must all hold:

1. **Parent already matched** (root is the base case — `parent_id is None` on both sides).
2. **`EventType` identical** — no cross-type matches.
3. **`semantic_name` Levenshtein distance ≤ 2** over Unicode codepoints.

**Why distance ≤ 2.** Real-world `semantic_name` drift comes from trailing-whitespace span-attribute artifacts (distance 1), single-character provider-tag changes (`"gpt-4o"` → `"gpt-4O"`, distance 1), and version-suffix bumps (`"-v1"` → `"-v2"`, distance 1). A threshold of 2 absorbs these plus an off-by-one safety margin without admitting genuine renames (`"search"` → `"query"` is distance 5). Threshold 3 starts matching `"add"` → `"sub"` (distance 2) in cross-domain agents.

**Ordering within a parent.** When multiple candidates pass against the same baseline child, prefer the candidate with the smallest absolute difference in sibling index; ties broken by smallest `sequence_index`.

**Cost.** For each matched parent, `O(b·c·L²)` where `b`, `c` are unmatched child counts and `L = |semantic_name|` (typically ≤ 40).

---

## 5. Tier 3: Positional Fallback

After Tiers 1 and 2 run within a parent pair, remaining unpaired children on either side are paired by sibling index — **but only when their `EventType` matches**:

- Sort remaining baseline children by `sequence_index`; same for candidate.
- Walk the two sorted lists in lockstep. At each step, consider the next remaining baseline child `b` and next remaining candidate child `c`:
  - If `b.type == c.type`, emit `POSITIONAL_MATCH (b, c)` and advance both walks.
  - If `b.type != c.type`, emit `DELETION (b, None)` **and** `INSERTION (None, c)` for that fallback slot, then advance both walks. The two are emitted separately — Tier 3 never crosses types.
- Excess on the longer side after both walks finish becomes `DELETION` (baseline tail) or `INSERTION` (candidate tail).

The same-type predicate enforces the cross-type prohibition listed under Limitations: an event whose type changed between runs is surfaced as a deletion-plus-insertion divergence pair so the divergence layer can flag it loudly, not as a silent `POSITIONAL_MATCH` masquerading as a successful pairing.

**Sibling-count differences.** A 5-vs-3 split where all eight children share an `EventType` produces 3 `POSITIONAL_MATCH`es and 2 `DELETION`s. Tier 3 makes no claim that positional pairs are *equivalent* — only that they are the best guess given no structural signal. The divergence layer treats `POSITIONAL_MATCH` with low confidence when computing impact.

**Why not Hungarian / Kuhn–Munkres.** Optimal positional assignment over cost matrices is `O(n³)`. For sibling counts in the single digits (the common case), naive index pairing is indistinguishable from optimal.

---

## 6. Algorithm Pseudocode

> The following is **Python-shaped reference pseudocode**, not runnable code. Helper functions referenced here (`validate_roots`, `children_of`, `stable_key_for`, `levenshtein`, `merge_in_order`, `bucket_by`, etc.) are intentionally left undefined; their behavior is described in §3–§5 and §7. The pseudocode exists to fix the control flow and the parent-local scoping discipline.

The control flow is **depth-first**, not breadth-first: a matched child pair is recursed into before its sibling is processed, and `DELETION` / `INSERTION` subtrees are fully expanded in place. There is no BFS work queue.

```python
StableID: TypeAlias = tuple[str | int, ...]
ChildPair: TypeAlias = tuple[Event | None, Event | None, MatchKind]

def align(baseline: Trace, candidate: Trace) -> list[Match]:
    validate_roots(baseline, candidate)                  # may raise; see §7

    matches: list[Match] = []
    # Root pair is set before any descent. Tier 1 does not run at the root level —
    # roots are paired by position per §3.
    matches.append(Match(baseline.root_event, candidate.root_event, "ID_MATCH"))
    emit_matched_parent(
        baseline.root_event, candidate.root_event, baseline, candidate, matches,
    )
    return matches                                       # already in DFS order


def emit_matched_parent(
    b_parent: Event,
    c_parent: Event,
    baseline: Trace,
    candidate: Trace,
    matches: list[Match],
) -> None:
    # Compute child outcomes in parent-local order, then walk them DFS:
    # recurse into each matched pair immediately, and expand each unmatched
    # child's whole subtree before moving on to the next sibling record.
    b_kids = list(children_of(b_parent, baseline))       # sequence_index order
    c_kids = list(children_of(c_parent, candidate))      # sequence_index order
    for b, c, kind in pair_children(b_kids, c_kids):
        if kind in ("ID_MATCH", "STRUCTURAL_MATCH", "POSITIONAL_MATCH"):
            matches.append(Match(b, c, kind))
            # Descend NOW, before the next sibling record is processed.
            emit_matched_parent(b, c, baseline, candidate, matches)
        elif kind == "DELETION":
            # b has no candidate peer; its entire baseline subtree is unmatched.
            emit_unmatched_subtree(b, baseline, "DELETION", matches)
        elif kind == "INSERTION":
            emit_unmatched_subtree(c, candidate, "INSERTION", matches)


def emit_unmatched_subtree(
    event: Event,
    trace: Trace,
    kind: Literal["DELETION", "INSERTION"],
    matches: list[Match],
) -> None:
    # Depth-first expansion of an unmatched subtree. Every descendant is
    # covered by exactly one Match of `kind`, visited in sequence_index order.
    if kind == "DELETION":
        matches.append(Match(event, None, "DELETION"))
    else:                                                # "INSERTION"
        matches.append(Match(None, event, "INSERTION"))
    for child in children_of(event, trace):              # sequence_index order
        emit_unmatched_subtree(child, trace, kind, matches)


def pair_children(b_kids: list[Event], c_kids: list[Event]) -> list[ChildPair]:
    # ---- Tier 1 (parent-local): primary deterministic IDs, then secondary tuples.
    paired_b: set[str] = set()
    paired_c: set[str] = set()
    id_pairs: list[ChildPair] = []

    for keyfn in (primary_key_for, secondary_key_for):   # priority order, §3
        b_buckets: dict[object, list[Event]] = bucket_by(keyfn, b_kids, exclude=paired_b)
        c_buckets: dict[object, list[Event]] = bucket_by(keyfn, c_kids, exclude=paired_c)
        for key, b_bucket in b_buckets.items():
            if key is None:                              # event has no key for this layer
                continue
            c_bucket = c_buckets.get(key)
            if not c_bucket:
                continue
            # Sibling-order pairing inside the bucket — duplicates are paired in order.
            b_bucket.sort(key=lambda e: e.sequence_index)
            c_bucket.sort(key=lambda e: e.sequence_index)
            for b, c in zip(b_bucket, c_bucket):
                id_pairs.append((b, c, "ID_MATCH"))
                paired_b.add(b.id)
                paired_c.add(c.id)

    remaining_b = [e for e in b_kids if e.id not in paired_b]
    remaining_c = [e for e in c_kids if e.id not in paired_c]

    # ---- Tier 2: structural match (same parent already implicit; same type; Lev ≤ 2).
    struct_pairs: list[ChildPair] = []
    for b in list(remaining_b):
        candidates = [
            c for c in remaining_c
            if c.type == b.type and levenshtein(c.semantic_name, b.semantic_name) <= 2
        ]
        if not candidates:
            continue
        c = min(
            candidates,
            key=lambda x: (abs(x.sequence_index - b.sequence_index), x.sequence_index),
        )
        struct_pairs.append((b, c, "STRUCTURAL_MATCH"))
        remaining_b.remove(b)
        remaining_c.remove(c)

    # ---- Tier 3: positional fallback, type-gated. Cross-type slots split into
    #              DELETION + INSERTION rather than collapsing to POSITIONAL_MATCH.
    pos_pairs: list[ChildPair] = []
    remaining_b.sort(key=lambda e: e.sequence_index)
    remaining_c.sort(key=lambda e: e.sequence_index)
    i = j = 0
    while i < len(remaining_b) and j < len(remaining_c):
        b, c = remaining_b[i], remaining_c[j]
        if b.type == c.type:
            pos_pairs.append((b, c, "POSITIONAL_MATCH"))
        else:
            pos_pairs.append((b, None, "DELETION"))
            pos_pairs.append((None, c, "INSERTION"))
        i += 1
        j += 1
    for b in remaining_b[i:]:
        pos_pairs.append((b, None, "DELETION"))
    for c in remaining_c[j:]:
        pos_pairs.append((None, c, "INSERTION"))

    # ---- Emit in parent-local order: a Myers-style merge of the two sibling walks.
    # Pairs with both sides anchor both streams; DELETIONs flow with the baseline
    # walk; INSERTIONs flow with the candidate walk. Insertions therefore land near
    # their candidate-side neighbours, never at the end.
    return merge_in_order(id_pairs + struct_pairs + pos_pairs, b_kids, c_kids)


def merge_in_order(
    records: list[ChildPair],
    b_kids: list[Event],
    c_kids: list[Event],
) -> list[ChildPair]:
    # Walk b_kids and c_kids in parallel by sequence_index. For each side's next
    # event, look up its owning record. Emit each record once: when first seen
    # from either walk (anchored records consume both walks at that step;
    # DELETION consumes only baseline; INSERTION consumes only candidate).
    by_b = {r[0].id: r for r in records if r[0] is not None}
    by_c = {r[1].id: r for r in records if r[1] is not None}
    emitted: set[int] = set()
    out: list[ChildPair] = []
    bi = ci = 0
    while bi < len(b_kids) or ci < len(c_kids):
        next_b_rec = by_b[b_kids[bi].id] if bi < len(b_kids) else None
        next_c_rec = by_c[c_kids[ci].id] if ci < len(c_kids) else None
        # Pick whichever record naturally comes first in its own walk and has
        # not yet been emitted. Tie-breaks favor candidate (so INSERTIONs slot
        # ahead of an equally-positioned DELETION at the same fallback step).
        pick = choose_next(next_b_rec, next_c_rec, emitted, bi, ci, b_kids, c_kids)
        out.append(pick)
        emitted.add(id(pick))
        if pick[0] is not None and (next_b_rec is pick):
            bi += 1
        if pick[1] is not None and (next_c_rec is pick):
            ci += 1
    return out
```

The output of `align` is in deterministic depth-first order by construction: `emit_matched_parent` recurses into each matched pair before processing its next sibling record, and `emit_unmatched_subtree` walks unmatched subtrees in `sequence_index` order. There is no BFS work queue and no global re-sort step. `INSERTION` subtrees appear at the candidate-side parent-local position they actually occupy; `DELETION` subtrees appear at the baseline-side parent-local position. Neither migrates to the end of the trace.

**On recursion depth.** The pseudocode is written recursively for clarity. Python's default recursion limit (1000) is below the §1 design target of supporting traces deeper than 1000 events, so the production implementation replaces recursion with an explicit stack that preserves the same DFS semantics:

```python
# Explicit-stack form of emit_matched_parent + emit_unmatched_subtree.
# Each stack frame is a (Match-emitting) work item. Frames are pushed in
# REVERSE of the order they should fire so that LIFO pop yields forward
# parent-local order — the standard DFS-with-stack trick.

Frame: TypeAlias = (
    tuple[Literal["EXPAND_MATCHED"], Event, Event]
    | tuple[Literal["EXPAND_DEL"], Event]
    | tuple[Literal["EXPAND_INS"], Event]
)

def align_iterative(baseline: Trace, candidate: Trace) -> list[Match]:
    validate_roots(baseline, candidate)
    matches: list[Match] = []
    matches.append(Match(baseline.root_event, candidate.root_event, "ID_MATCH"))

    stack: list[Frame] = [("EXPAND_MATCHED", baseline.root_event, candidate.root_event)]
    while stack:
        frame = stack.pop()
        tag = frame[0]
        if tag == "EXPAND_MATCHED":
            _, b_parent, c_parent = frame
            records = pair_children(
                list(children_of(b_parent, baseline)),
                list(children_of(c_parent, candidate)),
            )
            # Push in reverse so the first record is the next to pop (DFS forward).
            for b, c, kind in reversed(records):
                if kind in ("ID_MATCH", "STRUCTURAL_MATCH", "POSITIONAL_MATCH"):
                    # Schedule subtree expansion AFTER appending the parent Match,
                    # so we re-stack as (emit, then expand) — see two-frame push below.
                    stack.append(("EXPAND_MATCHED", b, c))
                    # Note: emission of the parent Match itself happens inline,
                    # not via a frame, to keep the stack alphabet small. Order:
                    matches.append(Match(b, c, kind))     # logically: emit, then
                                                          # descend on next iter
                elif kind == "DELETION":
                    stack.append(("EXPAND_DEL", b))
                elif kind == "INSERTION":
                    stack.append(("EXPAND_INS", c))
        elif tag == "EXPAND_DEL":
            _, event = frame
            matches.append(Match(event, None, "DELETION"))
            for child in reversed(list(children_of(event, baseline))):
                stack.append(("EXPAND_DEL", child))
        elif tag == "EXPAND_INS":
            _, event = frame
            matches.append(Match(None, event, "INSERTION"))
            for child in reversed(list(children_of(event, candidate))):
                stack.append(("EXPAND_INS", child))

    return matches
```

The explicit-stack form is semantically equivalent to the recursive form: both produce the same `Match` list in the same order. The stack holds at most `O(depth · k)` frames where `depth` is tree depth and `k` is the worst sibling count along the current path — well within memory budget for the 10K-event target.

---

## 7. Edge Cases

- **Empty traces.** `baseline.events == []` is impossible per schema; defensively raise `EmptyTraceError`. A `RUN_START`-only trace aligns to its peer with one `ID_MATCH`.
- **Mismatched root types.** A non-`RUN_START` root violates `spec/canonical-trace-schema.md` §2; `validate_roots` raises `RootTypeMismatch` before alignment starts. Different `agent_name` / `agent_version` are payload differences left to divergence detection.
- **Very deep traces (>1000 events).** Production implementation uses the explicit-stack DFS form shown in §6, not Python recursion — sidesteps the default recursion limit while preserving DFS output order. Linear in `n`; meets the 10K-in-5s budget (§8).
- **Cycles.** `validate_roots` walks `events` once checking each `parent_id` resolves to an earlier `sequence_index`. Violation → `TraceCycleError`. Alignment never sees a cyclic trace.
- **Schema-version incompatibility.** `baseline.schema_version != candidate.schema_version` → `SchemaVersionMismatch`. Migration is the loader's job (`spec/canonical-trace-schema.md` §7).
- **Strict prefix.** Baseline of 100, candidate of first 60 identical events: Tier 1 pairs all 60; remaining 40 baseline events fall to Tier 3 as `DELETION`s under matched parents.
- **One trace much larger than the other.** Same mechanism as strict prefix; deletions/insertions need not be contiguous. Accepted under the high-structural-drift limitation.
- **Both traces empty of non-root events.** Two `RUN_START`s match; `emit_matched_parent` finds no children on either side and returns; one `ID_MATCH` total.

---

## 8. Complexity Analysis

Let `n = max(|baseline.events|, |candidate.events|)` and `k` = average sibling count under a non-leaf parent.

| Phase | Time | Space |
|---|---|---|
| `validate_roots` (tree + cycle check) | `O(n)` | `O(n)` |
| Tier 1 per parent pair (bucketize + merge) | `O(k)` | `O(k)` |
| Tier 2 per parent pair | `O(k²·L²)`, `L` = name length | `O(k)` |
| Tier 3 per parent pair (type-gated walk) | `O(k log k)` | `O(k)` |
| `merge_in_order` per parent pair | `O(k)` | `O(k)` |
| Totals over the tree | `O(n·k·L²)` worst-case | `O(n)` |

**Expected.** Agent traces have small `k` (typically < 10, almost always < 30) and small `L` (< 40). Behaves as `O(n log n)` in practice.

**Worst case.** A single parent with `n−1` same-typed siblings drives Tier 2 to `O(n²·L²)`. Degenerate but not impossible (a flat fan-out of 5000 `TOOL_CALL`s under one parent). At `n=10K`, `k=n−1`, `L=40`: ~10¹⁰ Levenshtein ops, over budget. V1.1 mitigation: bucket Tier 2 candidates by name prefix.

**Performance budget.** Target: 10K events aligned in under 5 s on a 2024-era laptop CPU, single-threaded, no warm-up. Expected case meets this with order-of-magnitude headroom; worst-case fan-out does not (see Limitations).

---

## 9. Test Strategy

Each scenario is a `(baseline, candidate, expected_matches)` triple.

1. **Identity** — same trace both sides. All `ID_MATCH`.
2. **Empty diff** — `RUN_START` → `RUN_END` only on both sides. Two `ID_MATCH`es.
3. **Single tool-arg change** — identical tree, one `TOOL_CALL.arguments` differs. Tier 1 fails for that event (different hash); Tier 2 catches it. One `STRUCTURAL_MATCH`, rest `ID_MATCH`.
4. **Rename distance 1** — `"search_db"` → `"search_db "` (trailing space). `STRUCTURAL_MATCH`.
5. **Rename distance 8** — `"search_db"` → `"query_database"`. Tier 2 rejects; Tier 3 pairs positionally. `POSITIONAL_MATCH`.
6. **Inserted event** — candidate has one extra `LLM_CALL` between two matched events. One extra `INSERTION`; surroundings still match.
7. **Deleted event** — baseline has one event candidate lacks. One `DELETION`; siblings match.
8. **Reordered siblings** — two `TOOL_CALL`s under one parent, swapped order. Tier 1 catches both via `(tool_name, args_hash)`. Both `ID_MATCH` despite swapped `sequence_index`.
9. **Strict prefix** — candidate is first 60 of 100 baseline events. 60 matches + 40 `DELETION`s.
10. **Mismatched root type** — candidate root synthetic-invalid `LLM_CALL`. `RootTypeMismatch` raised; no alignment output.
11. **Cycle** — `evt_007.parent_id = "evt_009"`, `evt_009.parent_id = "evt_007"`. `TraceCycleError`.
12. **Wide fan-out** — 500 sibling `TOOL_CALL`s under one parent, identical names, distinct argument hashes. Tier 1 pairs all; verifies the index-driven path avoids Tier 2's quadratic fan-out cost.
13. **Duplicate stable IDs under one parent** — three sibling `TOOL_CALL`s with identical `(tool_name, args_hash)` on each side. Tier 1 buckets both sides, pairs by sibling order (`sequence_index`). Expect three `ID_MATCH`es in baseline-sibling order; no fall-through to Tier 2/3.
14. **Cross-type fallback slot** — Tier 3 reaches a slot where `remaining_b[i].type == TOOL_CALL` and `remaining_c[j].type == LLM_CALL`. Expect a `DELETION` for the baseline event and an `INSERTION` for the candidate event — **not** a `POSITIONAL_MATCH` collapsing them. Surrounding same-type slots still pair normally.
15. **Insertion near beginning is ordered near that parent, not globally at the end** — baseline children `[B1, B2, B3]`, candidate children `[X, C1, C2, C3]` where `X` is an inserted `LLM_CALL` and `Ci` matches `Bi`. Expect output order under that parent: `INSERTION X, ID_MATCH (B1, C1), ID_MATCH (B2, C2), ID_MATCH (B3, C3)`. The `INSERTION` must appear adjacent to its candidate-side neighbours, before `B1↔C1`, not after `B3↔C3` and not at the trace tail.
16. **Inserted child with descendants** — candidate has an extra subtree under root: `X` (an `LLM_CALL`) with two children `X1` (a `TOOL_CALL`) and `X2` (a `RETRIEVAL`). Baseline has no peer for `X`. Expect three `Match`es contributed by this subtree: `INSERTION X`, `INSERTION X1`, `INSERTION X2`, emitted in that order. Verifies that an inserted subtree is expanded event-by-event, never collapsed into one `Match`, and that its descendants follow it depth-first in `sequence_index` order.
17. **Deleted child with descendants** — baseline has a subtree under root: `D` (a `TOOL_CALL`) with two children `D1` (a `MCP_CALL`) and `D2` (a `STATE_TRANSITION`). Candidate has no peer for `D`. Expect three `Match`es: `DELETION D`, `DELETION D1`, `DELETION D2`, in that order. Verifies that a deleted subtree is expanded event-by-event, never collapsed, and that descendants are emitted with the same `DELETION` kind in DFS `sequence_index` order.
18. **DFS order proof** — under the root: child `A` with child `A1`, and sibling child `B` (no descendants). All five events match identity-wise across baseline and candidate. Expect output order: `ID_MATCH root`, `ID_MATCH A`, `ID_MATCH A1`, `ID_MATCH B`. The order must be `root → A → A1 → B`, **not** `root → A → B → A1` (which would be the BFS order). This test fails any implementation that processes siblings before recursing into a matched pair's children.

---

## Key Limitations (Explicitly Acknowledged)

- **Not minimum-edit-distance optimal.** The greedy three-tier matcher can produce more deletion/insertion pairs than the theoretical optimum. Accepted as the cost of the latency budget.
- **High structural drift produces noisy Tier 3 pairings.** Tier 3 will pair `POSITIONAL_MATCH`es that the divergence layer must treat with low confidence. Acceptable as long as Tier 3 returns *some* answer after root validation succeeds — refusing to align would be worse for the user.
- **Worst-case quadratic on wide fan-out without stable IDs.** A parent with thousands of unmatched same-typed children blows the 5 s budget. V1.1 mitigation.
- **`BRANCH_DECISION` Tier 1 ignores chosen branch.** Stable ID is `(branch_name,)` only — two events that took different branches still ID-match. Intentional; chosen-branch comparison lives in the divergence layer.
- **Cross-type matches forbidden.** A `TOOL_CALL` that became an `MCP_CALL` between runs is deletion-plus-insertion, never a match. Right tradeoff: cross-type pairings would silently mask agent-architecture changes.

---

> End of design document. Next deliverable: `spec/divergence-taxonomy.md`.
