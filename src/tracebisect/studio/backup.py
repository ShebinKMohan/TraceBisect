"""Safe backup, verification, and restore helpers for TraceBisect Studio."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tracebisect.studio.storage import SCHEMA_VERSION

_REQUIRED_TABLES = frozenset(
    {
        "studio_schema",
        "studio_traces",
        "studio_reports",
        "studio_cases",
        "studio_metadata",
        "studio_api_keys",
        "studio_ingestion_tokens",
        "studio_error_events",
        "studio_browser_sessions",
        "studio_users",
        "studio_workspace_memberships",
        "studio_invitations",
        "studio_recovery_codes",
        "studio_identity_sessions",
        "studio_email_outbox",
        "studio_email_webhook_events",
    }
)
_HASH_CHUNK_BYTES = 1024 * 1024


class StudioBackupError(RuntimeError):
    """Raised when a Studio backup cannot be created, verified, or restored."""


@dataclass(frozen=True, slots=True)
class StudioBackupInspection:
    """Operator-safe facts proven directly from a Studio backup."""

    schema_version: int
    workspace_count: int
    trace_count: int
    report_count: int
    case_count: int
    api_key_count: int
    ingestion_token_count: int
    user_count: int
    membership_count: int
    size_bytes: int
    sha256: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class StudioRecoveryDrillReport:
    """Portable evidence from one isolated backup-and-restore rehearsal."""

    started_at: str
    completed_at: str
    duration_ms: int
    backup: StudioBackupInspection
    restored_content_sha256: str
    restored_store_ready: bool
    report_version: int = 1


def create_studio_backup(
    database_path: str | Path,
    output_path: str | Path,
) -> StudioBackupInspection:
    """Create a consistent live SQLite snapshot without replacing an existing file."""
    source = _existing_file(database_path, label="Studio database")
    output = _new_destination(output_path, label="backup")
    if source == output:
        raise StudioBackupError("the backup path must be different from the Studio database")

    temporary = _temporary_path(output)
    try:
        _copy_database(source, temporary)
        _clear_ephemeral_state(temporary)
        inspection = inspect_studio_backup(temporary)
        _publish_new_file(temporary, output)
    except (OSError, sqlite3.DatabaseError) as exc:
        raise StudioBackupError("could not create a consistent Studio backup") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return inspection


def inspect_studio_backup(backup_path: str | Path) -> StudioBackupInspection:
    """Verify SQLite integrity, Studio schema compatibility, and record counts."""
    backup = _existing_file(backup_path, label="backup")
    try:
        with _read_only_connection(backup) as connection:
            connection.execute("PRAGMA trusted_schema = OFF")
            integrity_row = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity_row != ("ok",):
                raise StudioBackupError("the backup failed SQLite integrity verification")

            table_names = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            if not _REQUIRED_TABLES.issubset(table_names):
                raise StudioBackupError("the file is not a complete TraceBisect Studio backup")

            schema_row = connection.execute("SELECT version FROM studio_schema LIMIT 1").fetchone()
            if schema_row is None or not isinstance(schema_row[0], int):
                raise StudioBackupError("the backup has no valid Studio schema version")
            schema_version = schema_row[0]
            if schema_version != SCHEMA_VERSION:
                raise StudioBackupError(
                    f"Studio backup schema {schema_version} is not supported by this release"
                )

            workspace_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT workspace_id FROM studio_traces
                        UNION SELECT workspace_id FROM studio_reports
                        UNION SELECT workspace_id FROM studio_cases
                        UNION SELECT workspace_id FROM studio_metadata
                        UNION SELECT workspace_id FROM studio_api_keys
                        UNION SELECT workspace_id FROM studio_ingestion_tokens
                        UNION SELECT workspace_id FROM studio_workspace_memberships
                        UNION SELECT workspace_id FROM studio_invitations
                    )
                    """
                ).fetchone()[0]
            )
            trace_count = _table_count(connection, "studio_traces")
            report_count = _table_count(connection, "studio_reports")
            case_count = _table_count(connection, "studio_cases")
            api_key_count = _table_count(connection, "studio_api_keys")
            ingestion_token_count = _table_count(connection, "studio_ingestion_tokens")
            user_count = _table_count(connection, "studio_users")
            membership_count = _table_count(connection, "studio_workspace_memberships")
            if _table_count(connection, "studio_browser_sessions") != 0:
                raise StudioBackupError(
                    "the backup contains browser sessions and is unsafe to restore"
                )
            if _table_count(connection, "studio_identity_sessions") != 0:
                raise StudioBackupError(
                    "the backup contains identity sessions and is unsafe to restore"
                )
            if _table_count(connection, "studio_email_outbox") != 0:
                raise StudioBackupError(
                    "the backup contains email delivery records and is unsafe to restore"
                )
            if _table_count(connection, "studio_email_webhook_events") != 0:
                raise StudioBackupError(
                    "the backup contains email webhook records and is unsafe to restore"
                )
            if _table_count(connection, "studio_error_events") != 0:
                raise StudioBackupError(
                    "the backup contains retained server errors and is unsafe to restore"
                )
            content_sha256 = _content_sha256(connection)
    except StudioBackupError:
        raise
    except (OSError, sqlite3.DatabaseError, TypeError, ValueError) as exc:
        raise StudioBackupError("the file is not a valid TraceBisect Studio backup") from exc

    return StudioBackupInspection(
        schema_version=schema_version,
        workspace_count=workspace_count,
        trace_count=trace_count,
        report_count=report_count,
        case_count=case_count,
        api_key_count=api_key_count,
        ingestion_token_count=ingestion_token_count,
        user_count=user_count,
        membership_count=membership_count,
        size_bytes=backup.stat().st_size,
        sha256=_sha256(backup),
        content_sha256=content_sha256,
    )


