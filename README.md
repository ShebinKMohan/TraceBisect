# TraceBisect

Git bisect for AI agent traces.

TraceBisect is a local-first command-line tool for comparing two AI agent traces,
finding the first meaningful behavioral divergence, and exporting a pytest
regression test so the failure does not return.

This repository is currently in active V1 implementation. The core local CLI
flow is implemented: ingest, record, diff, export-pytest, and the generated-test
runtime.

## Install

```bash
pip install tracebisect
```

## Quickstart

```bash
tracebisect demo
```

The demo writes a baseline trace, a candidate trace, a scenario script, and a
generated pytest regression test into a temporary directory. It then renders the
same first-divergence output that `tracebisect diff` produces.

## Commands

- `tracebisect --version` — prints the package version.
- `tracebisect demo` — runs the built-in refund-search demo end to end.
- `tracebisect ingest` — converts OTel/OpenInference JSON or native `.tbtrace`
  input into canonical `.tbtrace` JSONL.
- `tracebisect record` — runs a scenario command with `TRACEBISECT_OUTPUT`
  set and validates the emitted `.tbtrace` file.
- `tracebisect diff` — aligns two canonical traces and renders the first
  meaningful divergence. The CLI default determinism mode is `permissive`;
  pass `--mode strict` or `--mode ci` when you need those comparison modes.
- `tracebisect export-pytest` — writes a live-capture pytest regression test
  using the public `tracebisect.testing` runtime API.

Example static comparison:

```bash
tracebisect diff baseline.tbtrace candidate.tbtrace
```

Example regression-test export:

```bash
tracebisect export-pytest baseline.tbtrace tests/test_refund_regression.py \
  --scenario "python examples/refund_agent.py --case refund_042" \
  --assert tool_args,final_output,cost
```

In a repository checkout, `examples/refund_agent.py` is a deterministic
scenario script that writes a canonical trace to `TRACEBISECT_OUTPUT`. Use
`--variant regressed` to emit the candidate trace with the tool-argument drift
shown by `tracebisect demo`.

## Native recorder

For small Python scenarios, use the built-in native recorder directly:

```python
from tracebisect import record_trace, wrap_tool


@wrap_tool
def search_database(query: str) -> list[dict[str, str]]:
    return [{"name": "alice"}]


with record_trace(user_input="Find refund users.") as recorder:
    with recorder.llm_call(
        model="gpt-4o-mini",
        provider="openai",
        messages=[{"role": "user", "content": "Find refund users."}],
        response_tool_calls=[
            {"name": "search_database", "arguments": {"query": "users WHERE active = true"}}
        ],
    ):
        rows = search_database(query="users WHERE active = true")

    recorder.finish(final_output=f"Found {len(rows)} users.")
```

When the script runs under `tracebisect record` or a generated pytest test,
TraceBisect supplies `TRACEBISECT_OUTPUT`; the recorder writes the canonical
`.tbtrace` there automatically.

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
