"""Secret-safe structured reporting for unexpected Studio server failures."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import traceback
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tracebisect.studio.audit import request_action
from tracebisect.studio.storage import StudioConfigurationError

ERROR_LOGGER_NAME = "uvicorn.error.tracebisect_errors"
ERROR_EVENT_VERSION = 1
ERROR_LOG_ENABLED_ENV = "TRACEBISECT_STUDIO_ERROR_LOG_ENABLED"
_SAFE_IDENTIFIER_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True, slots=True)
class StudioErrorReporter:
    """Emit bounded failure metadata without exception messages or request content."""

    enabled: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> StudioErrorReporter:
        values = os.environ if env is None else env
        raw = values.get(ERROR_LOG_ENABLED_ENV, "true").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            logging.getLogger(ERROR_LOGGER_NAME).setLevel(logging.ERROR)
            return cls(enabled=True)
        if raw in {"0", "false", "no", "off"}:
            return cls(enabled=False)
        raise StudioConfigurationError(f"{ERROR_LOG_ENABLED_ENV} must be true or false")

    def emit_unhandled(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        workspace_id: str | None,
        error: Exception,
    ) -> None:
        """Report one unexpected failure using only allow-listed operational fields."""
        if not self.enabled:
            return
        action = request_action(method, path)
        error_type = _safe_identifier(type(error).__name__, fallback="Exception", limit=80)
        failure_location = _failure_location(error)
        fingerprint = hashlib.sha256(
            f"{action}:{error_type}:{failure_location}".encode()
        ).hexdigest()[:20]
        payload: dict[str, str | int | None] = {
            "event": "studio.server_error",
            "event_version": ERROR_EVENT_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "request_id": request_id,
            "action": action,
            "method": method.upper(),
            "status_code": 500,
            "error_type": error_type,
            "failure_location": failure_location,
            "fingerprint": fingerprint,
            "workspace_id": workspace_id,
        }
        logging.getLogger(ERROR_LOGGER_NAME).error(
            "%s",
            json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        )

    def runtime_status(self) -> dict[str, str | bool]:
        """Describe the failure-reporting boundary without exposing deployment details."""
        return {
            "enabled": self.enabled,
            "format": "json",
            "event": "studio.server_error",
            "request_id_join": True,
            "includes_exception_messages": False,
        }


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
