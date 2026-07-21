"""Authentication and request-scoped workspace isolation tests."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import tracebisect.studio.api as studio_api
from tracebisect.studio.access_keys import (
    create_studio_api_key,
    revoke_studio_api_key,
)
from tracebisect.studio.audit import AUDIT_LOGGER_NAME
from tracebisect.studio.auth import StudioAuthConfig
from tracebisect.studio.metrics import StudioMetricsAccess
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
    assert config.runtime_status() == {
        "mode": "none",
        "required": False,
        "credential_source": "none",
        "browser_sessions": False,
        "self_service_access_management": False,
        "ingestion_tokens": False,
        "human_accounts": False,
        "browser_session_ttl_seconds": 0,
    }
    assert config.workspace_for_authorization(None) is None


def test_cors_origins_are_normalized_and_deduplicated() -> None:
    assert studio_api._configured_allowed_origins(
        "https://studio.example.com/,http://127.0.0.1:3000,https://studio.example.com"
    ) == ["https://studio.example.com", "http://127.0.0.1:3000"]


def test_browser_session_cookie_security_is_safe_by_default() -> None:
    assert studio_api._configured_secure_session_cookie(["https://studio.example.com"])
    assert not studio_api._configured_secure_session_cookie(
        ["http://127.0.0.1:3000", "http://localhost:3000"]
    )
    with pytest.raises(StudioConfigurationError, match="mixed or non-loopback HTTP"):
        studio_api._configured_secure_session_cookie(
            ["https://studio.example.com", "http://127.0.0.1:3000"]
        )
    with pytest.raises(StudioConfigurationError, match="must be auto, true, or false"):
        studio_api._configured_secure_session_cookie(
            ["https://studio.example.com"],
            "sometimes",
        )
    assert studio_api._browser_session_cookie_name(secure=True).startswith("__Host-")
    assert not studio_api._browser_session_cookie_name(secure=False).startswith("__Host-")


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
    assert config.runtime_status() == {
        "mode": "api-key",
        "required": True,
        "credential_source": "environment",
        "browser_sessions": False,
        "self_service_access_management": False,
        "ingestion_tokens": False,
        "human_accounts": False,
        "browser_session_ttl_seconds": 0,
    }
    assert config.workspace_for_authorization(f"Bearer {WORKSPACE_A_KEY}") == "workspace-a"
    assert config.workspace_for_authorization(f"bearer {WORKSPACE_A_KEY}") == "workspace-a"
    assert config.workspace_for_authorization(f"Bearer {WORKSPACE_B_KEY}") == "workspace-b"
    assert config.workspace_for_authorization(None) is None
    assert config.workspace_for_authorization("Basic abc") is None
    assert config.workspace_for_authorization("Bearer invalid") is None
    assert WORKSPACE_A_KEY not in repr(config)


def test_managed_api_key_auth_observes_expiry_and_immediate_revocation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "studio.db"
    pepper = "managed-pepper-with-at-least-32-characters"
    issued = create_studio_api_key(
        database_path,
        workspace_id="workspace-a",
        role="editor",
        label="Browser access",
        expires_in_days=90,
        pepper=pepper,
    )
    env = {
        "TRACEBISECT_STUDIO_STORAGE": "sqlite",
        "TRACEBISECT_STUDIO_SQLITE_PATH": str(database_path),
        "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
        "TRACEBISECT_STUDIO_API_KEY_PEPPER": pepper,
    }
    config = StudioAuthConfig.from_env(env)

    assert config.runtime_status() == {
        "mode": "api-key",
        "required": True,
        "credential_source": "managed",
        "browser_sessions": True,
        "self_service_access_management": True,
        "ingestion_tokens": True,
        "human_accounts": False,
        "browser_session_ttl_seconds": 28800,
    }
    assert config.workspace_for_authorization(f"Bearer {issued.api_key}") == "workspace-a"
    principal = config.principal_for_authorization(f"Bearer {issued.api_key}")
    assert principal is not None
    assert principal.role == "editor"
    assert config.workspace_for_authorization("Bearer tbsk_bad_bad") is None
    assert issued.api_key not in repr(config)
    assert pepper not in repr(config)

    revoke_studio_api_key(database_path, key_id=issued.record.key_id)

    assert config.workspace_for_authorization(f"Bearer {issued.api_key}") is None


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
            {"TRACEBISECT_STUDIO_API_KEY_PEPPER": "p" * 40},
            "TRACEBISECT_STUDIO_API_KEY_PEPPER is set but auth mode is 'none'",
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
            "api-key authentication requires durable SQLite or PostgreSQL storage",
        ),
        (
            {
                "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
                "TRACEBISECT_STUDIO_STORAGE": "sqlite",
                "TRACEBISECT_STUDIO_SQLITE_PATH": "studio.db",
                "TRACEBISECT_STUDIO_API_KEYS": json.dumps({WORKSPACE_A_KEY: "workspace-a"}),
                "TRACEBISECT_STUDIO_API_KEY_PEPPER": "p" * 40,
            },
            "configure either managed API keys",
        ),
        (
            {
                "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
                "TRACEBISECT_STUDIO_STORAGE": "sqlite",
                "TRACEBISECT_STUDIO_SQLITE_PATH": "studio.db",
                "TRACEBISECT_STUDIO_API_KEY_PEPPER": "short",
            },
            "TRACEBISECT_STUDIO_API_KEY_PEPPER must contain",
        ),
        (
            {
                "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
                "TRACEBISECT_STUDIO_STORAGE": "sqlite",
                "TRACEBISECT_STUDIO_API_KEY_PEPPER": "p" * 40,
            },
            "TRACEBISECT_STUDIO_SQLITE_PATH is required",
        ),
        (
            {
                "TRACEBISECT_STUDIO_BROWSER_SESSION_TTL_SECONDS": "3600",
            },
            "TRACEBISECT_STUDIO_BROWSER_SESSION_TTL_SECONDS is set but auth mode is 'none'",
        ),
        (
            {
                **_secured_env(Path("studio.db")),
                "TRACEBISECT_STUDIO_BROWSER_SESSION_TTL_SECONDS": "3600",
            },
            "TRACEBISECT_STUDIO_BROWSER_SESSION_TTL_SECONDS requires managed API keys",
        ),
        (
            {
                "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
                "TRACEBISECT_STUDIO_STORAGE": "sqlite",
                "TRACEBISECT_STUDIO_SQLITE_PATH": "studio.db",
                "TRACEBISECT_STUDIO_API_KEY_PEPPER": "p" * 40,
                "TRACEBISECT_STUDIO_BROWSER_SESSION_TTL_SECONDS": "60",
            },
            "TRACEBISECT_STUDIO_BROWSER_SESSION_TTL_SECONDS must be between",
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
    assert health_response.json()["auth"] == {
        "mode": "api-key",
        "required": True,
        "credential_source": "environment",
        "browser_sessions": False,
        "self_service_access_management": False,
        "ingestion_tokens": False,
        "human_accounts": False,
        "browser_session_ttl_seconds": 0,
        "browser_session_cookie_secure": False,
    }
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
    assert missing_response.json() == {
        "detail": "A valid Studio session, workspace key, or ingestion token is required."
    }
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
    assert workspace_a_session.json()["role"] == "admin"
    assert workspace_b_session.json()["workspace_id"] == "workspace-b"
    assert workspace_b_session.json()["role"] == "admin"
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


def test_managed_key_secures_live_api_and_revokes_without_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database_path = tmp_path / "studio.db"
    pepper = "live-managed-pepper-with-at-least-32-characters"
    issued = create_studio_api_key(
        database_path,
        workspace_id="workspace-a",
        role="editor",
        label="Browser access",
        expires_in_days=90,
        pepper=pepper,
    )
    env = {
        "TRACEBISECT_STUDIO_STORAGE": "sqlite",
        "TRACEBISECT_STUDIO_SQLITE_PATH": str(database_path),
        "TRACEBISECT_STUDIO_WORKSPACE_ID": "health-probe",
        "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
        "TRACEBISECT_STUDIO_API_KEY_PEPPER": pepper,
    }
    registry = StudioStoreRegistry(env)
    auth_config = StudioAuthConfig.from_env(env)
    monkeypatch.setattr(studio_api, "AUTH_CONFIG", auth_config)
    monkeypatch.setattr(studio_api, "STORE_REGISTRY", registry)
    monkeypatch.setattr(studio_api, "STORE", registry.default_store)
    asyncio.run(studio_api.RATE_LIMITER.reset())
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    client = TestClient(studio_api.app)
    headers = {"Authorization": f"Bearer {issued.api_key}"}

    health_response = client.get("/api/health")
    accepted_response = client.get("/api/session", headers=headers)

    assert health_response.json()["auth"]["credential_source"] == "managed"
    assert (
        "hashed expiring workspace keys with admin issuance and revocation"
        in health_response.json()["readiness"]["completed"]
    )
    assert accepted_response.status_code == 200
    assert accepted_response.json()["workspace_id"] == "workspace-a"
    assert accepted_response.json()["role"] == "editor"

    revoke_studio_api_key(database_path, key_id=issued.record.key_id)
    revoked_response = client.get("/api/session", headers=headers)

    assert revoked_response.status_code == 401
    audit_output = "\n".join(record.message for record in caplog.records)
    assert issued.api_key not in audit_output
    assert pepper not in audit_output
    registry.close()


def test_secured_metrics_require_a_dedicated_scrape_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    metrics_token = "dedicated-metrics-token-with-at-least-32-characters"
    env = {
        **_secured_env(tmp_path / "studio.db"),
        "TRACEBISECT_STUDIO_METRICS_TOKEN": metrics_token,
    }
    registry = StudioStoreRegistry(env)
    monkeypatch.setattr(studio_api, "AUTH_CONFIG", StudioAuthConfig.from_env(env))
    monkeypatch.setattr(studio_api, "METRICS_ACCESS", StudioMetricsAccess.from_env(env))
    monkeypatch.setattr(studio_api, "STORE_REGISTRY", registry)
    monkeypatch.setattr(studio_api, "STORE", registry.default_store)
    asyncio.run(studio_api.RATE_LIMITER.reset())
    studio_api.METRICS.reset()
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    client = TestClient(studio_api.app)

    missing = client.get("/api/metrics")
    workspace_admin = client.get(
        "/api/metrics",
        headers={"Authorization": f"Bearer {WORKSPACE_A_KEY}"},
    )
    accepted = client.get(
        "/api/metrics",
        headers={"Authorization": f"Bearer {metrics_token}"},
    )
    health = client.get("/api/health")

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert workspace_admin.status_code == 401
    assert accepted.status_code == 200
    assert health.json()["metrics"]["access"] == "bearer_token"
    assert (
        "low-cardinality Prometheus metrics with dedicated scrape access"
        in health.json()["readiness"]["completed"]
    )
    assert (
        "vendor-neutral alert rules and beginner incident runbook"
        in health.json()["readiness"]["completed"]
    )
    assert (
        "deployment wiring for metrics collection and alert delivery, "
        "plus shared error-event retention"
        in health.json()["readiness"]["blockers"]
    )
    assert "secret-safe structured server error events" in health.json()["readiness"]["completed"]
    assert "workspace-a" not in accepted.text
    assert metrics_token not in accepted.text
    assert WORKSPACE_A_KEY not in accepted.text
    audit_output = "\n".join(record.message for record in caplog.records)
    assert metrics_token not in audit_output
    assert WORKSPACE_A_KEY not in audit_output
    registry.close()


def test_secured_metrics_are_unavailable_without_scrape_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _secured_env(tmp_path / "studio.db")
    registry = StudioStoreRegistry(env)
    monkeypatch.setattr(studio_api, "AUTH_CONFIG", StudioAuthConfig.from_env(env))
    monkeypatch.setattr(studio_api, "METRICS_ACCESS", StudioMetricsAccess.from_env(env))
    monkeypatch.setattr(studio_api, "STORE_REGISTRY", registry)
    monkeypatch.setattr(studio_api, "STORE", registry.default_store)
    asyncio.run(studio_api.RATE_LIMITER.reset())
    studio_api.METRICS.reset()
    client = TestClient(studio_api.app)

    response = client.get("/api/metrics")

    assert response.status_code == 503
    assert response.headers.get("www-authenticate") is None
    assert response.json() == {
        "detail": "Studio metrics access is not configured for secured mode."
    }
    registry.close()


def test_viewer_role_is_read_only_while_editor_can_seed_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database_path = tmp_path / "studio.db"
    pepper = "role-test-pepper-with-at-least-32-characters"
    viewer = create_studio_api_key(
        database_path,
        workspace_id="workspace-a",
        role="viewer",
        label="Reviewer",
        expires_in_days=90,
        pepper=pepper,
    )
    editor = create_studio_api_key(
        database_path,
        workspace_id="workspace-a",
        role="editor",
        label="Developer",
        expires_in_days=90,
        pepper=pepper,
    )
    env = {
        "TRACEBISECT_STUDIO_STORAGE": "sqlite",
        "TRACEBISECT_STUDIO_SQLITE_PATH": str(database_path),
        "TRACEBISECT_STUDIO_WORKSPACE_ID": "health-probe",
        "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
        "TRACEBISECT_STUDIO_API_KEY_PEPPER": pepper,
    }
    registry = StudioStoreRegistry(env)
    auth_config = StudioAuthConfig.from_env(env)
    monkeypatch.setattr(studio_api, "AUTH_CONFIG", auth_config)
    monkeypatch.setattr(studio_api, "STORE_REGISTRY", registry)
    monkeypatch.setattr(studio_api, "STORE", registry.default_store)
    asyncio.run(studio_api.RATE_LIMITER.reset())
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    client = TestClient(studio_api.app)
    editor_headers = {"Authorization": f"Bearer {editor.api_key}"}
    viewer_headers = {"Authorization": f"Bearer {viewer.api_key}"}

    seeded = client.get("/api/demo-report", headers=editor_headers)
    viewer_session = client.get("/api/session", headers=viewer_headers)
    viewer_reads = [
        client.get("/api/traces", headers=viewer_headers),
        client.get("/api/runs", headers=viewer_headers),
        client.get("/api/regression-cases", headers=viewer_headers),
    ]
    counts_before = registry.get("workspace-a").runtime_status()
    viewer_mutations = [
        client.get("/api/demo-report", headers=viewer_headers),
        client.post("/api/traces/upload", headers=viewer_headers),
        client.post("/api/compare", headers=viewer_headers, json={}),
        client.post("/api/regression-cases", headers=viewer_headers, json={}),
        client.post("/api/regression-cases/unknown/run", headers=viewer_headers, json={}),
    ]

    assert seeded.status_code == 200
    assert viewer_session.status_code == 200
    assert viewer_session.json()["role"] == "viewer"
    assert all(response.status_code == 200 for response in viewer_reads)
    assert all(response.status_code == 403 for response in viewer_mutations)
    assert all("viewer access" in response.json()["detail"] for response in viewer_mutations)
    assert registry.get("workspace-a").runtime_status() == counts_before
    audit_output = "\n".join(record.message for record in caplog.records)
    assert '"status_code":403' in audit_output
    assert viewer.api_key not in audit_output
    assert editor.api_key not in audit_output
    registry.close()
