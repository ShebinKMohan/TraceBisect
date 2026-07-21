# Trace-upload tokens for agents and CI

Use a trace-upload token when software needs to send `.tbtrace` or `.json`
files into one Studio workspace. It has exactly one permission:

```text
POST /api/traces/upload
```

It cannot open Studio, list traces, read comparisons, run guardrails, manage
people, or create a browser session. People should use an invited account.
Interactive tools that genuinely need to read or compare workspace data need a
normal workspace key with the smallest suitable role.

## Create a token

The server operator needs the same `TRACEBISECT_STUDIO_API_KEY_PEPPER` used by
managed workspace keys. For SQLite, provide the database path:

```bash
tracebisect studio ingest-tokens create \
  --database .tracebisect/studio.db \
  --workspace team-a \
  --name 'Production support agent' \
  --expires-in-days 90
```

For PostgreSQL, keep `TRACEBISECT_STUDIO_DATABASE_URL` in the operator
environment and omit `--database`.

The command shows the token once. Store it in the agent or CI secret manager;
do not paste it into source code, logs, screenshots, tickets, or chat.

## Upload a trace

Send the token as a Bearer credential only to the upload endpoint:

```bash
curl --fail-with-body \
  --request POST \
  --header "Authorization: Bearer $TRACEBISECT_INGEST_TOKEN" \
  --form 'file=@run.tbtrace' \
  https://studio.example.com/api/traces/upload
```

A successful response contains non-secret trace metadata. A `401` means the
token is missing, invalid, expired, or revoked. A `403` means the token was sent
to an endpoint outside its upload-only permission. A `409` means that workspace
already has the supplied `trace_id`; give every run a unique trace ID. Upload
tokens are append-only and cannot replace existing evidence.

## List, rotate, and revoke

Existing plaintext tokens can never be shown again:

```bash
tracebisect studio ingest-tokens list \
  --database .tracebisect/studio.db

tracebisect studio ingest-tokens revoke \
  --database .tracebisect/studio.db \
  --token-id TOKEN_ID
```

Rotate without interrupting uploads:

1. Create a replacement token with a clear name.
2. Update the agent or CI secret.
3. Prove one upload succeeds with the replacement.
4. Revoke the old token ID.

Each workspace can have at most 100 active trace-upload tokens. SQLite and
PostgreSQL store only a domain-separated, peppered HMAC-SHA256 digest. Tokens
remain workspace-scoped through backup, restore, and the reconciled PostgreSQL
migration.
