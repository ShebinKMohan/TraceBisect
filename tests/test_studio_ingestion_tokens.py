"""Tests for upload-only Studio credentials used by agents and CI."""

from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import tracebisect.studio.api as studio_api
import tracebisect.studio.ingestion_tokens as ingestion_tokens
from tracebisect.cli import main
from tracebisect.studio.auth import StudioAuthConfig
from tracebisect.studio.ingestion_tokens import (
    INGESTION_TOKEN_SCOPE,
    StudioIngestionTokenError,
    create_studio_ingestion_token,
    list_studio_ingestion_tokens,
    list_workspace_studio_ingestion_tokens,
    principal_for_managed_ingestion_token,
    revoke_studio_ingestion_token,
    revoke_workspace_studio_ingestion_token,
)
from tracebisect.studio.storage import StudioStoreRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / "tests" / "fixtures" / "refund_search_baseline.tbtrace"
PEPPER = "ingestion-token-test-pepper-with-at-least-32-characters"
NOW = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)


def _managed_env(database_path: Path) -> dict[str, str]:
    return {
        "TRACEBISECT_STUDIO_STORAGE": "sqlite",
        "TRACEBISECT_STUDIO_SQLITE_PATH": str(database_path),
        "TRACEBISECT_STUDIO_WORKSPACE_ID": "health-probe",
        "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
        "TRACEBISECT_STUDIO_API_KEY_PEPPER": PEPPER,
    }


def test_ingestion_token_is_hashed_scoped_expiring_and_revocable(tmp_path: Path) -> None:
    database = tmp_path / "studio.db"
    issued = create_studio_ingestion_token(
        database,
        workspace_id="team-a",
        label="Production support agent",
        expires_in_days=1,
        pepper=PEPPER,
        now=NOW,
    )

    assert issued.token.startswith(f"tbit_{issued.record.token_id}_")
    assert issued.record.scope == INGESTION_TOKEN_SCOPE
    assert issued.record.status == "active"
    assert issued.token not in repr(issued)
    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT token_hash, scope FROM studio_ingestion_tokens WHERE token_id = ?",
            (issued.record.token_id,),
        ).fetchone()
    assert stored is not None
    assert stored[0] != issued.token
    assert len(str(stored[0])) == 64
    assert stored[1] == INGESTION_TOKEN_SCOPE

    principal = principal_for_managed_ingestion_token(
        database,
        token=issued.token,
        pepper=PEPPER,
        now=NOW,
    )
    assert principal is not None
    assert principal.workspace_id == "team-a"
    assert principal.scope == INGESTION_TOKEN_SCOPE
    assert (
        principal_for_managed_ingestion_token(
            database,
            token=issued.token,
            pepper=PEPPER,
            now=NOW + timedelta(days=2),
        )
        is None
    )

    revoked = revoke_studio_ingestion_token(
        database,
        token_id=issued.record.token_id,
        now=NOW,
    )
    assert revoked.status == "revoked"
    assert (
        principal_for_managed_ingestion_token(
            database,
            token=issued.token,
            pepper=PEPPER,
            now=NOW,
        )
        is None
    )


def test_ingestion_token_metadata_and_revocation_are_workspace_isolated(
    tmp_path: Path,
) -> None:
    database = tmp_path / "studio.db"
    team_a = create_studio_ingestion_token(
        database,
        workspace_id="team-a",
        label="Team A agent",
        expires_in_days=30,
        pepper=PEPPER,
        now=NOW,
    )
    team_b = create_studio_ingestion_token(
        database,
        workspace_id="team-b",
        label="Team B agent",
        expires_in_days=30,
        pepper=PEPPER,
        now=NOW,
    )

    assert [
        item.token_id
        for item in list_workspace_studio_ingestion_tokens(
            database,
            workspace_id="team-a",
            now=NOW,
        )
    ] == [team_a.record.token_id]
    assert {item.token_id for item in list_studio_ingestion_tokens(database, now=NOW)} == {
        team_a.record.token_id,
        team_b.record.token_id,
    }
    with pytest.raises(StudioIngestionTokenError, match="no ingestion token"):
        revoke_workspace_studio_ingestion_token(
            database,
            workspace_id="team-a",
            token_id=team_b.record.token_id,
            now=NOW,
        )
    assert (
        principal_for_managed_ingestion_token(
            database,
            token=team_b.token,
            pepper=PEPPER,
            now=NOW,
        )
        is not None
    )


