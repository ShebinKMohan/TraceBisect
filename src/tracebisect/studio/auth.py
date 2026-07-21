"""Authentication configuration for request-scoped Studio workspaces."""

from __future__ import annotations

import json
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from tracebisect.studio.access_keys import (
    API_KEY_PEPPER_ENV,
    IssuedStudioApiKey,
    StudioApiKeyError,
    StudioApiKeyRecord,
    WorkspaceRole,
    api_key_pepper,
    create_studio_api_key,
    list_workspace_studio_api_keys,
    principal_for_managed_api_key,
    revoke_workspace_studio_api_key,
)
from tracebisect.studio.access_sessions import (
    DEFAULT_BROWSER_SESSION_TTL_SECONDS,
    MAX_BROWSER_SESSION_TTL_SECONDS,
    MIN_BROWSER_SESSION_TTL_SECONDS,
    IssuedStudioBrowserSession,
    issue_studio_browser_session,
    principal_for_studio_browser_session,
    revoke_studio_browser_session,
)
from tracebisect.studio.identity import (
    IDENTITY_SECRET_ENV,
    AcceptedStudioInvitation,
    IssuedStudioInvitation,
    StudioIdentityLoginResult,
    StudioInvitationRecord,
    StudioMembershipRecord,
    StudioRecoveryResult,
    accept_studio_invitation,
    create_studio_invitation,
    identity_secret,
    list_studio_invitations,
    list_studio_memberships,
    login_studio_identity,
    preview_studio_invitation,
    principal_for_studio_identity_session,
    recover_studio_identity,
    remove_studio_membership,
    revoke_studio_identity_session,
    revoke_studio_invitation,
    update_studio_membership_role,
)
from tracebisect.studio.storage import StudioConfigurationError, validate_workspace_id

AuthMode = Literal["none", "api-key"]
CredentialSource = Literal["none", "environment", "managed"]
AuthKind = Literal["api_key", "browser_session", "identity_session"]
MIN_API_KEY_LENGTH = 32
MAX_API_KEY_LENGTH = 256
BROWSER_SESSION_TTL_ENV = "TRACEBISECT_STUDIO_BROWSER_SESSION_TTL_SECONDS"
_API_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{32,256}$")


