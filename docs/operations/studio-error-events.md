# Studio error events

Use this guide when Studio shows an unexpected-error message with a request ID.
Studio always emits a secret-safe JSON event. Durable SQLite and PostgreSQL
deployments also retain a bounded copy so an operator can search by request ID
after a process restart. PostgreSQL shares that retained history across API
instances. A hosting platform must still collect logs and deliver alerts.

## What the user sees

Unexpected route failures return HTTP `500` with a safe message and a bounded
request ID. The web app includes that ID in the visible error message so the
user can share it with support. It never sends the Python exception back to the
browser.

## What the server emits

The `uvicorn.error.tracebisect_errors` logger emits one compact JSON object for
the failure:

```json
{
  "action": "trace_compare",
  "error_type": "RuntimeError",
  "event": "studio.server_error",
  "event_version": 1,
  "failure_location": "compare.py:compare_traces:118",
  "fingerprint": "9d24f4be5b23595e721c",
  "method": "POST",
  "request_id": "request-example-1234",
  "status_code": 500,
  "timestamp": "2026-07-21T10:30:00Z",
  "workspace_id": "workspace-example"
}
```

The exact values above are examples. The event intentionally excludes:

- exception messages and stack traces;
- request headers, query values, and bodies;
- credentials, browser-session tokens, and uploaded trace content;
- uploaded filenames and full filesystem or database paths.

`failure_location` contains only a sanitized source basename, function, and
line. The stable fingerprint groups the same action, error type, and location
without storing the exception text.

## First response

1. Ask the user for the request ID shown by Studio. Never ask them to send an
   API key or the uploaded trace.
2. Search the durable Studio database:

   ```bash
   tracebisect studio error-events list \
     --database .tracebisect/studio.db \
     --request-id REQUEST_ID
   ```

   For PostgreSQL, keep `TRACEBISECT_STUDIO_DATABASE_URL` in the operator
   environment and omit `--database`.
3. Find the request-audit event with the same request ID. Confirm its action,
   status, timestamp, and workspace without copying sensitive data elsewhere.
4. Use the fingerprint to check whether the same failure is recurring.
5. Follow the relevant alert procedure in
   [`studio-alert-runbook.md`](studio-alert-runbook.md). Preserve evidence before
   changing or restoring data.

If the request-ID search returns no result, check the collected JSON logs. The
database may have been unavailable during the original failure, the event may
have aged out, or the deployment may be using restart-ephemeral memory storage.

## Retention boundary

- Retention is enabled automatically when Studio uses SQLite or PostgreSQL.
- At most 1,000 events per workspace and 10,000 events overall are kept for 30
  days. Public or pre-authentication failures share one separate null-workspace
  bucket.
- Expired and excess rows are removed inside the same serialized write used to
  retain a new event, so concurrent API instances cannot bypass the cap.
- Search accepts a request ID or the 20-character recurring-error fingerprint
  and returns at most 500 rows.
- The command line is an operator surface requiring database access; retained
  failures are not exposed through the workspace API or browser UI.
- Normal SQLite backups deliberately strip error history alongside sessions and
  delivery queues. A reconciled SQLite-to-PostgreSQL cutover preserves it so an
  active incident is not silently disconnected from its request IDs.
- JSON logging remains active even when the retention write fails. A secondary
  database problem never replaces the original user-facing `500` response.

## Configuration and readiness

Error events are enabled by default. Set
`TRACEBISECT_STUDIO_ERROR_LOG_ENABLED=false` only when the deployment has an
equivalent secret-safe reporter. `/api/health` publishes the active boundary,
including whether retention is `disabled`, `log_only`, `local_sqlite`, or
`shared_postgres`. `/api/ready` still lists hosting-platform metrics collection
and alert delivery as production work; database retention alone is not a
complete monitoring system.
