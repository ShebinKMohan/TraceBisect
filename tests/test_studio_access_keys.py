"""Tests for hashed, expiring, and revocable Studio workspace keys."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tracebisect.cli import main
from tracebisect.studio.access_keys import (
    StudioApiKeyError,
    create_studio_api_key,
    list_studio_api_keys,
    revoke_studio_api_key,
    workspace_for_managed_api_key,
)

PEPPER = "test-managed-key-pepper-with-more-than-32-characters"
NOW = datetime(2026, 7, 21, 5, 0, tzinfo=timezone.utc)


def test_key_is_shown_once_but_only_a_hash_is_persisted(tmp_path: Path) -> None:
    database = tmp_path / "new" / "studio.db"

    issued = create_studio_api_key(
        database,
        workspace_id="team-a",
        label="CI upload",
        expires_in_days=90,
        pepper=PEPPER,
        now=NOW,
    )

    assert database.exists()
    assert database.stat().st_mode & 0o777 == 0o600
    assert re.fullmatch(r"tbsk_[A-Za-z0-9_-]{12}_[A-Za-z0-9_-]{43}", issued.api_key)
    assert issued.record.status == "active"
    assert issued.record.expires_at == "2026-10-19T05:00:00Z"
    assert issued.api_key not in repr(issued)

    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT key_hash FROM studio_api_keys WHERE key_id = ?",
            (issued.record.key_id,),
        ).fetchone()
        assert stored is not None
        assert len(str(stored[0])) == 64
        database_text = "\n".join(connection.iterdump())

    assert issued.api_key not in database_text
    assert PEPPER not in database_text
    assert (
        workspace_for_managed_api_key(
            database,
            api_key=issued.api_key,
            pepper=PEPPER,
            now=NOW,
        )
        == "team-a"
    )
    assert (
        workspace_for_managed_api_key(
            database,
            api_key=issued.api_key,
            pepper="different-pepper-with-more-than-32-characters",
            now=NOW,
        )
        is None
    )


def test_expired_and_revoked_keys_fail_closed_immediately(tmp_path: Path) -> None:
    database = tmp_path / "studio.db"
    issued = create_studio_api_key(
        database,
        workspace_id="team-a",
        label="Temporary browser",
        expires_in_days=1,
        pepper=PEPPER,
        now=NOW,
    )

    assert (
        workspace_for_managed_api_key(
            database,
            api_key=issued.api_key,
            pepper=PEPPER,
            now=NOW + timedelta(days=1),
        )
        is None
    )
    assert list_studio_api_keys(database, now=NOW + timedelta(days=1))[0].status == "expired"

    revoked = revoke_studio_api_key(database, key_id=issued.record.key_id, now=NOW)

    assert revoked.status == "revoked"
    assert revoked.revoked_at == "2026-07-21T05:00:00Z"
    assert (
        workspace_for_managed_api_key(
            database,
            api_key=issued.api_key,
            pepper=PEPPER,
            now=NOW,
        )
        is None
    )
    assert revoke_studio_api_key(database, key_id=issued.record.key_id).status == "revoked"


def test_corrupt_key_metadata_fails_closed_with_operator_safe_error(tmp_path: Path) -> None:
    database = tmp_path / "studio.db"
    issued = create_studio_api_key(
        database,
        workspace_id="team-a",
        label="Browser",
        expires_in_days=90,
        pepper=PEPPER,
        now=NOW,
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE studio_api_keys SET expires_at = 'not-a-date' WHERE key_id = ?",
            (issued.record.key_id,),
        )

    assert (
        workspace_for_managed_api_key(
            database,
            api_key=issued.api_key,
            pepper=PEPPER,
            now=NOW,
        )
        is None
    )
    with pytest.raises(StudioApiKeyError, match="could not list"):
        list_studio_api_keys(database, now=NOW)


@pytest.mark.parametrize(
    ("label", "days", "message"),
    [
        ("", 90, "key name"),
        ("valid", 0, "key lifetime"),
        ("valid", 3651, "key lifetime"),
    ],
)
def test_key_creation_rejects_unsafe_metadata(
    tmp_path: Path,
    label: str,
    days: int,
    message: str,
) -> None:
    database = tmp_path / "studio.db"
    with pytest.raises(StudioApiKeyError, match=message):
        create_studio_api_key(
            database,
            workspace_id="team-a",
            label=label,
            expires_in_days=days,
            pepper=PEPPER,
            now=NOW,
        )
    assert not database.exists()


def test_key_cli_guides_create_list_and_revoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "studio.db"
    monkeypatch.setenv("TRACEBISECT_STUDIO_API_KEY_PEPPER", PEPPER)

    assert main(["studio", "keys", "generate-pepper"]) == 0
    pepper_output = capsys.readouterr().out
    assert "Do not commit it" in pepper_output
    assert re.search(
        r"TRACEBISECT_STUDIO_API_KEY_PEPPER=[A-Za-z0-9_-]{64}",
        pepper_output,
    )

    assert main(["studio", "keys"]) == 0
    help_output = capsys.readouterr().out
    assert "generate-pepper" in help_output
    assert "create" in help_output
    assert "list" in help_output
    assert "revoke" in help_output

    assert (
        main(
            [
                "studio",
                "keys",
                "create",
                "--database",
                str(database),
                "--workspace",
                "team-a",
                "--name",
                "Browser access",
            ]
        )
        == 0
    )
    create_output = capsys.readouterr().out
    token_match = re.search(r"tbsk_[A-Za-z0-9_-]{12}_[A-Za-z0-9_-]{43}", create_output)
    key_id_match = re.search(r"Key ID: ([A-Za-z0-9_-]{12})", create_output)
    assert token_match is not None
    assert key_id_match is not None
    plaintext_key = token_match.group(0)
    key_id = key_id_match.group(1)
    assert create_output.count(plaintext_key) == 1
    assert "cannot show it again" in create_output

    assert main(["studio", "keys", "list", "--database", str(database)]) == 0
    list_output = capsys.readouterr().out
    assert key_id in list_output
    assert "active" in list_output
    assert "team-a" in list_output
    assert plaintext_key not in list_output

    assert (
        main(
            [
                "studio",
                "keys",
                "revoke",
                "--database",
                str(database),
                "--key-id",
                key_id,
            ]
        )
        == 0
    )
    revoke_output = capsys.readouterr().out
    assert "Workspace access key revoked" in revoke_output
    assert plaintext_key not in revoke_output


def test_key_cli_requires_the_server_pepper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("TRACEBISECT_STUDIO_API_KEY_PEPPER", raising=False)

    exit_code = main(
        [
            "studio",
            "keys",
            "create",
            "--database",
            str(tmp_path / "studio.db"),
            "--workspace",
            "team-a",
            "--name",
            "Browser",
        ]
    )

    assert exit_code == 2
    error_output = capsys.readouterr().err
    assert "tracebisect studio keys create failed" in error_output
    assert "TRACEBISECT_STUDIO_API_KEY_PEPPER" in error_output
