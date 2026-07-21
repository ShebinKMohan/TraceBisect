# Studio invitation email delivery

TraceBisect can send workspace invitations through Resend while keeping the
secret link out of API responses and plaintext database rows. This is optional:
without the settings below, admins still receive a one-time private link to
share manually.

## Configure Resend

1. Add and verify a sending domain in Resend. Use a dedicated address such as
   `TraceBisect <invites@example.com>` rather than a personal mailbox.
2. Create a restricted production API key and store it in the deployment secret
   manager.
3. Set the public HTTPS URL people use to open Studio.

```bash
export TRACEBISECT_STUDIO_EMAIL_PROVIDER='resend'
export TRACEBISECT_STUDIO_EMAIL_FROM='TraceBisect <invites@example.com>'
export TRACEBISECT_STUDIO_EMAIL_REPLY_TO='support@example.com'
export TRACEBISECT_STUDIO_PUBLIC_URL='https://studio.example.com'
export RESEND_API_KEY='re_...'
export RESEND_WEBHOOK_SECRET='whsec_...'
```

Automatic delivery also requires durable SQLite or PostgreSQL storage and
`TRACEBISECT_STUDIO_IDENTITY_SECRET`. Startup fails with a safe configuration
error if a required setting is absent. PostgreSQL API and worker processes share
the same encrypted outbox through the configured database URL; see
[studio-postgres-core.md](studio-postgres-core.md). Non-loopback public URLs
must use HTTPS.

In the Resend dashboard, register
`https://studio.example.com/api/webhooks/resend` and subscribe to
`email.sent`, `email.delivered`, `email.delivery_delayed`, `email.failed`,
`email.suppressed`, `email.bounced`, and `email.complained`. Copy that endpoint's
signing secret into `RESEND_WEBHOOK_SECRET`; it is different from the API key.

## Run the durable worker

The API attempts a newly queued message after returning the response. A
scheduled worker is still required so temporary provider or network failures
are retried after restarts:

```bash
tracebisect studio email deliver \
  --database .tracebisect/studio.db \
  --limit 20
```

For PostgreSQL, keep the normal Studio storage environment and omit the SQLite
flag:

```bash
tracebisect studio email deliver --limit 20
tracebisect studio email work --limit 20 --poll-seconds 60
```

Run this command at least once per minute with the deployment scheduler. Only
one worker can own a delivery attempt at a time. A crashed worker's 90-second
lease can be reclaimed safely. Retry delays grow from 30 seconds to one hour,
with at most eight attempts.

Every provider request uses `tracebisect-invite-<message-id>` as its Resend
idempotency key. TraceBisect stops an uncertain retry after 23 hours because
Resend's documented idempotency window is 24 hours; an admin can then use
**Retry email** to create a new delivery deliberately.

## What is stored

The recipient address and delivery metadata are visible to operators. The
subject, bodies, sender, reply-to address, and secret invitation URL are stored
as AES-GCM authenticated ciphertext. The encryption key is derived from
`TRACEBISECT_STUDIO_IDENTITY_SECRET`; never rotate that secret casually.

The invitation token itself remains a keyed digest in the invitation table.
The link puts the token in the browser URL fragment (`#invite=...`), which is
not sent to the web server as part of the HTTP request target.

Webhook requests are verified against the unmodified raw body and Svix headers.
Studio stores only the event ID, provider message ID, type, normalized status,
and timestamps. Repeated `svix-id` values are ignored, and an older out-of-order
event cannot replace newer delivery state. An event that races ahead of the API
response is reconciled when the provider message ID is stored.

SQLite backup commands deliberately remove the email outbox and webhook event
metadata as well as login sessions. This prevents a restored local snapshot from
sending an old invitation. Pending invitations remain, but an admin must revoke
and recreate any invite that still needs delivery after a restore.

PostgreSQL provider backups retain outbox and webhook rows. Keep workers stopped
during a point-in-time restore, inspect pending/retry messages, revoke stale
invitations, and clear or reconcile unsafe delivery rows before restarting them.
Rotating `TRACEBISECT_STUDIO_IDENTITY_SECRET` makes restored ciphertext
undecryptable, so combine secret rotation with an explicit queue disposition.

## Status and recovery

Settings shows one of these operator-friendly states:

- **email queued / sending email / email will retry** — the worker still owns it;
- **email accepted by provider** — Resend accepted the request but no delivery
  webhook has been reconciled yet;
- **delivered to their mail server** — Resend reported server-level delivery;
- **delivery is delayed / bounced / suppressed / marked as spam** — signed
  provider state that needs monitoring or an admin decision;
- **email needs attention** — the provider rejected it or safe retries ended;
- **share the private link manually** — automatic delivery was disabled or the
  durable queue could not accept the message.

If queueing fails while an invitation is created, Studio returns the secret link
once instead of pretending email was sent. If a queued delivery fails, select
**Retry email**. Manual-only invitations cannot be converted because Studio no
longer has their plaintext token; revoke and create a new invitation.

## Current production boundary

This release proves encrypted queueing, bounded retries, lease-safe workers,
provider request acceptance, signed webhook verification, deduplication, and
ordered delivery/bounce reconciliation on SQLite and PostgreSQL. PostgreSQL
contract tests cover the shared pool and schema; the opt-in live test covers
cross-store delivery and webhook reconciliation without contacting Resend. It
does not automatically manage sender-domain health or provider suppression
lists, verify recipient ownership again after an email change, or prove a real
provider/TLS/restore deployment unless those checks are run by the operator.

Provider reference: [Resend send-email API](https://resend.com/docs/api-reference/emails/send-email)
and [Resend idempotency keys](https://resend.com/docs/dashboard/emails/idempotency-keys).
