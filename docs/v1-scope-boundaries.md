# V1 Scope Boundaries — Governance Document

> Status: Governance (Phase 1 V1)
> Role: Single canonical scope reference cited by every Phase 2+ implementation prompt.
> Sources: `spec/production-spec.md` §8.0 / §8.1 / §8.2 / §8.3, plus the four Phase 1 design documents in `spec/`.

---

## The V1 Acceptance Criterion

Reproduced verbatim from `spec/production-spec.md` §8.0:

> Given two real `.tbtrace` files generated from the same agent scenario before and after a change, TraceBisect finds the first meaningful divergence and exports a pytest test that fails on the bad behavior.

Every V1 scope decision traces back to this criterion. Anything that does not directly contribute is V2 or skipped.

---

## V1 Must Ship

Reproduced verbatim from `spec/production-spec.md` §8.1:

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

---

## V2 (Deferred — Do Not Implement in V1)

Reproduced verbatim from `spec/production-spec.md` §8.2:

- Source-specific importers (Langfuse, Phoenix, MLflow native formats — most cases are covered by OTel ingestion)
- Remaining four divergence types (`changed_input`, `changed_output`, `retrieval_changed`, `state_changed`)
- True cross-commit bisection
- HTML report viewer
- GitHub Action
- `counterfactual` determinism mode
- `skip` side-effect mode
- MCP replay
- TypeScript/JavaScript adapter

---

## Skip Entirely (Forbidden — Will Not Implement)

Reproduced verbatim from `spec/production-spec.md` §8.3:

- Hosted SaaS dashboard
- Authentication, RBAC, billing
- Team collaboration features
- Prompt management
- Full evaluation platform
- Enterprise governance dashboard
- Ten framework adapters
- Any feature that would require ongoing infrastructure to maintain

### Additional V1 boundaries

These are **boundary clarifications** that refine §8.3 with detail derived from Phase 1 design work in `spec/`. They are not additions to §8.3; they sharpen its edges.

- **Single ingestion source.** OTel / OpenInference + the native demo recorder only. No Langfuse, Phoenix, or MLflow native adapters in V1.
- **Framework adapters.** OpenAI SDK patches and Anthropic SDK patches inside the demo recorder only. No LangChain, LlamaIndex, AutoGen, CrewAI, or any other agent-framework adapter.
- **Recorder scope.** SDK LLM calls and `@wrap_tool`-decorated functions only. No transparent agent-framework hooks; unwrapped HTTP / subprocess / DB side effects remain the user's responsibility (`spec/production-spec.md` §7).
- **Five V1 divergence types only.** `missing_event`, `extra_event`, `changed_tool_args`, `branch_changed`, `cost_regression`. The remaining four are V2 per §8.2.
- **`assert_aligned` locked surface.** Keyword-only signature, `cost_threshold=1.5` default, determinism mode constrained to `permissive`, `strict`, or `ci`. No other modes, no positional calls (`spec/divergence-taxonomy.md` §6; `spec/severity-rules.md` §4, §6).

---

## Decision Protocol

Any future Claude Code task or Phase 2+ implementation prompt that proposes adding functionality not listed in **V1 Must Ship** must stop and produce a written justification referencing this document. The maintainer explicitly approves any deviation in writing before any code is written. Prompts that conflict with this scope must be rewritten before implementation begins, not negotiated during it.

Drift is the primary risk to shipping V1 on time. The acceptance criterion is intentionally narrow because the alternative — agreeing to one more "easy" feature per week for six weeks — produces a V1 with zero killer features. This document exists to make scope drift visible at the prompt-construction layer, where it is cheapest to correct.

---

## Cross-References

- `spec/production-spec.md` — full specification, v1.3
- `spec/canonical-trace-schema.md` — Step 1.1 — trace data model
- `spec/alignment-algorithm.md` — Step 1.2 — tiered matching, DFS output order
- `spec/divergence-taxonomy.md` — Step 1.3 — five V1 divergence types, `ImpactAnalysis`
- `spec/severity-rules.md` — Step 1.4 — severity ladder, first-divergence rule, determinism mode interaction
