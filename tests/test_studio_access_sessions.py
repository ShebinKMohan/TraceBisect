"""Tests for short-lived, revocable Studio browser sessions."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import tracebisect.studio.api as studio_api
from tracebisect.studio.access_keys import (
    create_studio_api_key,
    revoke_studio_api_key,
)
from tracebisect.studio.access_sessions import (
    MAX_ACTIVE_BROWSER_SESSIONS_PER_KEY,
    issue_studio_browser_session,
    principal_for_studio_browser_session,
    revoke_studio_browser_session,
)
from tracebisect.studio.auth import StudioAuthConfig
from tracebisect.studio.storage import StudioStoreRegistry


def _managed_access(
    database_path: Path,
    *,
    pepper: str,
    now: datetime | None = None,
):
    return create_studio_api_key(
        database_path,
        workspace_id="workspace-a",
        role="editor",
        label="Browser access",
        expires_in_days=90,
        pepper=pepper,
        now=now,
    )


def _managed_env(database_path: Path, pepper: str) -> dict[str, str]:
    return {
        "TRACEBISECT_STUDIO_STORAGE": "sqlite",
        "TRACEBISECT_STUDIO_SQLITE_PATH": str(database_path),
        "TRACEBISECT_STUDIO_WORKSPACE_ID": "health-probe",
        "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
        "TRACEBISECT_STUDIO_API_KEY_PEPPER": pepper,
        "TRACEBISECT_STUDIO_BROWSER_SESSION_TTL_SECONDS": "3600",
    }


def test_browser_session_is_hashed_bounded_and_individually_revocable(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "studio.db"
    pepper = "browser-session-pepper-with-at-least-32-characters"
    now = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)
    key = _managed_access(database_path, pepper=pepper, now=now)

    issued = issue_studio_browser_session(
        database_path,
        api_key=key.api_key,
        pepper=pepper,
        ttl_seconds=3600,
        now=now,
    )

    assert issued.principal.workspace_id == "workspace-a"
    assert issued.principal.role == "editor"
    assert issued.principal.expires_at == "2026-07-21T09:00:00Z"
    assert (
        principal_for_studio_browser_session(
            database_path,
            session_token=issued.session_token,
            pepper=pepper,
            now=now + timedelta(minutes=30),
        )
        == issued.principal
    )
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT key_id, session_hash FROM studio_browser_sessions"
        ).fetchone()
    assert row is not None
    assert row[0] == key.record.key_id
    assert len(str(row[1])) == 64
    assert issued.session_token not in str(row)
    assert key.api_key not in str(row)

    assert revoke_studio_browser_session(
        database_path,
        session_token=issued.session_token,
        pepper=pepper,
        now=now + timedelta(minutes=31),
    )
    assert (
        principal_for_studio_browser_session(
            database_path,
            session_token=issued.session_token,
            pepper=pepper,
            now=now + timedelta(minutes=32),
        )
        is None
    )


def test_key_expiry_and_revocation_immediately_invalidate_browser_sessions(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "studio.db"
    pepper = "session-parent-key-pepper-with-at-least-32-characters"
    now = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)
    key = create_studio_api_key(
        database_path,
        workspace_id="workspace-a",
        role="viewer",
        label="Short key",
        expires_in_days=1,
        pepper=pepper,
        now=now,
    )
    issued = issue_studio_browser_session(
        database_path,
        api_key=key.api_key,
        pepper=pepper,
        ttl_seconds=7 * 24 * 60 * 60,
        now=now,
    )

    assert issued.principal.expires_at == key.record.expires_at
    revoke_studio_api_key(
        database_path,
        key_id=key.record.key_id,
        now=now + timedelta(minutes=5),
    )
    assert (
        principal_for_studio_browser_session(
            database_path,
            session_token=issued.session_token,
            pepper=pepper,
            now=now + timedelta(minutes=6),
        )
        is None
    )


def test_browser_sessions_are_bounded_per_source_key(tmp_path: Path) -> None:
    database_path = tmp_path / "studio.db"
    pepper = "bounded-session-pepper-with-at-least-32-characters"
    now = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)
    key = _managed_access(database_path, pepper=pepper, now=now)

    sessions = [
        issue_studio_browser_session(
            database_path,
            api_key=key.api_key,
            pepper=pepper,
            ttl_seconds=3600,
            now=now + timedelta(seconds=index),
        )
        for index in range(MAX_ACTIVE_BROWSER_SESSIONS_PER_KEY + 1)
    ]

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM studio_browser_sessions WHERE key_id = ?",
            (key.record.key_id,),
        ).fetchone() == (MAX_ACTIVE_BROWSER_SESSIONS_PER_KEY,)
    assert (
        principal_for_studio_browser_session(
            database_path,
            session_token=sessions[0].session_token,
            pepper=pepper,
            now=now + timedelta(minutes=1),
        )
        is None
    )
    assert (
        principal_for_studio_browser_session(
            database_path,
            session_token=sessions[-1].session_token,
            pepper=pepper,
            now=now + timedelta(minutes=1),
        )
        is not None
    )


def test_managed_api_exchanges_key_for_httponly_session_and_logs_out(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "studio.db"
    pepper = "live-browser-session-pepper-with-at-least-32-characters"
    key = _managed_access(database_path, pepper=pepper)
    env = _managed_env(database_path, pepper)
    registry = StudioStoreRegistry(env)
    monkeypatch.setattr(studio_api, "AUTH_CONFIG", StudioAuthConfig.from_env(env))
    monkeypatch.setattr(studio_api, "STORE_REGISTRY", registry)
    monkeypatch.setattr(studio_api, "STORE", registry.default_store)
    monkeypatch.setattr(studio_api, "BROWSER_SESSION_COOKIE_SECURE", False)
    asyncio.run(studio_api.RATE_LIMITER.reset())
    client = TestClient(studio_api.app)
    origin = "http://127.0.0.1:3000"

    exchange = client.post(
        studio_api.BROWSER_SESSION_EXCHANGE_PATH,
        headers={"Authorization": f"Bearer {key.api_key}", "Origin": origin},
    )

    assert exchange.status_code == 200
    assert exchange.json()["workspace_id"] == "workspace-a"
    assert exchange.json()["role"] == "editor"
    assert exchange.json()["access_mode"] == "browser_session"
    assert exchange.json()["expires_at"] is not None
    assert "session_token" not in exchange.json()
    set_cookie = exchange.headers["set-cookie"]
    assert studio_api.BROWSER_SESSION_COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Max-Age=" in set_cookie
    assert key.api_key not in set_cookie
    assert exchange.headers["access-control-allow-credentials"] == "true"

    cookie_session = client.get("/api/session")
    rejected_origin = client.post(
        "/api/compare",
        headers={"Origin": "https://evil.example"},
        json={},
    )
    missing_csrf_header = client.post(
        "/api/compare",
        headers={"Origin": origin},
        json={},
    )
    accepted_origin = client.post(
        "/api/compare",
        headers={
            "Origin": origin,
            studio_api.BROWSER_CSRF_HEADER: studio_api.BROWSER_CSRF_VALUE,
        },
        json={},
    )

    assert cookie_session.status_code == 200
    assert cookie_session.json()["access_mode"] == "browser_session"
    assert rejected_origin.status_code == 403
    assert "origin protection" in rejected_origin.json()["detail"]
    assert missing_csrf_header.status_code == 403
    assert accepted_origin.status_code == 422

    logout = client.post(
        studio_api.BROWSER_SESSION_LOGOUT_PATH,
        headers={
            "Origin": origin,
            studio_api.BROWSER_CSRF_HEADER: studio_api.BROWSER_CSRF_VALUE,
        },
    )
    after_logout = client.get("/api/session")

    assert logout.status_code == 204
    assert after_logout.status_code == 401

    viewer_key = create_studio_api_key(
        database_path,
        workspace_id="workspace-a",
        role="viewer",
        label="Browser reviewer",
        expires_in_days=90,
        pepper=pepper,
    )
    viewer_exchange = client.post(
        studio_api.BROWSER_SESSION_EXCHANGE_PATH,
        headers={"Authorization": f"Bearer {viewer_key.api_key}", "Origin": origin},
    )
    viewer_mutation = client.post(
        "/api/compare",
        headers={
            "Origin": origin,
            studio_api.BROWSER_CSRF_HEADER: studio_api.BROWSER_CSRF_VALUE,
        },
        json={},
    )
    assert viewer_exchange.status_code == 200
    assert viewer_exchange.json()["role"] == "viewer"
    assert viewer_mutation.status_code == 403
    assert "viewer access" in viewer_mutation.json()["detail"]
    registry.close()


def test_revoking_source_key_invalidates_live_cookie_without_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "studio.db"
    pepper = "revoked-session-pepper-with-at-least-32-characters"
    key = _managed_access(database_path, pepper=pepper)
    env = _managed_env(database_path, pepper)
    registry = StudioStoreRegistry(env)
    monkeypatch.setattr(studio_api, "AUTH_CONFIG", StudioAuthConfig.from_env(env))
    monkeypatch.setattr(studio_api, "STORE_REGISTRY", registry)
    monkeypatch.setattr(studio_api, "STORE", registry.default_store)
    monkeypatch.setattr(studio_api, "BROWSER_SESSION_COOKIE_SECURE", False)
    asyncio.run(studio_api.RATE_LIMITER.reset())
    client = TestClient(studio_api.app)
    origin = "http://127.0.0.1:3000"
    exchange = client.post(
        studio_api.BROWSER_SESSION_EXCHANGE_PATH,
        headers={"Authorization": f"Bearer {key.api_key}", "Origin": origin},
    )
    assert exchange.status_code == 200

    revoke_studio_api_key(database_path, key_id=key.record.key_id)
    rejected = client.get("/api/session")

    assert rejected.status_code == 401
    assert rejected.headers["set-cookie"].startswith(f'{studio_api.BROWSER_SESSION_COOKIE_NAME}=""')
    registry.close()
