"""Short-lived, revocable browser sessions for managed Studio workspaces."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tracebisect.studio.access_keys import (
    ManagedApiKeyPrincipal,
    WorkspaceRole,
    principal_for_managed_api_key,
)
from tracebisect.studio.storage import (
    StudioPersistenceError,
    ensure_studio_schema,
    validate_workspace_id,
)

DEFAULT_BROWSER_SESSION_TTL_SECONDS = 8 * 60 * 60
MIN_BROWSER_SESSION_TTL_SECONDS = 5 * 60
MAX_BROWSER_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_ACTIVE_BROWSER_SESSIONS_PER_KEY = 20
SECURE_BROWSER_SESSION_COOKIE_NAME = "__Host-tracebisect-studio-session"
LOCAL_BROWSER_SESSION_COOKIE_NAME = "tracebisect-studio-session"
_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{12}$")
_SESSION_TOKEN_PATTERN = re.compile(r"^tbss_([A-Za-z0-9_-]{12})_([A-Za-z0-9_-]{43})$")


class StudioBrowserSessionError(RuntimeError):
    """Raised when a browser session cannot be created or revoked safely."""


@dataclass(frozen=True, slots=True)
class StudioBrowserSessionPrincipal:
    """Authorization facts resolved from one active browser session."""

    key_id: str
    workspace_id: str
    role: WorkspaceRole
    session_id: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class IssuedStudioBrowserSession:
    """A newly issued session whose plaintext token is returned exactly once."""

    principal: StudioBrowserSessionPrincipal
    session_token: str = field(repr=False)


def issue_studio_browser_session(
    database_path: str | Path,
    *,
    api_key: str,
    pepper: str,
    ttl_seconds: int,
    now: datetime | None = None,
) -> IssuedStudioBrowserSession:
    """Exchange one active managed key for a bounded, opaque browser session."""
    _validate_ttl(ttl_seconds)
    created = _utc_now(now)
    key_principal = principal_for_managed_api_key(
        database_path,
        api_key=api_key,
        pepper=pepper,
        now=created,
    )
    if key_principal is None:
        raise StudioBrowserSessionError("the workspace key is missing, expired, or revoked")
    key_expires = _parse_timestamp(key_principal.expires_at)
    expires = min(created + timedelta(seconds=ttl_seconds), key_expires)
    if expires <= created:
        raise StudioBrowserSessionError("the workspace key has expired")

    session_id = secrets.token_urlsafe(9)
    secret = secrets.token_urlsafe(32)
    if not _SESSION_ID_PATTERN.fullmatch(session_id) or len(secret) != 43:
        raise StudioBrowserSessionError("could not generate a valid browser session")
    session_token = f"tbss_{session_id}_{secret}"
    session_hash = _session_digest(session_token, pepper)
    database = Path(database_path).expanduser().resolve()

    try:
        with sqlite3.connect(database, timeout=5) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            ensure_studio_schema(connection)
            connection.execute(
                """
                DELETE FROM studio_browser_sessions
                WHERE expires_at <= ? OR revoked_at IS NOT NULL
                """,
                (_timestamp(created),),
            )
            connection.execute(
                """
                DELETE FROM studio_browser_sessions
                WHERE key_id IN (
                    SELECT key_id FROM studio_api_keys
                    WHERE expires_at <= ? OR revoked_at IS NOT NULL
                )
                """,
                (_timestamp(created),),
            )
            oldest_session_rows = connection.execute(
                """
                SELECT session_id FROM studio_browser_sessions
                WHERE key_id = ?
                ORDER BY created_at DESC, session_id DESC
                LIMIT -1 OFFSET ?
                """,
                (key_principal.key_id, MAX_ACTIVE_BROWSER_SESSIONS_PER_KEY - 1),
            ).fetchall()
            connection.executemany(
                "DELETE FROM studio_browser_sessions WHERE session_id = ?",
                oldest_session_rows,
            )
            connection.execute(
                """
                INSERT INTO studio_browser_sessions (
                    session_id, key_id, session_hash, created_at, expires_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (
                    session_id,
                    key_principal.key_id,
                    session_hash,
                    _timestamp(created),
                    _timestamp(expires),
                ),
            )
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError) as exc:
        raise StudioBrowserSessionError("could not save the browser session") from exc

    principal = StudioBrowserSessionPrincipal(
        key_id=key_principal.key_id,
        workspace_id=key_principal.workspace_id,
        role=key_principal.role,
        session_id=session_id,
        expires_at=_timestamp(expires),
    )
    return IssuedStudioBrowserSession(principal=principal, session_token=session_token)


