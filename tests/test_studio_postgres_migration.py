"""Tests for safe, reconciled SQLite-to-PostgreSQL Studio migration."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

import tracebisect.cli as cli
import tracebisect.studio.postgres_migration as postgres_migration
from tracebisect.cli import main
from tracebisect.studio.postgres_migration import (
    MIGRATION_TABLES,
    StudioPostgresMigrationError,
    migrate_sqlite_to_postgres,
    verify_sqlite_postgres_migration,
)
from tracebisect.studio.storage import ensure_studio_schema


def _populate_source(database: Path) -> None:
    created = "2026-07-21T08:00:00Z"
    expires = "2026-07-22T08:00:00Z"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        ensure_studio_schema(connection)
        connection.execute(
            "INSERT INTO studio_traces VALUES (?, ?, ?, ?, ?)",
            ("workspace-a", "trace-1", "Baseline", '{"trace_id":"trace-1"}\n', created),
        )
        connection.execute(
            "INSERT INTO studio_reports VALUES (?, ?, ?, ?)",
            (
                "workspace-a",
                "report-1",
                '{"created_at":"2026-07-21T08:00:00Z","report_id":"report-1"}',
                created,
            ),
        )
        connection.execute(
            "INSERT INTO studio_cases VALUES (?, ?, ?, ?)",
            (
                "workspace-a",
                "case-1",
                '{"case_id":"case-1","updated_at":"2026-07-21T08:00:00Z"}',
                created,
            ),
        )
        connection.execute(
            "INSERT INTO studio_metadata VALUES (?, ?, ?)",
            ("workspace-a", "demo_report_id", "report-1"),
        )
        connection.execute(
            """
            INSERT INTO studio_api_keys (
                key_id, workspace_id, role, label, key_hash,
                created_at, expires_at, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            ("key-1", "workspace-a", "admin", "Admin", "a" * 64, created, expires),
        )
        connection.execute(
            """
            INSERT INTO studio_users (
                user_id, email, display_name, password_hash, session_epoch,
                created_at, password_changed_at, disabled_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?, NULL)
            """,
            ("user-1", "owner@example.com", "Owner", "argon2-hash", created, created),
        )
        connection.execute(
            "INSERT INTO studio_workspace_memberships VALUES (?, ?, ?, ?, ?)",
            ("workspace-a", "user-1", "admin", created, created),
        )
        connection.execute(
            """
            INSERT INTO studio_invitations (
                invitation_id, workspace_id, email, role, token_hash,
                created_at, expires_at, accepted_at, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (
                "invite-1",
                "workspace-a",
                "person@example.com",
                "viewer",
                "b" * 64,
                created,
                expires,
            ),
        )
        connection.execute(
            "INSERT INTO studio_recovery_codes VALUES (?, ?, ?, ?, NULL)",
            ("recovery-1", "user-1", "c" * 64, created),
        )
        connection.execute(
            "INSERT INTO studio_browser_sessions VALUES (?, ?, ?, ?, ?, NULL)",
            ("browser-1", "key-1", "d" * 64, created, expires),
        )
        connection.execute(
            """
            INSERT INTO studio_identity_sessions (
                session_id, user_id, workspace_id, session_hash, session_epoch,
                created_at, expires_at, revoked_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?, NULL)
            """,
            ("identity-1", "user-1", "workspace-a", "e" * 64, created, expires),
        )
        connection.execute(
            """
            INSERT INTO studio_email_outbox (
                message_id, invitation_id, workspace_id, recipient_email,
                payload_ciphertext, status, attempt_count, created_at,
                available_at, lease_expires_at, lease_token, sent_at, failed_at,
                provider_message_id, last_error_code, provider_status,
                provider_event_at, provider_event_id
            ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?, NULL, NULL, NULL, NULL,
                      NULL, NULL, NULL, NULL, NULL)
            """,
            (
                "message-0001",
                "invite-1",
                "workspace-a",
                "person@example.com",
                "v1.nonce.ciphertext",
                created,
                created,
            ),
        )
        connection.execute(
            "INSERT INTO studio_email_webhook_events VALUES (?, ?, ?, ?, ?, ?)",
            ("event-0001", "provider-0001", "email.delivered", "delivered", created, created),
        )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _all_table_counts(database: Path) -> dict[str, int]:
    with sqlite3.connect(database) as connection:
        return {
            table.name: int(connection.execute(f"SELECT COUNT(*) FROM {table.name}").fetchone()[0])
            for table in MIGRATION_TABLES
        }


def test_complete_migration_is_source_safe_and_reconciles_every_table(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "destination.db"
    _populate_source(source)
    source_hash = _file_sha256(source)

    report = migrate_sqlite_to_postgres(source, destination)

    assert report.schema_version == 7
    assert report.workspace_count == 1
    assert report.total_rows == len(MIGRATION_TABLES)
    assert report.browser_session_count == 1
    assert report.identity_session_count == 1
    assert report.queued_email_count == 1
    assert all(item.row_count == 1 for item in report.table_results)
    assert _all_table_counts(destination) == {
        table.name: 1 for table in MIGRATION_TABLES
    }
    assert _file_sha256(source) == source_hash

    verified = verify_sqlite_postgres_migration(source, destination)
    assert verified.content_sha256 == report.content_sha256
    assert verified.table_results == report.table_results


def test_migration_refuses_a_nonempty_destination_without_merging(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "destination.db"
    _populate_source(source)
    with sqlite3.connect(destination) as connection:
        ensure_studio_schema(connection)
        connection.execute(
            "INSERT INTO studio_metadata VALUES (?, ?, ?)",
            ("existing", "owner", "keep"),
        )

    with pytest.raises(StudioPostgresMigrationError, match="already contains Studio data"):
        migrate_sqlite_to_postgres(source, destination)

    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT * FROM studio_metadata").fetchall() == [
            ("existing", "owner", "keep")
        ]


def test_migration_rejects_broken_source_relationships_before_destination_writes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "destination.db"
    _populate_source(source)
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM studio_api_keys WHERE key_id = 'key-1'")

    with pytest.raises(StudioPostgresMigrationError, match="broken record relationships"):
        migrate_sqlite_to_postgres(source, destination)

    assert not destination.exists()


def test_reconciliation_mismatch_rolls_back_all_copied_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "destination.db"
    _populate_source(source)

    def reject_reconciliation(*_args: object) -> None:
        raise StudioPostgresMigrationError("forced reconciliation failure")

    monkeypatch.setattr(
        postgres_migration,
        "_require_matching_signatures",
        reject_reconciliation,
    )

    with pytest.raises(StudioPostgresMigrationError, match="forced reconciliation failure"):
        migrate_sqlite_to_postgres(source, destination)

    assert _all_table_counts(destination) == {table.name: 0 for table in MIGRATION_TABLES}


def test_source_change_during_copy_is_detected_and_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "destination.db"
    _populate_source(source)
    original_copy = postgres_migration._copy_tables

    def copy_then_change_source(*args: object) -> None:
        original_copy(*args)  # type: ignore[arg-type]
        with sqlite3.connect(source) as connection:
            connection.execute(
                "UPDATE studio_metadata SET value = 'changed-live' "
                "WHERE key = 'demo_report_id'"
            )

    monkeypatch.setattr(postgres_migration, "_copy_tables", copy_then_change_source)

    with pytest.raises(StudioPostgresMigrationError, match="source changed during migration"):
        migrate_sqlite_to_postgres(source, destination)

    assert _all_table_counts(destination) == {table.name: 0 for table in MIGRATION_TABLES}


def test_verify_only_detects_same_count_content_drift(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "destination.db"
    _populate_source(source)
    migrate_sqlite_to_postgres(source, destination)
    with sqlite3.connect(destination) as connection:
        connection.execute(
            "UPDATE studio_metadata SET value = 'different' WHERE key = 'demo_report_id'"
        )

    with pytest.raises(StudioPostgresMigrationError, match="content does not match"):
        verify_sqlite_postgres_migration(source, destination)


def test_cli_explains_missing_destination_and_prints_verified_next_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "destination.db"
    _populate_source(source)
    monkeypatch.delenv("TRACEBISECT_STUDIO_DATABASE_URL", raising=False)

    assert main(["studio", "migrate-postgres", "--source", str(source)]) == 2
    assert "set TRACEBISECT_STUDIO_DATABASE_URL" in capsys.readouterr().err

    report = migrate_sqlite_to_postgres(source, destination)

    @contextmanager
    def fake_destination():  # type: ignore[no-untyped-def]
        yield object()

    monkeypatch.setattr(cli, "_studio_postgres_store", fake_destination)
    monkeypatch.setattr(postgres_migration, "migrate_sqlite_to_postgres", lambda *_args: report)

    assert main(["studio", "migrate-postgres", "--source", str(source)]) == 0
    output = capsys.readouterr().out
    assert "PostgreSQL migration completed and verified" in output
    assert "Rows reconciled: 13" in output
    assert "Keep the old API and every email worker stopped" in output
