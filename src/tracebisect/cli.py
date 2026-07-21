"""Command-line interface for TraceBisect.

The V1 CLI ships the locked command surface so generated regression tests and
downstream tooling can be written against stable shapes:

- ``tracebisect --version`` and ``tracebisect demo`` are real commands.
- ``ingest`` converts OTel/OpenInference JSON or native ``.tbtrace`` input
  into canonical ``.tbtrace`` JSONL.
- ``diff`` renders a terminal comparison of two canonical traces.
- ``export-pytest`` writes a live-capture regression test file.
- ``record`` runs a scenario command with the V1 capture environment contract.
- ``studio`` provides safe storage recovery, managed access, monitoring, and
  supervised invitation-delivery commands for a durable Studio deployment.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Literal

from colorama import init as colorama_init

from tracebisect.align import AlignmentError, align
from tracebisect.demo import build_refund_baseline_trace, build_refund_candidate_trace
from tracebisect.diff import detect_divergences
from tracebisect.jsonl import read_trace, write_trace
from tracebisect.otel import import_otel_json
from tracebisect.render import render_terminal_diff
from tracebisect.schema import TraceBisectSchemaError
from tracebisect.testing import capture_trace
from tracebisect.version import __version__

if TYPE_CHECKING:
    from tracebisect.studio.backup import StudioBackupInspection
    from tracebisect.studio.email_delivery import StudioEmailDelivery
    from tracebisect.studio.managed_database import StudioDatabaseTarget
    from tracebisect.studio.postgres_migration import StudioPostgresMigrationReport
    from tracebisect.studio.postgres_storage import PostgresStudioStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracebisect",
        description="Git bisect for AI agent traces.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"tracebisect {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    subparsers.add_parser(
        "demo",
        help="Run the built-in refund-search V1 demo.",
    )

    ingest = subparsers.add_parser(
        "ingest",
        help="Convert a source trace into the canonical .tbtrace format.",
    )
    ingest.add_argument("source", help="Path to the source trace.")
    ingest.add_argument("output", help="Destination .tbtrace path.")

    record = subparsers.add_parser(
        "record",
        help="Run a command and capture the canonical trace it emits.",
    )
    record.add_argument(
        "--output",
        required=True,
        help="Destination .tbtrace path (REQUIRED).",
    )
    record.add_argument(
        "--side-effects",
        choices=["stub", "live"],
        default="stub",
        help="Side-effect policy for the recorded run (default: stub).",
    )
    record.add_argument(
        "--allow-live",
        action="store_true",
        help="Required to enable --side-effects live.",
    )
    record.add_argument(
        "record_command",
        nargs=argparse.REMAINDER,
        help="-- <command> to run under the recorder.",
    )

    diff = subparsers.add_parser(
        "diff",
        help="Align two traces and render the first meaningful divergence.",
    )
    diff.add_argument("baseline", help="Baseline .tbtrace path.")
    diff.add_argument("candidate", help="Candidate .tbtrace path.")
    diff.add_argument(
        "--mode",
        choices=["permissive", "strict", "ci"],
        default="permissive",
        help="Determinism mode for comparison (default: permissive).",
    )

    # export-pytest LOCKED SIGNATURE.
    # Positionals: baseline, output. There is intentionally NO candidate
    # positional — the candidate trace is captured fresh in CI by running the
    # command supplied via --scenario. See spec section 4.5.
    export = subparsers.add_parser(
        "export-pytest",
        help="Generate a live-capture pytest regression test from a baseline.",
    )
    export.add_argument("baseline", help="Baseline .tbtrace path.")
    export.add_argument("output", help="Output .py path for the generated test.")
    export.add_argument(
        "--scenario",
        help="Command to run in CI to capture a fresh candidate trace (REQUIRED in V1).",
    )
    export.add_argument(
        "--assert",
        dest="assert_dimensions",
        help=(
            "Comma-separated assertion dimensions. V1 dimensions: "
            "tool_args, final_output, cost, branch, error_absence."
        ),
    )
    export.add_argument(
        "--cost-threshold",
        type=float,
        default=1.5,
        help="Maximum cost ratio relative to baseline (default: 1.5).",
    )

    studio = subparsers.add_parser(
        "studio",
        help="Operate Studio storage, access keys, and monitoring safely.",
    )
    studio_commands = studio.add_subparsers(
        dest="studio_command",
        metavar="<studio-command>",
    )
    studio.set_defaults(studio_parser=studio)

    studio_backup = studio_commands.add_parser(
        "backup",
        help="Create a consistent snapshot while Studio is running.",
    )
    studio_backup.add_argument(
        "--database",
        required=True,
        help="Current TRACEBISECT_STUDIO_SQLITE_PATH value.",
    )
    studio_backup.add_argument(
        "--output",
        required=True,
        help="New backup file path. Existing files are never replaced.",
    )

    studio_verify = studio_commands.add_parser(
        "verify",
        help="Check backup integrity, compatibility, and record counts.",
    )
    studio_verify.add_argument(
        "--backup",
        required=True,
        help="Backup file to verify.",
    )

    studio_restore = studio_commands.add_parser(
        "restore",
        help="Restore a verified backup to a new database file.",
    )
    studio_restore.add_argument(
        "--backup",
        required=True,
        help="Verified backup file to restore.",
    )
    studio_restore.add_argument(
        "--database",
        required=True,
        help="New database path. Existing files are never replaced.",
    )

    studio_migrate_postgres = studio_commands.add_parser(
        "migrate-postgres",
        help="Copy a complete SQLite Studio into an empty configured PostgreSQL database.",
    )
    studio_migrate_postgres.add_argument(
        "--source",
        required=True,
        help="Existing TRACEBISECT_STUDIO_SQLITE_PATH to snapshot and migrate.",
    )
    studio_migrate_postgres.add_argument(
        "--verify-only",
        action="store_true",
        help="Compare every source and destination table without changing either database.",
    )

    studio_keys = studio_commands.add_parser(
        "keys",
        help="Create, list, and revoke hashed workspace access keys.",
    )
    studio_keys_commands = studio_keys.add_subparsers(
        dest="studio_keys_command",
        metavar="<key-command>",
    )
    studio_keys.set_defaults(studio_keys_parser=studio_keys)

    studio_keys_commands.add_parser(
        "generate-pepper",
        help="Generate the server secret used to hash managed keys.",
    )

    studio_keys_create = studio_keys_commands.add_parser(
        "create",
        help="Issue one expiring workspace key and show it once.",
    )
    studio_keys_create.add_argument(
        "--database",
        help=(
            "SQLite database path. Omit for PostgreSQL when "
            "TRACEBISECT_STUDIO_DATABASE_URL is set."
        ),
    )
    studio_keys_create.add_argument(
        "--workspace",
        required=True,
        help="Workspace this key can access.",
    )
    studio_keys_create.add_argument(
        "--name",
        required=True,
        help="Human-readable owner or purpose, such as 'CI upload'.",
    )
    studio_keys_create.add_argument(
        "--role",
        choices=["viewer", "editor", "admin"],
        default="editor",
        help=(
            "Access level: viewer reads, editor runs workflows, admin owns access "
            "(default: editor)."
        ),
    )
    studio_keys_create.add_argument(
        "--expires-in-days",
        type=int,
        default=90,
        help="Key lifetime from today (default: 90, maximum: 3650).",
    )

    studio_keys_list = studio_keys_commands.add_parser(
        "list",
        help="Show key IDs, owners, expiry, and revocation status—never secrets.",
    )
    studio_keys_list.add_argument(
        "--database",
        help=(
            "SQLite database path. Omit for PostgreSQL when "
            "TRACEBISECT_STUDIO_DATABASE_URL is set."
        ),
    )

    studio_keys_revoke = studio_keys_commands.add_parser(
        "revoke",
        help="Immediately disable one key by its non-secret key ID.",
    )
    studio_keys_revoke.add_argument(
        "--database",
        help=(
            "SQLite database path. Omit for PostgreSQL when "
            "TRACEBISECT_STUDIO_DATABASE_URL is set."
        ),
    )
    studio_keys_revoke.add_argument(
        "--key-id",
        required=True,
        help="12-character key ID shown by the list command.",
    )

    studio_ingest_tokens = studio_commands.add_parser(
        "ingest-tokens",
        help="Create upload-only tokens for agents and CI without granting Studio access.",
    )
    studio_ingest_token_commands = studio_ingest_tokens.add_subparsers(
        dest="studio_ingest_token_command",
        metavar="<token-command>",
    )
    studio_ingest_tokens.set_defaults(studio_ingest_tokens_parser=studio_ingest_tokens)

    studio_ingest_token_create = studio_ingest_token_commands.add_parser(
        "create",
        help="Issue one expiring trace-upload token and show it once.",
    )
    studio_ingest_token_create.add_argument(
        "--database",
        help=(
            "SQLite database path. Omit for PostgreSQL when "
            "TRACEBISECT_STUDIO_DATABASE_URL is set."
        ),
    )
    studio_ingest_token_create.add_argument(
        "--workspace",
        required=True,
        help="Workspace that will receive uploaded traces.",
    )
    studio_ingest_token_create.add_argument(
        "--name",
        required=True,
        help="Human-readable agent or CI job name, such as 'Production support agent'.",
    )
    studio_ingest_token_create.add_argument(
        "--expires-in-days",
        type=int,
        default=90,
        help="Token lifetime from today (default: 90, maximum: 3650).",
    )

    studio_ingest_token_list = studio_ingest_token_commands.add_parser(
        "list",
        help="Show token IDs, workspace, expiry, and status—never secrets.",
    )
    studio_ingest_token_list.add_argument(
        "--database",
        help=(
            "SQLite database path. Omit for PostgreSQL when "
            "TRACEBISECT_STUDIO_DATABASE_URL is set."
        ),
    )

    studio_ingest_token_revoke = studio_ingest_token_commands.add_parser(
        "revoke",
        help="Immediately disable one upload token by its non-secret token ID.",
    )
    studio_ingest_token_revoke.add_argument(
        "--database",
        help=(
            "SQLite database path. Omit for PostgreSQL when "
            "TRACEBISECT_STUDIO_DATABASE_URL is set."
        ),
    )
    studio_ingest_token_revoke.add_argument(
        "--token-id",
        required=True,
        help="12-character token ID shown by the list command.",
    )

    studio_metrics = studio_commands.add_parser(
        "metrics",
        help="Configure safe access to production service metrics.",
    )
    studio_metrics_commands = studio_metrics.add_subparsers(
        dest="studio_metrics_command",
        metavar="<metrics-command>",
    )
    studio_metrics.set_defaults(studio_metrics_parser=studio_metrics)
    studio_metrics_commands.add_parser(
        "generate-token",
        help="Generate the dedicated bearer token used by a metrics scraper.",
    )

    studio_identity = studio_commands.add_parser(
        "identity",
        help="Configure invitation-only human accounts and recovery.",
    )
    studio_identity_commands = studio_identity.add_subparsers(
        dest="studio_identity_command",
        metavar="<identity-command>",
    )
    studio_identity.set_defaults(studio_identity_parser=studio_identity)
    studio_identity_commands.add_parser(
        "generate-secret",
        help="Generate the server secret used to protect identity credentials.",
    )

    studio_email = studio_commands.add_parser(
        "email",
        help="Deliver queued workspace invitations with bounded retries.",
    )
    studio_email_commands = studio_email.add_subparsers(
        dest="studio_email_command",
        metavar="<email-command>",
    )
    studio_email.set_defaults(studio_email_parser=studio_email)
    studio_email_deliver = studio_email_commands.add_parser(
        "deliver",
        help="Send a bounded batch of due invitation emails.",
    )
    studio_email_deliver.add_argument(
        "--database",
        help=(
            "SQLite database path. Omit for PostgreSQL when the Studio storage "
            "environment is configured."
        ),
    )
    studio_email_deliver.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum due messages to examine (default: 20, maximum: 100).",
    )
    studio_email_work = studio_email_commands.add_parser(
        "work",
        help="Continuously drain invitation email for a supervised deployment.",
    )
    studio_email_work.add_argument(
        "--database",
        help=(
            "SQLite database path. Omit for PostgreSQL when the Studio storage "
            "environment is configured."
        ),
    )
    studio_email_work.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum due messages per cycle (default: 20, maximum: 100).",
    )
    studio_email_work.add_argument(
        "--poll-seconds",
        type=int,
        default=60,
        help="Seconds between cycles (default: 60, range: 5-3600).",
    )

    return parser


def run_demo() -> int:
    colorama_init()
    artifact_dir = Path(tempfile.mkdtemp(prefix="tracebisect-demo-"))
    baseline_path = artifact_dir / "baseline.tbtrace"
    candidate_path = artifact_dir / "candidate.tbtrace"
    scenario_path = artifact_dir / "scenario_current.py"
    test_path = artifact_dir / "test_refund_regression.py"

    baseline = build_refund_baseline_trace()
    candidate = build_refund_candidate_trace()
    write_trace(baseline, baseline_path)
    write_trace(candidate, candidate_path)
    scenario_path.write_text(
        "\n".join(
            [
                "import os",
                "import shutil",
                "from pathlib import Path",
                f"source = Path({str(baseline_path)!r})",
                "target = Path(os.environ['TRACEBISECT_OUTPUT'])",
                "shutil.copyfile(source, target)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    test_path.write_text(
        _pytest_template(
            baseline_path=str(baseline_path),
            scenario_cmd=[sys.executable, str(scenario_path)],
            assertions=["tool_args", "final_output", "cost"],
            cost_threshold=1.5,
        ),
        encoding="utf-8",
    )

    matches = align(baseline, candidate)
    divergences = detect_divergences(matches, baseline, candidate)

    print("TraceBisect V1 demo")
    print("===================")
    print()
    print("Scenario: refund agent changed its search_database filter.")
    print()
    print(render_terminal_diff(divergences, use_color=sys.stdout.isatty()))
    print()
    print("Generated pytest regression test:")
    print(f"  {test_path}")
    print()
    print("Try it:")
    print(f"  python -m tracebisect diff {baseline_path} {candidate_path}")
    print(f"  pytest {test_path}")
    print()
    print(f"Artifacts: {artifact_dir}")
    return 0


def run_ingest(source: str, output: str) -> int:
    source_path = Path(source)
    output_path = Path(output)
    try:
        if source_path.suffix == ".tbtrace":
            trace = read_trace(source_path)
        else:
            trace = import_otel_json(source_path)
        write_trace(trace, output_path)
    except (OSError, TraceBisectSchemaError) as exc:
        print(f"tracebisect ingest failed: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote canonical trace to {output_path}")
    return 0


def run_record(
    output: str,
    *,
    record_command: list[str],
    side_effects: str,
    allow_live: bool,
) -> int:
    command = list(record_command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("tracebisect record failed: command is required after --", file=sys.stderr)
        return 2
    if side_effects == "live" and not allow_live:
        print(
            "tracebisect record failed: --side-effects live requires --allow-live",
            file=sys.stderr,
        )
        return 2
    try:
        trace = capture_trace(
            command,
            side_effects=side_effects,
            allow_live=allow_live,
        )
        write_trace(trace, output)
    except (OSError, TraceBisectSchemaError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"tracebisect record failed: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote recorded trace to {output}")
    return 0


def run_diff(baseline: str, candidate: str, *, mode: str = "permissive") -> int:
    if mode not in {"permissive", "strict", "ci"}:
        print(
            "tracebisect diff failed: mode must be permissive, strict, or ci",
            file=sys.stderr,
        )
        return 2
    try:
        baseline_trace = read_trace(baseline)
        candidate_trace = read_trace(candidate)
        matches = align(baseline_trace, candidate_trace)
        divergences = detect_divergences(matches, baseline_trace, candidate_trace)
    except (OSError, TraceBisectSchemaError, AlignmentError) as exc:
        print(f"tracebisect diff failed: {exc}", file=sys.stderr)
        return 1

    colorama_init()
    print(render_terminal_diff(divergences, use_color=sys.stdout.isatty()))
    return 1 if divergences else 0


def run_export_pytest(
    baseline: str,
    output: str,
    *,
    scenario: str | None,
    assert_dimensions: str | None,
    cost_threshold: float,
) -> int:
    if not scenario:
        print("tracebisect export-pytest failed: --scenario is required in V1", file=sys.stderr)
        return 2

    assertions = _parse_assertions(assert_dimensions)
    scenario_cmd = shlex.split(scenario)
    if not scenario_cmd:
        print("tracebisect export-pytest failed: --scenario must not be empty", file=sys.stderr)
        return 2

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _pytest_template(
            baseline_path=str(Path(baseline)),
            scenario_cmd=scenario_cmd,
            assertions=assertions,
            cost_threshold=cost_threshold,
        ),
        encoding="utf-8",
    )
    print(f"Wrote pytest regression test to {output_path}")
    return 0


def _parse_assertions(raw: str | None) -> list[str]:
    if raw is None:
        return ["tool_args", "final_output", "cost"]
    assertions = [part.strip() for part in raw.split(",") if part.strip()]
    if not assertions:
        raise ValueError("--assert must contain at least one assertion dimension")
    return assertions


def _pytest_template(
    *,
    baseline_path: str,
    scenario_cmd: list[str],
    assertions: list[str],
    cost_threshold: float,
) -> str:
    assertion_literal = "[" + ", ".join(json.dumps(item) for item in assertions) + "]"
    scenario_literal = json.dumps(scenario_cmd, indent=4)
    return f'''"""Generated TraceBisect regression test."""

from tracebisect.testing import (
    assert_aligned,
    capture_trace,
    load_baseline,
)

BASELINE_PATH = {json.dumps(baseline_path)}
SCENARIO_CMD = {scenario_literal}


def test_tracebisect_regression():
    baseline = load_baseline(BASELINE_PATH)
    candidate = capture_trace(SCENARIO_CMD)

    assert_aligned(
        baseline=baseline,
        candidate=candidate,
        assertions={assertion_literal},
        cost_threshold={cost_threshold!r},
        mode="ci",
    )
'''


def run_studio_backup(database: str, output: str) -> int:
    from tracebisect.studio.backup import create_studio_backup

    inspection = create_studio_backup(database, output)
    _print_studio_backup_summary("Studio backup created", Path(output), inspection)
    print()
    print("Next: copy this backup away from the Studio server, then verify it with:")
    print(f"  tracebisect studio verify --backup {shlex.quote(str(Path(output)))}")
    return 0


def run_studio_verify(backup: str) -> int:
    from tracebisect.studio.backup import inspect_studio_backup

    inspection = inspect_studio_backup(backup)
    _print_studio_backup_summary("Studio backup is healthy", Path(backup), inspection)
    return 0


def run_studio_restore(backup: str, database: str) -> int:
    from tracebisect.studio.backup import restore_studio_backup

    inspection = restore_studio_backup(backup, database)
    _print_studio_backup_summary("Studio backup restored", Path(database), inspection)
    print()
    print("Next: set TRACEBISECT_STUDIO_SQLITE_PATH to this new file and restart Studio.")
    return 0


def run_studio_postgres_migration(source: str, *, verify_only: bool) -> int:
    from tracebisect.studio.postgres_migration import (
        migrate_sqlite_to_postgres,
        verify_sqlite_postgres_migration,
    )

    with _studio_postgres_store() as destination:
        report = (
            verify_sqlite_postgres_migration(source, destination)
            if verify_only
            else migrate_sqlite_to_postgres(source, destination)
        )
    _print_studio_migration_summary(report, verify_only=verify_only)
    return 0


def _print_studio_migration_summary(
    report: StudioPostgresMigrationReport,
    *,
    verify_only: bool,
) -> None:
    title = (
        "PostgreSQL migration verified"
        if verify_only
        else "PostgreSQL migration completed and verified"
    )
    print(title)
    print(f"  Source: {report.source_path}")
    print(f"  Studio schema: {report.schema_version}")
    print(f"  Workspaces: {report.workspace_count}")
    print(f"  Rows reconciled: {report.total_rows}")
    print(f"  Content SHA-256: {report.content_sha256}")
    print()
    print("Security and delivery state copied")
    print(f"  Browser sessions: {report.browser_session_count}")
    print(f"  Human sessions: {report.identity_session_count}")
    print(f"  Queued/retrying email: {report.queued_email_count}")
    if not verify_only:
        print()
        print("Next:")
        print("  1. Keep the old API and every email worker stopped.")
        print("  2. Keep the existing key pepper and identity secret for this cutover.")
        print("  3. Point Studio at PostgreSQL and start one API instance.")
        print("  4. Check /api/ready before starting workers or adding replicas.")


def run_studio_email_deliver(database: str | None, limit: int) -> int:
    with _studio_email_delivery(database) as delivery:
        result = delivery.deliver_due(limit=limit)
    print("Studio invitation email delivery finished")
    print(f"  Examined: {result.examined}")
    print(f"  Accepted by provider: {result.sent}")
    print(f"  Waiting to retry: {result.retrying}")
    print(f"  Permanently failed: {result.failed}")
    return 2 if result.failed else 0


def run_studio_email_worker(database: str | None, limit: int, poll_seconds: int) -> int:
    from tracebisect.studio.email_delivery import StudioEmailDeliveryError

    if not 5 <= poll_seconds <= 3600:
        raise StudioEmailDeliveryError("email worker poll seconds must be between 5 and 3600")
    stop = Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    print(f"Studio email worker started; polling every {poll_seconds} seconds", flush=True)
    with _studio_email_delivery(database) as delivery:
        while not stop.is_set():
            result = delivery.deliver_due(limit=limit)
            print(
                "Studio email cycle: "
                f"examined={result.examined} accepted={result.sent} "
                f"retrying={result.retrying} failed={result.failed}",
                flush=True,
            )
            stop.wait(poll_seconds)
    print("Studio email worker stopped", flush=True)
    return 0


@contextmanager
def _studio_email_delivery(database: str | None) -> Iterator[StudioEmailDelivery]:
    """Share the configured durable store with the invitation email worker."""
    from tracebisect.studio.email_delivery import (
        StudioEmailDelivery,
        StudioEmailDeliveryError,
    )

    env = dict(os.environ)
    if database:
        env["TRACEBISECT_STUDIO_STORAGE"] = "sqlite"
        env["TRACEBISECT_STUDIO_SQLITE_PATH"] = database
        yield StudioEmailDelivery.from_env(env)
        return
    if env.get("TRACEBISECT_STUDIO_STORAGE", "").strip().lower() != "postgres":
        raise StudioEmailDeliveryError(
            "provide --database for SQLite or configure PostgreSQL Studio storage"
        )
    from tracebisect.studio.postgres_storage import PostgresStudioStore
    from tracebisect.studio.storage import create_studio_store

    store = create_studio_store(env)
    if not isinstance(store, PostgresStudioStore):
        raise StudioEmailDeliveryError("configured Studio storage is not PostgreSQL")
    try:
        yield StudioEmailDelivery.from_env(env, managed_database=store)
    finally:
        store.close()


@contextmanager
def _studio_postgres_store() -> Iterator[PostgresStudioStore]:
    """Open the configured destination without accepting its URL as a CLI argument."""
    from tracebisect.studio.postgres_migration import StudioPostgresMigrationError
    from tracebisect.studio.postgres_storage import PostgresStudioStore
    from tracebisect.studio.storage import create_studio_store

    env = dict(os.environ)
    if not env.get("TRACEBISECT_STUDIO_DATABASE_URL", "").strip():
        raise StudioPostgresMigrationError(
            "set TRACEBISECT_STUDIO_DATABASE_URL to the empty PostgreSQL destination"
        )
    env["TRACEBISECT_STUDIO_STORAGE"] = "postgres"
    env.pop("TRACEBISECT_STUDIO_SQLITE_PATH", None)
    store = create_studio_store(env)
    if not isinstance(store, PostgresStudioStore):
        raise StudioPostgresMigrationError("configured migration destination is not PostgreSQL")
    try:
        yield store
    finally:
        store.close()


def _print_studio_backup_summary(
    title: str,
    path: Path,
    inspection: StudioBackupInspection,
) -> None:
    print(title)
    print(f"  File: {path.expanduser().resolve()}")
    print(f"  Schema: {inspection.schema_version}")
    print(f"  Workspaces: {inspection.workspace_count}")
    print(f"  Traces: {inspection.trace_count}")
    print(f"  Comparisons: {inspection.report_count}")
    print(f"  Guardrails: {inspection.case_count}")
    print(f"  Access keys: {inspection.api_key_count}")
    print(f"  Ingestion tokens: {inspection.ingestion_token_count}")
    print(f"  People: {inspection.user_count}")
    print(f"  Memberships: {inspection.membership_count}")
    print(f"  Size: {inspection.size_bytes} bytes")
    print(f"  SHA-256: {inspection.sha256}")


def run_studio_keys_generate_pepper() -> int:
    from tracebisect.studio.access_keys import API_KEY_PEPPER_ENV, generate_api_key_pepper

    print("Managed-key server secret generated")
    print("Store this value in your deployment secret manager. Do not commit it.")
    print(f"  {API_KEY_PEPPER_ENV}={generate_api_key_pepper()}")
    print()
    print("Next: export that variable, then create a workspace key.")
    print(
        "  SQLite: tracebisect studio keys create --database studio.db "
        "--workspace team-a --name you"
    )
    print("  PostgreSQL: set TRACEBISECT_STUDIO_DATABASE_URL, then omit --database")
    return 0


def run_studio_keys_create(
    database: str | None,
    workspace: str,
    name: str,
    role: Literal["viewer", "editor", "admin"],
    expires_in_days: int,
) -> int:
    from tracebisect.studio.access_keys import api_key_pepper, create_studio_api_key

    with _studio_key_database(database) as target:
        issued = create_studio_api_key(
            target,
            workspace_id=workspace,
            role=role,
            label=name,
            expires_in_days=expires_in_days,
            pepper=api_key_pepper(),
        )
    print("Workspace access key created")
    print(f"  Key ID: {issued.record.key_id}")
    print(f"  Workspace: {issued.record.workspace_id}")
    print(f"  Role: {issued.record.role}")
    print(f"  Name: {issued.record.label}")
    print(f"  Expires: {issued.record.expires_at}")
    print()
    print("Copy this key now. TraceBisect stores only its hash and cannot show it again:")
    print(f"  {issued.api_key}")
    print()
    print("To rotate safely: create a replacement, update the client, then revoke this key ID.")
    return 0


def run_studio_keys_list(database: str | None) -> int:
    from tracebisect.studio.access_keys import list_studio_api_keys

    with _studio_key_database(database) as target:
        records = list_studio_api_keys(target)
    if not records:
        print("No managed workspace keys exist yet.")
        print("Create one with: tracebisect studio keys create --help")
        return 0
    print("Managed workspace keys")
    for record in records:
        print(
            f"- {record.key_id} · {record.status} · {record.role} · "
            f"{record.workspace_id} · {record.label}"
        )
        print(f"  expires {record.expires_at}")
    return 0


def run_studio_keys_revoke(database: str | None, key_id: str) -> int:
    from tracebisect.studio.access_keys import revoke_studio_api_key

    with _studio_key_database(database) as target:
        record = revoke_studio_api_key(target, key_id=key_id)
    print("Workspace access key revoked")
    print(f"  Key ID: {record.key_id}")
    print(f"  Workspace: {record.workspace_id}")
    print(f"  Role: {record.role}")
    print(f"  Name: {record.label}")
    print(f"  Revoked: {record.revoked_at}")
    return 0


def run_studio_ingest_tokens_create(
    database: str | None,
    workspace: str,
    name: str,
    expires_in_days: int,
) -> int:
    from tracebisect.studio.access_keys import api_key_pepper
    from tracebisect.studio.ingestion_tokens import create_studio_ingestion_token

    with _studio_key_database(database) as target:
        issued = create_studio_ingestion_token(
            target,
            workspace_id=workspace,
            label=name,
            expires_in_days=expires_in_days,
            pepper=api_key_pepper(),
        )
    print("Trace-upload token created")
    print(f"  Token ID: {issued.record.token_id}")
    print(f"  Workspace: {issued.record.workspace_id}")
    print(f"  Name: {issued.record.label}")
    print(f"  Permission: {issued.record.scope} (upload only)")
    print(f"  Expires: {issued.record.expires_at}")
    print()
    print("Copy this token now. TraceBisect stores only its hash and cannot show it again:")
    print(f"  {issued.token}")
    print()
    print("Send it as a Bearer token only to POST /api/traces/upload.")
    print("It cannot open Studio, read workspace data, or start a browser session.")
    return 0


def run_studio_ingest_tokens_list(database: str | None) -> int:
    from tracebisect.studio.ingestion_tokens import list_studio_ingestion_tokens

    with _studio_key_database(database) as target:
        records = list_studio_ingestion_tokens(target)
    if not records:
        print("No trace-upload tokens exist yet.")
        print("Create one with: tracebisect studio ingest-tokens create --help")
        return 0
    print("Trace-upload tokens")
    for record in records:
        print(
            f"- {record.token_id} · {record.status} · upload only · "
            f"{record.workspace_id} · {record.label}"
        )
        print(f"  expires {record.expires_at}")
    return 0


def run_studio_ingest_tokens_revoke(database: str | None, token_id: str) -> int:
    from tracebisect.studio.ingestion_tokens import revoke_studio_ingestion_token

    with _studio_key_database(database) as target:
        record = revoke_studio_ingestion_token(target, token_id=token_id)
    print("Trace-upload token revoked")
    print(f"  Token ID: {record.token_id}")
    print(f"  Workspace: {record.workspace_id}")
    print(f"  Name: {record.label}")
    print(f"  Revoked: {record.revoked_at}")
    return 0


@contextmanager
def _studio_key_database(database: str | None) -> Iterator[StudioDatabaseTarget]:
    """Resolve a beginner-friendly SQLite path or configured PostgreSQL URL."""
    if database:
        yield database
        return
    database_url = os.getenv("TRACEBISECT_STUDIO_DATABASE_URL", "").strip()
    if not database_url:
        from tracebisect.studio.access_keys import StudioApiKeyError

        raise StudioApiKeyError(
            "provide --database for SQLite or set TRACEBISECT_STUDIO_DATABASE_URL "
            "for PostgreSQL"
        )
    if not database_url.startswith(("postgresql://", "postgres://")):
        from tracebisect.studio.storage import StudioConfigurationError

        raise StudioConfigurationError(
            "TRACEBISECT_STUDIO_DATABASE_URL must be a PostgreSQL URL"
        )
    from tracebisect.studio.postgres_storage import PostgresStudioStore

    store = PostgresStudioStore(database_url, workspace_id="operator")
    try:
        yield store
    finally:
        store.close()


def run_studio_metrics_generate_token() -> int:
    from tracebisect.studio.metrics import METRICS_TOKEN_ENV, generate_metrics_token

    print("Studio metrics token generated")
    print("Store this value in your deployment secret manager. Do not use a workspace key.")
    print(f"  {METRICS_TOKEN_ENV}={generate_metrics_token()}")
    print()
    print("Next: configure your metrics scraper to send this value as a bearer token.")
    return 0


def run_studio_identity_generate_secret() -> int:
    from tracebisect.studio.identity import IDENTITY_SECRET_ENV, generate_identity_secret

    print("Human-account server secret generated")
    print("Store this value in your deployment secret manager. Do not commit it.")
    print(f"  {IDENTITY_SECRET_ENV}={generate_identity_secret()}")
    print()
    print("Restart Studio, then use an admin key in Settings to invite the first person.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    command = args.command

    if command is None:
        parser.print_help()
        return 0

    if command == "demo":
        return run_demo()

    if command == "ingest":
        return run_ingest(args.source, args.output)

    if command == "record":
        return run_record(
            args.output,
            record_command=args.record_command,
            side_effects=args.side_effects,
            allow_live=args.allow_live,
        )

    if command == "diff":
        return run_diff(args.baseline, args.candidate, mode=args.mode)

    if command == "export-pytest":
        try:
            return run_export_pytest(
                args.baseline,
                args.output,
                scenario=args.scenario,
                assert_dimensions=args.assert_dimensions,
                cost_threshold=args.cost_threshold,
            )
        except ValueError as exc:
            print(f"tracebisect export-pytest failed: {exc}", file=sys.stderr)
            return 2

    if command == "studio":
        if args.studio_command is None:
            args.studio_parser.print_help()
            return 0

        from tracebisect.studio.access_keys import StudioApiKeyError
        from tracebisect.studio.backup import StudioBackupError
        from tracebisect.studio.email_delivery import StudioEmailDeliveryError
        from tracebisect.studio.ingestion_tokens import StudioIngestionTokenError
        from tracebisect.studio.postgres_migration import StudioPostgresMigrationError
        from tracebisect.studio.storage import StudioConfigurationError

        try:
            if args.studio_command == "backup":
                return run_studio_backup(args.database, args.output)
            if args.studio_command == "verify":
                return run_studio_verify(args.backup)
            if args.studio_command == "restore":
                return run_studio_restore(args.backup, args.database)
            if args.studio_command == "migrate-postgres":
                return run_studio_postgres_migration(
                    args.source,
                    verify_only=args.verify_only,
                )
            if args.studio_command == "keys":
                if args.studio_keys_command is None:
                    args.studio_keys_parser.print_help()
                    return 0
                if args.studio_keys_command == "generate-pepper":
                    return run_studio_keys_generate_pepper()
                if args.studio_keys_command == "create":
                    return run_studio_keys_create(
                        args.database,
                        args.workspace,
                        args.name,
                        args.role,
                        args.expires_in_days,
                    )
                if args.studio_keys_command == "list":
                    return run_studio_keys_list(args.database)
                if args.studio_keys_command == "revoke":
                    return run_studio_keys_revoke(args.database, args.key_id)
            if args.studio_command == "ingest-tokens":
                if args.studio_ingest_token_command is None:
                    args.studio_ingest_tokens_parser.print_help()
                    return 0
                if args.studio_ingest_token_command == "create":
                    return run_studio_ingest_tokens_create(
                        args.database,
                        args.workspace,
                        args.name,
                        args.expires_in_days,
                    )
                if args.studio_ingest_token_command == "list":
                    return run_studio_ingest_tokens_list(args.database)
                if args.studio_ingest_token_command == "revoke":
                    return run_studio_ingest_tokens_revoke(args.database, args.token_id)
            if args.studio_command == "metrics":
                if args.studio_metrics_command is None:
                    args.studio_metrics_parser.print_help()
                    return 0
                if args.studio_metrics_command == "generate-token":
                    return run_studio_metrics_generate_token()
            if args.studio_command == "identity":
                if args.studio_identity_command is None:
                    args.studio_identity_parser.print_help()
                    return 0
                if args.studio_identity_command == "generate-secret":
                    return run_studio_identity_generate_secret()
            if args.studio_command == "email":
                if args.studio_email_command is None:
                    args.studio_email_parser.print_help()
                    return 0
                if args.studio_email_command == "deliver":
                    return run_studio_email_deliver(args.database, args.limit)
                if args.studio_email_command == "work":
                    return run_studio_email_worker(
                        args.database,
                        args.limit,
                        args.poll_seconds,
                    )
        except (
            StudioApiKeyError,
            StudioBackupError,
            StudioConfigurationError,
            StudioEmailDeliveryError,
            StudioIngestionTokenError,
            StudioPostgresMigrationError,
        ) as exc:
            failed_command = args.studio_command
            if args.studio_command == "keys" and args.studio_keys_command is not None:
                failed_command = f"keys {args.studio_keys_command}"
            if (
                args.studio_command == "ingest-tokens"
                and args.studio_ingest_token_command is not None
            ):
                failed_command = f"ingest-tokens {args.studio_ingest_token_command}"
            if args.studio_command == "metrics" and args.studio_metrics_command is not None:
                failed_command = f"metrics {args.studio_metrics_command}"
            if args.studio_command == "identity" and args.studio_identity_command is not None:
                failed_command = f"identity {args.studio_identity_command}"
            if args.studio_command == "email" and args.studio_email_command is not None:
                failed_command = f"email {args.studio_email_command}"
            print(f"tracebisect studio {failed_command} failed: {exc}", file=sys.stderr)
            return 2

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
