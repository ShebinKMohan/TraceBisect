"""Managed human identity for TraceBisect Studio.

The identity boundary is intentionally separate from API-key authentication:
people sign in with an email and password, while API keys remain available for
automation and for bootstrapping the first workspace administrator.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from tracebisect.studio.access_keys import WorkspaceRole
from tracebisect.studio.storage import (
    StudioConfigurationError,
    StudioPersistenceError,
    ensure_studio_schema,
    validate_workspace_id,
)

IDENTITY_SECRET_ENV = "TRACEBISECT_STUDIO_IDENTITY_SECRET"
MIN_IDENTITY_SECRET_LENGTH = 32
MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 128
MAX_EMAIL_LENGTH = 254
MAX_DISPLAY_NAME_LENGTH = 80
MAX_ACTIVE_MEMBERS_PER_WORKSPACE = 100
MAX_ACTIVE_INVITATIONS_PER_WORKSPACE = 100
MAX_STORED_INVITATIONS_PER_WORKSPACE = 500
DEFAULT_INVITATION_LIFETIME_DAYS = 7
MAX_INVITATION_LIFETIME_DAYS = 30
RECOVERY_CODE_COUNT = 8
DEFAULT_IDENTITY_SESSION_TTL_SECONDS = 8 * 60 * 60
MAX_ACTIVE_IDENTITY_SESSIONS_PER_USER = 20

_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{12}$")
_INVITATION_TOKEN_PATTERN = re.compile(r"^tbiv_([A-Za-z0-9_-]{12})_([A-Za-z0-9_-]{43})$")
_SESSION_TOKEN_PATTERN = re.compile(r"^tbis_([A-Za-z0-9_-]{12})_([A-Za-z0-9_-]{43})$")
_RECOVERY_CODE_PATTERN = re.compile(r"^tbrc_[A-Za-z0-9_-]{22}$")
_PASSWORD_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19_456,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)
_DUMMY_PASSWORD_HASH = _PASSWORD_HASHER.hash("not-a-real-tracebisect-password")


class StudioIdentityError(RuntimeError):
    """Raised when an identity operation cannot be completed safely."""


class StudioIdentityInvalidCredentials(StudioIdentityError):
    """Raised for invalid credentials without revealing which field failed."""


class StudioIdentityConflict(StudioIdentityError):
    """Raised when a safe identity invariant would be violated."""


class StudioIdentityNotFound(StudioIdentityError):
    """Raised when a workspace-scoped identity record does not exist."""


@dataclass(frozen=True, slots=True)
class StudioUserRecord:
    user_id: str
    email: str
    display_name: str
    created_at: str


@dataclass(frozen=True, slots=True)
class StudioMembershipRecord:
    user_id: str
    workspace_id: str
    email: str
    display_name: str
    role: WorkspaceRole
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class StudioInvitationRecord:
    invitation_id: str
    workspace_id: str
    email: str
    role: WorkspaceRole
    created_at: str
    expires_at: str
    accepted_at: str | None
    revoked_at: str | None

    @property
    def status(self) -> str:
        if self.revoked_at is not None:
            return "revoked"
        if self.accepted_at is not None:
            return "accepted"
        if _parse_timestamp(self.expires_at) <= datetime.now(timezone.utc):
            return "expired"
        return "pending"


@dataclass(frozen=True, slots=True)
class IssuedStudioInvitation:
    record: StudioInvitationRecord
    invitation_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class AcceptedStudioInvitation:
    user: StudioUserRecord
    membership: StudioMembershipRecord
    recovery_codes: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class StudioIdentitySessionPrincipal:
    user_id: str
    email: str
    display_name: str
    workspace_id: str
    role: WorkspaceRole
    session_id: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class IssuedStudioIdentitySession:
    principal: StudioIdentitySessionPrincipal
    session_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class StudioIdentityLoginResult:
    session: IssuedStudioIdentitySession | None
    workspaces: tuple[StudioMembershipRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class StudioRecoveryResult:
    accepted: bool
    recovery_codes: tuple[str, ...] = field(default=(), repr=False)


def generate_identity_secret() -> str:
    """Return a high-entropy secret suitable for identity token HMACs."""
    return secrets.token_urlsafe(48)


def identity_secret(env: Mapping[str, str]) -> str:
    """Read and validate the dedicated identity secret."""
    value = env.get(IDENTITY_SECRET_ENV, "")
    if value != value.strip() or len(value) < MIN_IDENTITY_SECRET_LENGTH:
        raise StudioConfigurationError(
            f"{IDENTITY_SECRET_ENV} must contain at least {MIN_IDENTITY_SECRET_LENGTH} "
            "characters with no surrounding whitespace"
        )
    return value


def canonical_email(value: str) -> str:
    normalized = value.strip().casefold()
    if len(normalized) > MAX_EMAIL_LENGTH or _EMAIL_PATTERN.fullmatch(normalized) is None:
        raise StudioIdentityError("enter a valid email address")
    return normalized


def validate_password(value: str) -> str:
    if not MIN_PASSWORD_LENGTH <= len(value) <= MAX_PASSWORD_LENGTH:
        raise StudioIdentityError(
            f"password must contain {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} characters"
        )
    return value


def create_studio_invitation(
    database_path: str | Path,
    *,
    workspace_id: str,
    email: str,
    role: WorkspaceRole,
    expires_in_days: int,
    identity_secret_value: str,
    now: datetime | None = None,
) -> IssuedStudioInvitation:
    """Create one workspace invitation and return its plaintext token once."""
    workspace = validate_workspace_id(workspace_id)
    normalized_email = canonical_email(email)
    validated_role = _role(role)
    if not 1 <= expires_in_days <= MAX_INVITATION_LIFETIME_DAYS:
        raise StudioIdentityError(
            f"invitation lifetime must be between 1 and {MAX_INVITATION_LIFETIME_DAYS} days"
        )
    created = _utc_now(now)
    expires = created + timedelta(days=expires_in_days)
    invitation_id = _new_opaque_id()
    secret = secrets.token_urlsafe(32)
    token = f"tbiv_{invitation_id}_{secret}"
    token_hash = _secret_digest("invitation", token, identity_secret_value)
    database = _database_path(database_path)
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            ensure_studio_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            member_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM studio_workspace_memberships WHERE workspace_id = ?",
                    (workspace,),
                ).fetchone()[0]
            )
            if member_count >= MAX_ACTIVE_MEMBERS_PER_WORKSPACE:
                raise StudioIdentityConflict("workspace already has the maximum number of members")
            pending_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM studio_invitations
                    WHERE workspace_id = ? AND accepted_at IS NULL AND revoked_at IS NULL
                      AND expires_at > ?
                    """,
                    (workspace, _timestamp(created)),
                ).fetchone()[0]
            )
            if pending_count >= MAX_ACTIVE_INVITATIONS_PER_WORKSPACE:
                raise StudioIdentityConflict(
                    "workspace already has the maximum pending invitations"
                )
            historical_limit = (
                MAX_STORED_INVITATIONS_PER_WORKSPACE - MAX_ACTIVE_INVITATIONS_PER_WORKSPACE
            )
            oldest_history = connection.execute(
                """
                SELECT invitation_id FROM studio_invitations
                WHERE workspace_id = ?
                  AND (accepted_at IS NOT NULL OR revoked_at IS NOT NULL OR expires_at <= ?)
                ORDER BY created_at DESC, invitation_id DESC
                LIMIT -1 OFFSET ?
                """,
                (workspace, _timestamp(created), historical_limit),
            ).fetchall()
            connection.executemany(
                "DELETE FROM studio_invitations WHERE invitation_id = ?",
                oldest_history,
            )
            connection.execute(
                """
                UPDATE studio_invitations SET revoked_at = ?
                WHERE workspace_id = ? AND email = ?
                  AND accepted_at IS NULL AND revoked_at IS NULL
                """,
                (_timestamp(created), workspace, normalized_email),
            )
            connection.execute(
                """
                INSERT INTO studio_invitations (
                    invitation_id, workspace_id, email, role, token_hash,
                    created_at, expires_at, accepted_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    invitation_id,
                    workspace,
                    normalized_email,
                    validated_role,
                    token_hash,
                    _timestamp(created),
                    _timestamp(expires),
                ),
            )
    except StudioIdentityError:
        raise
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError) as exc:
        raise StudioIdentityError("could not create the workspace invitation") from exc
    return IssuedStudioInvitation(
        record=StudioInvitationRecord(
            invitation_id=invitation_id,
            workspace_id=workspace,
            email=normalized_email,
            role=validated_role,
            created_at=_timestamp(created),
            expires_at=_timestamp(expires),
            accepted_at=None,
            revoked_at=None,
        ),
        invitation_token=token,
    )


def preview_studio_invitation(
    database_path: str | Path,
    *,
    invitation_token: str,
    identity_secret_value: str,
    now: datetime | None = None,
) -> StudioInvitationRecord:
    """Resolve a live invitation without changing it."""
    invitation_id = _token_id(invitation_token, _INVITATION_TOKEN_PATTERN)
    record, expected_hash = _invitation_row(database_path, invitation_id)
    if not secrets.compare_digest(
        expected_hash,
        _secret_digest("invitation", invitation_token, identity_secret_value),
    ):
        raise StudioIdentityInvalidCredentials("invitation is invalid or no longer active")
    current = _utc_now(now)
    if (
        record.accepted_at is not None
        or record.revoked_at is not None
        or _parse_timestamp(record.expires_at) <= current
    ):
        raise StudioIdentityInvalidCredentials("invitation is invalid or no longer active")
    return record


def accept_studio_invitation(
    database_path: str | Path,
    *,
    invitation_token: str,
    display_name: str,
    password: str,
    identity_secret_value: str,
    now: datetime | None = None,
) -> AcceptedStudioInvitation:
    """Accept an invitation, creating a user only when the email is new."""
    invitation_id = _token_id(invitation_token, _INVITATION_TOKEN_PATTERN)
    normalized_name = _display_name(display_name)
    validated_password = validate_password(password)
    current = _utc_now(now)
    database = _database_path(database_path)
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            ensure_studio_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT workspace_id, email, role, token_hash, created_at, expires_at,
                       accepted_at, revoked_at
                FROM studio_invitations WHERE invitation_id = ?
                """,
                (invitation_id,),
            ).fetchone()
            if row is None or not isinstance(row[3], str):
                _verify_dummy_password(validated_password)
                raise StudioIdentityInvalidCredentials("invitation is invalid or no longer active")
            expected_hash = str(row[3])
            if not secrets.compare_digest(
                expected_hash,
                _secret_digest("invitation", invitation_token, identity_secret_value),
            ):
                _verify_dummy_password(validated_password)
                raise StudioIdentityInvalidCredentials("invitation is invalid or no longer active")
            workspace = validate_workspace_id(str(row[0]))
            email = canonical_email(str(row[1]))
            role = _role(str(row[2]))
            if row[6] is not None or row[7] is not None or _parse_timestamp(str(row[5])) <= current:
                _verify_dummy_password(validated_password)
                raise StudioIdentityInvalidCredentials("invitation is invalid or no longer active")

            existing = connection.execute(
                """
                SELECT user_id, display_name, password_hash, created_at, disabled_at
                FROM studio_users WHERE email = ?
                """,
                (email,),
            ).fetchone()
            recovery_codes: tuple[str, ...] = ()
            if existing is None:
                user_id = _new_opaque_id()
                password_hash = _PASSWORD_HASHER.hash(validated_password)
                connection.execute(
                    """
                    INSERT INTO studio_users (
                        user_id, email, display_name, password_hash, session_epoch,
                        created_at, password_changed_at, disabled_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, NULL)
                    """,
                    (
                        user_id,
                        email,
                        normalized_name,
                        password_hash,
                        _timestamp(current),
                        _timestamp(current),
                    ),
                )
                user_name = normalized_name
                user_created_at = _timestamp(current)
                recovery_codes = _replace_recovery_codes(
                    connection,
                    user_id=user_id,
                    identity_secret_value=identity_secret_value,
                    current=current,
                )
            else:
                user_id = str(existing[0])
                user_name = str(existing[1])
                if existing[4] is not None or not _password_matches(
                    validated_password, str(existing[2])
                ):
                    raise StudioIdentityInvalidCredentials(
                        "invitation is invalid or no longer active"
                    )
                user_created_at = str(existing[3])

            if (
                connection.execute(
                    """
                SELECT 1 FROM studio_workspace_memberships
                WHERE workspace_id = ? AND user_id = ?
                """,
                    (workspace, user_id),
                ).fetchone()
                is not None
            ):
                raise StudioIdentityConflict("this account already belongs to the workspace")
            member_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM studio_workspace_memberships WHERE workspace_id = ?",
                    (workspace,),
                ).fetchone()[0]
            )
            if member_count >= MAX_ACTIVE_MEMBERS_PER_WORKSPACE:
                raise StudioIdentityConflict("workspace already has the maximum number of members")
            timestamp = _timestamp(current)
            connection.execute(
                """
                INSERT INTO studio_workspace_memberships (
                    workspace_id, user_id, role, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (workspace, user_id, role, timestamp, timestamp),
            )
            connection.execute(
                "UPDATE studio_invitations SET accepted_at = ? WHERE invitation_id = ?",
                (timestamp, invitation_id),
            )
    except StudioIdentityError:
        raise
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError, ValueError) as exc:
        raise StudioIdentityError("could not accept the workspace invitation") from exc

    user = StudioUserRecord(
        user_id=user_id,
        email=email,
        display_name=user_name,
        created_at=user_created_at,
    )
    membership = StudioMembershipRecord(
        user_id=user_id,
        workspace_id=workspace,
        email=email,
        display_name=user_name,
        role=role,
        created_at=_timestamp(current),
        updated_at=_timestamp(current),
    )
    return AcceptedStudioInvitation(
        user=user,
        membership=membership,
        recovery_codes=recovery_codes,
    )


def login_studio_identity(
    database_path: str | Path,
    *,
    email: str,
    password: str,
    identity_secret_value: str,
    ttl_seconds: int = DEFAULT_IDENTITY_SESSION_TTL_SECONDS,
    workspace_id: str | None = None,
    now: datetime | None = None,
) -> StudioIdentityLoginResult:
    """Verify a password and issue a workspace-scoped human session."""
    try:
        normalized_email = canonical_email(email)
    except StudioIdentityError:
        _verify_dummy_password(password)
        raise StudioIdentityInvalidCredentials("email or password was not accepted") from None
    if not password or len(password) > MAX_PASSWORD_LENGTH:
        _verify_dummy_password(password)
        raise StudioIdentityInvalidCredentials("email or password was not accepted")
    try:
        selected_workspace = (
            validate_workspace_id(workspace_id) if workspace_id is not None else None
        )
    except ValueError:
        _verify_dummy_password(password)
        raise StudioIdentityInvalidCredentials("email or password was not accepted") from None
    current = _utc_now(now)
    database = _database_path(database_path)
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            ensure_studio_schema(connection)
            user_row = connection.execute(
                """
                SELECT user_id, display_name, password_hash, session_epoch, disabled_at
                FROM studio_users WHERE email = ?
                """,
                (normalized_email,),
            ).fetchone()
            if user_row is None or user_row[4] is not None:
                _verify_dummy_password(password)
                raise StudioIdentityInvalidCredentials("email or password was not accepted")
            password_hash = str(user_row[2])
            if not _password_matches(password, password_hash):
                raise StudioIdentityInvalidCredentials("email or password was not accepted")
            user_id = str(user_row[0])
            display_name = str(user_row[1])
            session_epoch = int(user_row[3])
            if _PASSWORD_HASHER.check_needs_rehash(password_hash):
                connection.execute(
                    "UPDATE studio_users SET password_hash = ? WHERE user_id = ?",
                    (_PASSWORD_HASHER.hash(password), user_id),
                )
            memberships = tuple(
                _membership_from_row(row)
                for row in connection.execute(
                    """
                    SELECT membership.user_id, membership.workspace_id, user.email,
                           user.display_name, membership.role,
                           membership.created_at, membership.updated_at
                    FROM studio_workspace_memberships AS membership
                    JOIN studio_users AS user ON user.user_id = membership.user_id
                    WHERE membership.user_id = ?
                    ORDER BY membership.workspace_id
                    """,
                    (user_id,),
                )
            )
            if not memberships:
                raise StudioIdentityInvalidCredentials("email or password was not accepted")
            if selected_workspace is None and len(memberships) > 1:
                return StudioIdentityLoginResult(session=None, workspaces=memberships)
            membership = (
                memberships[0]
                if selected_workspace is None
                else next(
                    (item for item in memberships if item.workspace_id == selected_workspace),
                    None,
                )
            )
            if membership is None:
                raise StudioIdentityInvalidCredentials("email or password was not accepted")
            issued = _issue_identity_session(
                connection,
                user_id=user_id,
                email=normalized_email,
                display_name=display_name,
                workspace_id=membership.workspace_id,
                role=membership.role,
                session_epoch=session_epoch,
                identity_secret_value=identity_secret_value,
                ttl_seconds=ttl_seconds,
                current=current,
            )
            return StudioIdentityLoginResult(session=issued)
    except StudioIdentityError:
        raise
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError, ValueError) as exc:
        raise StudioIdentityError("could not sign in to Studio") from exc


def principal_for_studio_identity_session(
    database_path: str | Path,
    *,
    session_token: str,
    identity_secret_value: str,
    now: datetime | None = None,
) -> StudioIdentitySessionPrincipal | None:
    """Resolve a human session against the current user and membership state."""
    try:
        session_id = _token_id(session_token, _SESSION_TOKEN_PATTERN)
    except StudioIdentityInvalidCredentials:
        return None
    current = _utc_now(now)
    database = _database_path(database_path)
    try:
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=5) as connection:
            row = connection.execute(
                """
                SELECT session.session_hash, session.expires_at, session.revoked_at,
                       session.session_epoch, user.user_id, user.email, user.display_name,
                       user.session_epoch, user.disabled_at, membership.workspace_id,
                       membership.role
                FROM studio_identity_sessions AS session
                JOIN studio_users AS user ON user.user_id = session.user_id
                JOIN studio_workspace_memberships AS membership
                  ON membership.user_id = session.user_id
                 AND membership.workspace_id = session.workspace_id
                WHERE session.session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None or not isinstance(row[0], str):
            return None
        if not secrets.compare_digest(
            str(row[0]),
            _secret_digest("identity-session", session_token, identity_secret_value),
        ):
            return None
        if row[2] is not None or row[8] is not None or int(row[3]) != int(row[7]):
            return None
        expires = _parse_timestamp(str(row[1]))
        if expires <= current:
            return None
        return StudioIdentitySessionPrincipal(
            user_id=str(row[4]),
            email=canonical_email(str(row[5])),
            display_name=str(row[6]),
            workspace_id=validate_workspace_id(str(row[9])),
            role=_role(str(row[10])),
            session_id=session_id,
            expires_at=_timestamp(expires),
        )
    except (
        OSError,
        sqlite3.DatabaseError,
        StudioIdentityError,
        StudioPersistenceError,
        ValueError,
    ):
        return None


def revoke_studio_identity_session(
    database_path: str | Path,
    *,
    session_token: str,
    identity_secret_value: str,
    now: datetime | None = None,
) -> bool:
    try:
        session_id = _token_id(session_token, _SESSION_TOKEN_PATTERN)
    except StudioIdentityInvalidCredentials:
        return False
    current = _utc_now(now)
    database = _database_path(database_path)
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            ensure_studio_schema(connection)
            row = connection.execute(
                """
                SELECT session_hash, revoked_at
                FROM studio_identity_sessions WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None or not isinstance(row[0], str):
                return False
            if not secrets.compare_digest(
                str(row[0]),
                _secret_digest("identity-session", session_token, identity_secret_value),
            ):
                return False
            if row[1] is None:
                connection.execute(
                    "UPDATE studio_identity_sessions SET revoked_at = ? WHERE session_id = ?",
                    (_timestamp(current), session_id),
                )
        return True
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError) as exc:
        raise StudioIdentityError("could not close the Studio session") from exc


def recover_studio_identity(
    database_path: str | Path,
    *,
    email: str,
    recovery_code: str,
    new_password: str,
    identity_secret_value: str,
    now: datetime | None = None,
) -> StudioRecoveryResult:
    """Replace a password with one saved, single-use recovery code."""
    try:
        normalized_email = canonical_email(email)
        validated_password = validate_password(new_password)
    except StudioIdentityError:
        _verify_dummy_password(new_password)
        return StudioRecoveryResult(accepted=False)
    if _RECOVERY_CODE_PATTERN.fullmatch(recovery_code.strip()) is None:
        _verify_dummy_password(new_password)
        return StudioRecoveryResult(accepted=False)
    current = _utc_now(now)
    supplied_hash = _secret_digest(
        "recovery-code",
        recovery_code.strip(),
        identity_secret_value,
    )
    database = _database_path(database_path)
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            ensure_studio_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            user_row = connection.execute(
                "SELECT user_id, disabled_at FROM studio_users WHERE email = ?",
                (normalized_email,),
            ).fetchone()
            if user_row is None or user_row[1] is not None:
                _verify_dummy_password(validated_password)
                return StudioRecoveryResult(accepted=False)
            user_id = str(user_row[0])
            matched_code_id: str | None = None
            for code_id, expected_hash in connection.execute(
                """
                SELECT code_id, code_hash FROM studio_recovery_codes
                WHERE user_id = ? AND used_at IS NULL
                """,
                (user_id,),
            ):
                if isinstance(expected_hash, str) and secrets.compare_digest(
                    supplied_hash,
                    expected_hash,
                ):
                    matched_code_id = str(code_id)
            if matched_code_id is None:
                _verify_dummy_password(validated_password)
                return StudioRecoveryResult(accepted=False)
            timestamp = _timestamp(current)
            connection.execute(
                """
                UPDATE studio_recovery_codes SET used_at = ?
                WHERE code_id = ? AND used_at IS NULL
                """,
                (timestamp, matched_code_id),
            )
            password_hash = _PASSWORD_HASHER.hash(validated_password)
            connection.execute(
                """
                UPDATE studio_users
                SET password_hash = ?, password_changed_at = ?, session_epoch = session_epoch + 1
                WHERE user_id = ?
                """,
                (password_hash, timestamp, user_id),
            )
            connection.execute(
                """
                UPDATE studio_identity_sessions SET revoked_at = ?
                WHERE user_id = ? AND revoked_at IS NULL
                """,
                (timestamp, user_id),
            )
            replacement_codes = _replace_recovery_codes(
                connection,
                user_id=user_id,
                identity_secret_value=identity_secret_value,
                current=current,
            )
            return StudioRecoveryResult(accepted=True, recovery_codes=replacement_codes)
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError) as exc:
        raise StudioIdentityError("could not recover the Studio account") from exc


def list_studio_memberships(
    database_path: str | Path,
    *,
    workspace_id: str,
) -> list[StudioMembershipRecord]:
    workspace = validate_workspace_id(workspace_id)
    database = _database_path(database_path)
    try:
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=5) as connection:
            return [
                _membership_from_row(row)
                for row in connection.execute(
                    """
                    SELECT membership.user_id, membership.workspace_id, user.email,
                           user.display_name, membership.role,
                           membership.created_at, membership.updated_at
                    FROM studio_workspace_memberships AS membership
                    JOIN studio_users AS user ON user.user_id = membership.user_id
                    WHERE membership.workspace_id = ?
                    ORDER BY user.display_name COLLATE NOCASE, user.email
                    """,
                    (workspace,),
                )
            ]
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError, ValueError) as exc:
        raise StudioIdentityError("could not load workspace members") from exc


def update_studio_membership_role(
    database_path: str | Path,
    *,
    workspace_id: str,
    user_id: str,
    role: WorkspaceRole,
    now: datetime | None = None,
) -> StudioMembershipRecord:
    workspace = validate_workspace_id(workspace_id)
    _opaque_id(user_id, label="user ID")
    validated_role = _role(role)
    current = _utc_now(now)
    database = _database_path(database_path)
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            ensure_studio_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT membership.role, user.email, user.display_name,
                       membership.created_at
                FROM studio_workspace_memberships AS membership
                JOIN studio_users AS user ON user.user_id = membership.user_id
                WHERE membership.workspace_id = ? AND membership.user_id = ?
                """,
                (workspace, user_id),
            ).fetchone()
            if existing is None:
                raise StudioIdentityNotFound("workspace member not found")
            if str(existing[0]) == "admin" and validated_role != "admin":
                _require_another_admin(connection, workspace=workspace, excluded_user_id=user_id)
            updated_at = _timestamp(current)
            connection.execute(
                """
                UPDATE studio_workspace_memberships SET role = ?, updated_at = ?
                WHERE workspace_id = ? AND user_id = ?
                """,
                (validated_role, updated_at, workspace, user_id),
            )
            return StudioMembershipRecord(
                user_id=user_id,
                workspace_id=workspace,
                email=canonical_email(str(existing[1])),
                display_name=str(existing[2]),
                role=validated_role,
                created_at=str(existing[3]),
                updated_at=updated_at,
            )
    except StudioIdentityError:
        raise
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError, ValueError) as exc:
        raise StudioIdentityError("could not update the workspace member") from exc