def restore_studio_backup(
    backup_path: str | Path,
    database_path: str | Path,
) -> StudioBackupInspection:
    """Restore a verified backup to a new database path without overwriting data."""
    backup = _existing_file(backup_path, label="backup")
    destination = _new_destination(database_path, label="restore database")
    if backup == destination:
        raise StudioBackupError("the restore path must be different from the backup")

    source_inspection = inspect_studio_backup(backup)
    temporary = _temporary_path(destination)
    try:
        _copy_database(backup, temporary)
        restored_inspection = inspect_studio_backup(temporary)
        if _logical_signature(restored_inspection) != _logical_signature(source_inspection):
            raise StudioBackupError("the restored database does not match the verified backup")
        _publish_new_file(temporary, destination)
    except StudioBackupError:
        raise
    except (OSError, sqlite3.DatabaseError) as exc:
        raise StudioBackupError("could not restore the Studio backup") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return restored_inspection


def run_studio_recovery_drill(
    database_path: str | Path,
    report_path: str | Path,
    *,
    scratch_directory: str | Path | None = None,
) -> StudioRecoveryDrillReport:
    """Prove backup, restore, and store readiness without replacing the live database."""
    source = _existing_file(database_path, label="Studio database")
    report_destination = _new_destination(report_path, label="recovery drill report")
    scratch = _recovery_scratch_directory(scratch_directory)
    started_at = datetime.now(timezone.utc)
    started_clock = time.monotonic()

    try:
        with tempfile.TemporaryDirectory(
            prefix="tracebisect-studio-recovery-",
            dir=scratch,
        ) as raw_dir:
            drill_directory = Path(raw_dir)
            backup_path = drill_directory / "verified-backup.db"
            restored_path = drill_directory / "restored-studio.db"
            backup_inspection = create_studio_backup(source, backup_path)
            restored_inspection = restore_studio_backup(backup_path, restored_path)
            restored_store_ready = _restored_store_is_ready(restored_path)
            final_restored_inspection = inspect_studio_backup(restored_path)
            if _logical_signature(final_restored_inspection) != _logical_signature(
                backup_inspection
            ):
                raise StudioBackupError(
                    "the restored Studio changed while its readiness was checked"
                )
    except StudioBackupError:
        raise
    except OSError as exc:
        raise StudioBackupError("could not complete the isolated recovery drill") from exc

    completed_at = datetime.now(timezone.utc)
    report = StudioRecoveryDrillReport(
        started_at=_timestamp(started_at),
        completed_at=_timestamp(completed_at),
        duration_ms=round(max(0.0, time.monotonic() - started_clock) * 1000),
        backup=backup_inspection,
        restored_content_sha256=restored_inspection.content_sha256,
        restored_store_ready=restored_store_ready,
    )
    _write_recovery_drill_report(report_destination, report)
    return report


