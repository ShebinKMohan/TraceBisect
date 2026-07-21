"""Tests for secret-safe structured Studio server error events."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import tracebisect.studio.api as studio_api
from tracebisect.cli import main
from tracebisect.studio.error_reporting import (
    ERROR_LOGGER_NAME,
    StudioErrorEventError,
    StudioErrorReporter,
    list_studio_error_events,
)
from tracebisect.studio.storage import SQLiteStudioStore, StudioConfigurationError

SECRET_FAILURE_MESSAGE = "tbsk_secret-key-that-must-not-be-logged /private/customer/workspace.db"
NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def _captured_failure() -> RuntimeError:
    try:
        raise RuntimeError(SECRET_FAILURE_MESSAGE)
    except RuntimeError as exc:
        return exc


def test_error_event_is_bounded_correlatable_and_message_free(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reporter = StudioErrorReporter()
    caplog.set_level(logging.ERROR, logger=ERROR_LOGGER_NAME)

    reporter.emit_unhandled(
        request_id="request-1234",
        method="POST",
        path="/api/compare",
        workspace_id="workspace-a",
        error=_captured_failure(),
    )
    reporter.emit_unhandled(
        request_id="request-5678",
        method="POST",
        path="/api/compare",
        workspace_id="workspace-a",
        error=_captured_failure(),
    )

    assert len(caplog.records) == 2
    first = json.loads(caplog.records[0].message)
    second = json.loads(caplog.records[1].message)
    assert first == {
        "action": "trace_compare",
        "error_type": "RuntimeError",
        "event": "studio.server_error",
        "event_version": 1,
        "failure_location": first["failure_location"],
        "fingerprint": first["fingerprint"],
        "method": "POST",
        "request_id": "request-1234",
        "status_code": 500,
        "timestamp": first["timestamp"],
        "workspace_id": "workspace-a",
    }
    assert first["failure_location"].startswith("test_studio_error_reporting.py:")
    assert len(first["fingerprint"]) == 20
    assert second["fingerprint"] == first["fingerprint"]
    combined = "\n".join(record.message for record in caplog.records)
    assert SECRET_FAILURE_MESSAGE not in combined
    assert "secret-key" not in combined
    assert "/private/customer" not in combined


def test_error_reporter_can_be_disabled_and_fails_closed_on_bad_config(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger=ERROR_LOGGER_NAME)
    StudioErrorReporter(enabled=False).emit_unhandled(
        request_id="request-1234",
        method="GET",
        path="/api/demo-report",
        workspace_id=None,
        error=_captured_failure(),
    )

    assert caplog.records == []
    assert StudioErrorReporter.from_env({}).runtime_status() == {
        "enabled": True,
        "format": "json",
        "event": "studio.server_error",
        "request_id_join": True,
        "includes_exception_messages": False,
        "durable_retention": False,
        "retention_kind": "log_only",
        "retention_days": 0,
        "max_stored_events": 0,
        "max_stored_events_per_workspace": 0,
    }
    with pytest.raises(StudioConfigurationError, match="must be true or false"):
        StudioErrorReporter.from_env({"TRACEBISECT_STUDIO_ERROR_LOG_ENABLED": "sometimes"})


def test_error_events_are_retained_bounded_searchable_and_message_free(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = tmp_path / "studio.db"
    SQLiteStudioStore(database, workspace_id="workspace-a").close()
    reporter = StudioErrorReporter(
        managed_database=database,
        max_stored_events=3,
        max_stored_events_per_workspace=2,
        retention_days=30,
    )
    caplog.set_level(logging.ERROR, logger=ERROR_LOGGER_NAME)

    for request_id, occurred in (
        ("request-expired", NOW - timedelta(days=40)),
        ("request-older1", NOW - timedelta(days=2)),
        ("request-newer1", NOW - timedelta(days=1)),
        ("request-latest", NOW),
    ):
        reporter.emit_unhandled(
            request_id=request_id,
            method="POST",
            path="/api/compare",
            workspace_id="workspace-a",
            error=_captured_failure(),
            now=occurred,
        )
    reporter.emit_unhandled(
        request_id="request-team-b1",
        method="POST",
        path="/api/compare",
        workspace_id="workspace-b",
        error=_captured_failure(),
        now=NOW + timedelta(seconds=1),
    )

    records = list_studio_error_events(database)
    assert [record.request_id for record in records] == [
        "request-team-b1",
        "request-latest",
        "request-newer1",
    ]
    assert [record.workspace_id for record in records] == [
        "workspace-b",
        "workspace-a",
        "workspace-a",
    ]
    assert all(record.status_code == 500 for record in records)
    assert len({record.fingerprint for record in records}) == 1
    assert list_studio_error_events(database, request_id="request-latest") == [records[1]]
    assert list_studio_error_events(database, fingerprint=records[0].fingerprint) == records
    assert reporter.runtime_status() == {
        "enabled": True,
        "format": "json",
        "event": "studio.server_error",
        "request_id_join": True,
        "includes_exception_messages": False,
        "durable_retention": True,
        "retention_kind": "local_sqlite",
        "retention_days": 30,
        "max_stored_events": 3,
        "max_stored_events_per_workspace": 2,
    }
    assert SECRET_FAILURE_MESSAGE.encode() not in database.read_bytes()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM studio_error_events").fetchone() == (3,)
    with pytest.raises(StudioErrorEventError, match="request ID"):
        list_studio_error_events(database, request_id="unsafe request")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE studio_error_events SET action = ? WHERE request_id = ?",
            ("trace_compare\x1b[2J", "request-latest"),
        )
    with pytest.raises(StudioErrorEventError, match="action is invalid"):
        list_studio_error_events(database, request_id="request-latest")


def test_error_event_retention_failure_never_replaces_the_original_report(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    reporter = StudioErrorReporter(managed_database=tmp_path)
    caplog.set_level(logging.ERROR, logger=ERROR_LOGGER_NAME)

    reporter.emit_unhandled(
        request_id="request-1234",
        method="POST",
        path="/api/compare",
        workspace_id="workspace-a",
        error=_captured_failure(),
    )

    assert len(caplog.records) == 1
    assert json.loads(caplog.records[0].message)["request_id"] == "request-1234"


def test_error_event_cli_finds_a_user_request_without_exposing_messages(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "studio.db"
    SQLiteStudioStore(database, workspace_id="workspace-a").close()
    StudioErrorReporter(managed_database=database).emit_unhandled(
        request_id="request-support-1234",
        method="POST",
        path="/api/compare",
        workspace_id="workspace-a",
        error=_captured_failure(),
        now=NOW,
    )

    assert (
        main(
            [
                "studio",
                "error-events",
                "list",
                "--database",
                str(database),
                "--request-id",
                "request-support-1234",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "request-support-1234" in output
    assert "trace_compare" in output
    assert "never exception messages" in output
    assert SECRET_FAILURE_MESSAGE not in output


def test_health_reports_error_boundary_and_honest_disabled_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(studio_api.app)

    enabled = client.get("/api/health")
    assert enabled.json()["errors"] == StudioErrorReporter().runtime_status()
    assert "secret-safe structured server error events" in enabled.json()["readiness"]["completed"]

    database = tmp_path / "studio.db"
    SQLiteStudioStore(database, workspace_id="workspace-a").close()
    durable_reporter = StudioErrorReporter(managed_database=database)
    monkeypatch.setattr(studio_api, "ERROR_REPORTER", durable_reporter)
    durable = client.get("/api/health")
    assert durable.json()["errors"]["durable_retention"] is True
    assert durable.json()["errors"]["retention_kind"] == "local_sqlite"
    assert durable.json()["limits"]["max_stored_error_events"] == 10_000
    assert durable.json()["limits"]["max_stored_error_events_per_workspace"] == 1_000
    assert (
        "bounded durable server-error retention with request-ID search"
        in durable.json()["readiness"]["completed"]
    )

    monkeypatch.setattr(studio_api, "ERROR_REPORTER", StudioErrorReporter(enabled=False))
    disabled = client.get("/api/health")
    assert disabled.json()["errors"]["enabled"] is False
    assert disabled.json()["errors"]["retention_kind"] == "disabled"
    assert "structured server error events" in disabled.json()["readiness"]["blockers"]


def test_unexpected_api_failure_returns_safe_request_id_and_emits_one_error_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_demo(_store: object) -> None:
        raise RuntimeError(SECRET_FAILURE_MESSAGE)

    database = tmp_path / "studio.db"
    SQLiteStudioStore(database, workspace_id="workspace-a").close()
    monkeypatch.setattr(studio_api, "seed_demo_report", fail_demo)
    monkeypatch.setattr(
        studio_api,
        "ERROR_REPORTER",
        StudioErrorReporter(managed_database=database),
    )
    asyncio.run(studio_api.RATE_LIMITER.reset())
    studio_api.METRICS.reset()
    caplog.set_level(logging.ERROR, logger=ERROR_LOGGER_NAME)
    client = TestClient(studio_api.app)

    response = client.get(
        "/api/demo-report",
        headers={"X-Request-ID": "request-live-1234"},
    )
    metrics = client.get("/api/metrics")

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "request-live-1234"
    assert response.json() == {
        "detail": "Studio hit an unexpected error. Share the request ID with support.",
        "request_id": "request-live-1234",
    }
    assert len(caplog.records) == 1
    event = json.loads(caplog.records[0].message)
    assert event["event"] == "studio.server_error"
    assert event["request_id"] == "request-live-1234"
    assert event["action"] == "demo_seed"
    assert SECRET_FAILURE_MESSAGE not in caplog.records[0].message
    retained = list_studio_error_events(database, request_id="request-live-1234")
    assert len(retained) == 1
    assert retained[0].action == "demo_seed"
    assert (
        'tracebisect_studio_http_requests_total{action="demo_seed",result="server_error"} 1'
        in metrics.text
    )
