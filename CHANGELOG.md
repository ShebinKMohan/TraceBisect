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

