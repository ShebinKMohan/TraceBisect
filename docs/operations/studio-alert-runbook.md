# TraceBisect Studio Monitoring and Alert Runbook

> Status: Provisional operating targets for the current single-node Studio.
> These are internal reliability goals, not a customer SLA.

This guide is for the person operating TraceBisect Studio. It explains what to
connect, what each alert means, and the safest first action. You do not need to
understand Prometheus internals to follow the incident steps.

## What this monitoring covers

Studio exposes process-local metrics at `/api/metrics`. Prometheus stores those
values over time and evaluates the rules in
`deploy/prometheus/tracebisect-alerts.yml`.

The rules watch six user-visible or operator-actionable signals:

1. Can monitoring reach Studio?
2. Can Studio reach its database?
3. Are users seeing server errors?
4. Are normal page reads slow?
5. Are uploads and comparisons slow?
6. Are clients being rejected or rate limited repeatedly?

Metrics reset when an API process restarts. Prometheus owns history and combines
multiple processes. Metrics and alerts contain fixed action names, not workspace
IDs, trace IDs, filenames, user details, request IDs, or credentials.

## One-time Prometheus setup

### 1. Generate a monitoring-only token

```bash
tracebisect studio metrics generate-token
```

Save the printed value as `TRACEBISECT_STUDIO_METRICS_TOKEN` in the API secret
manager. Put the same value in a file readable only by Prometheus, such as
`/run/secrets/tracebisect_metrics_token`. Do not reuse a workspace key and do
not commit either secret.

### 2. Add the scrape job and rule file

The target below assumes the API is reachable as `tracebisect-api:8000`. Replace
only that address when your deployment uses a different service name.

```yaml
rule_files:
  - /etc/prometheus/rules/tracebisect-alerts.yml

scrape_configs:
  - job_name: tracebisect-studio
    metrics_path: /api/metrics
    scrape_interval: 30s
    scrape_timeout: 10s
    authorization:
      type: Bearer
      credentials_file: /run/secrets/tracebisect_metrics_token
    static_configs:
      - targets:
          - tracebisect-api:8000
```

Copy `deploy/prometheus/tracebisect-alerts.yml` to the rule-file path used by
your Prometheus deployment.

### 3. Validate before reloading

```bash
promtool check rules deploy/prometheus/tracebisect-alerts.yml
promtool check config /etc/prometheus/prometheus.yml
```

Reload Prometheus using the method supported by your deployment. Then query:

```promql
up{job="tracebisect-studio"}
tracebisect_studio_storage_ready
```

Both should return `1`. If `up` has no result, the job or target name is wrong.
If `up` is `0`, check the address, network path, and monitoring token. If
storage readiness is `0`, follow the storage alert below.

## Provisional service objectives

Use these as starting operating targets. Review them after 30 days of real
hosted traffic; do not advertise them as guarantees before that review.

| Signal | Initial objective | Why |
| --- | --- | --- |
| Metrics reachability | 99.9% over 30 days | Detects a stopped process or broken monitoring path. |
| Storage readiness | 99.9% over 30 days | Users cannot load or save durable work without storage. |
| Request reliability | 99.5% without server errors over 30 days | Counts product failures, not rejected or invalid client requests. |
| Interactive reads | 95% within 1 second | Keeps navigation, lists, and report opening responsive. |
| Upload/compare workflows | 95% within 5 seconds | Allows more time for trace parsing and comparison work. |

Prometheus queries for the monthly review:

```promql
avg_over_time(up{job="tracebisect-studio"}[30d])

avg_over_time(tracebisect_studio_storage_ready[30d])

1 - (
  sum(increase(tracebisect_studio_http_requests_total{result="server_error"}[30d]))
  /
  clamp_min(sum(increase(tracebisect_studio_http_requests_total[30d])), 1)
)

sum(increase(tracebisect_studio_http_request_duration_seconds_bucket{
  action=~"session_check|trace_list|run_list|run_read|case_list|case_read",
  le="1"
}[30d]))
/
clamp_min(sum(increase(tracebisect_studio_http_request_duration_seconds_count{
  action=~"session_check|trace_list|run_list|run_read|case_list|case_read"
}[30d])), 1)

sum(increase(tracebisect_studio_http_request_duration_seconds_bucket{
  action=~"demo_seed|trace_upload|trace_compare|case_create|case_run",
  le="5"
}[30d]))
/
clamp_min(sum(increase(tracebisect_studio_http_request_duration_seconds_count{
  action=~"demo_seed|trace_upload|trace_compare|case_create|case_run"
}[30d])), 1)
```

## First response to any alert

1. Record the alert name, start time, instance, and action label if present.
2. Open `/api/health` and `/api/ready`; neither endpoint requires a product key.
3. Check whether a deployment, key rotation, or database maintenance just ran.
4. Preserve the relevant `X-Request-ID` values from user reports or audit logs.
5. Do not paste keys, uploaded trace content, filenames, or workspace data into
   incident notes.
