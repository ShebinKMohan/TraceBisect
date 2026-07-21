# Scan untrusted Studio uploads

Studio accepts trace files from browsers, agents, and CI. A hosted deployment
must treat every uploaded byte as untrusted, even though Studio accepts only
`.tbtrace` and `.json` files and never executes their contents.

## What production mode does

The production Compose bundle starts a private ClamAV service before the API.
For every accepted upload, Studio:

1. Enforces the file extension, empty-file check, and 5 MB default size limit.
2. Streams the bounded bytes to ClamAV over the private backend network.
3. Continues to trace parsing only after an explicit clean result.
4. Deletes the temporary parse file and stores only the validated trace.

A detected threat returns `422` and stores nothing. An unavailable scanner,
timeout, malformed response, or scan error returns `503` and stores nothing.
Studio does not return the malware signature, scanner address, or internal error
to the browser.

## Configure a non-Compose deployment

Run a current `clamd` service on a private network, then configure the API:

```bash
export TRACEBISECT_STUDIO_UPLOAD_SCANNER=clamav
export TRACEBISECT_STUDIO_CLAMAV_HOST=private-clamav
export TRACEBISECT_STUDIO_CLAMAV_PORT=3310
```

Optional connection settings are:

```bash
export TRACEBISECT_STUDIO_CLAMAV_CONNECT_TIMEOUT_SECONDS=2
export TRACEBISECT_STUDIO_CLAMAV_READ_TIMEOUT_SECONDS=10
```

The scanner host must be a DNS name or IP address, not a URL. Keep port `3310`
private; do not publish it to the internet. ClamAV's `StreamMaxLength` must be at
least `TRACEBISECT_STUDIO_MAX_UPLOAD_BYTES`.

The default `TRACEBISECT_STUDIO_UPLOAD_SCANNER=none` exists for local learning
and tests. `/api/health` reports it as disabled and production readiness lists
malware scanning as incomplete.

## Verify the deployment

After ClamAV has loaded its signature database, open:

```bash
curl --fail --show-error https://studio.example.com/api/ready
curl --fail --show-error https://studio.example.com/api/health
```

Readiness must show both storage and the upload scanner as `ok`. Health must show
an enabled `clamav` provider with `fail_closed` and `scan_before_parse` set to
`true`. A scanner outage deliberately makes readiness return `503` so an
unscanned upload cannot enter service.

## Operate it safely

Persist `/var/lib/clamav`, allow the scanner outbound access for FreshClam, and
monitor signature-update failures and database age. ClamAV needs substantial
memory for its signature database; the official container guidance recommends
planning for 4 GB rather than assuming 2 GB is enough.

Do not log uploaded bytes, filenames, malware signatures, or scanner network
details in public incident notes. Use the request ID and `trace_upload` audit
action to correlate a user report.

## Current boundary

This closes the synchronous malware-scanning gap for bounded uploads. It does
not provide object storage, sandbox detonation, content-disarm/reconstruction,
or asynchronous scanning for future large imports. Those remain separate hosted
milestones.
