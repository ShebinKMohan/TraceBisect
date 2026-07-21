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
    WorkspaceRole,
    api_key_pepper,
    principal_for_managed_api_key,
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
from tracebisect.studio.storage import StudioConfigurationError, validate_workspace_id

AuthMode = Literal["none", "api-key"]
CredentialSource = Literal["none", "environment", "managed"]
AuthKind = Literal["api_key", "browser_session"]
MIN_API_KEY_LENGTH = 32
MAX_API_KEY_LENGTH = 256
BROWSER_SESSION_TTL_ENV = "TRACEBISECT_STUDIO_BROWSER_SESSION_TTL_SECONDS"
_API_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{32,256}$")


@dataclass(frozen=True, slots=True)
class StudioAuthPrincipal:
    """Workspace and role granted by one accepted bearer credential."""

    workspace_id: str
    role: WorkspaceRole
    auth_kind: AuthKind = "api_key"
    session_id: str | None = None
    expires_at: str | None = None


@dataclass(frozen=True, slots=True)
class StudioAuthConfig:
    """Fail-closed mapping from bearer credentials to authorized workspaces."""

    mode: AuthMode
    credential_source: CredentialSource = "none"
    _credentials: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    _database_path: Path | None = field(default=None, repr=False)
    _pepper: str | None = field(default=None, repr=False)
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
            return cls(mode="none")
        if raw_mode != "api-key":
            raise StudioConfigurationError(
                "TRACEBISECT_STUDIO_AUTH_MODE must be either 'none' or 'api-key'"
            )
        if raw_credentials and raw_pepper:
            raise StudioConfigurationError(
                "configure either managed API keys or TRACEBISECT_STUDIO_API_KEYS, not both"
            )
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
        if (
            not session_token
            or not self.browser_sessions_enabled
            or self._database_path is None
            or self._pepper is None
        ):
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
            auth_kind="browser_session",
            session_id=principal.session_id,
            expires_at=principal.expires_at,
        )

    def revoke_browser_session(self, session_token: str | None) -> bool:
        """Revoke one browser session when managed sessions are configured."""
        if (
            not session_token
            or not self.browser_sessions_enabled
            or self._database_path is None
            or self._pepper is None
        ):
            return False
        return revoke_studio_browser_session(
            self._database_path,
            session_token=session_token,
            pepper=self._pepper,
        )

    def runtime_status(self) -> dict[str, str | bool | int]:
        """Describe the auth boundary without exposing credentials or workspace names."""
        return {
            "mode": self.mode,
            "required": self.required,
            "credential_source": self.credential_source,
            "browser_sessions": self.browser_sessions_enabled,
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
