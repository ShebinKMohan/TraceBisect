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

tracebisect studio identity generate-secret
export TRACEBISECT_STUDIO_IDENTITY_SECRET='paste-the-generated-value-here'

tracebisect studio keys create \
  --database .tracebisect/studio.db \
  --workspace team-a \
  --name 'Team A browser' \
  --role editor \
  --expires-in-days 90

tracebisect studio metrics generate-token
export TRACEBISECT_STUDIO_METRICS_TOKEN='paste-the-generated-value-here'

TRACEBISECT_STUDIO_STORAGE=sqlite \
TRACEBISECT_STUDIO_SQLITE_PATH=.tracebisect/studio.db \
TRACEBISECT_STUDIO_AUTH_MODE=api-key \
TRACEBISECT_STUDIO_ALLOWED_ORIGINS='https://studio.example.com' \
uvicorn tracebisect.studio.api:app --port 8000
```

Studio stores a peppered HMAC digest—not the plaintext key—and rejects expired
or revoked keys immediately. After the first admin key opens Studio, admins can
create, review, rotate, and revoke workspace access directly in **Settings →
Workspace access**. The CLI remains the bootstrap and recovery path.

When the browser opens a managed workspace, it sends that key only to the
session-exchange endpoint. The API replaces it with an opaque, HttpOnly,
SameSite cookie, stores only a peppered session digest, and expires the session
after eight hours by default. Locking the workspace revokes that session;
revoking the source key invalidates every session created from it immediately.
The session never enters browser JavaScript or `localStorage`/`sessionStorage`.
See [`docs/operations/studio-browser-sessions.md`](docs/operations/studio-browser-sessions.md)
for the production HTTPS, origin, expiry, and sign-out contract.

Choose the smallest role that fits: `viewer` can inspect existing evidence,
`editor` can also upload, compare, save, and rerun guardrails, and `admin` can
manage workspace access. Studio enforces the role on every API request and shows
a clear read-only banner for viewer sessions. New keys are shown once; Studio
protects the key behind the current sign-in until the admin signs in with a
replacement. See
[`docs/operations/studio-access-management.md`](docs/operations/studio-access-management.md)
for the beginner workflow and recovery boundary.

With the dedicated identity secret configured, admins can invite people from
**Settings → People and invitations**. A new person creates a 15–128 character
password, saves eight one-time recovery codes, and then signs in with email and
password. Passwords use Argon2id; invitation tokens, recovery codes, and human
sessions are stored only as keyed digests. Multi-workspace accounts choose a
workspace only after password verification, and role/removal changes affect
active sessions immediately. Optional Resend delivery encrypts the complete
message payload in a durable outbox, hides the secret link from the API response,
and retries temporary failures with a stable idempotency key. Without email
configuration, Studio shows the private link once for manual sharing. See
[`docs/operations/studio-accounts.md`](docs/operations/studio-accounts.md) for
account setup and
[`docs/operations/studio-email-delivery.md`](docs/operations/studio-email-delivery.md)
for sender configuration, worker scheduling, and the current delivery boundary.

The bearer key or its derived browser session—not a client-provided workspace
header—selects the authorized workspace. The older
`TRACEBISECT_STUDIO_API_KEYS` JSON mapping remains available for migration and
local development; that legacy mode keeps the key in tab-scoped
`sessionStorage` and `/api/health` reports that managed expiry, revocation, and
browser sessions are unavailable.

Studio also emits one secret-safe JSON audit event per API request. Every
response carries `X-Request-ID`; audit events record the normalized action,
status, latency, authentication outcome, and authorized workspace while omitting
keys, headers, request bodies, query values, filenames, and resource IDs. Set
`TRACEBISECT_STUDIO_AUDIT_LOG_ENABLED=false` only when another layer provides an
equivalent request audit trail.

Unexpected server failures return a safe support message with that same request
ID and emit a separate `studio.server_error` JSON event. Error events contain a
bounded error type, code location, and stable fingerprint, but never the raw
exception message, request content, credential, uploaded filename, or database
path. See
[`docs/operations/studio-error-events.md`](docs/operations/studio-error-events.md)
for the event contract and first-response workflow. Set
`TRACEBISECT_STUDIO_ERROR_LOG_ENABLED=false` only when an equivalent safe error
reporter is installed. The hosting platform still owns retention, search,
delivery, and access control for both event streams.

`/api/metrics` exposes Prometheus text metrics for storage readiness, request
volume, result class, latency, authentication outcomes, in-flight work, and
process uptime. It never
uses workspace, user, resource, filename, or request identifiers as labels. In
protected mode, a dedicated `TRACEBISECT_STUDIO_METRICS_TOKEN` is required; do
not give a monitoring scraper a workspace key:

```bash
curl \
  --header "Authorization: Bearer $TRACEBISECT_STUDIO_METRICS_TOKEN" \
  http://127.0.0.1:8000/api/metrics
```

Open-local mode permits local scrapes without a token. Protected mode returns a
clear unavailable response until the dedicated token is configured. Metrics are
per API process and reset when that process restarts; the monitoring system owns
retention and multi-instance aggregation.

Production-ready starter alerts live in
[`deploy/prometheus/tracebisect-alerts.yml`](deploy/prometheus/tracebisect-alerts.yml).
They cover reachability, storage, server errors, latency, rate limiting, and
authentication failures without putting customer identifiers into labels. The
plain-English response steps and provisional service objectives are in
[`docs/operations/studio-alert-runbook.md`](docs/operations/studio-alert-runbook.md).
Validate both the rules and your deployment configuration before reloading
Prometheus:

```bash
promtool check rules deploy/prometheus/tracebisect-alerts.yml
promtool check config /etc/prometheus/prometheus.yml
```

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

Backups intentionally remove key-derived sessions, human identity sessions, and
the email outbox before they are published. A restored workspace keeps data, managed-key metadata,
Argon2id users, memberships, invitation hashes, and recovery-code hashes, but
requires every browser to sign in again. Restoring an older backup also restores
older credential state, so production recovery must include a security review
and credential rotation decision.

Each command reports the verified workspace, trace, comparison, guardrail,
managed-access-key, person, and membership counts plus a SHA-256 checksum.
Production operators must
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
- `tracebisect studio identity generate-secret` — creates the dedicated server
  secret for invitation-only human accounts, recovery, and sessions.
- `tracebisect studio email deliver` — sends a bounded batch from the encrypted
  invitation outbox and records safe retry/failure state.

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
keys plus invitation-only human accounts, saved-code recovery, browser team
membership administration, and optional encrypted Resend invitation delivery.
It does not yet include delivery/bounce webhooks, multi-factor or
identity-provider sign-in, billing, vendor-native direct importers, or
git-history bisection.

See [spec/production-spec.md](spec/production-spec.md) for the locked product
specification.
