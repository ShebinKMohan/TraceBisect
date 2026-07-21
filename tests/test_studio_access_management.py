"""API tests for admin self-service workspace access management."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import tracebisect.studio.access_keys as access_keys
import tracebisect.studio.api as studio_api
from tracebisect.studio.access_keys import (
    create_studio_api_key,
    workspace_for_managed_api_key,
)
from tracebisect.studio.audit import AUDIT_LOGGER_NAME
from tracebisect.studio.auth import StudioAuthConfig
from tracebisect.studio.storage import StudioStoreRegistry


def _managed_env(database_path: Path, pepper: str) -> dict[str, str]:
    return {
        "TRACEBISECT_STUDIO_STORAGE": "sqlite",
        "TRACEBISECT_STUDIO_SQLITE_PATH": str(database_path),
        "TRACEBISECT_STUDIO_WORKSPACE_ID": "health-probe",
        "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
        "TRACEBISECT_STUDIO_API_KEY_PEPPER": pepper,
    }


def _install_managed_runtime(
    *,
    env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> StudioStoreRegistry:
    registry = StudioStoreRegistry(env)
    monkeypatch.setattr(studio_api, "AUTH_CONFIG", StudioAuthConfig.from_env(env))
    monkeypatch.setattr(studio_api, "STORE_REGISTRY", registry)
    monkeypatch.setattr(studio_api, "STORE", registry.default_store)
    monkeypatch.setattr(studio_api, "BROWSER_SESSION_COOKIE_SECURE", False)
    asyncio.run(studio_api.RATE_LIMITER.reset())
    return registry


def test_admin_manages_only_its_workspace_and_plaintext_is_shown_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = tmp_path / "studio.db"
    pepper = "self-service-access-pepper-with-at-least-32-characters"
    team_a_admin = create_studio_api_key(
        database,
        workspace_id="team-a",
        role="admin",
        label="Current owner",
        expires_in_days=90,
        pepper=pepper,
    )
    team_a_editor = create_studio_api_key(
        database,
        workspace_id="team-a",
        role="editor",
        label="Developer",
        expires_in_days=90,
        pepper=pepper,
    )
    team_b_admin = create_studio_api_key(
        database,
        workspace_id="team-b",
        role="admin",
        label="Other owner",
        expires_in_days=90,
        pepper=pepper,
    )
    env = _managed_env(database, pepper)
    registry = _install_managed_runtime(env=env, monkeypatch=monkeypatch)
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    client = TestClient(studio_api.app)
    admin_headers = {"Authorization": f"Bearer {team_a_admin.api_key}"}
    editor_headers = {"Authorization": f"Bearer {team_a_editor.api_key}"}

    health = client.get("/api/health")
    session = client.get("/api/session", headers=admin_headers)
    listed = client.get(studio_api.ACCESS_KEYS_API_PATH, headers=admin_headers)
    editor_denied = client.get(studio_api.ACCESS_KEYS_API_PATH, headers=editor_headers)

    assert health.json()["auth"]["self_service_access_management"] is True
    assert "admin self-service access-key management" in health.json()["readiness"]["completed"]
    assert "key_id" not in session.json()
    assert listed.status_code == 200
    assert listed.json()["current_key_id"] == team_a_admin.record.key_id
    assert {record["key_id"] for record in listed.json()["keys"]} == {
        team_a_admin.record.key_id,
        team_a_editor.record.key_id,
    }
    assert team_b_admin.record.key_id not in listed.text
    assert team_a_admin.api_key not in listed.text
    assert team_a_editor.api_key not in listed.text
    assert editor_denied.status_code == 403
    assert editor_denied.json() == {
        "detail": "Workspace admin access is required to manage access keys."
    }

    monkeypatch.setattr(access_keys, "MAX_ACTIVE_STUDIO_API_KEYS_PER_WORKSPACE", 2)
    at_capacity = client.post(
        studio_api.ACCESS_KEYS_API_PATH,
        headers=admin_headers,
        json={"label": "Extra reviewer", "role": "viewer", "expires_in_days": 30},
    )
    assert at_capacity.status_code == 409
    assert "active access-key limit" in at_capacity.json()["detail"]
    monkeypatch.setattr(access_keys, "MAX_ACTIVE_STUDIO_API_KEYS_PER_WORKSPACE", 100)

    created = client.post(
        studio_api.ACCESS_KEYS_API_PATH,
        headers=admin_headers,
        json={"label": "QA reviewer", "role": "viewer", "expires_in_days": 30},
    )

    assert created.status_code == 201
    created_payload = created.json()
    created_key = created_payload["api_key"]
    created_id = created_payload["record"]["key_id"]
    assert created_key.startswith(f"tbsk_{created_id}_")
    assert created_payload["record"]["workspace_id"] == "team-a"
    assert created_payload["record"]["role"] == "viewer"
    assert created.text.count(created_key) == 1
    assert (
        workspace_for_managed_api_key(
            database,
            api_key=created_key,
            pepper=pepper,
        )
        == "team-a"
    )

    relisted = client.get(studio_api.ACCESS_KEYS_API_PATH, headers=admin_headers)
    protect_current = client.delete(
        f"{studio_api.ACCESS_KEYS_API_PATH}/{team_a_admin.record.key_id}",
        headers=admin_headers,
    )
    cross_workspace = client.delete(
        f"{studio_api.ACCESS_KEYS_API_PATH}/{team_b_admin.record.key_id}",
        headers=admin_headers,
    )
    revoked = client.delete(
        f"{studio_api.ACCESS_KEYS_API_PATH}/{created_id}",
        headers=admin_headers,
    )

    assert created_key not in relisted.text
    assert protect_current.status_code == 409
    assert "current session" in protect_current.json()["detail"]
    assert cross_workspace.status_code == 404
    assert (
        workspace_for_managed_api_key(
            database,
            api_key=team_b_admin.api_key,
            pepper=pepper,
        )
        == "team-b"
    )
    assert revoked.status_code == 200
    assert revoked.json()["key"]["status"] == "revoked"
    assert (
        client.get(
            "/api/session",
            headers={"Authorization": f"Bearer {created_key}"},
        ).status_code
        == 401
    )

    audit_output = "\n".join(record.message for record in caplog.records)
    assert '"action":"access_key_list"' in audit_output
    assert '"action":"access_key_create"' in audit_output
    assert '"action":"access_key_revoke"' in audit_output
    assert created_key not in audit_output
    assert created_id not in audit_output
    assert team_a_admin.api_key not in audit_output
    registry.close()


def test_cookie_admin_access_management_requires_origin_and_csrf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "studio.db"
    pepper = "self-service-cookie-pepper-with-at-least-32-characters"
    owner = create_studio_api_key(
        database,
        workspace_id="team-a",
        role="admin",
        label="Owner",
        expires_in_days=90,
        pepper=pepper,
    )
    target = create_studio_api_key(
        database,
        workspace_id="team-a",
        role="viewer",
        label="Temporary reviewer",
        expires_in_days=7,
        pepper=pepper,
    )
    env = _managed_env(database, pepper)
    registry = _install_managed_runtime(env=env, monkeypatch=monkeypatch)
    client = TestClient(studio_api.app)
    origin = "http://127.0.0.1:3000"
    exchange = client.post(
        studio_api.BROWSER_SESSION_EXCHANGE_PATH,
        headers={"Authorization": f"Bearer {owner.api_key}", "Origin": origin},
    )
    assert exchange.status_code == 200
    assert "key_id" not in exchange.json()

    missing_protection = client.delete(f"{studio_api.ACCESS_KEYS_API_PATH}/{target.record.key_id}")
    accepted = client.delete(
        f"{studio_api.ACCESS_KEYS_API_PATH}/{target.record.key_id}",
        headers={
            "Origin": origin,
            studio_api.BROWSER_CSRF_HEADER: studio_api.BROWSER_CSRF_VALUE,
        },
    )

    assert missing_protection.status_code == 403
    assert "origin protection" in missing_protection.json()["detail"]
    assert accepted.status_code == 200
    assert accepted.json()["key"]["status"] == "revoked"
    registry.close()
