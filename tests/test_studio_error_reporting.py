"""Tests for secret-safe structured Studio server error events."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

import tracebisect.studio.api as studio_api
from tracebisect.studio.error_reporting import (
    ERROR_LOGGER_NAME,
    StudioErrorReporter,
)
from tracebisect.studio.storage import StudioConfigurationError

SECRET_FAILURE_MESSAGE = "tbsk_secret-key-that-must-not-be-logged /private/customer/workspace.db"


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
    }
    with pytest.raises(StudioConfigurationError, match="must be true or false"):
        StudioErrorReporter.from_env({"TRACEBISECT_STUDIO_ERROR_LOG_ENABLED": "sometimes"})


def test_health_reports_error_boundary_and_honest_disabled_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(studio_api.app)

    enabled = client.get("/api/health")
    assert enabled.json()["errors"] == StudioErrorReporter().runtime_status()
    assert "secret-safe structured server error events" in enabled.json()["readiness"]["completed"]

    monkeypatch.setattr(studio_api, "ERROR_REPORTER", StudioErrorReporter(enabled=False))
    disabled = client.get("/api/health")
    assert disabled.json()["errors"]["enabled"] is False
    assert "structured server error events" in disabled.json()["readiness"]["blockers"]


def test_unexpected_api_failure_returns_safe_request_id_and_emits_one_error_event(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_demo(_store: object) -> None:
        raise RuntimeError(SECRET_FAILURE_MESSAGE)

    monkeypatch.setattr(studio_api, "seed_demo_report", fail_demo)
    monkeypatch.setattr(studio_api, "ERROR_REPORTER", StudioErrorReporter())
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
    assert (
        'tracebisect_studio_http_requests_total{action="demo_seed",result="server_error"} 1'
        in metrics.text
    )
