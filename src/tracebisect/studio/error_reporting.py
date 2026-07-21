"""Secret-safe structured reporting for unexpected Studio server failures."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import traceback
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tracebisect.studio.audit import request_action
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

ERROR_LOGGER_NAME = "uvicorn.error.tracebisect_errors"
ERROR_EVENT_VERSION = 1
ERROR_LOG_ENABLED_ENV = "TRACEBISECT_STUDIO_ERROR_LOG_ENABLED"
MAX_STORED_ERROR_EVENTS = 10_000
MAX_STORED_ERROR_EVENTS_PER_WORKSPACE = 1_000
ERROR_EVENT_RETENTION_DAYS = 30
MAX_ERROR_EVENT_LIST_LIMIT = 500
_SAFE_IDENTIFIER_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_FINGERPRINT_PATTERN = re.compile(r"^[a-f0-9]{20}$")
_EVENT_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
_STORED_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")


class StudioErrorEventError(RuntimeError):
    """Raised when retained error events cannot be searched safely."""


@dataclass(frozen=True, slots=True)
class StudioStoredErrorEvent:
    """Bounded, secret-safe metadata retained for one unexpected failure."""

    event_id: str
    request_id: str
    workspace_id: str | None
    action: str
    method: str
    status_code: int
    error_type: str
    failure_location: str
    fingerprint: str
    occurred_at: str
    event_version: int = ERROR_EVENT_VERSION


@dataclass(frozen=True, slots=True)
class StudioErrorReporter:
    """Emit bounded failure metadata without exception messages or request content."""

    enabled: bool = True
    managed_database: StudioDatabaseTarget | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    max_stored_events: int = MAX_STORED_ERROR_EVENTS
    max_stored_events_per_workspace: int = MAX_STORED_ERROR_EVENTS_PER_WORKSPACE
    retention_days: int = ERROR_EVENT_RETENTION_DAYS

    def __post_init__(self) -> None:
        if not 1 <= self.max_stored_events <= 1_000_000:
            raise StudioConfigurationError("stored server-error limit must be 1-1000000")
        if not 1 <= self.max_stored_events_per_workspace <= self.max_stored_events:
            raise StudioConfigurationError(
                "per-workspace server-error limit must not exceed the total limit"
            )
        if not 1 <= self.retention_days <= 3650:
            raise StudioConfigurationError("server-error retention must be 1-3650 days")

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        managed_database: StudioDatabaseTarget | None = None,
    ) -> StudioErrorReporter:
        values = os.environ if env is None else env
        raw = values.get(ERROR_LOG_ENABLED_ENV, "true").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            logging.getLogger(ERROR_LOGGER_NAME).setLevel(logging.ERROR)
            return cls(enabled=True, managed_database=managed_database)
        if raw in {"0", "false", "no", "off"}:
            return cls(enabled=False, managed_database=managed_database)
        raise StudioConfigurationError(f"{ERROR_LOG_ENABLED_ENV} must be true or false")

    def emit_unhandled(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        workspace_id: str | None,
        error: Exception,
        now: datetime | None = None,
    ) -> None:
        """Report one unexpected failure using only allow-listed operational fields."""
        if not self.enabled:
            return
        action = request_action(method, path)
        error_type = _safe_identifier(type(error).__name__, fallback="Exception", limit=80)
        failure_location = _failure_location(error)
        request_value = (
            request_id if _REQUEST_ID_PATTERN.fullmatch(request_id) else os.urandom(16).hex()
        )
        workspace_value = _safe_workspace_id(workspace_id)
        method_value = _safe_identifier(method.upper(), fallback="UNKNOWN", limit=16)
        fingerprint = hashlib.sha256(
            f"{action}:{error_type}:{failure_location}".encode()
        ).hexdigest()[:20]
        occurred = _utc_now(now)
        event = StudioStoredErrorEvent(
            event_id=os.urandom(16).hex(),
            request_id=request_value,
            workspace_id=workspace_value,
            action=action,
            method=method_value,
            status_code=500,
            error_type=error_type,
            failure_location=failure_location,
            fingerprint=fingerprint,
            occurred_at=_timestamp(occurred),
        )
        payload: dict[str, str | int | None] = {
            "event": "studio.server_error",
            "event_version": event.event_version,
            "timestamp": event.occurred_at,
            "request_id": event.request_id,
            "action": event.action,
            "method": event.method,
            "status_code": event.status_code,
            "error_type": event.error_type,
            "failure_location": event.failure_location,
            "fingerprint": event.fingerprint,
            "workspace_id": event.workspace_id,
        }
        logging.getLogger(ERROR_LOGGER_NAME).error(
            "%s",
            json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        )
        if self.managed_database is not None:
            _retain_error_event(
                self.managed_database,
                event,
                max_stored_events=self.max_stored_events,
                max_stored_events_per_workspace=self.max_stored_events_per_workspace,
                retention_days=self.retention_days,
                now=occurred,
            )

    def runtime_status(self) -> dict[str, str | bool | int]:
        """Describe the failure-reporting boundary without exposing deployment details."""
        retention_enabled = self.enabled and self.managed_database is not None
        retention_kind = "log_only" if self.enabled else "disabled"
        if retention_enabled:
            retention_kind = (
                "shared_postgres"
                if isinstance(self.managed_database, StudioManagedDatabase)
                else "local_sqlite"
            )
        return {
            "enabled": self.enabled,
            "format": "json",
            "event": "studio.server_error",
            "request_id_join": True,
            "includes_exception_messages": False,
            "durable_retention": retention_enabled,
            "retention_kind": retention_kind,
            "retention_days": self.retention_days if retention_enabled else 0,
            "max_stored_events": self.max_stored_events if retention_enabled else 0,
            "max_stored_events_per_workspace": (
                self.max_stored_events_per_workspace if retention_enabled else 0
            ),
        }


def list_studio_error_events(
    database_path: StudioDatabaseTarget,
    *,
    request_id: str | None = None,
    fingerprint: str | None = None,
    limit: int = 50,
) -> list[StudioStoredErrorEvent]:
    """Search retained server failures using only bounded operational fields."""
    database = _existing_database(database_path)
    if request_id is not None and _REQUEST_ID_PATTERN.fullmatch(request_id) is None:
        raise StudioErrorEventError("request ID must contain 8-64 safe characters")
    if fingerprint is not None and _FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
        raise StudioErrorEventError("fingerprint must contain exactly 20 lowercase hex characters")
    if not 1 <= limit <= MAX_ERROR_EVENT_LIST_LIMIT:
        raise StudioErrorEventError(
            f"result limit must be between 1 and {MAX_ERROR_EVENT_LIST_LIMIT}"
        )
    conditions: list[str] = []
    parameters: list[object] = []
    if request_id is not None:
        conditions.append("request_id = ?")
        parameters.append(request_id)
    if fingerprint is not None:
        conditions.append("fingerprint = ?")
        parameters.append(fingerprint)
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    parameters.append(limit)
    try:
        with studio_database_connection(database) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            ensure_managed_database_schema(connection)
            rows = connection.execute(
                f"""
                SELECT event_id, request_id, workspace_id, action, method,
                       status_code, error_type, failure_location, fingerprint,
                       occurred_at, event_version
                FROM studio_error_events
                {where_clause}
                ORDER BY occurred_at DESC, event_id DESC
                LIMIT ?
                """,
                tuple(parameters),
            ).fetchall()
    except (OSError, sqlite3.DatabaseError, StudioPersistenceError) as exc:
        raise StudioErrorEventError("could not search retained server error events") from exc
    return [_stored_event_from_row(row) for row in rows]


def _retain_error_event(
    database: StudioDatabaseTarget,
    event: StudioStoredErrorEvent,
    *,
    max_stored_events: int,
    max_stored_events_per_workspace: int,
    retention_days: int,
    now: datetime,
) -> bool:
    """Best-effort retention must never replace the original request failure."""
    cutoff = now - timedelta(days=retention_days)
    try:
        target = _existing_database(database)
        with studio_database_connection(target) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            ensure_managed_database_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM studio_error_events WHERE occurred_at < ?",
                (_timestamp(cutoff),),
            )
            connection.execute(
                """
                INSERT INTO studio_error_events (
                    event_id, request_id, workspace_id, action, method,
                    status_code, error_type, failure_location, fingerprint,
                    occurred_at, event_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.request_id,
                    event.workspace_id,
                    event.action,
                    event.method,
                    event.status_code,
                    event.error_type,
                    event.failure_location,
                    event.fingerprint,
                    event.occurred_at,
                    event.event_version,
                ),
            )
            workspace_condition = (
                "workspace_id IS NOT DISTINCT FROM ?"
                if connection.dialect == "postgres"
                else "workspace_id IS ?"
            )
            connection.execute(
                f"""
                DELETE FROM studio_error_events
                WHERE event_id IN (
                    SELECT event_id FROM studio_error_events
                    WHERE {workspace_condition}
                    ORDER BY occurred_at DESC, event_id DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (
                    event.workspace_id,
                    max_stored_events_per_workspace,
                ),
            )
            connection.execute(
                """
                DELETE FROM studio_error_events
                WHERE event_id IN (
                    SELECT event_id FROM studio_error_events
                    ORDER BY occurred_at DESC, event_id DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (max_stored_events,),
            )
        return True
    except Exception:
        # Logging already succeeded. Retention is deliberately fail-open here so
        # a secondary database problem cannot hide or replace the original 500.
        return False


def _stored_event_from_row(row: tuple[object, ...]) -> StudioStoredErrorEvent:
    (
        event_id,
        request_id,
        workspace_id,
        action,
        method,
        status_code,
        error_type,
        failure_location,
        fingerprint,
        occurred_at,
        event_version,
    ) = row
    workspace = None if workspace_id is None else validate_workspace_id(str(workspace_id))
    if not isinstance(status_code, int) or status_code != 500:
        raise StudioErrorEventError("stored server error status is invalid")
    if not isinstance(event_version, int) or event_version != ERROR_EVENT_VERSION:
        raise StudioErrorEventError("stored server error event version is invalid")
    request_value = str(request_id)
    fingerprint_value = str(fingerprint)
    event_id_value = str(event_id)
    action_value = str(action)
    method_value = str(method)
    error_type_value = str(error_type)
    location_value = str(failure_location)
    if _EVENT_ID_PATTERN.fullmatch(event_id_value) is None:
        raise StudioErrorEventError("stored server error event ID is invalid")
    if _REQUEST_ID_PATTERN.fullmatch(request_value) is None:
        raise StudioErrorEventError("stored server error request ID is invalid")
    if _FINGERPRINT_PATTERN.fullmatch(fingerprint_value) is None:
        raise StudioErrorEventError("stored server error fingerprint is invalid")
    for value, limit, label in (
        (action_value, 80, "action"),
        (method_value, 16, "method"),
        (error_type_value, 80, "error type"),
        (location_value, 180, "location"),
    ):
        if len(value) > limit or _STORED_IDENTIFIER_PATTERN.fullmatch(value) is None:
            raise StudioErrorEventError(f"stored server error {label} is invalid")
    return StudioStoredErrorEvent(
        event_id=event_id_value,
        request_id=request_value,
        workspace_id=workspace,
        action=action_value,
        method=method_value,
        status_code=status_code,
        error_type=error_type_value,
        failure_location=location_value,
        fingerprint=fingerprint_value,
        occurred_at=_stored_timestamp(occurred_at),
        event_version=event_version,
    )


def _existing_database(path: StudioDatabaseTarget) -> StudioDatabaseTarget:
    if isinstance(path, StudioManagedDatabase):
        return path
    database = Path(path).expanduser().resolve()
    if not database.is_file():
        raise StudioErrorEventError("Studio database does not exist; start durable Studio first")
    return database


def _utc_now(value: datetime | None) -> datetime:
    current = datetime.now(timezone.utc) if value is None else value
    if current.tzinfo is None:
        raise StudioConfigurationError("server-error timestamps must include a timezone")
    return current.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _stored_timestamp(value: object) -> str:
    try:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise StudioErrorEventError("stored server error timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise StudioErrorEventError("stored server error timestamp has no timezone")
    return _timestamp(parsed)


def _failure_location(error: Exception) -> str:
    frames = traceback.extract_tb(error.__traceback__, limit=32)
    if not frames:
        return "unknown"
    frame = frames[-1]
    filename = _safe_identifier(Path(frame.filename).name, fallback="unknown", limit=80)
    function = _safe_identifier(frame.name, fallback="unknown", limit=80)
    line = min(max(frame.lineno or 0, 0), 1_000_000)
    return f"{filename}:{function}:{line}"


def _safe_identifier(value: str, *, fallback: str, limit: int) -> str:
    sanitized = _SAFE_IDENTIFIER_PATTERN.sub("_", value).strip("._-")
    return (sanitized or fallback)[:limit]


def _safe_workspace_id(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return validate_workspace_id(value)
    except StudioConfigurationError:
        return None