def test_ingestion_token_capacity_is_bounded_and_revoked_tokens_free_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "studio.db"
    monkeypatch.setattr(ingestion_tokens, "MAX_ACTIVE_INGESTION_TOKENS_PER_WORKSPACE", 1)
    first = create_studio_ingestion_token(
        database,
        workspace_id="team-a",
        label="First agent",
        expires_in_days=30,
        pepper=PEPPER,
        now=NOW,
    )

    with pytest.raises(StudioIngestionTokenError, match="maximum number"):
        create_studio_ingestion_token(
            database,
            workspace_id="team-a",
            label="Second agent",
            expires_in_days=30,
            pepper=PEPPER,
            now=NOW,
        )

    revoke_studio_ingestion_token(
        database,
        token_id=first.record.token_id,
        now=NOW,
    )
    replacement = create_studio_ingestion_token(
        database,
        workspace_id="team-a",
        label="Replacement agent",
        expires_in_days=30,
        pepper=PEPPER,
        now=NOW,
    )
    assert replacement.record.status == "active"


def test_ingestion_token_can_only_upload_to_its_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "studio.db"
    issued = create_studio_ingestion_token(
        database,
        workspace_id="workspace-a",
        label="Production agent",
        expires_in_days=30,
        pepper=PEPPER,
    )
    env = _managed_env(database)
    registry = StudioStoreRegistry(env)
    auth = StudioAuthConfig.from_env(env)
    monkeypatch.setattr(studio_api, "AUTH_CONFIG", auth)
    monkeypatch.setattr(studio_api, "STORE_REGISTRY", registry)
    monkeypatch.setattr(studio_api, "STORE", registry.default_store)
    asyncio.run(studio_api.RATE_LIMITER.reset())
    client = TestClient(studio_api.app)
    headers = {"Authorization": f"Bearer {issued.token}"}

    principal = auth.principal_for_authorization(headers["Authorization"])
    assert principal is not None
    assert principal.role == "ingest"
    assert principal.auth_kind == "ingestion_token"
    assert auth.issue_browser_session(headers["Authorization"]) is None
    health = client.get("/api/health")
    with BASELINE.open("rb") as handle:
        uploaded = client.post(
            "/api/traces/upload",
            headers=headers,
            files={"file": ("baseline.tbtrace", handle, "application/octet-stream")},
        )
    with BASELINE.open("rb") as handle:
        duplicate = client.post(
            "/api/traces/upload",
            headers=headers,
            files={"file": ("baseline.tbtrace", handle, "application/octet-stream")},
        )
    denied_reads = [
        client.get("/api/session", headers=headers),
        client.get("/api/traces", headers=headers),
        client.get("/api/runs", headers=headers),
    ]
    denied_writes = [
        client.post("/api/browser-session", headers=headers),
        client.post("/api/compare", headers=headers, json={}),
        client.post("/api/regression-cases", headers=headers, json={}),
    ]

    assert uploaded.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json() == {
        "detail": (
            "A trace with this ID already exists. Give every uploaded run a unique trace ID."
        )
    }
    assert health.json()["auth"]["ingestion_tokens"] is True
    assert (
        "workspace-scoped upload-only ingestion tokens for agents and CI"
        in health.json()["readiness"]["completed"]
    )
    assert uploaded.json()["trace"]["id"]
    assert registry.get("workspace-a").runtime_status()["trace_count"] == 1
    assert registry.get("health-probe").runtime_status()["trace_count"] == 0
    for response in [*denied_reads, *denied_writes]:
        assert response.status_code == 403
        assert response.json() == {
            "detail": (
                "This ingestion token can only upload traces. It cannot open or read Studio."
            )
        }
    registry.close()


def test_ingestion_token_cli_explains_the_upload_only_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "studio.db"
    monkeypatch.setenv("TRACEBISECT_STUDIO_API_KEY_PEPPER", PEPPER)

    assert (
        main(
            [
                "studio",
                "ingest-tokens",
                "create",
                "--database",
                str(database),
                "--workspace",
                "team-a",
                "--name",
                "Production support agent",
            ]
        )
        == 0
    )
    create_output = capsys.readouterr().out
    token_match = re.search(r"tbit_[A-Za-z0-9_-]{12}_[A-Za-z0-9_-]{43}", create_output)
    token_id_match = re.search(r"Token ID: ([A-Za-z0-9_-]{12})", create_output)
    assert token_match is not None
    assert token_id_match is not None
    plaintext = token_match.group(0)
    token_id = token_id_match.group(1)
    assert create_output.count(plaintext) == 1
    assert "upload only" in create_output
    assert "cannot open Studio" in create_output

    assert main(["studio", "ingest-tokens", "list", "--database", str(database)]) == 0
    list_output = capsys.readouterr().out
    assert token_id in list_output
    assert "upload only" in list_output
    assert plaintext not in list_output

    assert (
        main(
            [
                "studio",
                "ingest-tokens",
                "revoke",
                "--database",
                str(database),
                "--token-id",
                token_id,
            ]
        )
        == 0
    )
    revoke_output = capsys.readouterr().out
    assert "Trace-upload token revoked" in revoke_output
    assert plaintext not in revoke_output
