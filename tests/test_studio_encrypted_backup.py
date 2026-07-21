"""Tests for authenticated portable Studio backups and their CLI workflow."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

import tracebisect.studio.encrypted_backup as encrypted_backup
from tracebisect.cli import build_parser, main
from tracebisect.studio.backup import StudioBackupError
from tracebisect.studio.encrypted_backup import (
    ENCRYPTED_BACKUP_ALGORITHM,
    ENCRYPTED_BACKUP_MAGIC,
    create_encrypted_studio_backup,
    create_studio_backup_encryption_key,
    inspect_encrypted_studio_backup,
    is_encrypted_studio_backup,
    restore_encrypted_studio_backup,
)
from tracebisect.studio.service import seed_demo_report
from tracebisect.studio.storage import SQLiteStudioStore


def _seed_database(path: Path) -> None:
    store = SQLiteStudioStore(path, workspace_id="workspace-a")
    seed_demo_report(store)
    store.close()


def test_encryption_key_file_is_owner_only_and_never_replaced(tmp_path: Path) -> None:
    key_path = tmp_path / "secrets" / "studio-backup.key"

    key = create_studio_backup_encryption_key(key_path)

    assert key.path == key_path.resolve()
    assert key.algorithm == ENCRYPTED_BACKUP_ALGORITHM
    assert len(key.key_id) == 16
    assert key_path.stat().st_mode & 0o777 == 0o600
    assert len(key_path.read_text(encoding="ascii").strip()) == 44

    original = key_path.read_bytes()
    with pytest.raises(StudioBackupError, match="already exists"):
        create_studio_backup_encryption_key(key_path)
    assert key_path.read_bytes() == original


def test_encrypted_backup_round_trip_preserves_verified_content(tmp_path: Path) -> None:
    source_path = tmp_path / "studio.db"
    key_path = tmp_path / "studio-backup.key"
    encrypted_path = tmp_path / "backups" / "studio.db.enc"
    restored_path = tmp_path / "restored" / "studio.db"
    _seed_database(source_path)
    key = create_studio_backup_encryption_key(key_path)

    created = create_encrypted_studio_backup(source_path, encrypted_path, key_path)
    inspected = inspect_encrypted_studio_backup(encrypted_path, key_path)
    restored = restore_encrypted_studio_backup(encrypted_path, restored_path, key_path)

    assert is_encrypted_studio_backup(encrypted_path) is True
    assert encrypted_path.read_bytes().startswith(ENCRYPTED_BACKUP_MAGIC)
    assert encrypted_path.stat().st_mode & 0o777 == 0o600
    assert created == inspected
    assert created.algorithm == ENCRYPTED_BACKUP_ALGORITHM
    assert created.key_id == key.key_id
    assert restored.key_id == key.key_id
    assert restored.encrypted_sha256 == created.encrypted_sha256
    assert restored.backup.content_sha256 == created.backup.content_sha256
    assert created.backup.workspace_count == 1
    assert created.backup.trace_count == 2
    assert created.backup.report_count == 1
    assert created.encrypted_size_bytes == encrypted_path.stat().st_size
    assert len(created.encrypted_sha256) == 64

    restored_store = SQLiteStudioStore(restored_path, workspace_id="workspace-a")
    try:
        assert len(restored_store.list_traces()) == 2
        assert len(restored_store.list_report_summaries()) == 1
    finally:
        restored_store.close()


def test_encrypted_backups_use_fresh_nonces_for_the_same_data_and_key(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "studio.db"
    key_path = tmp_path / "studio-backup.key"
    first_path = tmp_path / "first.db.enc"
    second_path = tmp_path / "second.db.enc"
    _seed_database(source_path)
    create_studio_backup_encryption_key(key_path)

    first = create_encrypted_studio_backup(source_path, first_path, key_path)
    second = create_encrypted_studio_backup(source_path, second_path, key_path)

    assert first.backup.content_sha256 == second.backup.content_sha256
    assert first.encrypted_sha256 != second.encrypted_sha256
    assert first_path.read_bytes() != second_path.read_bytes()


def test_encryption_streams_data_across_chunk_boundaries(tmp_path: Path) -> None:
    source_path = tmp_path / "large-plaintext.bin"
    encrypted_path = tmp_path / "large-backup.enc"
    decrypted_path = tmp_path / "large-plaintext-restored.bin"
    key = encrypted_backup.AESGCM.generate_key(bit_length=256)
    payload = b"a" * (encrypted_backup.ENCRYPTED_BACKUP_CHUNK_BYTES + 17)
    source_path.write_bytes(payload)

    encrypted_backup._encrypt_file(source_path, encrypted_path, key)
    encrypted_size, encrypted_sha256 = encrypted_backup._decrypt_file(
        encrypted_path,
        decrypted_path,
        key,
    )

    assert decrypted_path.read_bytes() == payload
    assert decrypted_path.stat().st_mode & 0o777 == 0o600
    assert encrypted_size == encrypted_path.stat().st_size
    assert encrypted_sha256 == hashlib.sha256(encrypted_path.read_bytes()).hexdigest()


def test_wrong_key_and_tampering_fail_before_restore_is_published(tmp_path: Path) -> None:
    source_path = tmp_path / "studio.db"
    key_path = tmp_path / "studio-backup.key"
    wrong_key_path = tmp_path / "wrong.key"
    encrypted_path = tmp_path / "studio.db.enc"
    wrong_key_restore = tmp_path / "wrong-key-restored.db"
    tampered_restore = tmp_path / "tampered-restored.db"
    _seed_database(source_path)
    create_studio_backup_encryption_key(key_path)
    create_studio_backup_encryption_key(wrong_key_path)
    create_encrypted_studio_backup(source_path, encrypted_path, key_path)

    with pytest.raises(StudioBackupError, match="failed authentication"):
        restore_encrypted_studio_backup(
            encrypted_path,
            wrong_key_restore,
            wrong_key_path,
        )
    assert not wrong_key_restore.exists()

    tampered = bytearray(encrypted_path.read_bytes())
    tampered[len(ENCRYPTED_BACKUP_MAGIC) + 20] ^= 1
    encrypted_path.write_bytes(tampered)
    with pytest.raises(StudioBackupError, match="failed authentication"):
        restore_encrypted_studio_backup(
            encrypted_path,
            tampered_restore,
            key_path,
        )
    assert not tampered_restore.exists()


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        ENCRYPTED_BACKUP_MAGIC,
        ENCRYPTED_BACKUP_MAGIC + b"\xff" + b"0" * 64,
        b"not-a-tracebisect-backup" + b"0" * 64,
    ],
)
def test_invalid_encrypted_formats_are_rejected(
    tmp_path: Path,
    payload: bytes,
) -> None:
    key_path = tmp_path / "studio-backup.key"
    encrypted_path = tmp_path / "invalid.db.enc"
    create_studio_backup_encryption_key(key_path)
    encrypted_path.write_bytes(payload)

    with pytest.raises(StudioBackupError):
        inspect_encrypted_studio_backup(encrypted_path, key_path)


def test_key_file_must_be_valid(tmp_path: Path) -> None:
    source_path = tmp_path / "studio.db"
    key_path = tmp_path / "studio-backup.key"
    output_path = tmp_path / "studio.db.enc"
    _seed_database(source_path)
    key_path.write_text("not-a-valid-key\n", encoding="ascii")
    key_path.chmod(0o600)

    with pytest.raises(StudioBackupError, match="key file is invalid"):
        create_encrypted_studio_backup(source_path, output_path, key_path)
    assert not output_path.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions are required")
def test_key_file_must_be_private_on_posix(tmp_path: Path) -> None:
    source_path = tmp_path / "studio.db"
    key_path = tmp_path / "studio-backup.key"
    output_path = tmp_path / "studio.db.enc"
    _seed_database(source_path)
    create_studio_backup_encryption_key(key_path)
    key_path.chmod(0o644)
    with pytest.raises(StudioBackupError, match="permissions are too open"):
        create_encrypted_studio_backup(source_path, output_path, key_path)
    assert not output_path.exists()


def test_encrypted_backup_never_replaces_an_existing_artifact(tmp_path: Path) -> None:
    source_path = tmp_path / "studio.db"
    key_path = tmp_path / "studio-backup.key"
    output_path = tmp_path / "studio.db.enc"
    _seed_database(source_path)
    create_studio_backup_encryption_key(key_path)
    output_path.write_bytes(b"keep-existing-backup")

    with pytest.raises(StudioBackupError, match="already exists"):
        create_encrypted_studio_backup(source_path, output_path, key_path)
    assert output_path.read_bytes() == b"keep-existing-backup"


def test_encrypted_backup_cli_guides_key_backup_verify_and_restore(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_path = tmp_path / "studio.db"
    key_path = tmp_path / "studio-backup.key"
    encrypted_path = tmp_path / "studio.db.enc"
    restored_path = tmp_path / "restored.db"
    _seed_database(source_path)

    assert main(["studio", "backup-key", "generate", "--output", str(key_path)]) == 0
    key_output = capsys.readouterr().out
    key_secret = key_path.read_text(encoding="ascii").strip()
    assert "Studio backup encryption key created" in key_output
    assert "Secret printed: no" in key_output
    assert "Store this key separately" in key_output
    assert key_secret not in key_output

    assert (
        main(
            [
                "studio",
                "backup",
                "--database",
                str(source_path),
                "--output",
                str(encrypted_path),
                "--encryption-key-file",
                str(key_path),
            ]
        )
        == 0
    )
    backup_output = capsys.readouterr().out
    assert "Encrypted Studio backup created" in backup_output
    assert "Authentication: passed" in backup_output
    assert "Encrypted SHA-256:" in backup_output
    assert "Keep the key in a separate" in backup_output
    assert key_secret not in backup_output

    assert main(["studio", "verify", "--backup", str(encrypted_path)]) == 2
    missing_key_error = capsys.readouterr().err
    assert "provide --encryption-key-file" in missing_key_error

    assert (
        main(
            [
                "studio",
                "verify",
                "--backup",
                str(encrypted_path),
                "--encryption-key-file",
                str(key_path),
            ]
        )
        == 0
    )
    verify_output = capsys.readouterr().out
    assert "Encrypted Studio backup is healthy" in verify_output
    assert "Authentication: passed" in verify_output
    assert key_secret not in verify_output

    assert (
        main(
            [
                "studio",
                "restore",
                "--backup",
                str(encrypted_path),
                "--database",
                str(restored_path),
            ]
        )
        == 2
    )
    missing_restore_key_error = capsys.readouterr().err
    assert "provide --encryption-key-file" in missing_restore_key_error
    assert not restored_path.exists()

    assert (
        main(
            [
                "studio",
                "restore",
                "--backup",
                str(encrypted_path),
                "--database",
                str(restored_path),
                "--encryption-key-file",
                str(key_path),
            ]
        )
        == 0
    )
    restore_output = capsys.readouterr().out
    assert "Encrypted Studio backup restored" in restore_output
    assert "Restored database:" in restore_output
    assert key_secret not in restore_output
    assert restored_path.exists()


def test_parser_keeps_encryption_optional_on_the_existing_backup_flow() -> None:
    parser = build_parser()
    backup = parser.parse_args(
        [
            "studio",
            "backup",
            "--database",
            "studio.db",
            "--output",
            "studio.db.enc",
            "--encryption-key-file",
            "backup.key",
        ]
    )
    verify = parser.parse_args(
        [
            "studio",
            "verify",
            "--backup",
            "studio.db.enc",
            "--encryption-key-file",
            "backup.key",
        ]
    )

    assert backup.encryption_key_file == "backup.key"
    assert verify.encryption_key_file == "backup.key"
