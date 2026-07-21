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

For a protected multi-workspace API, generate a server-side hashing secret and
create an expiring workspace key. The create command initializes the database
when needed and shows the new key exactly once:

```bash
tracebisect studio keys generate-pepper
export TRACEBISECT_STUDIO_API_KEY_PEPPER='paste-the-generated-value-here'

tracebisect studio keys create \
  --database .tracebisect/studio.db \
  --workspace team-a \
  --name 'Team A browser' \
  --role editor \
  --expires-in-days 90

TRACEBISECT_STUDIO_STORAGE=sqlite \
TRACEBISECT_STUDIO_SQLITE_PATH=.tracebisect/studio.db \
TRACEBISECT_STUDIO_AUTH_MODE=api-key \
TRACEBISECT_STUDIO_ALLOWED_ORIGINS='https://studio.example.com' \
uvicorn tracebisect.studio.api:app --port 8000
```

Studio stores a peppered HMAC digest—not the plaintext key—and rejects expired
or revoked keys immediately. Use `tracebisect studio keys list` to review safe
metadata. For zero-downtime rotation, create a replacement, update the client,
then run `tracebisect studio keys revoke --key-id ...` for the old key.

Choose the smallest role that fits: `viewer` can inspect existing evidence,
`editor` can also upload, compare, save, and rerun guardrails, and `admin` is the
operator/owner role. Studio enforces the role on every API request and shows a
clear read-only banner for viewer sessions.

The bearer key—not a client-provided workspace header—selects the authorized
workspace. Studio keeps an accepted browser key in `sessionStorage`, so closing
the tab clears it. The older `TRACEBISECT_STUDIO_API_KEYS` JSON mapping remains
available for migration and local development, but `/api/health` reports it as
an environment credential source without managed expiry or revocation.

Studio also emits one secret-safe JSON audit event per API request. Every
response carries `X-Request-ID`; audit events record the normalized action,
status, latency, authentication outcome, and authorized workspace while omitting
keys, headers, request bodies, query values, filenames, and resource IDs. Set
`TRACEBISECT_STUDIO_AUDIT_LOG_ENABLED=false` only when another layer provides an
equivalent request audit trail.

### Back up and restore Studio data

No SQLite knowledge is required. Create a consistent snapshot—even while Studio
is running—then verify it before moving it to off-site storage:

```bash
tracebisect studio backup \
  --database .tracebisect/studio.db \
  --output backups/studio-2026-07-21.db

tracebisect studio verify \
  --backup backups/studio-2026-07-21.db
```

Recovery is deliberately non-destructive: it writes a new database and refuses
to replace any existing file. After restoring, point Studio at the new file and
restart the API:

```bash
tracebisect studio restore \
  --backup backups/studio-2026-07-21.db \
  --database .tracebisect/restored-studio.db

TRACEBISECT_STUDIO_STORAGE=sqlite \
TRACEBISECT_STUDIO_SQLITE_PATH=.tracebisect/restored-studio.db \
uvicorn tracebisect.studio.api:app --port 8000
```

Each command reports the verified workspace, trace, comparison, guardrail, and
managed-access-key counts plus a SHA-256 checksum. Production operators must
still schedule encrypted, off-site backups and practice recovery in their
deployment environment.

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
- `tracebisect studio backup` — creates a consistent durable-database snapshot.
- `tracebisect studio verify` — checks backup integrity and schema compatibility.
- `tracebisect studio restore` — restores into a new database without replacing
  current data.
- `tracebisect studio keys generate-pepper` — creates the server secret used to
  hash managed keys.
- `tracebisect studio keys create/list/revoke` — manages expiring workspace keys
  without persisting or redisplaying their plaintext values.

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

V1 Studio is still intentionally narrow: it has opt-in role-scoped workspace
API-key protection, but no managed user accounts, billing, browser-based team
membership administration, vendor-native direct importers, or git-history
bisection.

See [spec/production-spec.md](spec/production-spec.md) for the locked product
specification.
