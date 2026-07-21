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
- The web container is non-root and read-only. Its browser client uses the same
  public origin, so credentials do not need a second public API hostname.
- The optional email worker is a supervised Python process with graceful
  `SIGTERM` handling. It shares only the SQLite volume and outbound network.
- Python dependencies are resolved from the committed `uv.lock`; frontend
  dependencies use `package-lock.json`.

The API trusts forwarded headers because its port is reachable only from the
private container network. Never publish container port `8000` directly.

## Prepare the host

Use a maintained Linux host with Docker Engine and the Docker Compose plugin.
Point the selected hostname's A/AAAA record at the host, and allow inbound TCP
80/443 plus UDP 443. Keep SSH and the Docker socket restricted to operators.

Create the private environment file:

```bash
cp deploy/.env.production.example deploy/.env.production
tracebisect studio keys generate-pepper
tracebisect studio identity generate-secret
tracebisect studio metrics generate-token
```

Paste the three generated values into `deploy/.env.production`, set the real
hostname, and keep the file mode owner-only:

```bash
chmod 600 deploy/.env.production
```

Do not commit that file. For a serious hosted deployment, inject these values
from the host or cloud secret manager instead of leaving them on disk.

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

## Verify the live boundary

```bash
curl --fail --show-error https://studio.example.com/api/ready
curl --fail --show-error https://studio.example.com/api/health
```

Replace the hostname. `/api/ready` must report storage ready. `/api/health`
must report secure browser cookies, managed identity, durable SQLite, and the
actual email/webhook state. It will continue to report
`production_saas_ready: false`; this topology still needs scheduled encrypted
off-site backups, centralized log/metric retention, external alert delivery,
and recovery drills.

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
