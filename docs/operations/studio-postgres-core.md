# Studio PostgreSQL core storage

PostgreSQL mode is the first multi-instance persistence milestone for
TraceBisect Studio. It stores traces, comparison reports, regression cases, and
demo metadata in one shared database. It does **not** yet make the complete
product a hosted multi-tenant SaaS.

## Current boundary

Supported in PostgreSQL mode:

- multiple API processes sharing fresh core workspace data;
- workspace-scoped traces, reports, regression cases, and demo metadata;
- bounded connection pooling with startup connection and schema checks;
- serialized workspace capacity, retention, case-update, and demo-seed writes;
- static workspace keys supplied through `TRACEBISECT_STUDIO_API_KEYS`.

Still SQLite-only:

- managed/hashed API-key issuance and revocation;
- HttpOnly browser sessions;
- human accounts, recovery codes, team membership, and invitations;
- the encrypted invitation-email outbox, worker, and webhook history;
- the local `studio backup`, `verify`, and `restore` commands.

Studio fails startup if managed identity or email settings are combined with
PostgreSQL. Do not expose an unauthenticated PostgreSQL deployment to the public
internet. Until the security repositories migrate together, protect it with
static environment keys behind a private gateway or use SQLite for the complete
single-node product.

## Configure the API

Install the Studio dependencies, then store the database URL and API key in the
deployment secret manager:

```bash
uv sync --extra studio

export TRACEBISECT_STUDIO_STORAGE='postgres'
export TRACEBISECT_STUDIO_DATABASE_URL='postgresql://studio:secret@db.example/tracebisect'
export TRACEBISECT_STUDIO_AUTH_MODE='api-key'
export TRACEBISECT_STUDIO_API_KEYS='{"replace-with-a-long-random-workspace-key":"team-a"}'
export TRACEBISECT_STUDIO_ALLOWED_ORIGINS='https://studio.example.com'
export TRACEBISECT_STUDIO_METRICS_TOKEN='replace-with-a-dedicated-monitoring-token'

uvicorn tracebisect.studio.api:app --port 8000
```

The API installs its versioned core tables under an advisory transaction lock.
Startup fails if the pool cannot connect, the schema cannot be installed, or a
newer unsupported schema is present. `/api/ready` returns `503` when the database
is unavailable; `/api/health` remains readable and reports the failed storage
check without exposing the database URL or workspace counts.

## Database role and network

Use a dedicated, non-superuser application role. Grant it network access only
from the API runtime and ownership or the minimum create/read/write privileges
needed for the `studio_*` tables and indexes. Do not reuse an administrator,
migration-owner, or human console credential as the application URL.

Require TLS according to the provider's connection-string guidance. Keep the
database URL out of source control, logs, shell history, build arguments, and
frontend environment variables. Rotate it through the provider and deployment
secret manager.

## Size the pool

Defaults are one minimum and ten maximum connections per API process:

```bash
export TRACEBISECT_STUDIO_POSTGRES_POOL_MIN='1'
export TRACEBISECT_STUDIO_POSTGRES_POOL_MAX='10'
export TRACEBISECT_STUDIO_POSTGRES_POOL_TIMEOUT_SECONDS='5'
export TRACEBISECT_STUDIO_POSTGRES_POOL_MAX_WAITING='32'
export TRACEBISECT_STUDIO_POSTGRES_CONNECT_TIMEOUT_SECONDS='5'
export TRACEBISECT_STUDIO_POSTGRES_STATEMENT_TIMEOUT_SECONDS='30'
export TRACEBISECT_STUDIO_POSTGRES_IDLE_TRANSACTION_TIMEOUT_SECONDS='30'
```

Budget the maximum across every API replica, deployment revision, worker, and
administrative connection. That total must stay below the provider limit with
room for recovery and migrations. Server-side prepared statements are disabled
so the client remains compatible with transaction-mode poolers; verify any
provider-specific pooler and TLS settings before rollout.

## Backup and migration

Enable provider-managed encrypted backups and point-in-time recovery, define
retention, and perform a restore drill into an isolated database before launch.
The SQLite backup CLI does not operate on PostgreSQL.

There is no automatic SQLite-to-PostgreSQL migration in this milestone. Do not
point a production deployment at an empty PostgreSQL database and assume its
SQLite data moved. Export/import tooling and reconciliation checks remain a
separate production gate.

## Verify before rollout

Run the contract suite on every change. When an isolated disposable PostgreSQL
database is available, also run the opt-in live parity test:

```bash
pytest -q tests/test_studio_postgres_storage.py

TRACEBISECT_TEST_POSTGRES_URL='postgresql://...' \
pytest -q tests/test_studio_postgres_storage.py \
  -k live_postgres_store_shares_fresh_data_and_isolates_workspaces
```

The live test creates random workspaces, proves fresh cross-pool reads and
workspace isolation, and removes those rows afterward. A skipped live test is
not evidence that a real provider connection, TLS policy, backup, or restore has
been verified.
