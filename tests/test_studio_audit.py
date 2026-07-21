"""Tests for secret-safe structured Studio audit events."""

from __future__ import annotations

import json
import logging

import pytest

from tracebisect.studio.audit import AUDIT_LOGGER_NAME, StudioAudit
from tracebisect.studio.storage import StudioConfigurationError


def test_request_id_accepts_only_bounded_safe_values() -> None:
    audit = StudioAudit()

    assert audit.request_id("request-1234") == "request-1234"
    generated_for_short = audit.request_id("short")
    generated_for_injection = audit.request_id("safe\nforged-log-line")

    assert len(generated_for_short) == 32
    assert len(generated_for_injection) == 32
    assert generated_for_short != generated_for_injection


def test_request_audit_is_structured_bounded_and_secret_safe(
    caplog: pytest.LogCaptureFixture,
) -> None:
    audit = StudioAudit()
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)

    audit.emit_request(
        request_id="request-1234",
        method="GET",
        path="/api/runs/resource-id-that-must-not-be-logged",
        status_code=404,
        duration_ms=12.34567,
        auth_outcome="authenticated",
        workspace_id="workspace-a",
    )

    assert len(caplog.records) == 1
    payload = json.loads(caplog.records[0].message)
    assert payload == {
        "action": "run_read",
        "auth_outcome": "authenticated",
        "duration_ms": 12.346,
        "event": "studio.http_request",
        "event_version": 1,
        "method": "GET",
        "request_id": "request-1234",
        "result": "client_error",
        "status_code": 404,
        "timestamp": payload["timestamp"],
        "workspace_id": "workspace-a",
    }
    assert payload["timestamp"].endswith("Z")
    assert "resource-id-that-must-not-be-logged" not in caplog.records[0].message
    assert "Authorization" not in caplog.records[0].message
    assert "Bearer" not in caplog.records[0].message


@pytest.mark.parametrize(
    ("path", "method", "action"),
    [
        ("/api/health", "GET", "health_check"),
        ("/api/metrics", "GET", "metrics_scrape"),
        ("/api/browser-session", "POST", "browser_session_create"),
        ("/api/browser-session/logout", "POST", "browser_session_revoke"),
        ("/api/access-keys", "GET", "access_key_list"),
        ("/api/access-keys", "POST", "access_key_create"),
        ("/api/access-keys/key-secret", "DELETE", "access_key_revoke"),
        ("/api/traces/upload", "POST", "trace_upload"),
        ("/api/compare", "POST", "trace_compare"),
        ("/api/regression-cases", "POST", "case_create"),
        ("/api/regression-cases/case-secret/run", "POST", "case_run"),
        ("/unknown", "GET", "unclassified_request"),
    ],
)
def test_request_audit_normalizes_actions(
    path: str,
    method: str,
    action: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    audit = StudioAudit()
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)

    audit.emit_request(
        request_id="request-1234",
        method=method,
        path=path,
        status_code=200,
        duration_ms=1,
        auth_outcome="not_required",
        workspace_id="local",
    )

    payload = json.loads(caplog.records[-1].message)
    assert payload["action"] == action
    assert "case-secret" not in caplog.records[-1].message
    assert "key-secret" not in caplog.records[-1].message


def test_disabled_audit_emits_nothing(caplog: pytest.LogCaptureFixture) -> None:
    audit = StudioAudit(enabled=False)
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)

    audit.emit_request(
        request_id="request-1234",
        method="GET",
        path="/api/health",
        status_code=200,
        duration_ms=1,
        auth_outcome="public",
        workspace_id=None,
    )

    assert caplog.records == []


def test_audit_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACEBISECT_STUDIO_AUDIT_LOG_ENABLED", "sometimes")

    with pytest.raises(StudioConfigurationError, match="must be true or false"):
        StudioAudit.from_env()
