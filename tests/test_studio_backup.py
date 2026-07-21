"""Tests for safe Studio backup, verification, and restore workflows."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tracebisect.cli import main
from tracebisect.studio.access_keys import (
    create_studio_api_key,
    principal_for_managed_api_key,
)
from tracebisect.studio.access_sessions import (
    issue_studio_browser_session,
    principal_for_studio_browser_session,
)
from tracebisect.studio.backup import (
    StudioBackupError,
    create_studio_backup,
    inspect_studio_backup,
    restore_studio_backup,
)
from tracebisect.studio.email_delivery import StudioEmailDelivery
from tracebisect.studio.identity import (
    accept_studio_invitation,
    create_studio_invitation,
    login_studio_identity,
    principal_for_studio_identity_session,
)
from tracebisect.studio.service import seed_demo_report
from tracebisect.studio.storage import SCHEMA_VERSION, SQLiteStudioStore


def test_live_backup_verifies_and_restores_all_workspace_data(tmp_path: Path) -> None:
    source_path = tmp_path / "studio.db"
    backup_path = tmp_path / "backups" / "studio-2026-07-21.db"
    restored_path = tmp_path / "restored" / "studio.db"
    source = SQLiteStudioStore(source_path, workspace_id="workspace-a")
    report = seed_demo_report(source)
    baseline = report["baseline"]
    candidate = report["candidate"]
    assert isinstance(baseline, dict)
    assert isinstance(candidate, dict)
    case = source.add_case_from_report(
        name="Refund guardrail",
        description="Protect the refund lookup flow.",
        tags=["refund"],
        baseline_trace_id=str(baseline["id"]),
        candidate_trace_id=str(candidate["id"]),
        scenario_cmd=["python", "examples/refund_agent.py"],
        assertions=["tool_args", "final_output"],
        cost_threshold=1.25,
    )
    pepper = "backup-test-pepper-with-more-than-32-characters"
    issued_key = create_studio_api_key(
        source_path,
        workspace_id="workspace-a",
        role="editor",
        label="Restored browser",
        expires_in_days=90,
        pepper=pepper,
    )
    issued_session = issue_studio_browser_session(
        source_path,
        api_key=issued_key.api_key,
        pepper=pepper,
        ttl_seconds=3600,
    )
    identity_secret = "backup-identity-secret-with-at-least-32-characters"
    invitation = create_studio_invitation(
        source_path,
        workspace_id="workspace-a",
        email="owner@example.com",
        role="admin",
        expires_in_days=7,
        identity_secret_value=identity_secret,
    )
    accepted = accept_studio_invitation(
        source_path,
        invitation_token=invitation.invitation_token,
        display_name="Backup Owner",
        password="correct horse battery staple",
        identity_secret_value=identity_secret,
    )
    identity_login = login_studio_identity(
        source_path,
        email=accepted.user.email,
        password="correct horse battery staple",
        identity_secret_value=identity_secret,
    )
    assert identity_login.session is not None
    queued_invitation = create_studio_invitation(
        source_path,
        workspace_id="workspace-a",
        email="next-owner@example.com",
        role="admin",
        expires_in_days=7,
        identity_secret_value=identity_secret,
    )
    email_delivery = StudioEmailDelivery.from_env(
        {
            "TRACEBISECT_STUDIO_STORAGE": "sqlite",
            "TRACEBISECT_STUDIO_SQLITE_PATH": str(source_path),
            "TRACEBISECT_STUDIO_IDENTITY_SECRET": identity_secret,
            "TRACEBISECT_STUDIO_EMAIL_PROVIDER": "resend",
            "TRACEBISECT_STUDIO_EMAIL_FROM": "invites@example.com",
            "TRACEBISECT_STUDIO_PUBLIC_URL": "https://studio.example.com",
            "RESEND_API_KEY": "re_backup_test_key_long_enough",
        }
    )
    email_delivery.queue_invitation(queued_invitation)
    with sqlite3.connect(source_path) as connection:
        connection.execute(
            """
            INSERT INTO studio_email_webhook_events (
                event_id, provider_message_id, event_type, provider_status,
                event_created_at, received_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "msg_backup_webhook_123",
                "provider-backup-message",
                "email.delivered",
                "delivered",
                "2026-07-21T08:00:00Z",
                "2026-07-21T08:00:01Z",
            ),
        )
    with pytest.raises(StudioBackupError, match="contains browser sessions"):
        inspect_studio_backup(source_path)

    inspection = create_studio_backup(source_path, backup_path)

    assert backup_path.exists()
    assert inspection.schema_version == SCHEMA_VERSION
    assert inspection.workspace_count == 1
    assert inspection.trace_count == 2
    assert inspection.report_count == 2
    assert inspection.case_count == 1
    assert inspection.api_key_count == 1
    assert inspection.user_count == 1
    assert inspection.membership_count == 1
    assert len(inspection.sha256) == 64
    assert len(inspection.content_sha256) == 64
    assert inspect_studio_backup(backup_path) == inspection
    with sqlite3.connect(backup_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM studio_browser_sessions").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM studio_identity_sessions").fetchone() == (
            0,
        )
        assert connection.execute("SELECT COUNT(*) FROM studio_email_outbox").fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM studio_email_webhook_events"
        ).fetchone() == (0,)

    # Prove that the backup is a point-in-time snapshot, not a reference to the live file.
    source.clear()
    source.close()
    assert inspect_studio_backup(backup_path).report_count == 2

    restored_inspection = restore_studio_backup(backup_path, restored_path)
    assert restored_inspection.trace_count == 2
    assert restored_inspection.report_count == 2
    assert restored_inspection.case_count == 1
    assert restored_inspection.api_key_count == 1
    assert restored_inspection.user_count == 1
    assert restored_inspection.membership_count == 1
    assert restored_inspection.content_sha256 == inspection.content_sha256

    restored = SQLiteStudioStore(restored_path, workspace_id="workspace-a")
    assert restored.get_report(str(report["report_id"])) == report
    assert restored.get_case(str(case["case_id"])) == case
    restored.close()
    restored_principal = principal_for_managed_api_key(
        restored_path,
        api_key=issued_key.api_key,
        pepper=pepper,
    )
    assert restored_principal is not None
    assert restored_principal.workspace_id == "workspace-a"
    assert restored_principal.role == "editor"
    assert (
        principal_for_studio_browser_session(
            restored_path,
            session_token=issued_session.session_token,
            pepper=pepper,
        )
        is None
    )
    assert (
        principal_for_studio_identity_session(
            restored_path,
            session_token=identity_login.session.session_token,
            identity_secret_value=identity_secret,
        )
        is None
    )
    assert (
        login_studio_identity(
            restored_path,
            email=accepted.user.email,
            password="correct horse battery staple",
            identity_secret_value=identity_secret,
        ).session
        is not None
    )


