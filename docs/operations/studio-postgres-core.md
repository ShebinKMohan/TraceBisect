# Studio PostgreSQL workspace storage

PostgreSQL mode is the first multi-instance persistence milestone for
TraceBisect Studio. It stores traces, comparison reports, regression cases, demo
metadata, managed workspace keys, browser sessions, human accounts, recovery
codes, team membership, invitations, the encrypted email outbox, delivery
webhook history, human sessions, and short-lived request-limit buckets in one
shared database. It does **not** yet make the complete product a hosted
multi-tenant SaaS.

## Current boundary

Supported in PostgreSQL mode:

- multiple API processes sharing fresh core workspace data;
- workspace-scoped traces, reports, regression cases, and demo metadata;
- bounded connection pooling with startup connection and schema checks;
- serialized workspace capacity, retention, case-update, and demo-seed writes;
- exact sliding-window request limits shared by every API process, with only
  SHA-256 client-bucket identifiers stored, PostgreSQL as the shared clock, and
  expired rows removed in bounded in-band batches; request paths are normalized
  to fixed actions so random paths cannot evade limits or create unbounded rows;
- digest-only managed workspace keys with expiry, roles, and immediate revocation;
- short-lived, digest-only HttpOnly browser sessions that cannot outlive a key;
- Argon2id human accounts, saved recovery codes, workspace team roles, manual
  invitation links, and revocable human sessions;
- encrypted invitation-message payloads, bounded delivery retries, reclaimable
  worker leases, stable provider idempotency keys, and signed webhook
  reconciliation shared across API and worker processes;
- first-admin bootstrap through the same `tracebisect studio keys` commands used
  for SQLite.

Still SQLite-specific:

- the local `studio backup`, `verify`, and `restore` commands.

PostgreSQL accepts the same human-identity and optional Resend settings as
SQLite. The API and every supervised email worker must point at the same
database and keep the identity secret stable so queued ciphertext remains
decryptable.

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
the first human admin link. Configure Resend when Studio should send that link
without returning it to the browser.

## Run invitation delivery

After setting the Resend variables from
[studio-email-delivery.md](studio-email-delivery.md), run the worker with the
same PostgreSQL environment. Omit `--database`; that flag selects a local SQLite
file.

```bash
tracebisect studio email deliver --limit 20
tracebisect studio email work --limit 20 --poll-seconds 60
```

Each worker owns a delivery through a 90-second database lease. Multiple workers
may poll the shared outbox, but each process consumes its own bounded connection
pool; include worker pools when calculating the provider connection budget.

The API installs its versioned workspace, access, identity, email-delivery, and
request-limit tables under an advisory transaction lock.
Startup fails if the pool cannot connect, the schema cannot be installed, or a
newer unsupported schema is present. `/api/ready` returns `503` when the database
or shared request protection is unavailable. The failure response remains
secret-safe and never exposes the database URL, raw bucket keys, or workspace
counts.

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

Treat a point-in-time restore as a credential and delivery-state rollback. Keep
API and worker processes stopped while deciding whether restored pending/retry
messages are still safe to send. Revoke stale invitations and clear or reconcile
restored outbox rows before workers restart. Before reopening
traffic, generate and deploy a new `TRACEBISECT_STUDIO_API_KEY_PEPPER` and
`TRACEBISECT_STUDIO_IDENTITY_SECRET`, create a new admin key with the new pepper,
and retire both old secrets. This invalidates restored keys, browser sessions,
human sessions, pending invitation links, and recovery codes, including items
revoked after the restored timestamp. Password hashes remain verifiable, so
people can sign in again with their password after the rotation.

## Move an existing SQLite Studio

This is a maintenance-window operation. It preserves product data, hashed
credentials, active sessions, invitations, encrypted email payloads, and webhook
history. Use these steps in order:

1. Stop the old API and every email worker. Leave them stopped until cutover is
   complete.
2. Create and verify a normal SQLite backup for rollback. The migration itself
   reads the original database because published backups deliberately omit
   sessions and email-delivery state.
3. Provision a PostgreSQL database containing no Studio rows. Set its URL only in
   the environment or deployment secret manager.
4. Run the migration command from the release that will serve PostgreSQL.

```bash
export TRACEBISECT_STUDIO_DATABASE_URL='postgresql://studio:secret@db.example/tracebisect'

tracebisect studio migrate-postgres \
  --source .tracebisect/studio.db
```

The command never writes to the SQLite source. It creates an isolated online
snapshot, upgrades only that disposable copy, checks SQLite integrity and
foreign-key relationships, and requires every destination product table to be
empty. It then copies all 13 product, access, identity, session, invitation,
outbox, and webhook tables in one destination transaction.

Before commit, TraceBisect compares every table count and a canonical SHA-256 of
every stored row. It also takes a second source snapshot and rolls back when the
source changed during the copy. A success message therefore means the copied
transaction reconciled; it is not merely a count of attempted inserts.

5. Optionally repeat the comparison without writing anything. Run this before
   starting PostgreSQL Studio; normal new activity will correctly make the two
   databases differ afterward.

```bash
tracebisect studio migrate-postgres \
  --source .tracebisect/studio.db \
  --verify-only
```

6. Keep the existing `TRACEBISECT_STUDIO_API_KEY_PEPPER` and
   `TRACEBISECT_STUDIO_IDENTITY_SECRET` for the cutover. Changing them would
   invalidate migrated credential digests and make migrated email ciphertext
   unreadable.
7. Set `TRACEBISECT_STUDIO_STORAGE=postgres`, start one API instance, and require
   `/api/ready` to report ready. Check sign-in, one workspace, and invitation
   status before starting workers or adding replicas.

The destination-empty rule is deliberate: this command never guesses how to
merge identities, credentials, reports, or delivery state. If it refuses a
destination, use a new empty database rather than deleting unknown rows. Keep the
old SQLite file and verified backup unchanged until the PostgreSQL backup and
restore drill succeeds.

## Verify before rollout

Run the contract suite on every change. When an isolated disposable PostgreSQL
database is available, also run the opt-in live parity and cross-instance rate
limit tests:

```bash
pytest -q tests/test_studio_postgres_storage.py tests/test_studio_rate_limit.py

TRACEBISECT_TEST_POSTGRES_URL='postgresql://...' \
pytest -q tests/test_studio_postgres_storage.py \
  -k live_postgres_store_shares_fresh_data_and_isolates_workspaces

TRACEBISECT_TEST_POSTGRES_URL='postgresql://...' \
pytest -q tests/test_studio_rate_limit.py \
  -k live_postgres_rate_limit_is_shared_across_store_instances
```

The live test creates random workspaces, proves fresh cross-pool reads, managed
key/session revocation, invitation acceptance, password sign-in, saved-code
recovery, human-session invalidation, encrypted outbox delivery through another
store instance, signed webhook deduplication/reconciliation, and workspace
isolation, then removes those rows. The rate-limit test proves that requests
through separate connection pools consume the same bucket and cleans up that
bucket. The suite uses an in-process fake mail transport and does not contact
Resend. A skipped live test is not evidence that a real provider
connection, TLS policy, backup, restore, or provider delivery has been verified.
The migration suite separately proves populated 13-table copies, non-empty
destination refusal, content-drift detection, transaction rollback, live-source
change detection, and PostgreSQL parameter translation. A real cutover should
still be rehearsed against an isolated provider database before production.
