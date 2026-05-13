# Changelog

## 0.0.1a0

- Initial alpha scaffold.
- Provides `tracebisect --version` and `tracebisect demo`.
- Adds CI and trusted-publishing release workflow.
- Adds stub subcommands `tracebisect ingest`, `tracebisect record`,
  `tracebisect diff`, and `tracebisect export-pytest` with the locked CLI
  signatures from spec section 4.2; each exits with code 2 and an
  alpha-not-implemented message in 0.0.1a0.
- Adds the public `tracebisect.testing` API surface (`capture_trace`,
  `load_baseline`, `assert_aligned`) with the locked signatures from spec
  section 4.5; all three raise `NotImplementedError` in the alpha so
  generated regression tests can be written against stable shapes.
- Adds `src/tracebisect/py.typed` (PEP 561 marker) so the package signals
  that it ships type hints.
- Adds `python -m tracebisect` support via `src/tracebisect/__main__.py`.
- Implements canonical trace schema, JSONL read/write, checked-in `.tbtrace`
  fixtures, OTel/OpenInference JSON import, and the real `tracebisect ingest`
  command for V1 development.
- Implements the V1 alignment engine, divergence detection, terminal diff
  rendering, `tracebisect diff`, `tracebisect export-pytest`, and the public
  `tracebisect.testing` runtime (`capture_trace`, `load_baseline`,
  `assert_aligned`) for generated regression tests.
- Implements the minimal V1 `tracebisect record` command contract: run a
  scenario with `TRACEBISECT_OUTPUT`, enforce `stub` / `live` side-effect mode
  gating, validate the emitted trace, and write canonical JSONL.
