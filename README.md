# TraceBisect

Git bisect for AI agent traces.

TraceBisect is a local-first command-line tool for comparing two AI agent traces,
finding the first meaningful behavioral divergence, and exporting a pytest
regression test so the failure does not return.

This repository is currently in active V1 implementation.

## Install

```bash
pip install tracebisect
```

## Current Alpha Commands

- `tracebisect --version` — prints the package version.
- `tracebisect demo` — prints a static preview of the v1.3 money-shot output.
- `tracebisect ingest` — converts OTel/OpenInference JSON or native `.tbtrace`
  input into canonical `.tbtrace` JSONL.
- `tracebisect diff` — aligns two canonical traces and renders the first
  meaningful divergence.
- `tracebisect export-pytest` — writes a live-capture pytest regression test
  using the public `tracebisect.testing` runtime API.
- `tracebisect record` — runs a scenario command with `TRACEBISECT_OUTPUT`
  set and validates the emitted `.tbtrace` file.

## V1 Scope

V1 will ship:

- OpenTelemetry/OpenInference and native `.tbtrace` ingestion
- trace alignment
- first-divergence detection
- terminal diff rendering
- pytest regression-test export with fresh CI capture

V1 will not ship a dashboard, SaaS service, vendor-native importers, or git
history bisection.

See [spec/production-spec.md](spec/production-spec.md) for the locked product
specification.
