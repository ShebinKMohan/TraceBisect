# Changelog

## 0.0.1a0

- Initial alpha scaffold.
- Adds CI and trusted-publishing release workflow.
- Adds `src/tracebisect/py.typed` (PEP 561 marker) so the package signals
  that it ships type hints.
- Adds `python -m tracebisect` support via `src/tracebisect/__main__.py`.
- Implements canonical trace schema, JSONL read/write, checked-in `.tbtrace`
  fixtures, OTel/OpenInference JSON import, and the real `tracebisect ingest`
  command for V1 development.
- Implements the V1 alignment engine, divergence detection, terminal diff
  rendering, `tracebisect diff` with determinism modes, generated pytest
  export, and the public `tracebisect.testing` runtime
  (`capture_trace`, `load_baseline`, `assert_aligned`) for generated
  regression tests.
- Implements the minimal V1 `tracebisect record` command contract: run a
  scenario with `TRACEBISECT_OUTPUT`, enforce `stub` / `live` side-effect mode
  gating, validate the emitted trace, and write canonical JSONL.
- Adds the native Python recorder API (`record_trace`, `wrap_tool`, and
  `TraceRecorder.llm_call`) for demo/onboarding scenarios that need to emit
  canonical traces without hand-building schema dataclasses.
- Upgrades `tracebisect demo` from a static preview to an end-to-end
  refund-search walkthrough that writes demo traces, renders the real first
  divergence, and exports a runnable pytest regression test.
- Adds `examples/refund_agent.py`, the deterministic scenario used by README
  commands and generated-test smoke coverage.
- Refreshes the README quickstart and static docs page around the implemented
  V1 local CLI flow.
