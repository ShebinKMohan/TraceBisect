# Severity Rules — Design Document

> Status: Design (Phase 1 V1)
> Companion to: `spec/production-spec.md` §5.4, §6
> Builds on: `spec/divergence-taxonomy.md` §3, §5; `spec/alignment-algorithm.md` §1
> Implementation target: `src/tracebisect/severity.py`, `src/tracebisect/diff.py` (not yet written)

---

## 1. The Severity Ladder

Severity is a fixed five-level ordinal — `CRITICAL > HIGH > MEDIUM > LOW > INFO` — assigned to every `Divergence` by `detect_divergences`. Definitions paraphrase `spec/production-spec.md` §5.4.

**CRITICAL.** Changes the run's outcome or violates a correctness contract. Spec triggers: `branch_changed`, `changed_tool_args` when the result differs, `missing_event` of `RUN_END`, error introduced where none existed. V1 paths: every `changed_tool_args` (stays CRITICAL regardless of result drift — §2), every `branch_changed`, the `RUN_END` escalation of `missing_event`, and the new-`ERROR` escalation of `extra_event`.

**HIGH.** Likely affects outcome; downstream impact uncertain at classification time. Spec triggers: `missing_event` / `extra_event` of `LLM_CALL` / `TOOL_CALL`, `retrieval_changed`, `state_changed`. V1 paths: `missing_event` and `extra_event` defaults. `retrieval_changed` and `state_changed` are V2.

**MEDIUM.** Affects metrics but not necessarily correctness. Spec triggers: `cost_regression` greater than 20 %, `changed_input` to `LLM_CALL` where output is semantically equivalent. V1 paths: `cost_regression` only, once the cost ratio exceeds the fixed 20 % severity threshold (§4). `changed_input` is V2.

**LOW.** Spec triggers: token-level LLM-response differences with identical structured parse, latency, timestamp drift.

**INFO.** Spec triggers: model version or code SHA changes (each *unless* behaviour also diverged).

**V1 resolution of LOW / INFO.** **V1 emits neither.** LOW triggers need `changed_output` (V2) or a latency/timestamp metadata-drift detector V1 does not ship. INFO triggers are *drift signals* over run metadata, not structural divergences; `permissive` mode records them in the rendered header instead. Both literals are preserved in the `Severity` alias (`spec/divergence-taxonomy.md` §2) so a future metadata-drift detector can begin producing them without a schema break — but in V1, `detect_divergences` only ever produces CRITICAL, HIGH, or MEDIUM.

---

## 2. Per-Type Classification Rules (V1)

For each V1 divergence type from `spec/divergence-taxonomy.md` §5. Rules are deterministic; classification never consults assertion selection, wall-clock, or randomness.

**`missing_event` — default `HIGH`.** *Escalate to `CRITICAL`* iff `baseline_event.type == "RUN_END"` (`spec/divergence-taxonomy.md` §3.1). *De-escalation:* none in V1 — a missing `STATE_TRANSITION` stays HIGH even when no assertion targets it; assertion dimensions gate test pass/fail (§6), not severity.

**`extra_event` — default `HIGH`.** *Escalate to `CRITICAL`* iff `candidate_event.type == "ERROR"` with no peer baseline `ERROR` at the same parent-local position (`spec/divergence-taxonomy.md` §3.2). *De-escalation:* none in V1.

**`changed_tool_args` — default `CRITICAL`.** No escalation path (top of ladder). **No de-escalation in V1**: per `spec/divergence-taxonomy.md` §3.5, args drift is itself the regression signal, so severity stays CRITICAL even when `result` is unchanged. The `changed_input` ↔ `changed_output` equivalence relaxation in `spec/production-spec.md` §5.4 applies only to those V2 types.

**`branch_changed` — default `CRITICAL`.** No escalation. No de-escalation in V1 — comparison is exact string equality on `BranchDecisionPayload.chosen_branch`; there is no "semantically equivalent branch" concept.

**`cost_regression` — default `MEDIUM`.** No escalation; even large multipliers stay MEDIUM (cost has no CRITICAL path). No de-escalation: MEDIUM fires only when `cost_delta_ratio > 1.20` (§4); at or below 1.20 no divergence is emitted.

**V2 divergence types — deferred.** `changed_input`, `changed_output`, `retrieval_changed`, `state_changed` are not classified by V1 rules; their severity is the V2 detector's responsibility. V1 baselines never contain those values (`spec/divergence-taxonomy.md` §2).

---

## 3. The First-Divergence Surfacing Rule

`tracebisect diff` surfaces exactly one event — the **first divergence** — as the headline (`spec/production-spec.md` §11).

> The first divergence is the earliest event, in `Match`-list order, carrying a `Divergence` of severity `≥ HIGH`. At the same event index, `CRITICAL` wins over `HIGH`.

