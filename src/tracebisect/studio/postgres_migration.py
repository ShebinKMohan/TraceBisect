"""Atomic SQLite-to-PostgreSQL migration and reconciliation for Studio."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from psycopg.types.json import Jsonb

from tracebisect.studio.managed_database import (
    StudioDatabaseConnection,
    StudioDatabaseTarget,
    ensure_managed_database_schema,
    studio_database_connection,
)
from tracebisect.studio.storage import (
    SCHEMA_VERSION,
    StudioPersistenceError,
    ensure_studio_schema,
)


class StudioPostgresMigrationError(RuntimeError):
    """Raised when migration safety or reconciliation cannot be proven."""


@dataclass(frozen=True, slots=True)
class StudioMigrationTableResult:
    """One reconciled table count safe to show to an operator."""

    name: str
    row_count: int


@dataclass(frozen=True, slots=True)
class StudioPostgresMigrationReport:
    """Facts proven from the isolated source snapshot and destination transaction."""

    source_path: str
    schema_version: int
    workspace_count: int
    table_results: tuple[StudioMigrationTableResult, ...]
    total_rows: int
    content_sha256: str
    browser_session_count: int
    identity_session_count: int
    queued_email_count: int


@dataclass(frozen=True, slots=True)
class _MigrationTable:
    name: str
    columns: tuple[str, ...]
    json_columns: tuple[str, ...] = ()

    @property
    def select_sql(self) -> str:
        columns = ", ".join(self.columns)
        order_columns = (
            self.columns[:2] if self.name in _COMPOSITE_KEY_TABLES else self.columns[:1]
        )
        order = ", ".join(order_columns)
        return f"SELECT {columns} FROM {self.name} ORDER BY {order}"

    @property
    def insert_sql(self) -> str:
        columns = ", ".join(self.columns)
        placeholders = ", ".join("?" for _column in self.columns)
        return f"INSERT INTO {self.name} ({columns}) VALUES ({placeholders})"


_COMPOSITE_KEY_TABLES = frozenset(
    {
        "studio_traces",
        "studio_reports",
        "studio_cases",
        "studio_metadata",
        "studio_workspace_memberships",
    }
)

# Dependency order matters: parents are inserted before foreign-key children.
MIGRATION_TABLES = (
    _MigrationTable(
        "studio_traces",
        ("workspace_id", "trace_key", "display_name", "trace_jsonl", "created_at"),
    ),
    _MigrationTable(
        "studio_reports",
        ("workspace_id", "report_id", "payload_json", "created_at"),
        json_columns=("payload_json",),
    ),
    _MigrationTable(
        "studio_cases",
        ("workspace_id", "case_id", "payload_json", "updated_at"),
        json_columns=("payload_json",),
    ),
    _MigrationTable("studio_metadata", ("workspace_id", "key", "value")),
    _MigrationTable(
        "studio_api_keys",
        (
            "key_id",
            "workspace_id",
            "role",
            "label",
            "key_hash",
            "created_at",
            "expires_at",
            "revoked_at",
        ),
    ),
    _MigrationTable(
        "studio_ingestion_tokens",
        (
            "token_id",
            "workspace_id",
            "label",
            "scope",
            "token_hash",
            "created_at",
            "expires_at",
            "revoked_at",
        ),
    ),
    _MigrationTable(
        "studio_error_events",
        (
            "event_id",
            "request_id",
            "workspace_id",
            "action",
            "method",
            "status_code",
            "error_type",
            "failure_location",
            "fingerprint",
            "occurred_at",
            "event_version",
        ),
    ),
    _MigrationTable(
        "studio_users",
        (
            "user_id",
            "email",
            "display_name",
            "password_hash",
            "session_epoch",
            "created_at",
            "password_changed_at",
            "disabled_at",
        ),
    ),
    _MigrationTable(
        "studio_workspace_memberships",
        ("workspace_id", "user_id", "role", "created_at", "updated_at"),
    ),
    _MigrationTable(
        "studio_invitations",
        (
            "invitation_id",
            "workspace_id",
            "email",
            "role",
            "token_hash",
            "created_at",
            "expires_at",
            "accepted_at",
            "revoked_at",
        ),
    ),
    _MigrationTable(
        "studio_recovery_codes",
        ("code_id", "user_id", "code_hash", "created_at", "used_at"),
    ),
    _MigrationTable(
        "studio_browser_sessions",
        ("session_id", "key_id", "session_hash", "created_at", "expires_at", "revoked_at"),
    ),
    _MigrationTable(
        "studio_identity_sessions",
        (
            "session_id",
            "user_id",
            "workspace_id",
            "session_hash",
            "session_epoch",
            "created_at",
            "expires_at",
            "revoked_at",
        ),
    ),
    _MigrationTable(
        "studio_email_outbox",
        (
            "message_id",
            "invitation_id",
            "workspace_id",
            "recipient_email",
            "payload_ciphertext",
            "status",
            "attempt_count",
            "created_at",
            "available_at",
            "lease_expires_at",
            "lease_token",
            "sent_at",
            "failed_at",
            "provider_message_id",
            "last_error_code",
            "provider_status",
            "provider_event_at",
            "provider_event_id",
        ),
    ),
    _MigrationTable(
        "studio_email_webhook_events",
        (
            "event_id",
            "provider_message_id",
            "event_type",
            "provider_status",
            "event_created_at",
            "received_at",
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class _DatabaseSignature:
    table_results: tuple[StudioMigrationTableResult, ...]
    content_sha256: str


def migrate_sqlite_to_postgres(
    source_path: str | Path,
    destination: StudioDatabaseTarget,
) -> StudioPostgresMigrationReport:
    """Copy one consistent SQLite snapshot into an empty destination atomically."""
    source = _existing_source(source_path)
    try:
        with _source_snapshot(source) as snapshot:
            _prepare_snapshot(snapshot)
            with studio_database_connection(snapshot, read_only=True) as source_connection:
                source_signature = _database_signature(source_connection)
                report = _report(source, source_connection, source_signature)
                with studio_database_connection(destination) as destination_connection:
                    ensure_managed_database_schema(destination_connection)
                    destination_connection.execute("BEGIN IMMEDIATE")
                    _require_empty_destination(destination_connection)
                    _copy_tables(source_connection, destination_connection)
                    destination_signature = _database_signature(destination_connection)
                    _require_matching_signatures(source_signature, destination_signature)
                    _require_stable_source(source, source_signature)
    except StudioPostgresMigrationError:
        raise
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError, TypeError, ValueError) as exc:
        raise StudioPostgresMigrationError(
            "migration failed; the destination transaction was not completed"
        ) from exc
    return report


def verify_sqlite_postgres_migration(
    source_path: str | Path,
    destination: StudioDatabaseTarget,
) -> StudioPostgresMigrationReport:
    """Reconcile every migrated table without changing either database."""
    source = _existing_source(source_path)
    try:
        with _source_snapshot(source) as snapshot:
            _prepare_snapshot(snapshot)
            with (
                studio_database_connection(snapshot, read_only=True) as source_connection,
                studio_database_connection(destination, read_only=True) as destination_connection,
            ):
                source_signature = _database_signature(source_connection)
                destination_signature = _database_signature(destination_connection)
                _require_matching_signatures(source_signature, destination_signature)
                return _report(source, source_connection, source_signature)
    except StudioPostgresMigrationError:
        raise
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError, TypeError, ValueError) as exc:
        raise StudioPostgresMigrationError("could not verify the migrated Studio data") from exc


def _existing_source(path: str | Path) -> Path:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise StudioPostgresMigrationError("the SQLite source does not exist or is not a file")
    return source


@contextmanager
def _source_snapshot(source: Path) -> Iterator[Path]:
    """Take an isolated online snapshot so source writes cannot skew reconciliation."""
    with tempfile.TemporaryDirectory(prefix="tracebisect-postgres-migration-") as directory:
        snapshot = Path(directory) / "studio-snapshot.db"
        try:
            with (
                sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True, timeout=5) as source_db,
                sqlite3.connect(snapshot, timeout=5) as snapshot_db,
            ):
                source_db.backup(snapshot_db)
            snapshot.chmod(0o600)
        except (OSError, sqlite3.DatabaseError) as exc:
            raise StudioPostgresMigrationError(
                "could not create a consistent snapshot of the SQLite source"
            ) from exc
        yield snapshot


def _prepare_snapshot(snapshot: Path) -> None:
    """Upgrade only the disposable snapshot, then prove integrity and completeness."""
    try:
        with sqlite3.connect(snapshot, timeout=5) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            ensure_studio_schema(connection)
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity != ("ok",):
                raise StudioPostgresMigrationError(
                    "the SQLite source snapshot failed integrity verification"
                )
            foreign_key_violation = connection.execute("PRAGMA foreign_key_check").fetchone()
            if foreign_key_violation is not None:
                raise StudioPostgresMigrationError(
                    "the SQLite source contains broken record relationships"
                )
            version = connection.execute("SELECT version FROM studio_schema LIMIT 1").fetchone()
            if version != (SCHEMA_VERSION,):
                raise StudioPostgresMigrationError(
                    "the SQLite source schema could not be upgraded for migration"
                )
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            required = {table.name for table in MIGRATION_TABLES}
            if not required.issubset(tables):
                raise StudioPostgresMigrationError(
                    "the SQLite source is missing required Studio tables"
                )
    except StudioPostgresMigrationError:
        raise
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError) as exc:
        raise StudioPostgresMigrationError(
            "the source is not a supported TraceBisect Studio SQLite database"
        ) from exc


def _require_empty_destination(connection: StudioDatabaseConnection) -> None:
    populated = [
        table.name for table in MIGRATION_TABLES if _table_count(connection, table.name) > 0
    ]
    if populated:
        raise StudioPostgresMigrationError(
            "the PostgreSQL destination already contains Studio data; nothing was changed"
        )


def _copy_tables(
    source: StudioDatabaseConnection,
    destination: StudioDatabaseConnection,
) -> None:
    for table in MIGRATION_TABLES:
        rows = source.execute(table.select_sql).fetchall()
        if rows:
            destination.executemany(
                table.insert_sql,
                [_destination_row(table, row, dialect=destination.dialect) for row in rows],
            )


def _destination_row(
    table: _MigrationTable,
    row: Sequence[object],
    *,
    dialect: str,
) -> tuple[object, ...]:
    if dialect != "postgres" or not table.json_columns:
        return tuple(row)
    normalized = _normalized_row(table, row)
    for column in table.json_columns:
        index = table.columns.index(column)
        normalized[index] = Jsonb(normalized[index])
    return tuple(normalized)


def _database_signature(connection: StudioDatabaseConnection) -> _DatabaseSignature:
    digest = hashlib.sha256()
    table_results: list[StudioMigrationTableResult] = []
    for table in MIGRATION_TABLES:
        rows = connection.execute(table.select_sql).fetchall()
        table_results.append(StudioMigrationTableResult(table.name, len(rows)))
        digest.update(table.name.encode("ascii"))
        digest.update(b"\0")
        for row in rows:
            normalized = _normalized_row(table, row)
            digest.update(
                json.dumps(
                    normalized,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            )
            digest.update(b"\n")
    return _DatabaseSignature(tuple(table_results), digest.hexdigest())


def _normalized_row(table: _MigrationTable, row: Sequence[object]) -> list[object]:
    if len(row) != len(table.columns):
        raise StudioPostgresMigrationError("stored Studio data has an unexpected row shape")
    normalized = list(row)
    for column in table.json_columns:
        index = table.columns.index(column)
        value = normalized[index]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise StudioPostgresMigrationError(
                    f"{table.name} contains invalid JSON and cannot be migrated"
                ) from exc
        if not isinstance(value, dict):
            raise StudioPostgresMigrationError(
                f"{table.name} contains non-object JSON and cannot be migrated"
            )
        normalized[index] = value
    return normalized


def _require_matching_signatures(
    source: _DatabaseSignature,
    destination: _DatabaseSignature,
) -> None:
    if source.table_results != destination.table_results:
        raise StudioPostgresMigrationError(
            "destination row counts do not match the SQLite source; migration was rolled back"
        )
    if source.content_sha256 != destination.content_sha256:
        raise StudioPostgresMigrationError(
            "destination content does not match the SQLite source; migration was rolled back"
        )


def _require_stable_source(source: Path, original: _DatabaseSignature) -> None:
    """Catch writes made by an API or worker while the destination transaction is open."""
    with _source_snapshot(source) as final_snapshot:
        _prepare_snapshot(final_snapshot)
        with studio_database_connection(final_snapshot, read_only=True) as connection:
            current = _database_signature(connection)
    if current != original:
        raise StudioPostgresMigrationError(
            "the SQLite source changed during migration; stop the old API and workers, then retry"
        )


def _report(
    source: Path,
    source_connection: StudioDatabaseConnection,
    signature: _DatabaseSignature,
) -> StudioPostgresMigrationReport:
    return StudioPostgresMigrationReport(
        source_path=str(source),
        schema_version=SCHEMA_VERSION,
        workspace_count=_workspace_count(source_connection),
        table_results=signature.table_results,
        total_rows=sum(item.row_count for item in signature.table_results),
        content_sha256=signature.content_sha256,
        browser_session_count=_table_count(source_connection, "studio_browser_sessions"),
        identity_session_count=_table_count(source_connection, "studio_identity_sessions"),
        queued_email_count=_query_count(
            source_connection,
            """
            SELECT COUNT(*) FROM studio_email_outbox
            WHERE status IN ('pending', 'sending', 'retry')
            """,
        ),
    )


def _workspace_count(connection: StudioDatabaseConnection) -> int:
    return _query_count(
        connection,
        """
        SELECT COUNT(*) FROM (
            SELECT workspace_id FROM studio_traces
            UNION SELECT workspace_id FROM studio_reports
            UNION SELECT workspace_id FROM studio_cases
            UNION SELECT workspace_id FROM studio_metadata
            UNION SELECT workspace_id FROM studio_api_keys
            UNION SELECT workspace_id FROM studio_ingestion_tokens
            UNION SELECT workspace_id FROM studio_error_events WHERE workspace_id IS NOT NULL
            UNION SELECT workspace_id FROM studio_workspace_memberships
            UNION SELECT workspace_id FROM studio_invitations
        ) AS workspaces
        """,
    )


def _table_count(connection: StudioDatabaseConnection, table_name: str) -> int:
    if table_name not in {table.name for table in MIGRATION_TABLES}:
        raise StudioPostgresMigrationError("unsupported Studio migration table")
    return _query_count(connection, f"SELECT COUNT(*) FROM {table_name}")


def _query_count(connection: StudioDatabaseConnection, query: str) -> int:
    row = connection.execute(query).fetchone()
    if row is None:
        raise StudioPostgresMigrationError("a Studio migration count could not be read")
    return int(str(row[0]))
