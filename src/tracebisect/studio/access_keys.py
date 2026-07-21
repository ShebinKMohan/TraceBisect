"""Hashed, expiring workspace API keys for durable TraceBisect Studio installs."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from tracebisect.studio.storage import (
    StudioConfigurationError,
    StudioPersistenceError,
    ensure_studio_schema,
    validate_workspace_id,
)

API_KEY_PEPPER_ENV = "TRACEBISECT_STUDIO_API_KEY_PEPPER"
MIN_PEPPER_LENGTH = 32
MAX_PEPPER_LENGTH = 512
MAX_KEY_LIFETIME_DAYS = 3650
MAX_KEY_LABEL_LENGTH = 80
MAX_ACTIVE_STUDIO_API_KEYS_PER_WORKSPACE = 100
_KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{12}$")
_MANAGED_KEY_PATTERN = re.compile(r"^tbsk_([A-Za-z0-9_-]{12})_([A-Za-z0-9_-]{43})$")

StudioApiKeyStatus = Literal["active", "expired", "revoked"]
WorkspaceRole = Literal["viewer", "editor", "admin"]
WORKSPACE_ROLES = frozenset({"viewer", "editor", "admin"})


class StudioApiKeyError(RuntimeError):
    """Raised when a managed Studio key operation cannot complete safely."""


@dataclass(frozen=True, slots=True)
class StudioApiKeyRecord:
    """Non-secret metadata shown by the operator key-management commands."""

    key_id: str
    workspace_id: str
    role: WorkspaceRole
    label: str
    created_at: str
    expires_at: str
    revoked_at: str | None
    status: StudioApiKeyStatus


@dataclass(frozen=True, slots=True)
class IssuedStudioApiKey:
    """A newly issued key; the plaintext value is returned exactly once."""

    record: StudioApiKeyRecord
    api_key: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ManagedApiKeyPrincipal:
    """Authorization facts resolved from one valid managed key."""

    key_id: str
    workspace_id: str
    role: WorkspaceRole
    expires_at: str


def api_key_pepper(env: Mapping[str, str] | None = None) -> str:
    """Read and validate the server-side pepper without exposing its value."""
    values = os.environ if env is None else env
    pepper = values.get(API_KEY_PEPPER_ENV, "")
    if not MIN_PEPPER_LENGTH <= len(pepper) <= MAX_PEPPER_LENGTH:
        raise StudioConfigurationError(
            f"{API_KEY_PEPPER_ENV} must contain {MIN_PEPPER_LENGTH}-{MAX_PEPPER_LENGTH} characters"
        )
    return pepper


def generate_api_key_pepper() -> str:
    """Generate a high-entropy server secret suitable for managed-key hashing."""
    return secrets.token_urlsafe(48)


def create_studio_api_key(
    database_path: str | Path,
    *,
    workspace_id: str,
    role: WorkspaceRole,
    label: str,
    expires_in_days: int,
    pepper: str,
    now: datetime | None = None,
) -> IssuedStudioApiKey:
    """Issue a high-entropy key and persist only its peppered HMAC digest."""
    validated_workspace = validate_workspace_id(workspace_id)
    validated_role = _validate_role(role)
    validated_label = _validate_label(label)
    if not 1 <= expires_in_days <= MAX_KEY_LIFETIME_DAYS:
        raise StudioApiKeyError(f"key lifetime must be between 1 and {MAX_KEY_LIFETIME_DAYS} days")
    _validate_pepper(pepper)
    created = _utc_now(now)
    expires = created + timedelta(days=expires_in_days)
    key_id = secrets.token_urlsafe(9)
    secret = secrets.token_urlsafe(32)
    if not _KEY_ID_PATTERN.fullmatch(key_id) or len(secret) != 43:
        raise StudioApiKeyError("could not generate a valid Studio API key")
    api_key = f"tbsk_{key_id}_{secret}"
    key_hash = _key_digest(api_key, pepper)
    database, database_was_created = _database_for_key_creation(database_path)

    try:
        with sqlite3.connect(database, timeout=5) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            ensure_studio_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            active_count = connection.execute(
                """
                SELECT COUNT(*) FROM studio_api_keys
                WHERE workspace_id = ? AND revoked_at IS NULL AND expires_at > ?
                """,
                (validated_workspace, _timestamp(created)),
            ).fetchone()
            if (
                active_count is None
                or not isinstance(active_count[0], int)
                or active_count[0] >= MAX_ACTIVE_STUDIO_API_KEYS_PER_WORKSPACE
            ):
                raise StudioApiKeyError(
                    "workspace already has the maximum number of active access keys"
                )
            connection.execute(
                """
                INSERT INTO studio_api_keys (
                    key_id, workspace_id, role, label, key_hash, created_at, expires_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    key_id,
                    validated_workspace,
                    validated_role,
                    validated_label,
                    key_hash,
                    _timestamp(created),
                    _timestamp(expires),
                ),
            )
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError) as exc:
        if database_was_created:
            database.unlink(missing_ok=True)
        raise StudioApiKeyError("could not save the new Studio API key") from exc

    record = StudioApiKeyRecord(
        key_id=key_id,
        workspace_id=validated_workspace,
        role=validated_role,
        label=validated_label,
        created_at=_timestamp(created),
        expires_at=_timestamp(expires),
        revoked_at=None,
        status="active",
    )
    return IssuedStudioApiKey(record=record, api_key=api_key)


