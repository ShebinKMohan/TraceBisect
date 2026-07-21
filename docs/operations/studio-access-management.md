# Studio workspace access

This guide is for a workspace admin who needs to give an integration or
emergency operator the smallest access it needs. People should normally use an
invited account; see [Studio accounts and team access](studio-accounts.md).
Routine key management happens inside Studio;
the command line is only needed to bootstrap the first admin or recover a
workspace that has no usable admin key.

## Create access in Studio

1. Open **Settings** and find **Workspace access**.
2. Enter a recognizable name such as `CI uploads` or `Emergency operator`.
3. Choose the smallest permission:
   - **Viewer** can inspect traces, comparisons, and generated tests.
   - **Editor** can also upload, compare, save, and rerun guardrails.
   - **Admin** can do everything an editor can and manage access.
4. Choose an expiry. Prefer a short lifetime unless the integration cannot be
   rotated regularly.
5. Select **Create access key**, copy the value, and save it in an approved
   secret manager. Studio shows the plaintext key only once.
6. Send it through a secure channel. Do not paste it into tickets, chat history,
   screenshots, or source control.

The access list shows the name, role, expiry, status, and non-secret key ID. It
never shows existing plaintext keys. A workspace can have at most 100 active
keys; revoke unused access before creating more.

## Revoke access

1. Find the named key in **Existing access**.
2. Select **Revoke**, then confirm the inline warning.
3. The key and every browser session created from it stop working immediately.

Studio disables revocation for the key behind the current sign-in. To rotate
that key without locking yourself out:

1. Create and securely save a replacement admin key.
2. Select **Lock workspace**.
3. Open the workspace with the replacement.
4. Return to **Settings → Workspace access** and revoke the old key.

## Bootstrap and recovery

The first admin key still comes from the trusted server environment:

```bash
tracebisect studio keys create \
  --database .tracebisect/studio.db \
  --workspace team-a \
  --name 'Workspace owner' \
  --role admin
```

For PostgreSQL, store `TRACEBISECT_STUDIO_DATABASE_URL` and
`TRACEBISECT_STUDIO_API_KEY_PEPPER` in the trusted operator environment, then
run the same command without `--database`:

```bash
tracebisect studio keys create \
  --workspace team-a \
  --name 'Workspace owner' \
  --role admin
```

If every admin key is lost, expired, or revoked, a server operator with access
to the database and configured pepper must create a replacement with the same
command. Human accounts use saved one-time recovery codes; automatic recovery
email is not implemented yet.

## Security boundary

- Only an authenticated workspace admin can call the access-management API.
- Editor and viewer requests are rejected before route execution.
- Every list, create, and revoke operation is scoped to the authenticated
  workspace; a valid key ID from another workspace is treated as not found.
- Browser-session changes require Studio's exact allowed origin and CSRF header.
- Request audit events use fixed action names and never contain the new secret or
  a key ID.
- SQLite and PostgreSQL store a peppered HMAC digest, not the plaintext key.

This feature manages workspace keys. Human accounts, invitation links, saved
recovery codes, and team membership are documented separately. Automated email,
identity-provider sign-in, multi-factor authentication, and billing are not yet
provided.
