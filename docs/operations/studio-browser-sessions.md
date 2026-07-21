# TraceBisect Studio Browser Sessions

This guide explains Studio's protected browser cookie. People normally use an
invited email/password account; access keys remain available for integrations,
bootstrap, and emergency operator access. Account enrollment and recovery are
covered in [Studio accounts and team access](studio-accounts.md).

## What a new user does

1. Open TraceBisect Studio.
2. Enter the email and password created from the workspace invitation.
3. Choose a workspace if the account belongs to more than one.
4. Use Studio normally. The browser does not receive a reusable account token.
5. Select **Lock workspace** on a shared computer or when work is finished.

Studio stores the session in an HttpOnly cookie, which means page JavaScript
cannot read or copy it. The database contains only a keyed digest of the opaque
session token—not the plaintext token or password. A person can also choose
**Use an access key**; that key is exchanged once for the same kind of protected
cookie and is not saved in the page.

## What ends a session

A browser session stops working when any of these happens:

- The user selects **Lock workspace**. Studio revokes the current session before
  returning to the sign-in screen.
- The session reaches its configured expiry time.
- An operator revokes the workspace key that created it.
- The source workspace key expires.
- An account password is recovered, a live membership is removed, or the
  account session version changes.
- A restored SQLite backup replaces the active database. SQLite backups
  intentionally contain no browser sessions, so recovery requires everyone to
  sign in again. A PostgreSQL point-in-time restore must rotate the API-key
  pepper and identity secret, then bootstrap a new admin key before reopening
  traffic. The identity-secret rotation also invalidates restored human sessions,
  pending invitations, and recovery codes; password hashes remain verifiable.

Revoking a source key invalidates all sessions created from that key without an
API restart:

```bash
tracebisect studio keys list --database .tracebisect/studio.db
tracebisect studio keys revoke --database .tracebisect/studio.db --key-id KEY_ID
```

For PostgreSQL, set `TRACEBISECT_STUDIO_DATABASE_URL` in the trusted operator
environment and omit `--database` from both commands.

## Session lifetime

The default maximum lifetime is eight hours. A session can never outlive its
source workspace key. To use a shorter or longer window, set a value from 300
seconds (five minutes) to 604800 seconds (seven days):

```bash
export TRACEBISECT_STUDIO_BROWSER_SESSION_TTL_SECONDS=28800
```

Use the shortest lifetime that fits the real workday. Reducing the value affects
new sessions; it does not rewrite an already-issued session.

Studio retains at most 20 active browser sessions per source key and 20 active
human sessions per account. Issuing a 21st session removes the oldest one,
which bounds database growth if a client signs in repeatedly.

## Production origin and HTTPS requirements

Set the exact frontend origin—never `*`—and serve the browser and API from the
same HTTPS site, such as `studio.example.com` and `api.example.com`:

```bash
export TRACEBISECT_STUDIO_ALLOWED_ORIGINS=https://studio.example.com
```

Studio uses these controls together:

- `HttpOnly` prevents JavaScript from reading the session cookie.
- `SameSite=Strict` prevents the browser from sending it in cross-site requests.
- `Secure` limits the cookie to HTTPS in hosted deployments.
- Hosted cookies use the `__Host-` prefix, a host-only path, and no `Domain`
  attribute so sibling subdomains cannot overwrite them.
- Exact Origin checks reject state-changing cookie requests from an unapproved
  frontend.
- Studio requires an `X-TraceBisect-CSRF: 1` header on state-changing browser
  requests, forcing those requests through the approved CORS policy.
- Credentialed CORS permits the approved frontend to call the API while keeping
  other origins out.

Cookie security is automatic for a configuration containing only HTTPS origins.
It is disabled automatically only for the built-in `localhost`/`127.0.0.1`
development origins. Mixed HTTPS and HTTP origins, or non-loopback HTTP origins,
fail at startup unless the operator makes an explicit choice:

```bash
export TRACEBISECT_STUDIO_BROWSER_SESSION_COOKIE_SECURE=true
```

Do not set that value to `false` for a hosted deployment. If a reverse proxy
terminates TLS, keep the public allowed origin on HTTPS and leave the cookie
setting on `auto` or `true`.

## Compatibility boundary

CLI tools that need broader workspace access may continue to send a managed
workspace key as an `Authorization: Bearer ...` header. An agent or CI job that
only uploads traces should use a
[trace-upload token](studio-ingestion-tokens.md) instead. Browser sessions are
an additional, safer browser path; they do not remove API access.

The legacy `TRACEBISECT_STUDIO_API_KEYS` JSON mapping cannot issue managed
browser sessions because it has no durable key ID, expiry, or revocation record.
It remains a migration/local-development mode and continues to use tab-scoped
browser key storage. Use managed keys for a hosted deployment.

## Incident checks

If a user is unexpectedly signed out:

1. Check whether the session or source key expired.
2. Check key metadata with `tracebisect studio keys list`; do not ask the user to
   paste the plaintext key into logs or a ticket.
3. Confirm the frontend origin exactly matches
   `TRACEBISECT_STUDIO_ALLOWED_ORIGINS`.
4. Confirm hosted cookies include `Secure`, `HttpOnly`, and `SameSite=Strict`.
5. Create a replacement key only when the original is expired, revoked, or
   suspected of exposure.

Request audit events and metrics never contain the browser token or workspace
key. Keep that same rule in reverse-proxy and incident tooling.