def list_studio_api_keys(
    database_path: str | Path,
    *,
    now: datetime | None = None,
) -> list[StudioApiKeyRecord]:
    """List key metadata without ever reading or returning a plaintext key."""
    return _list_studio_api_keys(database_path, workspace_id=None, now=now)


def list_workspace_studio_api_keys(
    database_path: str | Path,
    *,
    workspace_id: str,
    now: datetime | None = None,
) -> list[StudioApiKeyRecord]:
    """List only the non-secret key metadata owned by one workspace."""
    return _list_studio_api_keys(
        database_path,
        workspace_id=validate_workspace_id(workspace_id),
        now=now,
    )


def _list_studio_api_keys(
    database_path: str | Path,
    *,
    workspace_id: str | None,
    now: datetime | None,
) -> list[StudioApiKeyRecord]:
    database = _existing_database(database_path)
    current = _utc_now(now)
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            ensure_studio_schema(connection)
            if workspace_id is None:
                rows = connection.execute(
                    """
                    SELECT key_id, workspace_id, role, label, created_at, expires_at, revoked_at
                    FROM studio_api_keys
                    ORDER BY created_at DESC, key_id
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT key_id, workspace_id, role, label, created_at, expires_at, revoked_at
                    FROM studio_api_keys
                    WHERE workspace_id = ?
                    ORDER BY created_at DESC, key_id
                    """,
                    (workspace_id,),
                ).fetchall()
        records = [_record_from_row(row, now=current) for row in rows]
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError, TypeError, ValueError) as exc:
        raise StudioApiKeyError("could not list Studio API keys") from exc
    return records


def revoke_studio_api_key(
    database_path: str | Path,
    *,
    key_id: str,
    now: datetime | None = None,
) -> StudioApiKeyRecord:
    """Revoke one key immediately while leaving its audit-safe metadata available."""
    database = _existing_database(database_path)
    if not _KEY_ID_PATTERN.fullmatch(key_id):
        raise StudioApiKeyError("key ID must be the 12-character ID shown by the list command")
    revoked = _utc_now(now)
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            ensure_studio_schema(connection)
            row = connection.execute(
                """
                SELECT key_id, workspace_id, role, label, created_at, expires_at, revoked_at
                FROM studio_api_keys WHERE key_id = ?
                """,
                (key_id,),
            ).fetchone()
            if row is None:
                raise StudioApiKeyError("no Studio API key exists with that key ID")
            if row[6] is None:
                connection.execute(
                    "UPDATE studio_api_keys SET revoked_at = ? WHERE key_id = ?",
                    (_timestamp(revoked), key_id),
                )
                row = (*row[:6], _timestamp(revoked))
            record = _record_from_row(row, now=revoked)
    except StudioApiKeyError:
        raise
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError, TypeError, ValueError) as exc:
        raise StudioApiKeyError("could not revoke the Studio API key") from exc
    return record


def revoke_workspace_studio_api_key(
    database_path: str | Path,
    *,
    workspace_id: str,
    key_id: str,
    now: datetime | None = None,
) -> StudioApiKeyRecord:
    """Revoke a key only when it belongs to the authenticated workspace."""
    database = _existing_database(database_path)
    validated_workspace = validate_workspace_id(workspace_id)
    if not _KEY_ID_PATTERN.fullmatch(key_id):
        raise StudioApiKeyError("key ID must be the 12-character ID shown in Studio")
    revoked = _utc_now(now)
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            ensure_studio_schema(connection)
            row = connection.execute(
                """
                SELECT key_id, workspace_id, role, label, created_at, expires_at, revoked_at
                FROM studio_api_keys
                WHERE key_id = ? AND workspace_id = ?
                """,
                (key_id, validated_workspace),
            ).fetchone()
            if row is None:
                raise StudioApiKeyError("no workspace access key exists with that key ID")
            if row[6] is None:
                connection.execute(
                    """
                    UPDATE studio_api_keys SET revoked_at = ?
                    WHERE key_id = ? AND workspace_id = ?
                    """,
                    (_timestamp(revoked), key_id, validated_workspace),
                )
                row = (*row[:6], _timestamp(revoked))
            record = _record_from_row(row, now=revoked)
    except StudioApiKeyError:
        raise
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError, TypeError, ValueError) as exc:
        raise StudioApiKeyError("could not revoke the workspace access key") from exc
    return record


