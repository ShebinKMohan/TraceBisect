# Check a live Studio deployment

Use one command after a deployment or configuration change:

```bash
tracebisect studio deployment-check --url https://studio.example.com
```

Replace the example address with the public Studio address. The command reads
only `/api/ready` and `/api/health`; it does not need a workspace key and cannot
read traces, comparisons, people, or settings.

## Read the result

The first two lines answer different questions:

- **Hosted core: READY** means the live API passed its safety checks for hosted
  traffic: durable storage, required sign-in, secure browser sessions,
  fail-closed upload scanning, safe request/error records, and protected
  monitoring.
- **Full SaaS: READY** would mean Studio also reports that no production-SaaS
  milestones remain. The current product honestly reports **NOT READY** while
  provider backups, off-host operations, or other listed work is incomplete.

Every check is labelled:

- `[PASS]` — the live endpoint proves the safeguard is active.
- `[WARNING]` — the state is acceptable only in a limited context, such as HTTP
  on this computer.
- `[FAIL]` — do not put this deployment into public traffic yet.

The final `Next:` line gives one concrete first action. Fix that item and run the
same command again instead of trying to solve the entire list at once.

## Safe defaults

Public addresses must use HTTPS. Plain HTTP is accepted only for `localhost` or
a loopback IP so an operator can check a local development process:

```bash
tracebisect studio deployment-check --url http://127.0.0.1:8000
```

Health responses are size-bounded, must be JSON objects, and cannot redirect to
a different origin. The default timeout is five seconds per endpoint. Set a
different bounded timeout only when the deployment network needs it:

```bash
tracebisect studio deployment-check \
  --url https://studio.example.com \
  --timeout-seconds 10
```

The command exits with status `0` when the hosted core is ready and `2` when a
required core safeguard fails or the check cannot be completed. Full-SaaS work
is still printed when the hosted core passes, so a successful command is not a
claim that every provider or multi-region milestone is finished.

## When a check fails

Follow the first `Next:` action, then use the linked operating guide when the
fix needs more detail:

- Storage or recovery: [studio-recovery-drill.md](studio-recovery-drill.md)
- Sign-in and access: [studio-access-management.md](studio-access-management.md)
- Invitation email: [studio-email-delivery.md](studio-email-delivery.md)
- Upload scanning: [studio-upload-scanning.md](studio-upload-scanning.md)
- Monitoring and incidents: [studio-alert-runbook.md](studio-alert-runbook.md)

Do not disable a failing safeguard merely to make the command green.
