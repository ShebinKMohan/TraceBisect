"""Authentication and request-scoped workspace isolation tests."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import tracebisect.studio.api as studio_api
from tracebisect.studio.audit import AUDIT_LOGGER_NAME
from tracebisect.studio.auth import StudioAuthConfig
from tracebisect.studio.storage import (
    StudioConfigurationError,
    StudioStoreRegistry,
)

WORKSPACE_A_KEY = "workspace-a-secret-key-0000000001"
WORKSPACE_B_KEY = "workspace-b-secret-key-0000000002"


def _secured_env(database_path: Path) -> dict[str, str]:
    return {
        "TRACEBISECT_STUDIO_STORAGE": "sqlite",
        "TRACEBISECT_STUDIO_SQLITE_PATH": str(database_path),
        "TRACEBISECT_STUDIO_WORKSPACE_ID": "health-probe",
        "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
        "TRACEBISECT_STUDIO_API_KEYS": json.dumps(
            {
                WORKSPACE_A_KEY: "workspace-a",
                WORKSPACE_B_KEY: "workspace-b",
            }
        ),
    }


def test_auth_defaults_to_open_local_mode() -> None:
    config = StudioAuthConfig.from_env({})

    assert config.required is False
    assert config.runtime_status() == {"mode": "none", "required": False}
    assert config.workspace_for_authorization(None) is None


def test_cors_origins_are_normalized_and_deduplicated() -> None:
    assert studio_api._configured_allowed_origins(
        "https://studio.example.com/,http://127.0.0.1:3000,https://studio.example.com"
    ) == ["https://studio.example.com", "http://127.0.0.1:3000"]


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "file:///tmp/studio",
        "https://user@example.com",
        "https://studio.example.com/path",
        "https://studio.example.com?debug=true",
    ],
)
def test_cors_origins_fail_closed(origin: str) -> None:
    with pytest.raises(StudioConfigurationError, match="must contain only HTTP"):
        studio_api._configured_allowed_origins(origin)


@pytest.mark.parametrize("raw", ["0", "-1", "not-a-number"])
def test_resource_limit_settings_fail_closed(raw: str) -> None:
    with pytest.raises(StudioConfigurationError, match="must be a positive integer"):
        studio_api._positive_env_int("TRACEBISECT_TEST_LIMIT", 10, raw)


def test_api_key_auth_maps_credentials_to_one_workspace(tmp_path: Path) -> None:
    config = StudioAuthConfig.from_env(_secured_env(tmp_path / "studio.db"))

    assert config.required is True
    assert config.runtime_status() == {"mode": "api-key", "required": True}
    assert config.workspace_for_authorization(f"Bearer {WORKSPACE_A_KEY}") == "workspace-a"
    assert config.workspace_for_authorization(f"bearer {WORKSPACE_A_KEY}") == "workspace-a"
    assert config.workspace_for_authorization(f"Bearer {WORKSPACE_B_KEY}") == "workspace-b"
    assert config.workspace_for_authorization(None) is None
    assert config.workspace_for_authorization("Basic abc") is None
    assert config.workspace_for_authorization("Bearer invalid") is None
    assert WORKSPACE_A_KEY not in repr(config)


@pytest.mark.parametrize(
    ("env", "message"),
    [
        (
            {"TRACEBISECT_STUDIO_AUTH_MODE": "session"},
            "TRACEBISECT_STUDIO_AUTH_MODE must be either 'none' or 'api-key'",
        ),
        (
            {"TRACEBISECT_STUDIO_API_KEYS": '{"secret":"workspace"}'},
            "TRACEBISECT_STUDIO_API_KEYS is set but auth mode is 'none'",
        ),
        (
            {"TRACEBISECT_STUDIO_AUTH_MODE": "api-key"},
            "TRACEBISECT_STUDIO_API_KEYS is required",
        ),
        (
            {
                "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
                "TRACEBISECT_STUDIO_API_KEYS": "not-json",
            },
            "TRACEBISECT_STUDIO_API_KEYS must be a JSON object",
        ),
        (
            {
                "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
                "TRACEBISECT_STUDIO_API_KEYS": '{"too-short":"workspace"}',
            },
            "Studio API keys must contain 32-256 URL-safe characters",
        ),
        (
            {
                "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
                "TRACEBISECT_STUDIO_API_KEYS": json.dumps({WORKSPACE_A_KEY: "workspace-a"}),
            },
            "api-key authentication requires TRACEBISECT_STUDIO_STORAGE=sqlite",
        ),
    ],
)
def test_auth_configuration_fails_closed(env: dict[str, str], message: str) -> None:
    with pytest.raises(StudioConfigurationError, match=message):
        StudioAuthConfig.from_env(env)


def test_secured_api_rejects_spoofing_and_isolates_workspace_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    env = _secured_env(tmp_path / "studio.db")
    auth_config = StudioAuthConfig.from_env(env)
    registry = StudioStoreRegistry(env)
    monkeypatch.setattr(studio_api, "AUTH_CONFIG", auth_config)
    monkeypatch.setattr(studio_api, "STORE_REGISTRY", registry)
    monkeypatch.setattr(studio_api, "STORE", registry.default_store)
    asyncio.run(studio_api.RATE_LIMITER.reset())
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    client = TestClient(studio_api.app)

    health_response = client.get("/api/health")
    missing_response = client.get(
        "/api/demo-report",
        headers={"Origin": "http://127.0.0.1:3000"},
    )
    invalid_response = client.get(
        "/api/demo-report",
        headers={"Authorization": "Bearer invalid"},
    )

    assert health_response.status_code == 200
    assert health_response.json()["auth"] == {"mode": "api-key", "required": True}
    assert health_response.json()["runtime"]["workspace_id"] == "protected"
    assert health_response.json()["runtime"]["trace_count"] == 0
    assert health_response.json()["readiness"]["production_saas_ready"] is False
    assert "bearer API-key authentication" in health_response.json()["readiness"]["completed"]
    assert (
        "verified local backup and non-destructive restore tooling"
        in health_response.json()["readiness"]["completed"]
    )
    assert missing_response.status_code == 401
    assert missing_response.headers["www-authenticate"] == "Bearer"
    assert missing_response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    assert missing_response.json() == {"detail": "A valid Studio workspace API key is required."}
    assert invalid_response.status_code == 401

    workspace_a_headers = {
        "Authorization": f"Bearer {WORKSPACE_A_KEY}",
        "X-TraceBisect-Workspace": "workspace-b",
    }
    workspace_b_headers = {"Authorization": f"Bearer {WORKSPACE_B_KEY}"}
    workspace_a_session = client.get("/api/session", headers=workspace_a_headers)
    workspace_b_session = client.get("/api/session", headers=workspace_b_headers)
    workspace_a_report = client.get("/api/demo-report", headers=workspace_a_headers)
    workspace_b_report = client.get("/api/demo-report", headers=workspace_b_headers)

    assert workspace_a_session.status_code == 200
    assert workspace_a_session.json()["workspace_id"] == "workspace-a"
    assert workspace_b_session.json()["workspace_id"] == "workspace-b"
    assert workspace_a_report.status_code == 200
    assert workspace_b_report.status_code == 200
    assert workspace_a_report.json()["report_id"] != workspace_b_report.json()["report_id"]

    cross_workspace_response = client.get(
        f"/api/runs/{workspace_b_report.json()['report_id']}",
        headers=workspace_a_headers,
    )

    assert cross_workspace_response.status_code == 404
    assert registry.get("workspace-a").runtime_status()["report_count"] == 1
    assert registry.get("workspace-b").runtime_status()["report_count"] == 1
    audit_output = "\n".join(record.message for record in caplog.records)
    assert '"auth_outcome":"rejected"' in audit_output
    assert '"workspace_id":"workspace-a"' in audit_output
    assert '"workspace_id":"workspace-b"' in audit_output
    assert WORKSPACE_A_KEY not in audit_output
    assert WORKSPACE_B_KEY not in audit_output
    registry.close()
