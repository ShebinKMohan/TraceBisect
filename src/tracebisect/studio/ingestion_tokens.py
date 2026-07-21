"""Upload-only credentials for agents and CI sending traces to Studio."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from tracebisect.studio.access_keys import (
    MAX_KEY_LABEL_LENGTH,
    MAX_KEY_LIFETIME_DAYS,
    MAX_PEPPER_LENGTH,
    MIN_PEPPER_LENGTH,
)
from tracebisect.studio.managed_database import (
    StudioDatabaseTarget,
    StudioManagedDatabase,
    ensure_managed_database_schema,
    studio_database_connection,
)
from tracebisect.studio.storage import (
    StudioConfigurationError,
    StudioPersistenceError,
    validate_workspace_id,
)

INGESTION_TOKEN_SCOPE = "trace:write"
MAX_ACTIVE_INGESTION_TOKENS_PER_WORKSPACE = 100
_TOKEN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{12}$")
_MANAGED_TOKEN_PATTERN = re.compile(r"^tbit_([A-Za-z0-9_-]{12})_([A-Za-z0-9_-]{43})$")
_CLI_SAFE_ID_FIRST_CHARACTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

StudioIngestionTokenStatus = Literal["active", "expired", "revoked"]


class StudioIngestionTokenError(RuntimeError):
    """Raised when an upload-token operation cannot complete safely."""


@dataclass(frozen=True, slots=True)
class StudioIngestionTokenRecord:
    """Non-secret upload-token metadata safe to list and audit."""

    token_id: str
    workspace_id: str
    label: str
    scope: str
    created_at: str
    expires_at: str
    revoked_at: str | None
    status: StudioIngestionTokenStatus


@dataclass(frozen=True, slots=True)
class IssuedStudioIngestionToken:
    """A newly issued upload token whose plaintext is returned once."""

    record: StudioIngestionTokenRecord
    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ManagedIngestionTokenPrincipal:
    """Authorization facts resolved from one active upload token."""

    token_id: str
    workspace_id: str
    scope: str
    expires_at: str


def create_studio_ingestion_token(
    database_path: StudioDatabaseTarget,
    *,
    workspace_id: str,
    label: str,
    expires_in_days: int,
    pepper: str,
    now: datetime | None = None,
) -> IssuedStudioIngestionToken:
    """Issue an upload-only token and persist only its peppered digest."""
    validated_workspace = validate_workspace_id(workspace_id)
    validated_label = _validate_label(label)
    if not 1 <= expires_in_days <= MAX_KEY_LIFETIME_DAYS:
        raise StudioIngestionTokenError(
            f"token lifetime must be between 1 and {MAX_KEY_LIFETIME_DAYS} days"
        )
    _validate_pepper(pepper)
    created = _utc_now(now)
    expires = created + timedelta(days=expires_in_days)
    raw_token_id = secrets.token_urlsafe(9)
    token_id = f"{secrets.choice(_CLI_SAFE_ID_FIRST_CHARACTERS)}{raw_token_id[1:]}"
    secret = secrets.token_urlsafe(32)
    if not _TOKEN_ID_PATTERN.fullmatch(token_id) or len(secret) != 43:
        raise StudioIngestionTokenError("could not generate a valid ingestion token")
    token = f"tbit_{token_id}_{secret}"
    token_hash = _token_digest(token, pepper)
    database, database_was_created = _database_for_token_creation(database_path)

    try:
        with studio_database_connection(database) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            ensure_managed_database_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            active_count = connection.execute(
                """
                SELECT COUNT(*) FROM studio_ingestion_tokens
                WHERE workspace_id = ? AND revoked_at IS NULL AND expires_at > ?
                """,
                (validated_workspace, _timestamp(created)),
            ).fetchone()
            if (
                active_count is None
                or not isinstance(active_count[0], int)
                or active_count[0] >= MAX_ACTIVE_INGESTION_TOKENS_PER_WORKSPACE
            ):
                raise StudioIngestionTokenError(
                    "workspace already has the maximum number of active ingestion tokens"
                )
            connection.execute(
                """
                INSERT INTO studio_ingestion_tokens (
                    token_id, workspace_id, label, scope, token_hash,
                    created_at, expires_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    token_id,
                    validated_workspace,
                    validated_label,
                    INGESTION_TOKEN_SCOPE,
                    token_hash,
                    _timestamp(created),
                    _timestamp(expires),
                ),
            )
    except StudioIngestionTokenError:
        raise
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError) as exc:
        if database_was_created and isinstance(database, Path):
            database.unlink(missing_ok=True)
        raise StudioIngestionTokenError("could not save the new ingestion token") from exc

    record = StudioIngestionTokenRecord(
        token_id=token_id,
        workspace_id=validated_workspace,
        label=validated_label,
        scope=INGESTION_TOKEN_SCOPE,
        created_at=_timestamp(created),
        expires_at=_timestamp(expires),
        revoked_at=None,
        status="active",
    )
    return IssuedStudioIngestionToken(record=record, token=token)


