# TraceBisect

Git bisect for AI agent traces.

TraceBisect is a regression-debugging platform for AI agents. The Python CLI is
the core engine: it compares two agent traces, finds the first meaningful
behavioral divergence, and exports a pytest regression test so the failure does
not return. TraceBisect Studio is the web dashboard around that engine.

The implemented V1 flow includes ingest, record, diff, export-pytest, the
generated-test runtime, and a first SaaS-style Studio dashboard slice.

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

## TraceBisect Studio

Studio is the portfolio-facing web product. It wraps the existing Python engine
with a FastAPI backend and a Next.js 16 dashboard so developers can upload two
traces, compare them visually, inspect the first divergence, and copy a
generated pytest regression test.

Run the API:

```bash
uvicorn tracebisect.studio.api:app --reload --port 8000
```

Run the web dashboard:

```bash
cd studio/web
npm install
npm run dev
```

Then open <http://127.0.0.1:3000>. The seeded refund-agent report loads from
the same engine used by `tracebisect demo`. This zero-configuration mode keeps
data in memory, and the UI labels that reset behavior directly.

To keep traces, comparisons, and guardrails across API restarts, enable the
built-in workspace-scoped SQLite store:

```bash
TRACEBISECT_STUDIO_STORAGE=sqlite \
TRACEBISECT_STUDIO_SQLITE_PATH=.tracebisect/studio.db \
uvicorn tracebisect.studio.api:app --port 8000
```

Set `TRACEBISECT_STUDIO_WORKSPACE_ID` when one database file is shared by
multiple isolated local workspaces. `/api/health` reports the active storage
mode and honest SaaS-readiness blockers; `/api/ready` is the process/storage
readiness probe.

For a protected multi-workspace API, map long bearer keys to workspace IDs.
Studio validates the key before opening the dashboard and keeps it in browser
`sessionStorage`, so closing the tab clears it:

```bash
TRACEBISECT_STUDIO_STORAGE=sqlite \
TRACEBISECT_STUDIO_SQLITE_PATH=.tracebisect/studio.db \
TRACEBISECT_STUDIO_AUTH_MODE=api-key \
TRACEBISECT_STUDIO_API_KEYS='{"replace-with-a-random-key-at-least-32-characters":"team-a"}' \
TRACEBISECT_STUDIO_ALLOWED_ORIGINS='https://studio.example.com' \
uvicorn tracebisect.studio.api:app --port 8000
```

The bearer key—not a client-provided workspace header—selects the authorized
workspace. This is a secured self-hosted foundation, not managed user accounts,
self-service key rotation, or team RBAC.

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
- Studio web dashboard for upload, compare, visual report, and pytest copy flow

V1 Studio is still intentionally narrow: it has opt-in workspace API-key
protection, but no managed user accounts, billing, team RBAC, vendor-native
direct importers, or git-history bisection.

See [spec/production-spec.md](spec/production-spec.md) for the locked product
specification.
