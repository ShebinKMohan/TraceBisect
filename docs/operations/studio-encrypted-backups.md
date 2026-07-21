# Create and restore encrypted Studio backups

This guide creates a portable encrypted copy of a TraceBisect Studio SQLite
database. You do not need SQLite or cryptography knowledge. The live database
can remain open while the backup is created.

## What you need

Choose two private locations:

- a recovery-key location such as a secret manager or restricted recovery disk;
- an off-site backup location that is not on the Studio server.

Do not keep both in the same account, directory, or host. Anyone with both can
read the product data. Losing the key makes every backup encrypted with it
unrecoverable.

## 1. Generate the recovery key once

```bash
tracebisect studio backup-key generate \
  --output /secure/tracebisect/studio-backup.key
```

The command creates a new owner-only file, prints a safe 16-character key ID,
and never prints the secret. It refuses to replace an existing file. Record the
key ID in the recovery runbook, but never commit the key to source control.

## 2. Create an encrypted backup

Use a new filename every time:

```bash
tracebisect studio backup \
  --database .tracebisect/studio.db \
  --output backups/studio-2026-07-21.db.enc \
  --encryption-key-file /secure/tracebisect/studio-backup.key
```

The command takes a consistent online SQLite snapshot, removes ephemeral
sessions, queued email, webhook history, and retained server errors, verifies
the Studio schema and logical content, encrypts the file, authenticates the
completed artifact, and only then publishes it. It never replaces an existing
backup.

Copy the `.enc` file to the off-site backup location. Do not copy the key there.
The CLI does not schedule or upload the file for you.

## 3. Verify the copied artifact

Run verification against the copy that recovery would actually use:

```bash
tracebisect studio verify \
  --backup /off-site-copy/studio-2026-07-21.db.enc \
  --encryption-key-file /secure/tracebisect/studio-backup.key
```

A pass proves that the key matches, the artifact has not been modified, SQLite
integrity passes, the schema is supported, and the expected record counts can be
read. Save the encrypted SHA-256, content SHA-256, key ID, date, and operator
name in the recovery record. A wrong key or modified artifact fails
authentication and produces no restored database.

## 4. Restore without replacing current data

Always restore to a new path first:

```bash
tracebisect studio restore \
  --backup /off-site-copy/studio-2026-07-21.db.enc \
  --database .tracebisect/restored-studio.db \
  --encryption-key-file /secure/tracebisect/studio-backup.key
```

TraceBisect authenticates the encrypted artifact before it publishes the
restored database, verifies the restored logical content, and refuses to
replace any existing file. Point Studio at the restored path only after the
command passes:

```bash
TRACEBISECT_STUDIO_STORAGE=sqlite \
TRACEBISECT_STUDIO_SQLITE_PATH=.tracebisect/restored-studio.db \
uvicorn tracebisect.studio.api:app --port 8000
```

Every browser must sign in again after a restore. An older backup also restores
older users, memberships, invitation hashes, recovery-code hashes, managed key
metadata, and ingestion-token metadata. Review access and rotate credentials
after an incident restore.

## Rotate keys safely

Create a new key file and use it for all new backups. Keep the old key until
every backup encrypted with it has expired under the retention policy. Record
which safe key ID protects each backup. Test a restore with the new key before
retiring the old one.

Never overwrite or edit a key file. Never reuse a key file for another product.

## Security boundary

The portable format uses AES-256-GCM with a fresh 96-bit nonce and a 128-bit
authentication tag. Its version and nonce header are authenticated too. Large
files are processed in bounded chunks. Decrypted bytes stay in an owner-only
temporary directory and are not inspected or restored until authentication
succeeds.

This feature does not provide a scheduler, off-site transfer, retention
lifecycle, cloud secret manager, PostgreSQL point-in-time recovery, or provider
restore drill. A production operator must still automate the copy, alert on
failures, protect and test key access, delete expired artifacts, and rehearse a
restore from the real off-site location.