def remove_studio_membership(
    database_path: str | Path,
    *,
    workspace_id: str,
    user_id: str,
    now: datetime | None = None,
) -> StudioMembershipRecord:
    workspace = validate_workspace_id(workspace_id)
    _opaque_id(user_id, label="user ID")
    current = _utc_now(now)
    database = _database_path(database_path)
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            ensure_studio_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT membership.user_id, membership.workspace_id, user.email,
                       user.display_name, membership.role,
                       membership.created_at, membership.updated_at
                FROM studio_workspace_memberships AS membership
                JOIN studio_users AS user ON user.user_id = membership.user_id
                WHERE membership.workspace_id = ? AND membership.user_id = ?
                """,
                (workspace, user_id),
            ).fetchone()
            if existing is None:
                raise StudioIdentityNotFound("workspace member not found")
            record = _membership_from_row(existing)
            if record.role == "admin":
                _require_another_admin(connection, workspace=workspace, excluded_user_id=user_id)
            timestamp = _timestamp(current)
            connection.execute(
                """
                UPDATE studio_identity_sessions SET revoked_at = ?
                WHERE user_id = ? AND workspace_id = ? AND revoked_at IS NULL
                """,
                (timestamp, user_id, workspace),
            )
            connection.execute(
                """
                DELETE FROM studio_workspace_memberships
                WHERE workspace_id = ? AND user_id = ?
                """,
                (workspace, user_id),
            )
            return record
    except StudioIdentityError:
        raise
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError, ValueError) as exc:
        raise StudioIdentityError("could not remove the workspace member") from exc


def list_studio_invitations(
    database_path: str | Path,
    *,
    workspace_id: str,
) -> list[StudioInvitationRecord]:
    workspace = validate_workspace_id(workspace_id)
    database = _database_path(database_path)
    try:
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=5) as connection:
            return [
                _invitation_from_row(row)
                for row in connection.execute(
                    """
                    SELECT invitation_id, workspace_id, email, role, created_at,
                           expires_at, accepted_at, revoked_at
                    FROM studio_invitations
                    WHERE workspace_id = ?
                    ORDER BY created_at DESC, invitation_id DESC
                    """,
                    (workspace,),
                )
            ]
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError, ValueError) as exc:
        raise StudioIdentityError("could not load workspace invitations") from exc


def revoke_studio_invitation(
    database_path: str | Path,
    *,
    workspace_id: str,
    invitation_id: str,
    now: datetime | None = None,
) -> StudioInvitationRecord:
    workspace = validate_workspace_id(workspace_id)
    _opaque_id(invitation_id, label="invitation ID")
    current = _utc_now(now)
    database = _database_path(database_path)
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            ensure_studio_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT invitation_id, workspace_id, email, role, created_at,
                       expires_at, accepted_at, revoked_at
                FROM studio_invitations
                WHERE workspace_id = ? AND invitation_id = ?
                """,
                (workspace, invitation_id),
            ).fetchone()
            if row is None:
                raise StudioIdentityNotFound("workspace invitation not found")
            record = _invitation_from_row(row)
            if record.accepted_at is not None:
                raise StudioIdentityConflict("accepted invitations cannot be revoked")
            if record.revoked_at is None:
                revoked_at = _timestamp(current)
                connection.execute(
                    "UPDATE studio_invitations SET revoked_at = ? WHERE invitation_id = ?",
                    (revoked_at, invitation_id),
                )
                record = StudioInvitationRecord(
                    invitation_id=record.invitation_id,
                    workspace_id=record.workspace_id,
                    email=record.email,
                    role=record.role,
                    created_at=record.created_at,
                    expires_at=record.expires_at,
                    accepted_at=record.accepted_at,
                    revoked_at=revoked_at,
                )
            return record
    except StudioIdentityError:
        raise
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError, ValueError) as exc:
        raise StudioIdentityError("could not revoke the workspace invitation") from exc


