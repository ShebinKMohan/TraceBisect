# Rehearse a Studio SQLite recovery

This guide proves that a current SQLite Studio can be backed up, restored, and
opened again without replacing the live database. You do not need SQLite
knowledge, and you do not need to stop Studio.

## Run one safe drill

Choose a new report filename for each drill:

```bash
tracebisect studio recovery-drill \
  --database .tracebisect/studio.db \
  --report operations/recovery-drills/2026-07-21.json
```

The command exits successfully only after all five checks pass:

1. A point-in-time backup is created from the live database.
2. SQLite integrity, Studio schema, and record counts are verified.
3. Browser sessions, human sessions, queued email, webhook history, and retained
   server errors are confirmed absent from the backup.
4. The backup is restored into an isolated temporary database and its logical
   content is matched to the backup.
5. The restored Studio store opens and passes its readiness check.

Temporary backup and restore files are removed automatically. The live database
is read for its snapshot but is never replaced. The JSON report contains the
date, duration, checks, record counts, and content hashes; it contains no
passwords, keys, sessions, email bodies, trace contents, or temporary paths.

## When the database is large

The default drill uses the operating system's private temporary directory. It
needs working space for both a backup and a restored copy. Choose a private
scratch directory with at least twice the current database size when the default
temporary space is too small:

```bash
tracebisect studio recovery-drill \
  --database /data/studio.db \
  --report /operations/recovery-drills/2026-07-21.json \
  --scratch-directory /private-recovery-scratch
```

The command creates the scratch directory when needed and removes its drill
subdirectory afterward. It never replaces an existing evidence report, so a
mistyped date cannot silently erase an earlier result.

## What to record

Keep the JSON report with the drill date, operator name, deployment version, and
follow-up owner. Run the drill at least monthly and before a risky upgrade.

If the command fails, the absence of a pass report is intentional. Keep Studio
on the current database, preserve the error text, check available disk space and
file permissions, and correct the cause before rerunning with a new report path.

## What this does not prove

This local drill does not prove encryption, off-site transfer, host-loss
recovery, PostgreSQL provider backups, point-in-time recovery, external secret
access, or an agreed recovery-time objective. A hosted launch still needs a
restore from the actual off-site or provider backup into an isolated environment.