def list_studio_ingestion_tokens(
    database_path: StudioDatabaseTarget,
    *,
    now: datetime | None = None,
) -> list[StudioIngestionTokenRecord]:
    """List every upload-token record without returning plaintext credentials."""
    return _list_studio_ingestion_tokens(database_path, workspace_id=None, now=now)


def list_workspace_studio_ingestion_tokens(
    database_path: StudioDatabaseTarget,
    *,
    workspace_id: str,
    now: datetime | None = None,
) -> list[StudioIngestionTokenRecord]:
    """List upload-token records owned by exactly one workspace."""
    return _list_studio_ingestion_tokens(
        database_path,
        workspace_id=validate_workspace_id(workspace_id),
        now=now,
    )


def _list_studio_ingestion_tokens(
    database_path: StudioDatabaseTarget,
    *,
    workspace_id: str | None,
    now: datetime | None,
) -> list[StudioIngestionTokenRecord]:
    database = _existing_database(database_path)
    current = _utc_now(now)
    try:
        with studio_database_connection(database) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            ensure_managed_database_schema(connection)
            if workspace_id is None:
                rows = connection.execute(
                    """
                    SELECT token_id, workspace_id, label, scope,
                           created_at, expires_at, revoked_at
                    FROM studio_ingestion_tokens
                    ORDER BY created_at DESC, token_id
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT token_id, workspace_id, label, scope,
                           created_at, expires_at, revoked_at
                    FROM studio_ingestion_tokens
                    WHERE workspace_id = ?
                    ORDER BY created_at DESC, token_id
                    """,
                    (workspace_id,),
                ).fetchall()
        return [_record_from_row(row, now=current) for row in rows]
    except StudioIngestionTokenError:
        raise
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError, TypeError, ValueError) as exc:
        raise StudioIngestionTokenError("could not list ingestion tokens") from exc


def revoke_studio_ingestion_token(
    database_path: StudioDatabaseTarget,
    *,
    token_id: str,
    now: datetime | None = None,
) -> StudioIngestionTokenRecord:
    """Revoke one upload token while retaining non-secret metadata."""
    return _revoke_studio_ingestion_token(
        database_path,
        workspace_id=None,
        token_id=token_id,
        now=now,
    )


def revoke_workspace_studio_ingestion_token(
    database_path: StudioDatabaseTarget,
    *,
    workspace_id: str,
    token_id: str,
    now: datetime | None = None,
) -> StudioIngestionTokenRecord:
    """Revoke an upload token only within the authenticated workspace."""
    return _revoke_studio_ingestion_token(
        database_path,
        workspace_id=validate_workspace_id(workspace_id),
        token_id=token_id,
        now=now,
    )


def _revoke_studio_ingestion_token(
    database_path: StudioDatabaseTarget,
    *,
    workspace_id: str | None,
    token_id: str,
    now: datetime | None,
) -> StudioIngestionTokenRecord:
    database = _existing_database(database_path)
    if not _TOKEN_ID_PATTERN.fullmatch(token_id):
        raise StudioIngestionTokenError(
            "token ID must be the 12-character ID shown by the list command"
        )
    revoked = _utc_now(now)
    try:
        with studio_database_connection(database) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            ensure_managed_database_schema(connection)
            if workspace_id is None:
                row = connection.execute(
                    """
                    SELECT token_id, workspace_id, label, scope,
                           created_at, expires_at, revoked_at
                    FROM studio_ingestion_tokens WHERE token_id = ?
                    """,
                    (token_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT token_id, workspace_id, label, scope,
                           created_at, expires_at, revoked_at
                    FROM studio_ingestion_tokens
                    WHERE token_id = ? AND workspace_id = ?
                    """,
                    (token_id, workspace_id),
                ).fetchone()
            if row is None:
                raise StudioIngestionTokenError("no ingestion token exists with that token ID")
            if row[6] is None:
                if workspace_id is None:
                    connection.execute(
                        "UPDATE studio_ingestion_tokens SET revoked_at = ? WHERE token_id = ?",
                        (_timestamp(revoked), token_id),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE studio_ingestion_tokens SET revoked_at = ?
                        WHERE token_id = ? AND workspace_id = ?
                        """,
                        (_timestamp(revoked), token_id, workspace_id),
                    )
                row = (*row[:6], _timestamp(revoked))
            return _record_from_row(row, now=revoked)
    except StudioIngestionTokenError:
        raise
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError, TypeError, ValueError) as exc:
        raise StudioIngestionTokenError("could not revoke the ingestion token") from exc


