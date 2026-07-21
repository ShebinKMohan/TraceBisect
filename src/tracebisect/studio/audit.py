"""Secret-safe structured audit events for TraceBisect Studio requests."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from tracebisect.studio.storage import StudioConfigurationError

AUDIT_LOGGER_NAME = "uvicorn.error.tracebisect_audit"
AUDIT_EVENT_VERSION = 1
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

AuditAuthOutcome = Literal[
    "authenticated",
    "not_required",
    "not_checked",
    "public",
    "rejected",
]


@dataclass(frozen=True, slots=True)
class StudioAudit:
    """Emit bounded JSON events that can be collected by a hosting platform."""

    enabled: bool = True

    @classmethod
    def from_env(cls) -> StudioAudit:
        raw = os.getenv("TRACEBISECT_STUDIO_AUDIT_LOG_ENABLED", "true").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            logging.getLogger(AUDIT_LOGGER_NAME).setLevel(logging.INFO)
            return cls(enabled=True)
        if raw in {"0", "false", "no", "off"}:
            return cls(enabled=False)
        raise StudioConfigurationError("TRACEBISECT_STUDIO_AUDIT_LOG_ENABLED must be true or false")

    def request_id(self, supplied: str | None) -> str:
        """Accept a bounded safe correlation ID or generate a server-owned ID."""
        if supplied is not None and _REQUEST_ID_PATTERN.fullmatch(supplied):
            return supplied
        return uuid.uuid4().hex

    def emit_request(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        auth_outcome: AuditAuthOutcome,
        workspace_id: str | None,
    ) -> None:
        """Emit one request event without headers, bodies, query values, or resource IDs."""
        if not self.enabled:
            return
        payload: dict[str, str | int | float | None] = {
            "event": "studio.http_request",
            "event_version": AUDIT_EVENT_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "request_id": request_id,
            "action": request_action(method, path),
            "method": method.upper(),
            "status_code": status_code,
            "result": request_result(status_code),
            "duration_ms": round(max(0.0, duration_ms), 3),
            "auth_outcome": auth_outcome,
            "workspace_id": workspace_id,
        }
        logging.getLogger(AUDIT_LOGGER_NAME).info(
            "%s",
            json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        )


def request_action(method: str, path: str) -> str:
    """Map resource-bearing paths to a fixed, secret-safe action name."""
    normalized_method = method.upper()
    if path == "/api/health":
        return "health_check"
    if path == "/api/ready":
        return "readiness_check"
    if path == "/api/metrics":
        return "metrics_scrape"
    if path == "/api/session":
        return "session_check"
    if path == "/api/browser-session/logout":
        return "browser_session_revoke"
    if path == "/api/browser-session":
        return "browser_session_create"
    if path == "/api/demo-report":
        return "demo_seed"
    if path == "/api/traces":
        return "trace_list"
    if path == "/api/traces/upload":
        return "trace_upload"
    if path == "/api/compare":
        return "trace_compare"
    if path == "/api/runs":
        return "run_list"
    if path.startswith("/api/runs/"):
        return "run_read"
    if path == "/api/regression-cases":
        return "case_create" if normalized_method == "POST" else "case_list"
    if path.startswith("/api/regression-cases/") and path.endswith("/run"):
        return "case_run"
    if path.startswith("/api/regression-cases/"):
        return "case_read"
    return "unclassified_request"


def request_result(status_code: int) -> str:
    """Collapse arbitrary HTTP codes into a bounded operational result."""
    if status_code < 400:
        return "success"
    if status_code in {401, 403}:
        return "denied"
    if status_code == 429:
        return "rate_limited"
    if status_code < 500:
        return "client_error"
    return "server_error"