def _copy_database(source: Path, destination: Path) -> None:
    with (
        _read_only_connection(source) as source_connection,
        sqlite3.connect(destination) as destination_connection,
    ):
        source_connection.backup(destination_connection)
        destination_connection.execute("PRAGMA journal_mode = DELETE")
    with destination.open("rb") as handle:
        os.fsync(handle.fileno())


def _clear_ephemeral_state(database_path: Path) -> None:
    """Keep product data without resurrecting sessions or sending stale email."""
    with sqlite3.connect(database_path, timeout=5) as connection:
        connection.execute("DELETE FROM studio_browser_sessions")
        connection.execute("DELETE FROM studio_identity_sessions")
        connection.execute("DELETE FROM studio_email_outbox")
        connection.execute("DELETE FROM studio_email_webhook_events")
        connection.execute("DELETE FROM studio_error_events")
    with database_path.open("rb") as handle:
        os.fsync(handle.fileno())


def _restored_store_is_ready(database_path: Path) -> bool:
    from tracebisect.studio.storage import SQLiteStudioStore, StudioPersistenceError

    try:
        store = SQLiteStudioStore(database_path, workspace_id="recovery-drill")
    except StudioPersistenceError as exc:
        raise StudioBackupError("the restored Studio could not start") from exc
    try:
        if not store.check_health():
            raise StudioBackupError("the restored Studio did not pass its readiness check")
        return True
    finally:
        store.close()


def _read_only_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)


