"""Tests for restart-safe, workspace-scoped Studio persistence."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tracebisect.studio.service import seed_demo_report
from tracebisect.studio.storage import (
    SCHEMA_VERSION,
    SQLiteStudioStore,
    StudioConfigurationError,
    create_studio_store,
)


def test_store_factory_keeps_zero_configuration_memory_mode() -> None:
    store = create_studio_store({})

    assert store.runtime_status() == {
        "kind": "memory",
        "durable": False,
        "workspace_id": "local",
        "trace_count": 0,
        "report_count": 0,
        "case_count": 0,
    }


def test_new_sqlite_database_is_private_by_default(tmp_path: Path) -> None:
    database_path = tmp_path / "studio.db"
    store = SQLiteStudioStore(database_path, workspace_id="workspace-a")

    assert database_path.stat().st_mode & 0o777 == 0o600
    store.close()


def test_memory_store_reports_configured_workspace_id() -> None:
    store = create_studio_store({"TRACEBISECT_STUDIO_WORKSPACE_ID": "developer-a"})

    assert store.runtime_status()["workspace_id"] == "developer-a"


@pytest.mark.parametrize(
    ("env", "message"),
    [
        (
            {"TRACEBISECT_STUDIO_STORAGE": "redis"},
            "TRACEBISECT_STUDIO_STORAGE must be 'memory', 'sqlite', or 'postgres'",
        ),
        (
            {"TRACEBISECT_STUDIO_STORAGE": "sqlite"},
            "TRACEBISECT_STUDIO_SQLITE_PATH is required",
        ),
        (
            {"TRACEBISECT_STUDIO_WORKSPACE_ID": "bad workspace"},
            "TRACEBISECT_STUDIO_WORKSPACE_ID must be",
        ),
        (
            {"TRACEBISECT_STUDIO_MAX_STORED_TRACES": "0"},
            "TRACEBISECT_STUDIO_MAX_STORED_TRACES must be a positive integer",
        ),
    ],
)
def test_store_factory_fails_fast_for_invalid_configuration(
    env: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(StudioConfigurationError, match=message):
        create_studio_store(env)


def test_sqlite_store_survives_restart_and_restores_demo_and_cases(tmp_path: Path) -> None:
    database_path = tmp_path / "studio.sqlite3"
    first = SQLiteStudioStore(database_path, workspace_id="workspace-a")
    report = seed_demo_report(first)
    baseline = report["baseline"]
    candidate = report["candidate"]
    assert isinstance(baseline, dict)
    assert isinstance(candidate, dict)
    baseline_trace_id = baseline["id"]
    candidate_trace_id = candidate["id"]
    assert isinstance(baseline_trace_id, str)
    assert isinstance(candidate_trace_id, str)
    case = first.add_case_from_report(
        name="Refund guardrail",
        description="Protect the refund lookup flow.",
        tags=["refund"],
        baseline_trace_id=baseline_trace_id,
        candidate_trace_id=candidate_trace_id,
        scenario_cmd=["python", "examples/refund_agent.py", "--case", "refund_042"],
        assertions=["tool_args", "final_output"],
        cost_threshold=1.25,
    )
    first.close()

    restored = SQLiteStudioStore(database_path, workspace_id="workspace-a")

    assert restored.check_health() is True
    assert restored.runtime_status() == {
        "kind": "sqlite",
        "durable": True,
        "workspace_id": "workspace-a",
        "trace_count": 2,
        "report_count": 2,
        "case_count": 1,
    }
    assert restored.get_report(str(report["report_id"])) == report
    assert restored.get_case(str(case["case_id"])) == case
    assert seed_demo_report(restored)["report_id"] == report["report_id"]
    restored.close()


def test_sqlite_store_migrates_v1_data_to_managed_key_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "studio.sqlite3"
    original = SQLiteStudioStore(database_path, workspace_id="workspace-a")
    report = seed_demo_report(original)
    original.close()
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE studio_api_keys")
        connection.execute("UPDATE studio_schema SET version = 1")

    migrated = SQLiteStudioStore(database_path, workspace_id="workspace-a")

    assert migrated.get_report(str(report["report_id"])) == report
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version FROM studio_schema").fetchone() == (
            SCHEMA_VERSION,
        )
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'studio_api_keys'"
        ).fetchone() == ("studio_api_keys",)
        assert connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'studio_browser_sessions'
            """
        ).fetchone() == ("studio_browser_sessions",)
    migrated.close()