@dataclass(frozen=True, slots=True)
class StudioAuthPrincipal:
    """Workspace and role granted by one accepted bearer credential."""

    workspace_id: str
    role: WorkspaceRole
    key_id: str | None = None
    auth_kind: AuthKind = "api_key"
    session_id: str | None = None
    expires_at: str | None = None
    user_id: str | None = None
    email: str | None = None
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class StudioAuthConfig:
    """Fail-closed mapping from bearer credentials to authorized workspaces."""

    mode: AuthMode
    credential_source: CredentialSource = "none"
    _credentials: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    _database_path: Path | None = field(default=None, repr=False)
    _pepper: str | None = field(default=None, repr=False)
    _identity_secret: str | None = field(default=None, repr=False)
    _browser_session_ttl_seconds: int = field(
        default=DEFAULT_BROWSER_SESSION_TTL_SECONDS,
        repr=False,
    )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> StudioAuthConfig:
        values = os.environ if env is None else env
        raw_mode = values.get("TRACEBISECT_STUDIO_AUTH_MODE", "none").strip().lower()
        raw_credentials = values.get("TRACEBISECT_STUDIO_API_KEYS", "").strip()
        raw_pepper = values.get(API_KEY_PEPPER_ENV, "")
        raw_identity_secret = values.get(IDENTITY_SECRET_ENV, "")
        raw_browser_session_ttl = values.get(BROWSER_SESSION_TTL_ENV, "").strip()
        if raw_mode == "none":
            if raw_credentials:
                raise StudioConfigurationError(
                    "TRACEBISECT_STUDIO_API_KEYS is set but auth mode is 'none'"
                )
            if raw_pepper:
                raise StudioConfigurationError(
                    f"{API_KEY_PEPPER_ENV} is set but auth mode is 'none'"
                )
            if raw_browser_session_ttl:
                raise StudioConfigurationError(
                    f"{BROWSER_SESSION_TTL_ENV} is set but auth mode is 'none'"
                )
            if raw_identity_secret:
                raise StudioConfigurationError(
                    f"{IDENTITY_SECRET_ENV} is set but auth mode is 'none'"
                )
            return cls(mode="none")
        if raw_mode != "api-key":
            raise StudioConfigurationError(
                "TRACEBISECT_STUDIO_AUTH_MODE must be either 'none' or 'api-key'"
            )
        if raw_credentials and raw_pepper:
            raise StudioConfigurationError(
                "configure either managed API keys or TRACEBISECT_STUDIO_API_KEYS, not both"
            )
        if raw_identity_secret and not raw_pepper:
            raise StudioConfigurationError(f"{IDENTITY_SECRET_ENV} requires managed API keys")
        if raw_pepper:
            pepper = api_key_pepper(values)
            browser_session_ttl_seconds = _browser_session_ttl(raw_browser_session_ttl)
            raw_database_path = values.get("TRACEBISECT_STUDIO_SQLITE_PATH", "").strip()
            if not raw_database_path:
                raise StudioConfigurationError(
                    "TRACEBISECT_STUDIO_SQLITE_PATH is required for managed API keys"
                )
            config = cls(
                mode="api-key",
                credential_source="managed",
                _database_path=Path(raw_database_path).expanduser().resolve(),
                _pepper=pepper,
                _identity_secret=identity_secret(values) if raw_identity_secret else None,
                _browser_session_ttl_seconds=browser_session_ttl_seconds,
            )
        else:
            if raw_browser_session_ttl:
                raise StudioConfigurationError(
                    f"{BROWSER_SESSION_TTL_ENV} requires managed API keys"
                )
            credentials = _parse_credentials(raw_credentials)
            config = cls(
                mode="api-key",
                credential_source="environment",
                _credentials=credentials,
            )
        storage_kind = values.get("TRACEBISECT_STUDIO_STORAGE", "memory").strip().lower()
        if storage_kind != "sqlite":
            raise StudioConfigurationError(
                "api-key authentication requires TRACEBISECT_STUDIO_STORAGE=sqlite"
            )
        return config

    @property
    def required(self) -> bool:
        return self.mode == "api-key"

    def workspace_for_authorization(self, authorization: str | None) -> str | None:
        """Return the authorized workspace, or ``None`` for a missing/invalid key."""
        principal = self.principal_for_authorization(authorization)
        return principal.workspace_id if principal is not None else None

    def principal_for_authorization(
        self,
        authorization: str | None,
    ) -> StudioAuthPrincipal | None:
        """Return the workspace role granted by a valid bearer credential."""
        if not self.required:
            return None
        candidate = _bearer_candidate(authorization)
        if candidate is None:
            return None
        if self.credential_source == "managed":
            if self._database_path is None or self._pepper is None:
                return None
            principal = principal_for_managed_api_key(
                self._database_path,
                api_key=candidate,
                pepper=self._pepper,
            )
            if principal is None:
                return None
            return StudioAuthPrincipal(
                workspace_id=principal.workspace_id,
                role=principal.role,
                key_id=principal.key_id,
            )
        matched_workspace: str | None = None
        for expected, workspace_id in self._credentials:
            if secrets.compare_digest(candidate, expected):
                matched_workspace = workspace_id
        if matched_workspace is None:
            return None
        return StudioAuthPrincipal(workspace_id=matched_workspace, role="admin")

    @property
    def browser_sessions_enabled(self) -> bool:
        """Return whether managed keys can be exchanged for browser sessions."""
        return self.credential_source == "managed"

    @property
    def browser_session_ttl_seconds(self) -> int:
        """Return the configured maximum browser-session lifetime."""
        return self._browser_session_ttl_seconds

    def issue_browser_session(self, authorization: str | None) -> IssuedStudioBrowserSession | None:
        """Exchange a managed bearer key for a short-lived opaque session."""
        candidate = _bearer_candidate(authorization)
        if (
            candidate is None
            or not self.browser_sessions_enabled
            or self._database_path is None
            or self._pepper is None
        ):
            return None
        return issue_studio_browser_session(
            self._database_path,
            api_key=candidate,
            pepper=self._pepper,
            ttl_seconds=self._browser_session_ttl_seconds,
        )

    def principal_for_browser_session(
        self,
        session_token: str | None,
    ) -> StudioAuthPrincipal | None:
        """Resolve an opaque browser cookie to its live workspace authorization."""
        if not session_token or not self.browser_sessions_enabled or self._database_path is None:
            return None
        if session_token.startswith("tbis_"):
            if self._identity_secret is None:
                return None
            identity_principal = principal_for_studio_identity_session(
                self._database_path,
                session_token=session_token,
                identity_secret_value=self._identity_secret,
            )
            if identity_principal is None:
                return None
            return StudioAuthPrincipal(
                workspace_id=identity_principal.workspace_id,
                role=identity_principal.role,
                auth_kind="identity_session",
                session_id=identity_principal.session_id,
                expires_at=identity_principal.expires_at,
                user_id=identity_principal.user_id,
                email=identity_principal.email,
                display_name=identity_principal.display_name,
            )
        if self._pepper is None:
            return None
        principal = principal_for_studio_browser_session(
            self._database_path,
            session_token=session_token,
            pepper=self._pepper,
        )
        if principal is None:
            return None
        return StudioAuthPrincipal(
            workspace_id=principal.workspace_id,
            role=principal.role,
            key_id=principal.key_id,
            auth_kind="browser_session",
            session_id=principal.session_id,
            expires_at=principal.expires_at,
        )

    def revoke_browser_session(self, session_token: str | None) -> bool:
        """Revoke one browser session when managed sessions are configured."""
        if not session_token or not self.browser_sessions_enabled or self._database_path is None:
            return False
        if session_token.startswith("tbis_"):
            if self._identity_secret is None:
                return False
            return revoke_studio_identity_session(
                self._database_path,
                session_token=session_token,
                identity_secret_value=self._identity_secret,
            )
        if self._pepper is None:
            return False
        return revoke_studio_browser_session(
            self._database_path,
            session_token=session_token,
            pepper=self._pepper,
        )

    @property
    def access_management_enabled(self) -> bool:
        """Return whether admins can manage scoped keys through the product API."""
        return self.credential_source == "managed"

    @property
    def identity_enabled(self) -> bool:
        """Return whether managed human accounts are configured."""
        return self.credential_source == "managed" and self._identity_secret is not None

    def login_identity(
        self,
        *,
        email: str,
        password: str,
        workspace_id: str | None,
    ) -> StudioIdentityLoginResult:
        database_path, secret = self._managed_identity_material()
        return login_studio_identity(
            database_path,
            email=email,
            password=password,
            workspace_id=workspace_id,
            identity_secret_value=secret,
            ttl_seconds=self._browser_session_ttl_seconds,
        )

    def preview_invitation(self, invitation_token: str) -> StudioInvitationRecord:
        database_path, secret = self._managed_identity_material()
        return preview_studio_invitation(
            database_path,
            invitation_token=invitation_token,
            identity_secret_value=secret,
        )

    def accept_invitation(
        self,
        *,
        invitation_token: str,
        display_name: str,
        password: str,
    ) -> AcceptedStudioInvitation:
        database_path, secret = self._managed_identity_material()
        return accept_studio_invitation(
            database_path,
            invitation_token=invitation_token,
            display_name=display_name,
            password=password,
            identity_secret_value=secret,
        )

    def recover_identity(
        self,
        *,
        email: str,
        recovery_code: str,
        new_password: str,
    ) -> StudioRecoveryResult:
        database_path, secret = self._managed_identity_material()
        return recover_studio_identity(
            database_path,
            email=email,
            recovery_code=recovery_code,
            new_password=new_password,
            identity_secret_value=secret,
        )

    def list_workspace_members(self, workspace_id: str) -> list[StudioMembershipRecord]:
        database_path, _secret = self._managed_identity_material()
        return list_studio_memberships(database_path, workspace_id=workspace_id)

    def update_workspace_member(
        self,
        *,
        workspace_id: str,
        user_id: str,
        role: WorkspaceRole,
    ) -> StudioMembershipRecord:
        database_path, _secret = self._managed_identity_material()
        return update_studio_membership_role(
            database_path,
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
        )

    def remove_workspace_member(
        self,
        *,
        workspace_id: str,
        user_id: str,
    ) -> StudioMembershipRecord:
        database_path, _secret = self._managed_identity_material()
        return remove_studio_membership(
            database_path,
            workspace_id=workspace_id,
            user_id=user_id,
        )

    def list_workspace_invitations(self, workspace_id: str) -> list[StudioInvitationRecord]:
        database_path, _secret = self._managed_identity_material()
        return list_studio_invitations(database_path, workspace_id=workspace_id)

    def create_workspace_invitation(
        self,
        *,
        workspace_id: str,
        email: str,
        role: WorkspaceRole,
        expires_in_days: int,
    ) -> IssuedStudioInvitation:
        database_path, secret = self._managed_identity_material()
        return create_studio_invitation(
            database_path,
            workspace_id=workspace_id,
            email=email,
            role=role,
            expires_in_days=expires_in_days,
            identity_secret_value=secret,
        )

    def revoke_workspace_invitation(
        self,
        *,
        workspace_id: str,
        invitation_id: str,
    ) -> StudioInvitationRecord:
        database_path, _secret = self._managed_identity_material()
        return revoke_studio_invitation(
            database_path,
            workspace_id=workspace_id,
            invitation_id=invitation_id,
        )

    def list_workspace_access_keys(self, workspace_id: str) -> list[StudioApiKeyRecord]:
        """List non-secret key metadata for exactly one authenticated workspace."""
        database_path, _pepper = self._managed_key_material()
        return list_workspace_studio_api_keys(
            database_path,
            workspace_id=workspace_id,
        )

    def create_workspace_access_key(
        self,
        *,
        workspace_id: str,
        role: WorkspaceRole,
        label: str,
        expires_in_days: int,
    ) -> IssuedStudioApiKey:
        """Create one workspace key whose plaintext value is returned exactly once."""
        database_path, pepper = self._managed_key_material()
        return create_studio_api_key(
            database_path,
            workspace_id=workspace_id,
            role=role,
            label=label,
            expires_in_days=expires_in_days,
            pepper=pepper,
        )

    def revoke_workspace_access_key(
        self,
        *,
        workspace_id: str,
        key_id: str,
    ) -> StudioApiKeyRecord:
        """Revoke one key without allowing access across workspace boundaries."""
        database_path, _pepper = self._managed_key_material()
        return revoke_workspace_studio_api_key(
            database_path,
            workspace_id=workspace_id,
            key_id=key_id,
        )

    def _managed_key_material(self) -> tuple[Path, str]:
        if (
            not self.access_management_enabled
            or self._database_path is None
            or self._pepper is None
        ):
            raise StudioApiKeyError("managed workspace access is not enabled")
        return self._database_path, self._pepper

    def _managed_identity_material(self) -> tuple[Path, str]:
        if (
            not self.identity_enabled
            or self._database_path is None
            or self._identity_secret is None
        ):
            raise StudioApiKeyError("managed human identity is not enabled")
        return self._database_path, self._identity_secret

    def runtime_status(self) -> dict[str, str | bool | int]:
        """Describe the auth boundary without exposing credentials or workspace names."""
        return {
            "mode": self.mode,
            "required": self.required,
            "credential_source": self.credential_source,
            "browser_sessions": self.browser_sessions_enabled,
            "self_service_access_management": self.access_management_enabled,
            "human_accounts": self.identity_enabled,
            "browser_session_ttl_seconds": (
                self._browser_session_ttl_seconds if self.browser_sessions_enabled else 0
            ),
        }