def principal_for_managed_ingestion_token(
    database_path: StudioDatabaseTarget,
    *,
    token: str,
    pepper: str,
    now: datetime | None = None,
) -> ManagedIngestionTokenPrincipal | None:
    """Resolve an active upload token, failing closed on invalid data or storage."""
    match = _MANAGED_TOKEN_PATTERN.fullmatch(token)
    if match is None:
        return None
    token_id = match.group(1)
    try:
        _validate_pepper(pepper)
        database = _existing_database(database_path)
        with studio_database_connection(database, read_only=True) as connection:
            row = connection.execute(
                """
                SELECT workspace_id, scope, token_hash, expires_at, revoked_at
                FROM studio_ingestion_tokens WHERE token_id = ?
                """,
                (token_id,),
            ).fetchone()
        if row is None:
            return None
        workspace_id, scope, expected_hash, expires_at, revoked_at = row
        supplied_hash = _token_digest(token, pepper)
        digest_matches = isinstance(expected_hash, str) and secrets.compare_digest(
            supplied_hash,
            expected_hash,
        )
        if not digest_matches or revoked_at is not None or scope != INGESTION_TOKEN_SCOPE:
            return None
        if _parse_timestamp(str(expires_at)) <= _utc_now(now):
            return None
        return ManagedIngestionTokenPrincipal(
            token_id=token_id,
            workspace_id=validate_workspace_id(str(workspace_id)),
            scope=INGESTION_TOKEN_SCOPE,
            expires_at=str(expires_at),
        )
    except (
        OSError,
        sqlite3.DatabaseError,
        StudioConfigurationError,
        StudioIngestionTokenError,
        StudioPersistenceError,
        ValueError,
    ):
        return None


def _record_from_row(
    row: tuple[object, ...],
    *,
    now: datetime,
) -> StudioIngestionTokenRecord:
    token_id, workspace_id, label, scope, created_at, expires_at, revoked_at = row
    if scope != INGESTION_TOKEN_SCOPE:
        raise StudioIngestionTokenError("stored ingestion-token scope is invalid")
    revoked_value = str(revoked_at) if revoked_at is not None else None
    expires_value = str(expires_at)
    if revoked_value is not None:
        status: StudioIngestionTokenStatus = "revoked"
    elif _parse_timestamp(expires_value) <= now:
        status = "expired"
    else:
        status = "active"
    return StudioIngestionTokenRecord(
        token_id=str(token_id),
        workspace_id=validate_workspace_id(str(workspace_id)),
        label=str(label),
        scope=INGESTION_TOKEN_SCOPE,
        created_at=str(created_at),
        expires_at=expires_value,
        revoked_at=revoked_value,
        status=status,
    )


def _existing_database(path: StudioDatabaseTarget) -> StudioDatabaseTarget:
    if isinstance(path, StudioManagedDatabase):
        return path
    database = Path(path).expanduser().resolve()
    if not database.is_file():
        raise StudioIngestionTokenError(
            "Studio database does not exist; start durable Studio first"
        )
    return database


def _database_for_token_creation(
    path: StudioDatabaseTarget,
) -> tuple[StudioDatabaseTarget, bool]:
    if isinstance(path, StudioManagedDatabase):
        return path, False
    database = Path(path).expanduser().resolve()
    if database.exists():
        if not database.is_file():
            raise StudioIngestionTokenError("Studio database path is not a file")
        return database, False
    try:
        database.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
    except OSError as exc:
        raise StudioIngestionTokenError("could not create the Studio database") from exc
    return database, True


def _validate_label(value: str) -> str:
    label = value.strip()
    if not label or len(label) > MAX_KEY_LABEL_LENGTH or any(ord(char) < 32 for char in label):
        raise StudioIngestionTokenError(
            f"token name must contain 1-{MAX_KEY_LABEL_LENGTH} printable characters"
        )
    return label


def _validate_pepper(value: str) -> None:
    if not MIN_PEPPER_LENGTH <= len(value) <= MAX_PEPPER_LENGTH:
        raise StudioIngestionTokenError(
            f"API key pepper must contain {MIN_PEPPER_LENGTH}-{MAX_PEPPER_LENGTH} characters"
        )


def _token_digest(token: str, pepper: str) -> str:
    return hmac.new(
        pepper.encode("utf-8"),
        f"ingestion-token:{token}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def _utc_now(value: datetime | None) -> datetime:
    current = datetime.now(timezone.utc) if value is None else value
    if current.tzinfo is None:
        raise StudioIngestionTokenError("token timestamps must include a timezone")
    return current.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("ingestion-token timestamp has no timezone")
    return parsed.astimezone(timezone.utc)
