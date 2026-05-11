# TraceBisect

Git bisect for AI agent traces.

TraceBisect is a local-first command-line tool for comparing two AI agent traces,
finding the first meaningful behavioral divergence, and exporting a pytest
regression test so the failure does not return.

This repository is currently in alpha scaffold state.

## Install

```bash
pip install tracebisect
```

## Current Alpha Commands

- `tracebisect --version` — prints the package version.
- `tracebisect demo` — prints a static preview of the v1.3 money-shot output.
- `tracebisect ingest`, `tracebisect record`, `tracebisect diff`,
  `tracebisect export-pytest` — alpha stubs. They parse the locked CLI
  signatures from spec section 4.2 but exit with code 2 and an
  "alpha — not implemented yet" message until V1 lands.

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