def _bearer_candidate(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, separator, candidate = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator:
        return None
    if not candidate or len(candidate) > MAX_API_KEY_LENGTH:
        return None
    return candidate


def _browser_session_ttl(raw: str) -> int:
    if not raw:
        return DEFAULT_BROWSER_SESSION_TTL_SECONDS
    try:
        ttl_seconds = int(raw)
    except ValueError as exc:
        raise StudioConfigurationError(
            f"{BROWSER_SESSION_TTL_ENV} must be an integer between "
            f"{MIN_BROWSER_SESSION_TTL_SECONDS} and {MAX_BROWSER_SESSION_TTL_SECONDS}"
        ) from exc
    if not MIN_BROWSER_SESSION_TTL_SECONDS <= ttl_seconds <= MAX_BROWSER_SESSION_TTL_SECONDS:
        raise StudioConfigurationError(
            f"{BROWSER_SESSION_TTL_ENV} must be between "
            f"{MIN_BROWSER_SESSION_TTL_SECONDS} and {MAX_BROWSER_SESSION_TTL_SECONDS}"
        )
    return ttl_seconds


def _parse_credentials(raw: str) -> tuple[tuple[str, str], ...]:
    if not raw:
        raise StudioConfigurationError(
            "TRACEBISECT_STUDIO_API_KEYS is required when auth mode is 'api-key'"
        )
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StudioConfigurationError(
            "TRACEBISECT_STUDIO_API_KEYS must be a JSON object of API key to workspace id"
        ) from exc
    if not isinstance(parsed, dict) or not parsed:
        raise StudioConfigurationError(
            "TRACEBISECT_STUDIO_API_KEYS must be a non-empty JSON object"
        )

    credentials: list[tuple[str, str]] = []
    for api_key, workspace_id in cast(dict[object, object], parsed).items():
        if not isinstance(api_key, str) or not isinstance(workspace_id, str):
            raise StudioConfigurationError(
                "TRACEBISECT_STUDIO_API_KEYS entries must map string keys to string workspaces"
            )
        if not _API_KEY_PATTERN.fullmatch(api_key):
            raise StudioConfigurationError(
                f"Studio API keys must contain {MIN_API_KEY_LENGTH}-{MAX_API_KEY_LENGTH} "
                "URL-safe characters"
            )
        credentials.append((api_key, validate_workspace_id(workspace_id)))
    return tuple(credentials)