def _issue_identity_session(
    connection: sqlite3.Connection,
    *,
    user_id: str,
    email: str,
    display_name: str,
    workspace_id: str,
    role: WorkspaceRole,
    session_epoch: int,
    identity_secret_value: str,
    ttl_seconds: int,
    current: datetime,
) -> IssuedStudioIdentitySession:
    if ttl_seconds < 300 or ttl_seconds > 7 * 24 * 60 * 60:
        raise StudioIdentityError("identity session lifetime is outside the safe range")
    session_id = _new_opaque_id()
    session_token = f"tbis_{session_id}_{secrets.token_urlsafe(32)}"
    expires = current + timedelta(seconds=ttl_seconds)
    timestamp = _timestamp(current)
    connection.execute(
        "DELETE FROM studio_identity_sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL",
        (timestamp,),
    )
    oldest_rows = connection.execute(
        """
        SELECT session_id FROM studio_identity_sessions
        WHERE user_id = ?
        ORDER BY created_at DESC, session_id DESC
        LIMIT -1 OFFSET ?
        """,
        (user_id, MAX_ACTIVE_IDENTITY_SESSIONS_PER_USER - 1),
    ).fetchall()
    connection.executemany(
        "DELETE FROM studio_identity_sessions WHERE session_id = ?",
        oldest_rows,
    )
    connection.execute(
        """
        INSERT INTO studio_identity_sessions (
            session_id, user_id, workspace_id, session_hash, session_epoch,
            created_at, expires_at, revoked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            session_id,
            user_id,
            workspace_id,
            _secret_digest("identity-session", session_token, identity_secret_value),
            session_epoch,
            timestamp,
            _timestamp(expires),
        ),
    )
    principal = StudioIdentitySessionPrincipal(
        user_id=user_id,
        email=email,
        display_name=display_name,
        workspace_id=workspace_id,
        role=role,
        session_id=session_id,
        expires_at=_timestamp(expires),
    )
    return IssuedStudioIdentitySession(principal=principal, session_token=session_token)


def _replace_recovery_codes(
    connection: sqlite3.Connection,
    *,
    user_id: str,
    identity_secret_value: str,
    current: datetime,
) -> tuple[str, ...]:
    connection.execute("DELETE FROM studio_recovery_codes WHERE user_id = ?", (user_id,))
    codes = tuple(f"tbrc_{secrets.token_urlsafe(16)}" for _ in range(RECOVERY_CODE_COUNT))
    timestamp = _timestamp(current)
    connection.executemany(
        """
        INSERT INTO studio_recovery_codes (code_id, user_id, code_hash, created_at, used_at)
        VALUES (?, ?, ?, ?, NULL)
        """,
        [
            (
                _new_opaque_id(),
                user_id,
                _secret_digest("recovery-code", code, identity_secret_value),
                timestamp,
            )
            for code in codes
        ],
    )
    return codes


def _invitation_row(
    database_path: str | Path,
    invitation_id: str,
) -> tuple[StudioInvitationRecord, str]:
    database = _database_path(database_path)
    try:
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=5) as connection:
            row = connection.execute(
                """
                SELECT invitation_id, workspace_id, email, role, created_at,
                       expires_at, accepted_at, revoked_at, token_hash
                FROM studio_invitations WHERE invitation_id = ?
                """,
                (invitation_id,),
            ).fetchone()
        if row is None or not isinstance(row[8], str):
            raise StudioIdentityInvalidCredentials("invitation is invalid or no longer active")
        return _invitation_from_row(row[:8]), str(row[8])
    except StudioIdentityError:
        raise
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError, ValueError) as exc:
        raise StudioIdentityError("could not load the workspace invitation") from exc


def _membership_from_row(row: tuple[object, ...]) -> StudioMembershipRecord:
    return StudioMembershipRecord(
        user_id=_opaque_id(str(row[0]), label="user ID"),
        workspace_id=validate_workspace_id(str(row[1])),
        email=canonical_email(str(row[2])),
        display_name=str(row[3]),
        role=_role(str(row[4])),
        created_at=str(row[5]),
        updated_at=str(row[6]),
    )


def _invitation_from_row(row: tuple[object, ...]) -> StudioInvitationRecord:
    return StudioInvitationRecord(
        invitation_id=_opaque_id(str(row[0]), label="invitation ID"),
        workspace_id=validate_workspace_id(str(row[1])),
        email=canonical_email(str(row[2])),
        role=_role(str(row[3])),
        created_at=str(row[4]),
        expires_at=str(row[5]),
        accepted_at=None if row[6] is None else str(row[6]),
        revoked_at=None if row[7] is None else str(row[7]),
    )


def _require_another_admin(
    connection: sqlite3.Connection,
    *,
    workspace: str,
    excluded_user_id: str,
) -> None:
    row = connection.execute(
        """
        SELECT COUNT(*) FROM studio_workspace_memberships
        WHERE workspace_id = ? AND role = 'admin' AND user_id != ?
        """,
        (workspace, excluded_user_id),
    ).fetchone()
    if row is None or int(row[0]) < 1:
        raise StudioIdentityConflict("a workspace must keep at least one account administrator")


def _display_name(value: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized or len(normalized) > MAX_DISPLAY_NAME_LENGTH:
        raise StudioIdentityError(
            f"display name must contain 1-{MAX_DISPLAY_NAME_LENGTH} characters"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise StudioIdentityError("display name contains unsupported characters")
    return normalized


def _password_matches(password: str, password_hash: str) -> bool:
    try:
        return bool(_PASSWORD_HASHER.verify(password_hash, password))
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def _verify_dummy_password(password: str) -> None:
    _password_matches(password[:MAX_PASSWORD_LENGTH], _DUMMY_PASSWORD_HASH)


def _secret_digest(domain: str, value: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        f"{domain}:{value}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _token_id(token: str, pattern: re.Pattern[str]) -> str:
    match = pattern.fullmatch(token)
    if match is None:
        raise StudioIdentityInvalidCredentials("credential is invalid or no longer active")
    return match.group(1)


def _new_opaque_id() -> str:
    value = secrets.token_urlsafe(9)
    if _OPAQUE_ID_PATTERN.fullmatch(value) is None:
        raise StudioIdentityError("could not generate a valid identity identifier")
    return value


def _opaque_id(value: str, *, label: str) -> str:
    if _OPAQUE_ID_PATTERN.fullmatch(value) is None:
        raise StudioIdentityError(f"{label} must be a 12-character URL-safe identifier")
    return value


def _role(value: str) -> WorkspaceRole:
    if value not in {"viewer", "editor", "admin"}:
        raise StudioIdentityError("workspace role is invalid")
    if value == "viewer":
        return "viewer"
    if value == "editor":
        return "editor"
    return "admin"


def _database_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _utc_now(value: datetime | None) -> datetime:
    current = datetime.now(timezone.utc) if value is None else value
    if current.tzinfo is None:
        raise StudioIdentityError("identity timestamps must include a timezone")
    return current.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise StudioIdentityError("identity timestamp has no timezone")
    return parsed.astimezone(timezone.utc)