"Earliest" is defined by `spec/alignment-algorithm.md` §1's *DFS output order* invariant: `Match`es appear in deterministic depth-first order; matched child pairs are followed immediately by their subtree before any sibling's `Match`es; `INSERTION` / `DELETION` land at their parent-local position. Because that ordering is byte-stable (same §1's *Determinism* invariant), the first-divergence selection is byte-stable too. Co-located divergences (e.g. `changed_tool_args` plus a per-event `cost_regression` on the same `Match`) break ties inside one event index: CRITICAL precedes HIGH; equal severities break by detector name lexicographically. MEDIUM and below are never eligible to be the headline.

**Why one, not all.** TraceBisect is bisect-style debugging (`spec/production-spec.md` §2.4): misbehaviour cascades from a single earliest divergence, and surfacing only that point matches the user's `git bisect` mental model. Rendering every downstream difference pushes the actionable line off-screen, raises cognitive load, and rewards fixing symptoms instead of the root cause. The full list is still available in the JSON output for tooling.

---

## 4. Configuration Knobs

The severity layer exposes two thresholds. They are **distinct concepts** and must not be conflated.

**`cost_regression` ASSERTION threshold (`cost_threshold`).** Default `1.5`. **User-tunable in V1**, via `tracebisect export-pytest --cost-threshold <float>` and the `assert_aligned(cost_threshold=<float>)` keyword (`spec/divergence-taxonomy.md` §6). *Controls:* whether `assert_aligned` raises `AssertionError` when `assertions=["cost"]` is selected. Test pass / fail only.

**`cost_regression` SEVERITY threshold.** Fixed at `> 20 %` (i.e. `cost_delta_ratio > 1.20`), per `spec/production-spec.md` §5.4. **Not user-tunable in V1** — no CLI flag, no `assert_aligned` parameter. *Controls:* whether `detect_divergences` emits a MEDIUM `cost_regression` for rendering. Visual prominence only; not test pass / fail.

The two are independent. At `cost_delta_ratio = 1.30`, `tracebisect diff` renders a MEDIUM `cost_regression` but an exported test with the default `cost_threshold=1.5` does **not** fail. Symmetrically, a user passing `cost_threshold=1.05` fails the test at `1.10` even though no `cost_regression` divergence was emitted.

**V2 only:** semantic-equivalence tolerance for output diffs (needed for the V2 `changed_input` / `changed_output` MEDIUM-vs-HIGH split); per-tool ignore lists. Neither is configurable in V1 because the detectors do not exist.

---

## 5. Worked Examples

**A — Refund agent, tool args drift.** A refund agent calls `process_refund(order_id, amount_cents)`; baseline `amount_cents=4999`, candidate (after a rounding-logic refactor) `amount_cents=5000`. Alignment pairs the `TOOL_CALL` events via Tier 2 `STRUCTURAL_MATCH`. `detect_divergences` emits `changed_tool_args` at **CRITICAL**. This *is* the first divergence and is rendered as the headline.

**B — Support bot, missing retrieval.** A support bot in baseline calls `search_kb(...)` before drafting; candidate (post prompt rewrite) skips it. The baseline `TOOL_CALL` becomes `DELETION` → `missing_event` at **HIGH** (run still completes, so no `RUN_END` escalation). First divergence in DFS order.

**C — Data-analysis agent, crash mid-run.** Baseline reaches `RUN_END`; candidate aborts on an unhandled `KeyError`. Baseline's `RUN_END` becomes `DELETION` → `missing_event` escalated to **CRITICAL**. The candidate's new `ERROR` has no baseline peer → `extra_event` escalated to **CRITICAL**. Earliest in DFS order — usually the `extra_event` — is the first divergence.

**D — Routing agent, branch flipped.** A `BRANCH_DECISION` (`branch_name="route_ticket"`) chose `auto_resolve` in baseline, `tier2_specialist` in candidate. Alignment pairs by `(branch_name,)`; `branch_changed` is emitted at **CRITICAL**. First divergence; the subtree under `tier2_specialist` is cascade context.

**E — Operations agent, cost regression only.** An incident-summarising agent switched to a larger model. Trace shape identical end-to-end; total cost rose from `$0.00021` to `$0.00034` (`cost_delta_ratio ≈ 1.62`). One trace-level `cost_regression` at **MEDIUM** (above the 20 % severity threshold). No divergence has `severity ≥ HIGH`, so **no first-divergence headline** is rendered. An exported test with `assertions=["cost"], cost_threshold=1.5` still *fails*.

---

## 6. Determinism Mode Interaction

Severity classification is **identical across all three modes** (`spec/production-spec.md` §6). Modes change what surrounds classification, not the rules.

**`permissive` (CLI default).** Comparison proceeds despite code SHA, prompt, and model drift; drift is recorded as run-level metadata in the rendered header. V1 does not emit INFO `Divergence` objects for that drift (§1).

**`strict`.** Severity classification is identical to permissive. The difference is structural: `strict` raises typed validation errors *before* `detect_divergences` runs when the comparison would be meaningless — incompatible trace schemas, missing required event types, root-shape incompatibility, or any of the optional `--require-same-{code,prompt,model}` flags.

**`ci` (exported pytest default).** Pass / fail is determined by the **selected assertion dimensions** in `assertions=[...]` (`spec/divergence-taxonomy.md` §6) and by structural validity required for those dimensions (e.g. a missing `RUN_END` blocks a `final_output` check). Severity appears in failure messages and rendered diffs — it is **not** a blanket "every HIGH or CRITICAL divergence fails the test" rule. A run can contain a CRITICAL `branch_changed` and still pass an exported test that selected only `assertions=["cost"]`. Symmetrically, a MEDIUM `cost_regression` can fail a test whose `cost_threshold` it breaches. Severity ranks divergences for humans reading `tracebisect diff`; assertions decide what CI fails on.

---

> End of design document.
