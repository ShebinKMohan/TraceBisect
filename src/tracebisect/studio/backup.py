"""Safe backup, verification, and restore helpers for TraceBisect Studio."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tracebisect.studio.storage import SCHEMA_VERSION

_REQUIRED_TABLES = frozenset(
    {
        "studio_schema",
        "studio_traces",
        "studio_reports",
        "studio_cases",
        "studio_metadata",
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
    size_bytes: int
    sha256: str
    content_sha256: str


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
                    )
                    """
                ).fetchone()[0]
            )
            trace_count = _table_count(connection, "studio_traces")
            report_count = _table_count(connection, "studio_reports")
            case_count = _table_count(connection, "studio_cases")
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


def _copy_database(source: Path, destination: Path) -> None:
    with (
        _read_only_connection(source) as source_connection,
        sqlite3.connect(destination) as destination_connection,
    ):
        source_connection.backup(destination_connection)
        destination_connection.execute("PRAGMA journal_mode = DELETE")
    with destination.open("rb") as handle:
        os.fsync(handle.fileno())


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


def _publish_new_file(temporary: Path, destination: Path) -> None:
    try:
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise StudioBackupError("destination already exists; no data was replaced") from exc
    except OSError as exc:
        raise StudioBackupError("could not publish the completed database file") from exc


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
) -> tuple[int, int, int, int, int, str]:
    return (
        inspection.schema_version,
        inspection.workspace_count,
        inspection.trace_count,
        inspection.report_count,
        inspection.case_count,
        inspection.content_sha256,
    )
