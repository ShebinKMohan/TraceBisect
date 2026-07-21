# Studio PostgreSQL workspace storage

PostgreSQL mode is the first multi-instance persistence milestone for
TraceBisect Studio. It stores traces, comparison reports, regression cases, demo
metadata, managed workspace keys, browser sessions, human accounts, recovery
codes, team membership, manual invitations, and human sessions in one shared
database. It does **not** yet make the complete product a hosted multi-tenant
SaaS.

## Current boundary

Supported in PostgreSQL mode:

- multiple API processes sharing fresh core workspace data;
- workspace-scoped traces, reports, regression cases, and demo metadata;
- bounded connection pooling with startup connection and schema checks;
- serialized workspace capacity, retention, case-update, and demo-seed writes;
- digest-only managed workspace keys with expiry, roles, and immediate revocation;
- short-lived, digest-only HttpOnly browser sessions that cannot outlive a key;
- Argon2id human accounts, saved recovery codes, workspace team roles, manual
  invitation links, and revocable human sessions;
- first-admin bootstrap through the same `tracebisect studio keys` commands used
  for SQLite.

Still SQLite-only:

- the encrypted automatic invitation-email outbox, worker, and webhook history;
- the local `studio backup`, `verify`, and `restore` commands.

PostgreSQL accepts the human identity secret and exposes manual invitation links
once. It still fails startup if automatic email settings are enabled because the
encrypted delivery outbox has not migrated. Use SQLite when the current
automatic invitation-email experience is required.

## Configure the API

Install the Studio dependencies, generate a hashing secret, and store the secret
and database URL in the deployment secret manager. The bootstrap command uses
the URL from the environment so it does not need to appear as a command argument:

```bash
uv sync --extra studio

tracebisect studio keys generate-pepper
tracebisect studio identity generate-secret

export TRACEBISECT_STUDIO_STORAGE='postgres'
export TRACEBISECT_STUDIO_DATABASE_URL='postgresql://studio:secret@db.example/tracebisect'
export TRACEBISECT_STUDIO_AUTH_MODE='api-key'
export TRACEBISECT_STUDIO_API_KEY_PEPPER='paste-the-generated-value-here'
export TRACEBISECT_STUDIO_IDENTITY_SECRET='paste-the-generated-value-here'
export TRACEBISECT_STUDIO_ALLOWED_ORIGINS='https://studio.example.com'
export TRACEBISECT_STUDIO_METRICS_TOKEN='replace-with-a-dedicated-monitoring-token'

tracebisect studio keys create \
  --workspace team-a \
  --name 'First workspace admin' \
  --role admin \
  --expires-in-days 90

uvicorn tracebisect.studio.api:app --port 8000
```

Copy the printed key immediately; PostgreSQL stores only its HMAC-SHA256 digest.
Studio exchanges the key for a short-lived HttpOnly cookie, so the managed key
does not remain in browser storage. Use **Settings → Workspace access** for later
key creation and revocation. Use **Settings → People and invitations** to create
the first human admin link and share it through a private channel.

The API installs its versioned workspace and access tables under an advisory
transaction lock.
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

Treat a point-in-time restore as a credential rollback. Before reopening
traffic, generate and deploy a new `TRACEBISECT_STUDIO_API_KEY_PEPPER` and
`TRACEBISECT_STUDIO_IDENTITY_SECRET`, create a new admin key with the new pepper,
and retire both old secrets. This invalidates restored keys, browser sessions,
human sessions, pending invitation links, and recovery codes, including items
revoked after the restored timestamp. Password hashes remain verifiable, so
people can sign in again with their password after the rotation.

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

The live test creates random workspaces, proves fresh cross-pool reads, managed
key/session revocation, manual invitation acceptance, password sign-in,
saved-code recovery, human-session invalidation, and workspace isolation, then
removes those rows. A skipped live test is not evidence that a real provider
connection, TLS policy, backup, or restore has been verified.