def workspace_for_managed_api_key(
    database_path: str | Path,
    *,
    api_key: str,
    pepper: str,
    now: datetime | None = None,
) -> str | None:
    """Resolve one active managed key to its workspace, failing closed on any error."""
    principal = principal_for_managed_api_key(
        database_path,
        api_key=api_key,
        pepper=pepper,
        now=now,
    )
    return principal.workspace_id if principal is not None else None


def principal_for_managed_api_key(
    database_path: str | Path,
    *,
    api_key: str,
    pepper: str,
    now: datetime | None = None,
) -> ManagedApiKeyPrincipal | None:
    """Resolve one active managed key to its workspace role, failing closed on errors."""
    match = _MANAGED_KEY_PATTERN.fullmatch(api_key)
    if match is None:
        return None
    key_id = match.group(1)
    try:
        _validate_pepper(pepper)
        database = _existing_database(database_path)
        with sqlite3.connect(
            f"{database.as_uri()}?mode=ro",
            uri=True,
            timeout=5,
        ) as connection:
            row = connection.execute(
                """
                SELECT workspace_id, role, key_hash, expires_at, revoked_at
                FROM studio_api_keys WHERE key_id = ?
                """,
                (key_id,),
            ).fetchone()
        if row is None:
            return None
        workspace_id, role, expected_hash, expires_at, revoked_at = row
        supplied_hash = _key_digest(api_key, pepper)
        digest_matches = isinstance(expected_hash, str) and secrets.compare_digest(
            supplied_hash,
            expected_hash,
        )
        if not digest_matches or revoked_at is not None:
            return None
        if _parse_timestamp(str(expires_at)) <= _utc_now(now):
            return None
        return ManagedApiKeyPrincipal(
            key_id=key_id,
            workspace_id=validate_workspace_id(str(workspace_id)),
            role=_validate_role(str(role)),
            expires_at=str(expires_at),
        )
    except (
        OSError,
        sqlite3.DatabaseError,
        StudioApiKeyError,
        StudioConfigurationError,
        ValueError,
    ):
        return None


def _record_from_row(row: tuple[object, ...], *, now: datetime) -> StudioApiKeyRecord:
    key_id, workspace_id, role, label, created_at, expires_at, revoked_at = row
    revoked_value = str(revoked_at) if revoked_at is not None else None
    expires_value = str(expires_at)
    if revoked_value is not None:
        status: StudioApiKeyStatus = "revoked"
    elif _parse_timestamp(expires_value) <= now:
        status = "expired"
    else:
        status = "active"
    return StudioApiKeyRecord(
        key_id=str(key_id),
        workspace_id=str(workspace_id),
        role=_validate_role(str(role)),
        label=str(label),
        created_at=str(created_at),
        expires_at=expires_value,
        revoked_at=revoked_value,
        status=status,
    )


def _existing_database(path: str | Path) -> Path:
    database = Path(path).expanduser().resolve()
    if not database.is_file():
        raise StudioApiKeyError("Studio database does not exist; start durable Studio first")
    return database


def _database_for_key_creation(path: str | Path) -> tuple[Path, bool]:
    database = Path(path).expanduser().resolve()
    if database.exists():
        if not database.is_file():
            raise StudioApiKeyError("Studio database path is not a file")
        return database, False
    try:
        database.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
    except OSError as exc:
        raise StudioApiKeyError("could not create the Studio database") from exc
    return database, True


def _validate_label(value: str) -> str:
    label = value.strip()
    if not label or len(label) > MAX_KEY_LABEL_LENGTH or any(ord(char) < 32 for char in label):
        raise StudioApiKeyError(
            f"key name must contain 1-{MAX_KEY_LABEL_LENGTH} printable characters"
        )
    return label


def _validate_role(value: str) -> WorkspaceRole:
    if value not in WORKSPACE_ROLES:
        allowed = ", ".join(sorted(WORKSPACE_ROLES))
        raise StudioApiKeyError(f"workspace role must be one of: {allowed}")
    if value == "viewer":
        return "viewer"
    if value == "editor":
        return "editor"
    return "admin"


def _validate_pepper(value: str) -> None:
    if not MIN_PEPPER_LENGTH <= len(value) <= MAX_PEPPER_LENGTH:
        raise StudioApiKeyError(
            f"API key pepper must contain {MIN_PEPPER_LENGTH}-{MAX_PEPPER_LENGTH} characters"
        )


def _key_digest(api_key: str, pepper: str) -> str:
    return hmac.new(
        pepper.encode("utf-8"),
        api_key.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def _utc_now(value: datetime | None) -> datetime:
    current = datetime.now(timezone.utc) if value is None else value
    if current.tzinfo is None:
        raise StudioApiKeyError("key timestamps must include a timezone")
    return current.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Studio API key timestamp has no timezone")
    return parsed.astimezone(timezone.utc)
