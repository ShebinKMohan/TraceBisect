"""Security and API tests for managed Studio human identity."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import tracebisect.studio.api as studio_api
import tracebisect.studio.identity as identity
from tracebisect.cli import main
from tracebisect.studio.access_keys import create_studio_api_key
from tracebisect.studio.audit import AUDIT_LOGGER_NAME
from tracebisect.studio.auth import StudioAuthConfig
from tracebisect.studio.identity import (
    StudioIdentityConflict,
    accept_studio_invitation,
    create_studio_invitation,
    list_studio_memberships,
    login_studio_identity,
    preview_studio_invitation,
    principal_for_studio_identity_session,
    recover_studio_identity,
    remove_studio_membership,
    update_studio_membership_role,
)
from tracebisect.studio.storage import StudioConfigurationError, StudioStoreRegistry

IDENTITY_SECRET = "identity-test-secret-with-at-least-thirty-two-characters"
PASSWORD = "correct horse battery staple"
ORIGIN = "http://127.0.0.1:3000"
CSRF_HEADERS = {
    "Origin": ORIGIN,
    studio_api.BROWSER_CSRF_HEADER: studio_api.BROWSER_CSRF_VALUE,
}


def _invite(
    database: Path,
    *,
    workspace: str = "workspace-a",
    email: str = "owner@example.com",
    role: str = "admin",
    now: datetime | None = None,
):
    return create_studio_invitation(
        database,
        workspace_id=workspace,
        email=email,
        role=role,  # type: ignore[arg-type]
        expires_in_days=7,
        identity_secret_value=IDENTITY_SECRET,
        now=now,
    )


def _enroll(database: Path, *, workspace: str = "workspace-a"):
    invitation = _invite(database, workspace=workspace)
    return accept_studio_invitation(
        database,
        invitation_token=invitation.invitation_token,
        display_name="Alex Owner",
        password=PASSWORD,
        identity_secret_value=IDENTITY_SECRET,
    )


def _managed_env(database: Path, pepper: str) -> dict[str, str]:
    return {
        "TRACEBISECT_STUDIO_STORAGE": "sqlite",
        "TRACEBISECT_STUDIO_SQLITE_PATH": str(database),
        "TRACEBISECT_STUDIO_WORKSPACE_ID": "health-probe",
        "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
        "TRACEBISECT_STUDIO_API_KEY_PEPPER": pepper,
        "TRACEBISECT_STUDIO_IDENTITY_SECRET": IDENTITY_SECRET,
        "TRACEBISECT_STUDIO_BROWSER_SESSION_TTL_SECONDS": "3600",
    }


def _install_runtime(database: Path, pepper: str, monkeypatch) -> StudioStoreRegistry:
    env = _managed_env(database, pepper)
    registry = StudioStoreRegistry(env)
    monkeypatch.setattr(studio_api, "AUTH_CONFIG", StudioAuthConfig.from_env(env))
    monkeypatch.setattr(studio_api, "STORE_REGISTRY", registry)
    monkeypatch.setattr(studio_api, "STORE", registry.default_store)
    monkeypatch.setattr(studio_api, "BROWSER_SESSION_COOKIE_SECURE", False)
    asyncio.run(studio_api.RATE_LIMITER.reset())
    return registry


def test_identity_secret_is_dedicated_to_managed_auth(tmp_path: Path) -> None:
    with pytest.raises(StudioConfigurationError, match="auth mode is 'none'"):
        StudioAuthConfig.from_env({"TRACEBISECT_STUDIO_IDENTITY_SECRET": IDENTITY_SECRET})
    with pytest.raises(StudioConfigurationError, match="requires managed API keys"):
        StudioAuthConfig.from_env(
            {
                "TRACEBISECT_STUDIO_STORAGE": "sqlite",
                "TRACEBISECT_STUDIO_SQLITE_PATH": str(tmp_path / "studio.db"),
                "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
                "TRACEBISECT_STUDIO_API_KEYS": '{"abcdefghijklmnopqrstuvwxyzABCDEF":"team-a"}',
                "TRACEBISECT_STUDIO_IDENTITY_SECRET": IDENTITY_SECRET,
            }
        )
    with pytest.raises(StudioConfigurationError, match="at least 32 characters"):
        StudioAuthConfig.from_env(
            {
                "TRACEBISECT_STUDIO_STORAGE": "sqlite",
                "TRACEBISECT_STUDIO_SQLITE_PATH": str(tmp_path / "studio.db"),
                "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
                "TRACEBISECT_STUDIO_API_KEY_PEPPER": (
                    "managed-pepper-with-at-least-thirty-two-characters"
                ),
                "TRACEBISECT_STUDIO_IDENTITY_SECRET": "too-short",
            }
        )


def test_identity_secret_cli_shows_one_deployment_value(capsys) -> None:
    assert main(["studio", "identity", "generate-secret"]) == 0
    output = capsys.readouterr().out
    assignment = next(
        line.strip()
        for line in output.splitlines()
        if line.strip().startswith("TRACEBISECT_STUDIO_IDENTITY_SECRET=")
    )
    secret = assignment.partition("=")[2]
    assert len(secret) >= 64
    assert output.count(secret) == 1
    assert "Do not commit it" in output


def test_invitation_password_recovery_and_sessions_store_only_protected_secrets(
    tmp_path: Path,
) -> None:
    database = tmp_path / "studio.db"
    now = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)
    invitation = _invite(database, now=now)

    assert (
        preview_studio_invitation(
            database,
            invitation_token=invitation.invitation_token,
            identity_secret_value=IDENTITY_SECRET,
            now=now,
        )
        == invitation.record
    )
    accepted = accept_studio_invitation(
        database,
        invitation_token=invitation.invitation_token,
        display_name="  Alex   Owner  ",
        password=PASSWORD,
        identity_secret_value=IDENTITY_SECRET,
        now=now,
    )
    assert accepted.user.display_name == "Alex Owner"
    assert len(accepted.recovery_codes) == 8
    assert len(set(accepted.recovery_codes)) == 8

    signed_in = login_studio_identity(
        database,
        email="OWNER@example.com",
        password=PASSWORD,
        identity_secret_value=IDENTITY_SECRET,
        now=now + timedelta(minutes=1),
    )
    assert signed_in.session is not None
    session = signed_in.session
    assert session.principal.workspace_id == "workspace-a"
    assert session.principal.role == "admin"
    assert (
        principal_for_studio_identity_session(
            database,
            session_token=session.session_token,
            identity_secret_value=IDENTITY_SECRET,
            now=now + timedelta(minutes=2),
        )
        == session.principal
    )

    with sqlite3.connect(database) as connection:
        user_row = connection.execute("SELECT email, password_hash FROM studio_users").fetchone()
        invitation_hash = connection.execute(
            "SELECT token_hash FROM studio_invitations"
        ).fetchone()[0]
        session_hash = connection.execute(
            "SELECT session_hash FROM studio_identity_sessions"
        ).fetchone()[0]
        recovery_hashes = {
            row[0] for row in connection.execute("SELECT code_hash FROM studio_recovery_codes")
        }
    assert user_row is not None
    assert user_row[0] == "owner@example.com"
    assert str(user_row[1]).startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert PASSWORD not in str(user_row)
    assert invitation.invitation_token not in str(invitation_hash)
    assert session.session_token not in str(session_hash)
    assert all(code not in recovery_hashes for code in accepted.recovery_codes)

    recovered = recover_studio_identity(
        database,
        email="owner@example.com",
        recovery_code=accepted.recovery_codes[0],
        new_password="a newly replaced password",
        identity_secret_value=IDENTITY_SECRET,
        now=now + timedelta(minutes=3),
    )
    assert recovered.accepted is True
    assert len(recovered.recovery_codes) == 8
    assert (
        principal_for_studio_identity_session(
            database,
            session_token=session.session_token,
            identity_secret_value=IDENTITY_SECRET,
            now=now + timedelta(minutes=4),
        )
        is None
    )
    assert (
        recover_studio_identity(
            database,
            email="owner@example.com",
            recovery_code=accepted.recovery_codes[0],
            new_password="another replacement password",
            identity_secret_value=IDENTITY_SECRET,
            now=now + timedelta(minutes=5),
        ).accepted
        is False
    )
    assert (
        login_studio_identity(
            database,
            email="owner@example.com",
            password="a newly replaced password",
            identity_secret_value=IDENTITY_SECRET,
            now=now + timedelta(minutes=6),
        ).session
        is not None
    )


def test_inactive_invitation_history_is_bounded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "studio.db"
    monkeypatch.setattr(identity, "MAX_ACTIVE_INVITATIONS_PER_WORKSPACE", 2)
    monkeypatch.setattr(identity, "MAX_STORED_INVITATIONS_PER_WORKSPACE", 3)
    for index in range(5):
        invitation = _invite(database, email=f"person-{index}@example.com")
        identity.revoke_studio_invitation(
            database,
            workspace_id="workspace-a",
            invitation_id=invitation.record.invitation_id,
        )
    pending = _invite(database, email="waiting@example.com")

    records = identity.list_studio_invitations(database, workspace_id="workspace-a")
    assert len(records) <= 3
    assert pending.record.invitation_id in {record.invitation_id for record in records}


def test_multi_workspace_login_and_membership_changes_apply_to_live_sessions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "studio.db"
    accepted = _enroll(database)
    second = _invite(database, workspace="workspace-b", role="viewer")
    existing_acceptance = accept_studio_invitation(
        database,
        invitation_token=second.invitation_token,
        display_name="Ignored Existing Name",
        password=PASSWORD,
        identity_secret_value=IDENTITY_SECRET,
    )
    assert existing_acceptance.user.user_id == accepted.user.user_id
    assert existing_acceptance.recovery_codes == ()

    choice = login_studio_identity(
        database,
        email=accepted.user.email,
        password=PASSWORD,
        identity_secret_value=IDENTITY_SECRET,
    )
    assert choice.session is None
    assert [item.workspace_id for item in choice.workspaces] == ["workspace-a", "workspace-b"]
    selected = login_studio_identity(
        database,
        email=accepted.user.email,
        password=PASSWORD,
        workspace_id="workspace-b",
        identity_secret_value=IDENTITY_SECRET,
    )
    assert selected.session is not None
    assert selected.session.principal.role == "viewer"

    updated = update_studio_membership_role(
        database,
        workspace_id="workspace-b",
        user_id=accepted.user.user_id,
        role="editor",
    )
    assert updated.role == "editor"
    live = principal_for_studio_identity_session(
        database,
        session_token=selected.session.session_token,
        identity_secret_value=IDENTITY_SECRET,
    )
    assert live is not None
    assert live.role == "editor"

    removed = remove_studio_membership(
        database,
        workspace_id="workspace-b",
        user_id=accepted.user.user_id,
    )
    assert removed.role == "editor"
    assert (
        principal_for_studio_identity_session(
            database,
            session_token=selected.session.session_token,
            identity_secret_value=IDENTITY_SECRET,
        )
        is None
    )
    assert [
        item.workspace_id for item in list_studio_memberships(database, workspace_id="workspace-a")
    ] == ["workspace-a"]

    try:
        update_studio_membership_role(
            database,
            workspace_id="workspace-a",
            user_id=accepted.user.user_id,
            role="viewer",
        )
    except StudioIdentityConflict as exc:
        assert "at least one account administrator" in str(exc)
    else:
        raise AssertionError("the last human administrator was demoted")


def test_api_admin_invites_person_and_person_signs_in_with_httponly_cookie(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    database = tmp_path / "studio.db"
    pepper = "identity-api-pepper-with-at-least-thirty-two-characters"
    owner_key = create_studio_api_key(
        database,
        workspace_id="workspace-a",
        role="admin",
        label="Bootstrap owner",
        expires_in_days=90,
        pepper=pepper,
    )
    registry = _install_runtime(database, pepper, monkeypatch)
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    client = TestClient(studio_api.app)
    key_headers = {"Authorization": f"Bearer {owner_key.api_key}"}

    health = client.get("/api/health")
    created = client.post(
        studio_api.TEAM_INVITATIONS_API_PATH,
        headers=key_headers,
        json={"email": "owner@example.com", "role": "admin", "expires_in_days": 7},
    )
    assert health.json()["auth"]["human_accounts"] is True
    assert "Argon2id human accounts" in " ".join(health.json()["readiness"]["completed"])
    assert created.status_code == 201
    invitation_token = created.json()["invitation_token"]
    assert created.text.count(invitation_token) == 1

    listed = client.get(studio_api.TEAM_INVITATIONS_API_PATH, headers=key_headers)
    assert invitation_token not in listed.text
    preview = client.post(
        studio_api.IDENTITY_INVITATION_PREVIEW_PATH,
        json={"invitation_token": invitation_token},
    )
    assert preview.status_code == 200
    assert preview.json()["invitation"]["workspace_id"] == "workspace-a"
    assert invitation_token not in "\n".join(record.message for record in caplog.records)

    missing_origin = client.post(
        studio_api.IDENTITY_INVITATION_ACCEPT_PATH,
        json={
            "invitation_token": invitation_token,
            "display_name": "Alex Owner",
            "password": PASSWORD,
        },
    )
    accepted = client.post(
        studio_api.IDENTITY_INVITATION_ACCEPT_PATH,
        headers=CSRF_HEADERS,
        json={
            "invitation_token": invitation_token,
            "display_name": "Alex Owner",
            "password": PASSWORD,
        },
    )
    assert missing_origin.status_code == 403
    assert accepted.status_code == 201
    assert accepted.json()["sign_in_required"] is True
    assert len(accepted.json()["recovery_codes"]) == 8
    recovery_code = accepted.json()["recovery_codes"][0]
    assert studio_api.BROWSER_SESSION_COOKIE_NAME not in accepted.headers.get("set-cookie", "")

    invalid = client.post(
        studio_api.IDENTITY_LOGIN_PATH,
        headers=CSRF_HEADERS,
        json={"email": "owner@example.com", "password": "not the right password"},
    )
    signed_in = client.post(
        studio_api.IDENTITY_LOGIN_PATH,
        headers=CSRF_HEADERS,
        json={"email": "owner@example.com", "password": PASSWORD},
    )
    assert invalid.status_code == 401
    assert signed_in.status_code == 200
    assert signed_in.json()["access_mode"] == "identity_session"
    assert signed_in.json()["user"]["email"] == "owner@example.com"
    assert "HttpOnly" in signed_in.headers["set-cookie"]
    assert PASSWORD not in signed_in.text

    members = client.get(studio_api.TEAM_MEMBERS_API_PATH)
    assert members.status_code == 200
    assert members.json()["members"][0]["display_name"] == "Alex Owner"
    assert members.json()["current_user_id"] == members.json()["members"][0]["user_id"]

    recovery_without_origin = client.post(
        studio_api.IDENTITY_RECOVERY_PATH,
        json={
            "email": "owner@example.com",
            "recovery_code": recovery_code,
            "new_password": "a newly replaced password",
        },
    )
    recovered = client.post(
        studio_api.IDENTITY_RECOVERY_PATH,
        headers=CSRF_HEADERS,
        json={
            "email": "owner@example.com",
            "recovery_code": recovery_code,
            "new_password": "a newly replaced password",
        },
    )
    assert recovery_without_origin.status_code == 403
    assert recovered.status_code == 200
    assert recovered.json()["accepted"] is True
    assert recovered.json()["sign_in_required"] is True
    assert len(recovered.json()["recovery_codes"]) == 8
    assert client.get("/api/session").status_code == 401
    signed_in_again = client.post(
        studio_api.IDENTITY_LOGIN_PATH,
        headers=CSRF_HEADERS,
        json={"email": "owner@example.com", "password": "a newly replaced password"},
    )
    assert signed_in_again.status_code == 200

    logout = client.post(studio_api.BROWSER_SESSION_LOGOUT_PATH, headers=CSRF_HEADERS)
    assert logout.status_code == 204
    assert client.get("/api/session").status_code == 401
    registry.close()


def test_api_multi_workspace_choice_reveals_memberships_only_after_valid_password(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "studio.db"
    _enroll(database)
    second = _invite(database, workspace="workspace-b", role="viewer")
    accept_studio_invitation(
        database,
        invitation_token=second.invitation_token,
        display_name="Alex Owner",
        password=PASSWORD,
        identity_secret_value=IDENTITY_SECRET,
    )
    pepper = "multi-workspace-pepper-with-at-least-thirty-two-characters"
    registry = _install_runtime(database, pepper, monkeypatch)
    client = TestClient(studio_api.app)

    invalid = client.post(
        studio_api.IDENTITY_LOGIN_PATH,
        headers=CSRF_HEADERS,
        json={"email": "owner@example.com", "password": "wrong password value"},
    )
    choice = client.post(
        studio_api.IDENTITY_LOGIN_PATH,
        headers=CSRF_HEADERS,
        json={"email": "owner@example.com", "password": PASSWORD},
    )
    assert invalid.status_code == 401
    assert "workspace-a" not in invalid.text
    missing_account = client.post(
        studio_api.IDENTITY_LOGIN_PATH,
        headers=CSRF_HEADERS,
        json={"email": "missing@example.com", "password": "wrong password value"},
    )
    assert missing_account.status_code == 401
    assert missing_account.json() == invalid.json()
    invalid_workspace = client.post(
        studio_api.IDENTITY_LOGIN_PATH,
        headers=CSRF_HEADERS,
        json={
            "email": "owner@example.com",
            "password": PASSWORD,
            "workspace_id": "not a workspace!",
        },
    )
    assert invalid_workspace.status_code == 401
    assert invalid_workspace.json() == invalid.json()
    assert choice.status_code == 409
    assert choice.json()["workspaces"] == [
        {"workspace_id": "workspace-a", "role": "admin"},
        {"workspace_id": "workspace-b", "role": "viewer"},
    ]
    registry.close()
