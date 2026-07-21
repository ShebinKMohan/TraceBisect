# Studio accounts and team access

This guide is for the person setting up human access to a protected TraceBisect
Studio workspace. People sign in with an email and password. Workspace keys
remain available for integrations, initial bootstrap, and operator recovery.

## Enable human accounts

Human accounts require managed workspace keys, durable SQLite or PostgreSQL
storage, and a separate identity secret. Generate both server secrets once and
store them in the deployment secret manager. PostgreSQL supports accounts,
manual invitations, recovery, membership, and sessions; automatic invitation
email remains SQLite-only. See [studio-postgres-core.md](studio-postgres-core.md).

```bash
tracebisect studio keys generate-pepper
tracebisect studio identity generate-secret
```

Configure the generated values with the protected Studio environment:

```bash
export TRACEBISECT_STUDIO_API_KEY_PEPPER='managed-key secret'
export TRACEBISECT_STUDIO_IDENTITY_SECRET='human-account secret'

TRACEBISECT_STUDIO_STORAGE=sqlite \
TRACEBISECT_STUDIO_SQLITE_PATH=.tracebisect/studio.db \
TRACEBISECT_STUDIO_AUTH_MODE=api-key \
TRACEBISECT_STUDIO_ALLOWED_ORIGINS='https://studio.example.com' \
uvicorn tracebisect.studio.api:app --port 8000
```

Keep `TRACEBISECT_STUDIO_IDENTITY_SECRET` stable. Replacing it invalidates
current human sessions, pending invitation links, and saved recovery codes.
Passwords remain verifiable, so a person can sign in again after a deliberate
secret rotation, but old recovery codes will not work.

## Invite the first person

1. Create an initial admin key with `tracebisect studio keys create`.
2. Open Studio with that key.
3. Open **Settings → People and invitations**.
4. Enter the person's email, choose **Admin**, and select an expiry.
5. Select **Invite person**.
6. With email delivery configured, confirm that Studio reports the message as
   queued. Otherwise copy the one-time link and send it through a private channel.

Studio stores only a protected invitation-token digest. With automatic delivery,
the complete email body and secret URL are authenticated ciphertext in a durable
outbox, and the API does not return the token. Without it, the plaintext link is
shown once. See [studio-email-delivery.md](studio-email-delivery.md) for setup.

## Accept an invitation

The invited person opens the link and:

1. Confirms the workspace and invited email.
2. Enters the name teammates should see.
3. Creates a password with 15–128 characters. Spaces and Unicode are allowed;
   Studio does not require arbitrary symbol or capital-letter rules.
4. Saves the eight one-time recovery codes in a password manager.
5. Continues to sign in. Invitation acceptance never signs the browser in
   automatically.

Passwords are hashed with Argon2id using 19 MiB memory, two iterations, and one
lane. Recovery codes, invitations, and sessions are stored as keyed digests,
not plaintext values.

If the email already belongs to an account, the invitation form verifies that
account's current password and adds the new workspace. It does not replace the
password or issue a second recovery-code set.

## Sign in and choose a workspace

Use **Email** and **Password** on the Studio sign-in card. If the account belongs
to one workspace, Studio opens it directly. If it belongs to more than one,
Studio asks which workspace to open only after the password has been verified.

The browser receives a short-lived HttpOnly, SameSite cookie. The cookie is
workspace-scoped by its server-side session record; client headers cannot
change the workspace. Selecting **Lock workspace** revokes the presented
session and clears the cookie.

## Recover an account

1. Choose **Use a recovery code** on the sign-in card.
2. Enter the account email, one saved recovery code, and a new password.
3. Save the replacement recovery codes.
4. Sign in with the new password.

A successful reset consumes the presented code, replaces every remaining code,
increments the account session version, and revokes all active human sessions.
Recovery never signs the browser in automatically. Invalid email/code pairs use
the same public response so Studio does not disclose which accounts exist.

If every recovery code is lost, a server operator can use an admin workspace
key to create a new invitation after an explicit account-recovery decision. The
current release does not provide an email-based reset link.

## Manage the team

Workspace admins use **Settings → People and invitations** to:

- see accepted people and pending invitations;
- change a person between Viewer, Editor, and Admin;
- revoke a pending invitation; and
- remove a person from the current workspace.

Role changes are read from the membership table on every request, so an active
session gains or loses permission immediately. Removing a membership revokes
that person's sessions for the workspace. Studio refuses to demote or remove
the last human admin and refuses to remove the account behind the current
session.

The limits are 100 accepted people, 100 active invitations, and 500 total
invitation records per workspace. Studio removes the oldest inactive invitation
history as that final bound is reached. Login and recovery have an additional
per-client, per-account limiter on top of the general API limiter. A
multi-instance deployment still needs a distributed limiter.

## Backup and restore boundary

SQLite Studio backups preserve users, Argon2id password hashes, memberships,
invitations, and recovery-code hashes. They remove key-derived browser sessions,
human identity sessions, and queued/sent email records before publication, so
every browser must sign in and old email cannot be sent after a restore.

Restoring an older backup also restores the credential state from that point in
time, including older password hashes, keys, invitations, and recovery-code
hashes. Treat a production restore as a security event: restrict access, rotate
server secrets and bootstrap keys when warranted, revoke stale invitations,
and tell people to replace passwords/recovery codes before reopening traffic.
PostgreSQL deployments use provider backup and point-in-time recovery instead;
rotate both the API-key pepper and identity secret after a restore before
reopening traffic.

## Current hosted boundary

This milestone provides invitation-only accounts, offline recovery, team roles,
and revocable human sessions on SQLite and PostgreSQL. Optional encrypted Resend
invitation delivery and signed delivery/bounce reconciliation remain single-node
SQLite capabilities. It does not yet provide a PostgreSQL email outbox,
automated sender-domain/suppression operations, email ownership re-verification,
multi-factor or identity-provider sign-in, distributed rate limiting, or billing.