def test_backup_and_restore_never_replace_existing_files(tmp_path: Path) -> None:
    source_path = tmp_path / "studio.db"
    source = SQLiteStudioStore(source_path, workspace_id="workspace-a")
    seed_demo_report(source)
    source.close()
    backup_path = tmp_path / "backup.db"
    backup_path.write_bytes(b"keep this backup")

    with pytest.raises(StudioBackupError, match="already exists"):
        create_studio_backup(source_path, backup_path)
    assert backup_path.read_bytes() == b"keep this backup"

    valid_backup = tmp_path / "valid-backup.db"
    create_studio_backup(source_path, valid_backup)
    restored_path = tmp_path / "restored.db"
    restored_path.write_bytes(b"keep this database")

    with pytest.raises(StudioBackupError, match="already exists"):
        restore_studio_backup(valid_backup, restored_path)
    assert restored_path.read_bytes() == b"keep this database"


def test_backup_verification_rejects_corruption_and_unknown_schema(tmp_path: Path) -> None:
    corrupt_path = tmp_path / "corrupt.db"
    corrupt_path.write_text("not sqlite", encoding="utf-8")

    with pytest.raises(StudioBackupError, match="not a valid"):
        inspect_studio_backup(corrupt_path)

    incompatible_path = tmp_path / "future.db"
    store = SQLiteStudioStore(incompatible_path, workspace_id="workspace-a")
    store.close()
    with sqlite3.connect(incompatible_path) as connection:
        connection.execute("UPDATE studio_schema SET version = 999")

    with pytest.raises(StudioBackupError, match="schema 999 is not supported"):
        inspect_studio_backup(incompatible_path)


def test_studio_backup_cli_guides_backup_verify_and_restore(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_path = tmp_path / "studio.db"
    backup_path = tmp_path / "backup.db"
    restored_path = tmp_path / "restored.db"
    source = SQLiteStudioStore(source_path, workspace_id="workspace-a")
    seed_demo_report(source)
    source.close()

    assert (
        main(
            [
                "studio",
                "backup",
                "--database",
                str(source_path),
                "--output",
                str(backup_path),
            ]
        )
        == 0
    )
    backup_output = capsys.readouterr().out
    assert "Studio backup created" in backup_output
    assert "Next:" in backup_output
    assert "tracebisect studio verify" in backup_output
    assert "SHA-256:" in backup_output

    assert main(["studio", "verify", "--backup", str(backup_path)]) == 0
    verify_output = capsys.readouterr().out
    assert "Studio backup is healthy" in verify_output
    assert "Workspaces: 1" in verify_output

    assert (
        main(
            [
                "studio",
                "restore",
                "--backup",
                str(backup_path),
                "--database",
                str(restored_path),
            ]
        )
        == 0
    )
    restore_output = capsys.readouterr().out
    assert "Studio backup restored" in restore_output
    assert "TRACEBISECT_STUDIO_SQLITE_PATH" in restore_output


def test_studio_backup_cli_reports_safe_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing.db"
    output = tmp_path / "backup.db"

    assert main(["studio", "backup", "--database", str(missing), "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert "tracebisect studio backup failed" in captured.err
    assert "does not exist" in captured.err
    assert not output.exists()