def _existing_file(path: str | Path, *, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise StudioBackupError(f"{label} does not exist or is not a file")
    return resolved


def _new_destination(path: str | Path, *, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if resolved.exists():
        raise StudioBackupError(f"{label} already exists; choose a new path")
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StudioBackupError(f"could not create the {label} directory") from exc
    return resolved


def _temporary_path(destination: Path) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(raw_path)
    temporary.chmod(0o600)
    return temporary


def _recovery_scratch_directory(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    scratch = Path(path).expanduser().resolve()
    if scratch.exists() and not scratch.is_dir():
        raise StudioBackupError("recovery drill scratch path is not a directory")
    try:
        scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise StudioBackupError("could not create the recovery drill scratch directory") from exc
    if not scratch.is_dir():
        raise StudioBackupError("recovery drill scratch path is not a directory")
    return scratch


def _publish_new_file(
    temporary: Path,
    destination: Path,
    *,
    label: str = "database file",
) -> None:
    try:
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise StudioBackupError(f"{label} already exists; no data was replaced") from exc
    except OSError as exc:
        raise StudioBackupError(f"could not publish the completed {label}") from exc


def _write_recovery_drill_report(
    destination: Path,
    report: StudioRecoveryDrillReport,
) -> None:
    temporary = _temporary_path(destination)
    payload = {
        "report_version": report.report_version,
        "result": "passed",
        "started_at": report.started_at,
        "completed_at": report.completed_at,
        "duration_ms": report.duration_ms,
        "checks": {
            "backup_integrity": "passed",
            "ephemeral_state_stripped": "passed",
            "restore_content_match": "passed",
            "restored_store_readiness": "passed",
            "live_database_replaced": False,
        },
        "evidence": {
            "schema_version": report.backup.schema_version,
            "workspace_count": report.backup.workspace_count,
            "trace_count": report.backup.trace_count,
            "comparison_count": report.backup.report_count,
            "guardrail_count": report.backup.case_count,
            "api_key_count": report.backup.api_key_count,
            "ingestion_token_count": report.backup.ingestion_token_count,
            "user_count": report.backup.user_count,
            "membership_count": report.backup.membership_count,
            "backup_size_bytes": report.backup.size_bytes,
            "backup_sha256": report.backup.sha256,
            "content_sha256": report.backup.content_sha256,
            "restored_content_sha256": report.restored_content_sha256,
        },
    }
    try:
        with temporary.open("w", encoding="utf-8") as report_file:
            json.dump(payload, report_file, ensure_ascii=True, indent=2, sort_keys=True)
            report_file.write("\n")
            report_file.flush()
            os.fsync(report_file.fileno())
        _publish_new_file(temporary, destination, label="recovery drill report")
    except StudioBackupError:
        raise
    except OSError as exc:
        raise StudioBackupError("could not write the recovery drill report") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _table_count(connection: sqlite3.Connection, table_name: str) -> int:
    # Only internal constant table names reach this helper.
    row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    if row is None:
        raise StudioBackupError("the backup is missing a required Studio table")
    return int(row[0])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_sha256(connection: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    queries = (
        ("schema", "SELECT version FROM studio_schema ORDER BY rowid"),
        (
            "traces",
            """
            SELECT workspace_id, trace_key, display_name, trace_jsonl, created_at
            FROM studio_traces ORDER BY workspace_id, trace_key
            """,
        ),
        (
            "reports",
            """
            SELECT workspace_id, report_id, payload_json, created_at
            FROM studio_reports ORDER BY workspace_id, report_id
            """,
        ),
        (
            "cases",
            """
            SELECT workspace_id, case_id, payload_json, updated_at
            FROM studio_cases ORDER BY workspace_id, case_id
            """,
        ),
        (
            "metadata",
            """
            SELECT workspace_id, key, value
            FROM studio_metadata ORDER BY workspace_id, key
            """,
        ),
        (
            "api_keys",
            """
            SELECT key_id, workspace_id, role, label, key_hash, created_at, expires_at, revoked_at
            FROM studio_api_keys ORDER BY key_id
            """,
        ),
        (
            "ingestion_tokens",
            """
            SELECT token_id, workspace_id, label, scope, token_hash,
                   created_at, expires_at, revoked_at
            FROM studio_ingestion_tokens ORDER BY token_id
            """,
        ),
        (
            "error_events",
            """
            SELECT event_id, request_id, workspace_id, action, method,
                   status_code, error_type, failure_location, fingerprint,
                   occurred_at, event_version
            FROM studio_error_events ORDER BY event_id
            """,
        ),
        (
            "browser_sessions",
            """
            SELECT session_id, key_id, session_hash, created_at, expires_at, revoked_at
            FROM studio_browser_sessions ORDER BY session_id
            """,
        ),
        (
            "users",
            """
            SELECT user_id, email, display_name, password_hash, session_epoch,
                   created_at, password_changed_at, disabled_at
            FROM studio_users ORDER BY user_id
            """,
        ),
        (
            "memberships",
            """
            SELECT workspace_id, user_id, role, created_at, updated_at
            FROM studio_workspace_memberships ORDER BY workspace_id, user_id
            """,
        ),
        (
            "invitations",
            """
            SELECT invitation_id, workspace_id, email, role, token_hash,
                   created_at, expires_at, accepted_at, revoked_at
            FROM studio_invitations ORDER BY invitation_id
            """,
        ),
        (
            "recovery_codes",
            """
            SELECT code_id, user_id, code_hash, created_at, used_at
            FROM studio_recovery_codes ORDER BY code_id
            """,
        ),
        (
            "identity_sessions",
            """
            SELECT session_id, user_id, workspace_id, session_hash, session_epoch,
                   created_at, expires_at, revoked_at
            FROM studio_identity_sessions ORDER BY session_id
            """,
        ),
        (
            "email_outbox",
            """
            SELECT message_id, invitation_id, workspace_id, recipient_email,
                   payload_ciphertext, status, attempt_count, created_at,
                   available_at, lease_expires_at, lease_token, sent_at, failed_at,
                   provider_message_id, last_error_code, provider_status,
                   provider_event_at, provider_event_id
            FROM studio_email_outbox ORDER BY message_id
            """,
        ),
        (
            "email_webhook_events",
            """
            SELECT event_id, provider_message_id, event_type, provider_status,
                   event_created_at, received_at
            FROM studio_email_webhook_events ORDER BY event_id
            """,
        ),
    )
    for label, query in queries:
        digest.update(label.encode("ascii"))
        digest.update(b"\0")
        for row in connection.execute(query):
            digest.update(json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def _logical_signature(
    inspection: StudioBackupInspection,
) -> tuple[int, int, int, int, int, int, int, int, int, str]:
    return (
        inspection.schema_version,
        inspection.workspace_count,
        inspection.trace_count,
        inspection.report_count,
        inspection.case_count,
        inspection.api_key_count,
        inspection.ingestion_token_count,
        inspection.user_count,
        inspection.membership_count,
        inspection.content_sha256,
    )
