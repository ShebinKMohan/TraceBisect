# Changelog

## Unreleased

- Fixes a false negative where a tool call replaced by a different tool with
  identical arguments (for example `lookup_order` becoming `cancel_order`)
  reported no divergence. When alignment pairs two tool calls,
  `changed_tool_args` now compares tool identity (`tool_name`, plus
  `server_name` for MCP calls) before arguments, so `tracebisect diff`, Studio,
  and generated `tool_args` regression tests catch a swap at an aligned step.
  Names are compared after removing invisible format characters, NFC
  normalization, and trimming, and are otherwise case-sensitive. Placeholder
  names match any name: `unknown_tool` when a tool span has neither
  `tool.name` nor a span name, and MCP server `unknown` when
  `mcp.server.name` is absent.
- Alignment no longer lets a shared raw event id, such as the native
  recorder's position-based `evt_NNN`, pair two different sibling tool calls
  when either tool still appears among the other run's siblings. Reordering
  distinctly named sibling tool calls now reports no divergence, and inserting
  or removing one before later siblings reports that single extra or missing
  step. Tool calls that move under a different parent step, such as another
  model turn, are still compared step by step, and same-name calls with
  different arguments still pair by sibling order.
- Studio's comparison detail explains a swap as a replaced tool, including
  tool labels that contain spaces. Run lists and issue groups still label it
  "Tool arguments changed".

## 0.1.0

- Adds TraceBisect Studio MVP: a FastAPI backend and Next.js 16 dashboard that
  wrap the existing trace engine with a visual first-divergence report, trace
  upload, compare action, and generated pytest preview.
- Implements canonical trace schema, JSONL read/write, checked-in `.tbtrace`
  fixtures, OTel/OpenInference JSON import, and the real `tracebisect ingest`
  command.
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

## 0.0.1a0

- Initial alpha scaffold.
- Adds CI and trusted-publishing release workflow.
- Adds `src/tracebisect/py.typed` (PEP 561 marker) so the package signals
  that it ships type hints.
- Adds `python -m tracebisect` support via `src/tracebisect/__main__.py`.
