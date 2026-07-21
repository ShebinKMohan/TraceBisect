"""Authenticated, streaming encryption for portable Studio SQLite backups."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tracebisect.studio.backup import (
    StudioBackupError,
    StudioBackupInspection,
    create_studio_backup,
    inspect_studio_backup,
    restore_studio_backup,
)

ENCRYPTED_BACKUP_MAGIC = b"TRACEBISECT-STUDIO-BACKUP\x00"
ENCRYPTED_BACKUP_VERSION = 1
ENCRYPTED_BACKUP_ALGORITHM = "AES-256-GCM"
ENCRYPTED_BACKUP_NONCE_BYTES = 12
ENCRYPTED_BACKUP_TAG_BYTES = 16
ENCRYPTED_BACKUP_KEY_BYTES = 32
ENCRYPTED_BACKUP_CHUNK_BYTES = 1024 * 1024
MAX_KEY_FILE_BYTES = 256


@dataclass(frozen=True, slots=True)
class StudioBackupEncryptionKey:
    """Non-secret facts about one generated encryption key file."""

    path: Path
    key_id: str
    algorithm: str = ENCRYPTED_BACKUP_ALGORITHM


@dataclass(frozen=True, slots=True)
class StudioEncryptedBackupInspection:
    """Verified plaintext facts plus safe encrypted-artifact metadata."""

    backup: StudioBackupInspection
    encrypted_size_bytes: int
    encrypted_sha256: str
    key_id: str
    format_version: int = ENCRYPTED_BACKUP_VERSION
    algorithm: str = ENCRYPTED_BACKUP_ALGORITHM


def create_studio_backup_encryption_key(
    output_path: str | Path,
) -> StudioBackupEncryptionKey:
    """Create one owner-only AES-256 key file without printing or replacing it."""
    output = _new_destination(output_path, label="backup encryption key")
    key = AESGCM.generate_key(bit_length=256)
    temporary = _temporary_path(output)
    try:
        with temporary.open("wb") as handle:
            handle.write(base64.urlsafe_b64encode(key) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        _publish_new_file(temporary, output, label="backup encryption key")
    except StudioBackupError:
        raise
    except OSError as exc:
        raise StudioBackupError("could not create the backup encryption key") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return StudioBackupEncryptionKey(path=output, key_id=_key_id(key))


def create_encrypted_studio_backup(
    database_path: str | Path,
    output_path: str | Path,
    key_file: str | Path,
) -> StudioEncryptedBackupInspection:
    """Create, verify, encrypt, and publish one portable Studio backup."""
    output = _new_destination(output_path, label="encrypted backup")
    key = _read_key_file(key_file)
    temporary = _temporary_path(output)
    try:
        with tempfile.TemporaryDirectory(prefix="tracebisect-studio-encrypt-") as raw_dir:
            plaintext_path = Path(raw_dir) / "verified-backup.db"
            create_studio_backup(database_path, plaintext_path)
            _encrypt_file(plaintext_path, temporary, key)
            encrypted_inspection = _inspect_encrypted_with_key(temporary, key)
        _publish_new_file(temporary, output, label="encrypted backup")
    except StudioBackupError:
        raise
    except OSError as exc:
        raise StudioBackupError("could not create the encrypted Studio backup") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return encrypted_inspection


def inspect_encrypted_studio_backup(
    encrypted_path: str | Path,
    key_file: str | Path,
) -> StudioEncryptedBackupInspection:
    """Authenticate an encrypted artifact before inspecting its private plaintext."""
    encrypted = _existing_file(encrypted_path, label="encrypted backup")
    key = _read_key_file(key_file)
    return _inspect_encrypted_with_key(encrypted, key)


def _inspect_encrypted_with_key(
    encrypted: Path,
    key: bytes,
) -> StudioEncryptedBackupInspection:
    with tempfile.TemporaryDirectory(prefix="tracebisect-studio-verify-") as raw_dir:
        plaintext_path = Path(raw_dir) / "verified-backup.db"
        encrypted_size, encrypted_sha256 = _decrypt_file(encrypted, plaintext_path, key)
        backup_inspection = inspect_studio_backup(plaintext_path)
    return _encrypted_inspection(
        key,
        backup_inspection,
        encrypted_size=encrypted_size,
        encrypted_sha256=encrypted_sha256,
    )


def restore_encrypted_studio_backup(
    encrypted_path: str | Path,
    database_path: str | Path,
    key_file: str | Path,
) -> StudioEncryptedBackupInspection:
    """Authenticate and restore an encrypted backup without publishing plaintext."""
    encrypted = _existing_file(encrypted_path, label="encrypted backup")
    destination = _new_destination(database_path, label="restore database")
    key = _read_key_file(key_file)
    with tempfile.TemporaryDirectory(prefix="tracebisect-studio-restore-") as raw_dir:
        plaintext_path = Path(raw_dir) / "verified-backup.db"
        encrypted_size, encrypted_sha256 = _decrypt_file(encrypted, plaintext_path, key)
        backup_inspection = restore_studio_backup(plaintext_path, destination)
    return _encrypted_inspection(
        key,
        backup_inspection,
        encrypted_size=encrypted_size,
        encrypted_sha256=encrypted_sha256,
    )


def is_encrypted_studio_backup(path: str | Path) -> bool:
    """Return whether a file starts with the versioned encrypted-backup magic."""
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file():
        return False
    try:
        with candidate.open("rb") as handle:
            return handle.read(len(ENCRYPTED_BACKUP_MAGIC)) == ENCRYPTED_BACKUP_MAGIC
    except OSError:
        return False


def _encrypt_file(source: Path, destination: Path, key: bytes) -> None:
    nonce = os.urandom(ENCRYPTED_BACKUP_NONCE_BYTES)
    header = ENCRYPTED_BACKUP_MAGIC + bytes([ENCRYPTED_BACKUP_VERSION]) + nonce
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    try:
        with source.open("rb") as plaintext, destination.open("wb") as encrypted:
            encrypted.write(header)
            for chunk in iter(lambda: plaintext.read(ENCRYPTED_BACKUP_CHUNK_BYTES), b""):
                encrypted.write(encryptor.update(chunk))
            encrypted.write(encryptor.finalize())
            encrypted.write(encryptor.tag)
            encrypted.flush()
            os.fsync(encrypted.fileno())
    except (OSError, ValueError) as exc:
        raise StudioBackupError("could not encrypt the Studio backup") from exc


def _decrypt_file(source: Path, destination: Path, key: bytes) -> tuple[int, str]:
    minimum_size = (
        len(ENCRYPTED_BACKUP_MAGIC) + 1 + ENCRYPTED_BACKUP_NONCE_BYTES + ENCRYPTED_BACKUP_TAG_BYTES
    )
    authenticated = False
    encrypted_sha256 = hashlib.sha256()
    try:
        source_size = source.stat().st_size
        if source_size < minimum_size:
            raise StudioBackupError("the encrypted backup is incomplete")
        with source.open("rb") as encrypted:
            magic = encrypted.read(len(ENCRYPTED_BACKUP_MAGIC))
            version_raw = encrypted.read(1)
            nonce = encrypted.read(ENCRYPTED_BACKUP_NONCE_BYTES)
            if magic != ENCRYPTED_BACKUP_MAGIC:
                raise StudioBackupError("the file is not an encrypted Studio backup")
            if version_raw != bytes([ENCRYPTED_BACKUP_VERSION]):
                raise StudioBackupError("the encrypted backup format is not supported")
            header = magic + version_raw + nonce
            encrypted.seek(-ENCRYPTED_BACKUP_TAG_BYTES, os.SEEK_END)
            tag = encrypted.read(ENCRYPTED_BACKUP_TAG_BYTES)
            ciphertext_bytes = source_size - len(header) - len(tag)
            encrypted.seek(len(header))
            encrypted_sha256.update(header)

            decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(header)
            remaining = ciphertext_bytes
            with destination.open("wb") as plaintext:
                while remaining:
                    chunk = encrypted.read(min(ENCRYPTED_BACKUP_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise StudioBackupError("the encrypted backup is incomplete")
                    remaining -= len(chunk)
                    encrypted_sha256.update(chunk)
                    plaintext.write(decryptor.update(chunk))
                encrypted_sha256.update(tag)
                plaintext.write(decryptor.finalize())
                plaintext.flush()
                os.fsync(plaintext.fileno())
            destination.chmod(0o600)
            authenticated = True
    except StudioBackupError:
        raise
    except InvalidTag as exc:
        raise StudioBackupError(
            "the encrypted backup failed authentication; check the file and key"
        ) from exc
    except (OSError, ValueError) as exc:
        raise StudioBackupError("could not decrypt the Studio backup") from exc
    finally:
        if not authenticated:
            destination.unlink(missing_ok=True)
    return source_size, encrypted_sha256.hexdigest()


def _read_key_file(path: str | Path) -> bytes:
    key_path = _existing_file(path, label="backup encryption key")
    try:
        if os.name == "posix" and key_path.stat().st_mode & 0o077:
            raise StudioBackupError("backup encryption key permissions are too open; use chmod 600")
        payload = key_path.read_bytes()
        if len(payload) > MAX_KEY_FILE_BYTES:
            raise StudioBackupError("the backup encryption key file is invalid")
        key = base64.b64decode(payload.strip(), altchars=b"-_", validate=True)
    except StudioBackupError:
        raise
    except (OSError, binascii.Error, ValueError) as exc:
        raise StudioBackupError("the backup encryption key file is invalid") from exc
    if len(key) != ENCRYPTED_BACKUP_KEY_BYTES:
        raise StudioBackupError("the backup encryption key file is invalid")
    return key


def _encrypted_inspection(
    key: bytes,
    backup: StudioBackupInspection,
    *,
    encrypted_size: int,
    encrypted_sha256: str,
) -> StudioEncryptedBackupInspection:
    return StudioEncryptedBackupInspection(
        backup=backup,
        encrypted_size_bytes=encrypted_size,
        encrypted_sha256=encrypted_sha256,
        key_id=_key_id(key),
    )


def _key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


def _existing_file(path: str | Path, *, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise StudioBackupError(f"{label} does not exist or is not a file")
    return resolved


def _new_destination(path: str | Path, *, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if resolved.exists():
        raise StudioBackupError(f"{label} already exists; choose a new path")
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StudioBackupError(f"could not create the {label} directory") from exc
    return resolved


def _temporary_path(destination: Path) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(raw_path)
    temporary.chmod(0o600)
    return temporary


def _publish_new_file(temporary: Path, destination: Path, *, label: str) -> None:
    try:
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise StudioBackupError(f"{label} already exists; no file was replaced") from exc
    except OSError as exc:
        raise StudioBackupError(f"could not publish the completed {label}") from exc
