# Studio error events

Use this guide when Studio shows an unexpected-error message with a request ID.
It describes the built-in single-process reporting boundary; your hosting
platform must still collect, retain, search, and protect the events.

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
2. Find the `studio.server_error` event with that request ID.
3. Find the request-audit event with the same request ID. Confirm its action,
   status, timestamp, and workspace without copying sensitive data elsewhere.
4. Use the fingerprint to check whether the same failure is recurring.
5. Follow the relevant alert procedure in
   [`studio-alert-runbook.md`](studio-alert-runbook.md). Preserve evidence before
   changing or restoring data.

## Configuration and readiness

Error events are enabled by default. Set
`TRACEBISECT_STUDIO_ERROR_LOG_ENABLED=false` only when the deployment has an
equivalent secret-safe reporter. `/api/health` publishes the active boundary,
and `/api/ready` keeps hosted event retention and delivery listed as production
work rather than claiming that local JSON output is a complete monitoring
system.