def test_sqlite_store_migrates_v2_keys_to_admin_role(tmp_path: Path) -> None:
    database_path = tmp_path / "studio.sqlite3"
    current = SQLiteStudioStore(database_path, workspace_id="workspace-a")
    current.close()
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE studio_api_keys")
        connection.execute(
            """
            CREATE TABLE studio_api_keys (
                key_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                label TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO studio_api_keys (
                key_id, workspace_id, label, key_hash, created_at, expires_at, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                "legacyKey123",
                "workspace-a",
                "Legacy key",
                "0" * 64,
                "2026-07-21T00:00:00Z",
                "2026-10-21T00:00:00Z",
            ),
        )
        connection.execute("UPDATE studio_schema SET version = 2")

    migrated = SQLiteStudioStore(database_path, workspace_id="workspace-a")

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT role FROM studio_api_keys WHERE key_id = 'legacyKey123'"
        ).fetchone() == ("admin",)
        assert connection.execute("SELECT version FROM studio_schema").fetchone() == (
            SCHEMA_VERSION,
        )
    migrated.close()


def test_sqlite_store_migrates_v3_to_browser_session_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "studio.sqlite3"
    current = SQLiteStudioStore(database_path, workspace_id="workspace-a")
    current.close()
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE studio_browser_sessions")
        connection.execute("UPDATE studio_schema SET version = 3")

    migrated = SQLiteStudioStore(database_path, workspace_id="workspace-a")

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version FROM studio_schema").fetchone() == (
            SCHEMA_VERSION,
        )
        assert connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'studio_browser_sessions'
            """
        ).fetchone() == ("studio_browser_sessions",)
    migrated.close()


def test_sqlite_store_migrates_v4_to_human_identity_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "studio.sqlite3"
    original = SQLiteStudioStore(database_path, workspace_id="workspace-a")
    original.close()
    identity_tables = (
        "studio_identity_sessions",
        "studio_recovery_codes",
        "studio_invitations",
        "studio_workspace_memberships",
        "studio_users",
    )
    with sqlite3.connect(database_path) as connection:
        for table in identity_tables:
            connection.execute(f"DROP TABLE {table}")
        connection.execute("UPDATE studio_schema SET version = 4")

    migrated = SQLiteStudioStore(database_path, workspace_id="workspace-a")

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version FROM studio_schema").fetchone() == (
            SCHEMA_VERSION,
        )
        restored_tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert set(identity_tables).issubset(restored_tables)
    migrated.close()


def test_sqlite_store_migrates_v5_to_encrypted_email_outbox(tmp_path: Path) -> None:
    database_path = tmp_path / "studio.sqlite3"
    original = SQLiteStudioStore(database_path, workspace_id="workspace-a")
    original.close()
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE studio_email_outbox")
        connection.execute("UPDATE studio_schema SET version = 5")

    migrated = SQLiteStudioStore(database_path, workspace_id="workspace-a")

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version FROM studio_schema").fetchone() == (
            SCHEMA_VERSION,
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(studio_email_outbox)")
        }
    assert {"payload_ciphertext", "lease_token", "provider_message_id"}.issubset(columns)
    migrated.close()


def test_sqlite_store_migrates_v6_to_email_webhook_reconciliation(tmp_path: Path) -> None:
    database_path = tmp_path / "studio.sqlite3"
    original = SQLiteStudioStore(database_path, workspace_id="workspace-a")
    original.close()
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE studio_email_webhook_events")
        connection.execute("ALTER TABLE studio_email_outbox DROP COLUMN provider_event_id")
        connection.execute("ALTER TABLE studio_email_outbox DROP COLUMN provider_event_at")
        connection.execute("ALTER TABLE studio_email_outbox DROP COLUMN provider_status")
        connection.execute("UPDATE studio_schema SET version = 6")

    migrated = SQLiteStudioStore(database_path, workspace_id="workspace-a")

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version FROM studio_schema").fetchone() == (
            SCHEMA_VERSION,
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(studio_email_outbox)")
        }
        webhook_table = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'studio_email_webhook_events'
            """
        ).fetchone()
    assert {"provider_status", "provider_event_at", "provider_event_id"}.issubset(columns)
    assert webhook_table == ("studio_email_webhook_events",)
    migrated.close()


def test_sqlite_store_isolates_workspaces_in_one_database(tmp_path: Path) -> None:
    database_path = tmp_path / "studio.sqlite3"
    workspace_a = SQLiteStudioStore(database_path, workspace_id="workspace-a")
    seed_demo_report(workspace_a)
    workspace_a.close()

    workspace_b = SQLiteStudioStore(database_path, workspace_id="workspace-b")

    assert workspace_b.list_traces() == []
    assert workspace_b.list_report_summaries() == []
    assert workspace_b.list_cases() == []
    workspace_b.close()