def principal_for_studio_browser_session(
    database_path: str | Path,
    *,
    session_token: str,
    pepper: str,
    now: datetime | None = None,
) -> StudioBrowserSessionPrincipal | None:
    """Resolve an active browser session, failing closed on invalid storage or data."""
    match = _SESSION_TOKEN_PATTERN.fullmatch(session_token)
    if match is None:
        return None
    session_id = match.group(1)
    current = _utc_now(now)
    database = Path(database_path).expanduser().resolve()
    try:
        with sqlite3.connect(
            f"{database.as_uri()}?mode=ro",
            uri=True,
            timeout=5,
        ) as connection:
            row = connection.execute(
                """
                SELECT
                    session.session_hash,
                    session.expires_at,
                    session.revoked_at,
                    api_key.key_id,
                    api_key.workspace_id,
                    api_key.role,
                    api_key.expires_at,
                    api_key.revoked_at
                FROM studio_browser_sessions AS session
                JOIN studio_api_keys AS api_key ON api_key.key_id = session.key_id
                WHERE session.session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        (
            expected_hash,
            session_expires_at,
            session_revoked_at,
            _key_id,
            workspace_id,
            role,
            key_expires_at,
            key_revoked_at,
        ) = row
        supplied_hash = _session_digest(session_token, pepper)
        if not isinstance(expected_hash, str) or not secrets.compare_digest(
            supplied_hash,
            expected_hash,
        ):
            return None
        if session_revoked_at is not None or key_revoked_at is not None:
            return None
        session_expires = _parse_timestamp(str(session_expires_at))
        if session_expires <= current or _parse_timestamp(str(key_expires_at)) <= current:
            return None
        key_principal = _principal_from_row(
            key_id=str(_key_id),
            workspace_id=str(workspace_id),
            role=str(role),
            expires_at=str(key_expires_at),
        )
        return StudioBrowserSessionPrincipal(
            key_id=key_principal.key_id,
            workspace_id=key_principal.workspace_id,
            role=key_principal.role,
            session_id=session_id,
            expires_at=_timestamp(session_expires),
        )
    except (
        OSError,
        sqlite3.DatabaseError,
        StudioBrowserSessionError,
        StudioPersistenceError,
        ValueError,
    ):
        return None


def revoke_studio_browser_session(
    database_path: str | Path,
    *,
    session_token: str,
    pepper: str,
    now: datetime | None = None,
) -> bool:
    """Revoke the exact presented session without revealing whether another exists."""
    match = _SESSION_TOKEN_PATTERN.fullmatch(session_token)
    if match is None:
        return False
    session_id = match.group(1)
    revoked = _utc_now(now)
    database = Path(database_path).expanduser().resolve()
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            ensure_studio_schema(connection)
            row = connection.execute(
                """
                SELECT session_hash, revoked_at
                FROM studio_browser_sessions WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None or not isinstance(row[0], str):
                return False
            if not secrets.compare_digest(
                _session_digest(session_token, pepper),
                row[0],
            ):
                return False
            if row[1] is None:
                connection.execute(
                    """
                    UPDATE studio_browser_sessions SET revoked_at = ? WHERE session_id = ?
                    """,
                    (_timestamp(revoked), session_id),
                )
        return True
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError) as exc:
        raise StudioBrowserSessionError("could not revoke the browser session") from exc


def _principal_from_row(
    *,
    key_id: str,
    workspace_id: str,
    role: str,
    expires_at: str,
) -> ManagedApiKeyPrincipal:
    if role not in {"viewer", "editor", "admin"}:
        raise StudioBrowserSessionError("the browser session role is invalid")
    validated_role: WorkspaceRole
    if role == "viewer":
        validated_role = "viewer"
    elif role == "editor":
        validated_role = "editor"
    else:
        validated_role = "admin"
    return ManagedApiKeyPrincipal(
        key_id=key_id,
        workspace_id=validate_workspace_id(workspace_id),
        role=validated_role,
        expires_at=expires_at,
    )


def _validate_ttl(ttl_seconds: int) -> None:
    if not MIN_BROWSER_SESSION_TTL_SECONDS <= ttl_seconds <= MAX_BROWSER_SESSION_TTL_SECONDS:
        raise StudioBrowserSessionError(
            "browser session lifetime must be between "
            f"{MIN_BROWSER_SESSION_TTL_SECONDS} and {MAX_BROWSER_SESSION_TTL_SECONDS} seconds"
        )


def _session_digest(session_token: str, pepper: str) -> str:
    return hmac.new(
        pepper.encode("utf-8"),
        f"browser-session:{session_token}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def _utc_now(value: datetime | None) -> datetime:
    current = datetime.now(timezone.utc) if value is None else value
    if current.tzinfo is None:
        raise StudioBrowserSessionError("browser session timestamps must include a timezone")
    return current.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise StudioBrowserSessionError("browser session timestamp has no timezone")
    return parsed.astimezone(timezone.utc)