6. Do not delete, overwrite, or restore the database while investigating. Take a
   verified backup first if a storage change becomes necessary.

Useful checks:

```bash
curl --fail --show-error https://studio.example.com/api/health
curl --include https://studio.example.com/api/ready
tracebisect studio verify --backup /path/to/latest-backup.db
```

Replace `studio.example.com` and the backup path with the deployment's real
values.

## Alert severity

- **Critical** means users are likely unable to use Studio or are repeatedly
  receiving server failures. A person should investigate now.
- **Warning** means service is degraded or a client/security pattern needs
  attention. Investigate during the current support window unless user impact
  is already visible.

## TraceBisectStudioUnavailable

**Meaning:** Prometheus could not scrape a configured Studio target for five
minutes. Studio may be stopped, unreachable, or rejecting the monitoring token.

**Check:** Confirm the target address, process state, network route, TLS status,
and recent metrics-token rotation. Check `/api/health` from the same network as
Prometheus.

**Safe action:** Correct a bad target or secret reference. Restart the API only
when the process is actually unhealthy and storage is protected.

**Resolved when:** `up{job="tracebisect-studio"}` stays at `1` and no target is
missing.

## TraceBisectStudioStorageUnavailable

**Meaning:** Studio is running, but at least one process cannot use its
configured SQLite database.

**Check:** Open `/api/ready`, check database-file presence and permissions, disk
space, and the latest storage-related audit or server error. Do not expose the
database path in public incident channels.

**Safe action:** Take or locate a verified backup. Correct a mount or permission
problem before considering restore. Restore only into a new file using
`tracebisect studio restore`; the command intentionally refuses to overwrite an
existing database.

**Resolved when:** `/api/ready` returns `200` and
`tracebisect_studio_storage_ready` stays at `1` for every instance.

## TraceBisectStudioServerErrorRateHigh

**Meaning:** More than 5% of at least 20 requests for the named action failed
with server errors for ten minutes.

**Check:** Filter audit logs by the alert's action and the affected time window.
Use request IDs to join a user's error to one audit event. Check storage
readiness before assuming the application code is at fault.

**Safe action:** Roll back a recent deployment when the error began immediately
after it. For a data or storage error, preserve the database and follow the
storage procedure.

**Resolved when:** The action's server-error ratio remains below 5% and a known
good request succeeds.

## TraceBisectStudioInteractiveLatencyHigh

**Meaning:** Fewer than 95% of at least 20 requests for the named read action
finished within one second.

**Check:** Compare affected instances, storage readiness, CPU/memory pressure,
and recent data growth. Confirm the issue with the specific action rather than
judging the whole service from one slow request.

**Safe action:** Remove a demonstrably unhealthy instance from traffic or roll
back a latency regression. Do not raise thresholds simply to silence the alert.

**Resolved when:** At least 95% of the action's requests complete within one
second for ten minutes.

## TraceBisectStudioWorkflowLatencyHigh

**Meaning:** Fewer than 95% of at least 10 upload, comparison, or guardrail
requests for the named action finished within five seconds.

**Check:** Inspect trace sizes, action labels, storage readiness, process
pressure, and whether unusually large imports are reaching the synchronous MVP
path.

**Safe action:** Roll back a new performance regression. If normal trace sizes
now exceed the synchronous budget, open the planned background-job milestone;
do not conceal the problem by silently accepting unbounded work.

**Resolved when:** At least 95% of the action's requests complete within five
seconds for fifteen minutes.

## TraceBisectStudioRateLimitingHigh

**Meaning:** The named action was rate limited more than 20 times in ten minutes
and the pattern persisted.

**Check:** Look for a retry loop, multiple browser tabs, an integration using an
incorrect interval, or abusive source traffic. Keep source network details out
of public notes.

**Safe action:** Stop or correct the looping client. Block abusive traffic at the
edge when appropriate. Raise limits only after measuring legitimate demand and
available capacity.

**Resolved when:** Rate-limited requests stop increasing abnormally and the
legitimate workflow succeeds without repeated `429` responses.

## TraceBisectStudioAuthenticationFailuresHigh

**Meaning:** Studio rejected more than 25 credentials in ten minutes and the
pattern persisted. A key may be missing, expired, revoked, incorrectly rotated,
or under attack.

**Check:** Review recent key issuance/revocation metadata and deployment secret
changes. Determine whether failures began with an approved rotation. Never log
or compare plaintext keys.

**Safe action:** Correct the client secret reference or finish a planned
zero-downtime rotation. Revoke a suspected key by key ID. Apply edge controls for
abusive traffic; do not weaken key validation.

**Resolved when:** Rejections return to the normal baseline and an intended
client authenticates successfully with an active key.

## Closing an incident

Record the cause, affected interval, user-visible impact, safe fix, and one
follow-up owner. Preserve request IDs and aggregate metrics, but exclude
credentials and customer trace data. Revisit an alert threshold only with real
traffic evidence and a written reason.
