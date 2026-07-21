# Production-style single-node deployment

This deployment runs the TraceBisect API, web dashboard, Caddy TLS proxy, and
optional invitation-email worker on one Docker host. It is a hardened way to
operate the current SQLite product; it is not a horizontally scalable or
multi-region SaaS topology.

## What the bundle enforces

- Only Caddy publishes ports (`80` and `443`). The API and web containers stay
  on a private network.
- Caddy obtains and renews HTTPS certificates and sends browser/API traffic to
  the correct private service.
- The API is a non-root, read-only container with one persistent `/data` volume,
  a bounded temporary filesystem, dropped Linux capabilities, and a readiness
  check.
- A private ClamAV service scans bounded trace bytes before parsing. The scanner
  port is not published, its signature database has a persistent volume, and a
  detected threat or unavailable scanner stores nothing.
- The web container is non-root and read-only. Its browser client uses the same
  public origin, so credentials do not need a second public API hostname.
- The optional email worker is a supervised Python process with graceful
  `SIGTERM` handling. It shares only the SQLite volume and outbound network.
- The optional Prometheus service scrapes the private API with a file-mounted
  operations token, evaluates the starter alerts, and keeps a bounded 30-day or
  2 GB local history. Its UI binds to host loopback only.
- Python dependencies are resolved from the committed `uv.lock`; frontend
  dependencies use `package-lock.json`.

The API trusts forwarded headers because its port is reachable only from the
private container network. Never publish container port `8000` directly.

## Prepare the host

Use a maintained Linux host with Docker Engine and the Docker Compose plugin.
Point the selected hostname's A/AAAA record at the host, and allow inbound TCP
80/443 plus UDP 443. Keep SSH and the Docker socket restricted to operators.
Plan at least 4 GB of memory for ClamAV in addition to the memory required by
the API, web, proxy, and optional monitoring or email services.

Create the private environment file:

```bash
cp deploy/.env.production.example deploy/.env.production
tracebisect studio keys generate-pepper
tracebisect studio identity generate-secret
tracebisect studio metrics generate-token \
  --output deploy/secrets/tracebisect_metrics_token
```

Paste the two printed server secrets into `deploy/.env.production`, set the real
hostname, and leave the metrics-token file path at its documented default. Keep
both the environment file and generated secret directory private:

```bash
chmod 600 deploy/.env.production
chmod 700 deploy/secrets
```

Do not commit either secret file. For a serious hosted deployment, inject these
values from the host or cloud secret manager instead of leaving them on disk.

## Validate and start

```bash
docker compose \
  --env-file deploy/.env.production \
  -f deploy/compose.production.yml \
  config --quiet

docker compose \
  --env-file deploy/.env.production \
  -f deploy/compose.production.yml \
  up --build -d
```

Watch startup without printing environment values:

```bash
docker compose --env-file deploy/.env.production -f deploy/compose.production.yml ps
docker compose --env-file deploy/.env.production -f deploy/compose.production.yml \
  logs --tail=100 api web caddy
```

Create the first bootstrap key inside the private API container:

```bash
docker compose \
  --env-file deploy/.env.production \
  -f deploy/compose.production.yml \
  exec api tracebisect studio keys create \
  --database /data/studio.db \
  --workspace main \
  --name 'Initial browser' \
  --role admin \
  --expires-in-days 7
```

Open the HTTPS site, use that key once, then invite the long-lived human admin
from **Settings → People and invitations**. Create a replacement recovery key
before the bootstrap key expires.

## Enable invitation email

Set the Resend values described in
[studio-email-delivery.md](studio-email-delivery.md), including the signing
secret, then start the email profile:

```bash
docker compose \
  --profile email \
  --env-file deploy/.env.production \
  -f deploy/compose.production.yml \
  up --build -d
```

Do not enable the profile while `TRACEBISECT_STUDIO_EMAIL_PROVIDER=none`; the
worker intentionally fails rather than pretending delivery is configured.

## Enable local monitoring

Start the monitoring profile after the normal services are healthy:

```bash
docker compose \
  --profile monitoring \
  --env-file deploy/.env.production \
  -f deploy/compose.production.yml \
  up --build -d

curl --fail --show-error http://127.0.0.1:9090/-/ready
```

Prometheus is reachable only from the Docker host at
`http://127.0.0.1:9090`. Use an SSH tunnel for remote operator access; do not
publish this port to the internet. Open **Status → Targets** and confirm the
single `tracebisect-studio` target is `UP`, then open **Alerts** and confirm the
rules loaded.

The named `prometheus-data` volume retains at most 30 days or 2 GB, whichever
limit is reached first. This gives the single host restart-durable metrics and
rule evaluation; it does not send notifications or copy history off the host.

## Verify the live boundary

```bash
curl --fail --show-error https://studio.example.com/api/ready
curl --fail --show-error https://studio.example.com/api/health
```

Replace the hostname. `/api/ready` must report both storage and the configured
upload scanner ready. `/api/health` must show the scanner enabled and ready,
along with secure browser cookies, managed identity, durable SQLite, and the
actual email/webhook state. It will continue to report
`production_saas_ready: false`; this topology still needs scheduled encrypted
off-site backups, centralized request-log retention, external alert delivery,
off-host metrics retention, and recovery drills.

If scanner readiness fails, inspect the private ClamAV service and signature
update state before accepting uploads. Do not disable scanning to make readiness
pass. See [studio-upload-scanning.md](studio-upload-scanning.md) for the
fail-closed contract and troubleshooting boundary.

## Practice recovery monthly

Run the isolated drill inside the API container, then copy its evidence report
to the operator's records:

```bash
docker compose \
  --env-file deploy/.env.production \
  -f deploy/compose.production.yml \
  exec api tracebisect studio recovery-drill \
  --database /data/studio.db \
  --report /tmp/recovery-drill-2026-07-21.json

mkdir -p operations/recovery-drills
docker compose \
  --env-file deploy/.env.production \
  -f deploy/compose.production.yml \
  cp api:/tmp/recovery-drill-2026-07-21.json operations/recovery-drills/2026-07-21.json
```

Use a new date for every drill. The container's private `/tmp` is limited to
64 MB, and the drill needs room for two database copies. For a larger database,
run the CLI from a restricted operator host with a larger private
`--scratch-directory`. See
[studio-recovery-drill.md](studio-recovery-drill.md) for pass criteria and the
remaining off-site/provider boundary.

## Back up before every upgrade

```bash
docker compose \
  --env-file deploy/.env.production \
  -f deploy/compose.production.yml \
  exec api tracebisect studio backup \
  --database /data/studio.db \
  --output /tmp/studio-before-upgrade.db

docker compose \
  --env-file deploy/.env.production \
  -f deploy/compose.production.yml \
  cp api:/tmp/studio-before-upgrade.db ./studio-before-upgrade.db

tracebisect studio verify --backup ./studio-before-upgrade.db
```

Encrypt and move the verified file away from the Docker host. Then rebuild and
restart. Never scale the `api` service above one replica: process metrics,
rate limits, and SQLite are intentionally single-node in this release.

For restore steps and incident decisions, use
[studio-alert-runbook.md](studio-alert-runbook.md) and
[studio-production-hardening.md](../studio-production-hardening.md).
