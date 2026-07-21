"""Beginner-readable checks for a live TraceBisect Studio deployment."""

from __future__ import annotations

import ipaddress
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Literal, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

MAX_HEALTH_RESPONSE_BYTES = 64 * 1024
MIN_TIMEOUT_SECONDS = 0.1
MAX_TIMEOUT_SECONDS = 30.0

CheckStatus = Literal["pass", "warning", "fail"]
JsonMapping = Mapping[str, object]
EndpointFetcher = Callable[[str, float], tuple[int, JsonMapping]]


class StudioDeploymentCheckError(RuntimeError):
    """Raised when a live deployment cannot be checked safely."""


@dataclass(frozen=True, slots=True)
class StudioDeploymentCheckItem:
    """One operator-facing deployment fact."""

    key: str
    label: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True, slots=True)
class StudioDeploymentCheckReport:
    """Facts proven from the public readiness and health endpoints."""

    base_url: str
    hosted_core_ready: bool
    full_saas_ready: bool
    checks: tuple[StudioDeploymentCheckItem, ...]
    remaining_saas_work: tuple[str, ...]


_CORE_CHECK_KEYS = {
    "connection",
    "api",
    "storage",
    "access",
    "browser_sessions",
    "uploads",
    "audit",
    "errors",
    "metrics",
    "rate_limits",
}

_BLOCKER_GUIDANCE = {
    "restart-safe durable storage": "Use durable storage so saved work survives a restart.",
    "dedicated production metrics scrape access": (
        "Give monitoring its own token instead of a workspace key."
    ),
    "durable server-error retention and request-ID search": (
        "Keep safe server-error history so support can investigate a request ID."
    ),
    "authentication and authorization": "Require sign-in and enforce user permissions.",
    "request-scoped workspace isolation": "Keep every request inside its signed-in workspace.",
    "malware scanning for untrusted trace uploads": (
        "Scan uploaded trace files before parsing or saving them."
    ),
    "available malware scanner for untrusted trace uploads": (
        "Restore the required upload scanner before accepting files."
    ),
    "distributed rate limiting across API replicas": (
        "Share rate limits before running more than one API instance."
    ),
    "scheduled encrypted off-site backups and recovery drills": (
        "Schedule encrypted off-site backups and practise restoring them."
    ),
    "managed user accounts, recovery, and team membership administration": (
        "Finish managed accounts, recovery, and team access."
    ),
    "hosted deployment observability": (
        "Send metrics, alerts, and operational history to managed off-host services."
    ),
}


def check_studio_deployment(
    base_url: str,
    *,
    timeout_seconds: float = 5.0,
    fetcher: EndpointFetcher | None = None,
) -> StudioDeploymentCheckReport:
    """Check one live Studio without credentials or product-data access."""
    normalized_url, local_http = _validated_base_url(base_url)
    timeout = _validated_timeout(timeout_seconds)
    fetch = _fetch_json if fetcher is None else fetcher
    ready_status, ready_payload = fetch(f"{normalized_url}/api/ready", timeout)
    health_status, health_payload = fetch(f"{normalized_url}/api/health", timeout)

    runtime = _mapping(health_payload, "runtime")
    auth = _mapping(health_payload, "auth")
    uploads = _mapping(health_payload, "uploads")
    audit = _mapping(health_payload, "audit")
    errors = _mapping(health_payload, "errors")
    metrics = _mapping(health_payload, "metrics")
    rate_limiting = _mapping(health_payload, "rate_limiting")
    readiness = _mapping(health_payload, "readiness")

    api_ready = (
        ready_status == 200
        and ready_payload.get("ready") is True
        and health_status == 200
        and health_payload.get("ok") is True
        and health_payload.get("product") == "TraceBisect Studio"
    )
    durable_storage = runtime.get("durable") is True
    protected_access = auth.get("required") is True
    secure_browser_sessions = (
        auth.get("browser_sessions") is True and auth.get("browser_session_cookie_secure") is True
    )
    secure_uploads = (
        uploads.get("enabled") is True
        and uploads.get("ready") is True
        and uploads.get("fail_closed") is True
        and uploads.get("scan_before_parse") is True
    )
    safe_audit = audit.get("enabled") is True
    safe_errors = (
        errors.get("enabled") is True
        and errors.get("includes_exception_messages") is False
        and errors.get("request_id_join") is True
        and errors.get("durable_retention") is True
    )
    protected_metrics = metrics.get("access") == "dedicated_token"
    bounded_rate_limits = (
        rate_limiting.get("algorithm") == "sliding_window"
        and rate_limiting.get("stores_raw_client_keys") is False
    )

    checks = (
        StudioDeploymentCheckItem(
            key="connection",
            label="Public address",
            status="warning" if local_http else "pass",
            detail=(
                "Local HTTP is acceptable for this check, but a public deployment must use HTTPS."
                if local_http
                else "The deployment address uses HTTPS."
            ),
        ),
        StudioDeploymentCheckItem(
            key="api",
            label="Studio can serve users",
            status="pass" if api_ready else "fail",
            detail=(
                "Readiness and health checks both passed."
                if api_ready
                else "Readiness or health failed. Keep Studio out of user traffic."
            ),
        ),
        StudioDeploymentCheckItem(
            key="storage",
            label="Saved work survives restarts",
            status="pass" if durable_storage else "fail",
            detail=(
                f"Studio reports durable {_safe_text(runtime.get('kind'), fallback='storage')}."
                if durable_storage
                else "Studio is using temporary memory storage. Configure SQLite or PostgreSQL."
            ),
        ),
        StudioDeploymentCheckItem(
            key="access",
            label="Visitors must sign in",
            status="pass" if protected_access else "fail",
            detail=(
                "Authentication and workspace permissions are required."
                if protected_access
                else "Studio is open without authentication. Do not expose it publicly."
            ),
        ),
        StudioDeploymentCheckItem(
            key="browser_sessions",
            label="Browser sign-in is protected",
            status="pass" if secure_browser_sessions else "fail",
            detail=(
                "Browser sessions use secure cookies."
                if secure_browser_sessions
                else "Enable managed browser sessions and secure cookies before public use."
            ),
        ),
        StudioDeploymentCheckItem(
            key="uploads",
            label="Uploads fail closed",
            status="pass" if secure_uploads else "fail",
            detail=(
                "Files are scanned before parsing or storage."
                if secure_uploads
                else "A required, ready malware scanner is not protecting uploads."
            ),
        ),
        StudioDeploymentCheckItem(
            key="audit",
            label="Requests leave a safe audit trail",
            status="pass" if safe_audit else "fail",
            detail=(
                "Structured request auditing is enabled."
                if safe_audit
                else "Enable the structured request audit trail before public use."
            ),
        ),
        StudioDeploymentCheckItem(
            key="errors",
            label="Support errors are safe to investigate",
            status="pass" if safe_errors else "fail",
            detail=(
                "Error events use request IDs without exposing exception messages."
                if safe_errors
                else "Safe, durable request-ID error reporting is incomplete."
            ),
        ),
        StudioDeploymentCheckItem(
            key="metrics",
            label="Monitoring has separate access",
            status="pass" if protected_metrics else "fail",
            detail=(
                "Metrics require a dedicated monitoring token."
                if protected_metrics
                else "Protect metrics with a dedicated token, not a workspace key."
            ),
        ),
        StudioDeploymentCheckItem(
            key="rate_limits",
            label="Repeated requests are bounded",
            status="pass" if bounded_rate_limits else "fail",
            detail=(
                "Studio applies bounded sliding-window limits without storing raw client keys."
                if bounded_rate_limits
                else "Confirm rate limiting before opening Studio to untrusted traffic."
            ),
        ),
    )

    hosted_core_ready = all(
        check.status == "pass" for check in checks if check.key in _CORE_CHECK_KEYS
    )
    blockers = _remaining_work(readiness.get("blockers"))
    api_claims_full_saas = readiness.get("production_saas_ready") is True
    if not api_claims_full_saas and not blockers:
        blockers = (
            "Studio reports that full SaaS readiness is incomplete but did not name "
            "the remaining work.",
        )
    full_saas_ready = api_claims_full_saas and hosted_core_ready and not blockers
    return StudioDeploymentCheckReport(
        base_url=normalized_url,
        hosted_core_ready=hosted_core_ready,
        full_saas_ready=full_saas_ready,
        checks=checks,
        remaining_saas_work=blockers,
    )


def _validated_base_url(value: str) -> tuple[str, bool]:
    raw = value.strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise StudioDeploymentCheckError("the Studio URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise StudioDeploymentCheckError("the Studio URL must start with https://")
    if parsed.username is not None or parsed.password is not None:
        raise StudioDeploymentCheckError("the Studio URL must not contain credentials")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise StudioDeploymentCheckError(
            "the Studio URL must not contain a path, query, or fragment"
        )
    host = parsed.hostname
    if (
        len(host) > 253
        or not host.isascii()
        or any(character.isspace() or ord(character) < 33 for character in host)
    ):
        raise StudioDeploymentCheckError("the Studio URL contains an invalid hostname")
    local_http = parsed.scheme == "http" and _is_loopback_host(host)
    if parsed.scheme == "http" and not local_http:
        raise StudioDeploymentCheckError(
            "public Studio checks require HTTPS; HTTP is allowed only on this computer"
        )
    default_port = 443 if parsed.scheme == "https" else 80
    netloc = _url_host(host) if port in {None, default_port} else f"{_url_host(host)}:{port}"
    return urlunsplit((parsed.scheme, netloc, "", "", "")), local_http


def _validated_timeout(value: float) -> float:
    if not math.isfinite(value) or not MIN_TIMEOUT_SECONDS <= value <= MAX_TIMEOUT_SECONDS:
        raise StudioDeploymentCheckError(
            f"timeout seconds must be between {MIN_TIMEOUT_SECONDS} and {MAX_TIMEOUT_SECONDS}"
        )
    return value


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host.lower()


def _fetch_json(url: str, timeout_seconds: float) -> tuple[int, JsonMapping]:
    try:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "tracebisect-deployment-check/1",
            },
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                return response.status, _read_json_response(response, expected_url=url)
        except HTTPError as exc:
            return exc.code, _read_json_response(exc, expected_url=url)
    except (OSError, URLError, UnicodeError, ValueError) as exc:
        raise StudioDeploymentCheckError(
            f"could not reach {_endpoint_name(url)}; check the address, TLS, and network path"
        ) from exc


def _read_json_response(response: object, *, expected_url: str) -> JsonMapping:
    geturl = getattr(response, "geturl", None)
    final_url = str(geturl()) if callable(geturl) else expected_url
    if _origin(final_url) != _origin(expected_url):
        raise StudioDeploymentCheckError("a health endpoint redirected to a different origin")
    read = getattr(response, "read", None)
    if not callable(read):
        raise StudioDeploymentCheckError("a health endpoint returned an unreadable response")
    payload = cast(bytes, read(MAX_HEALTH_RESPONSE_BYTES + 1))
    if len(payload) > MAX_HEALTH_RESPONSE_BYTES:
        raise StudioDeploymentCheckError("a health endpoint returned too much data")
    try:
        decoded = json.loads(payload)
    except (JSONDecodeError, UnicodeDecodeError) as exc:
        raise StudioDeploymentCheckError("a health endpoint did not return valid JSON") from exc
    if not isinstance(decoded, dict):
        raise StudioDeploymentCheckError("a health endpoint returned an unexpected JSON shape")
    return cast(JsonMapping, decoded)


def _origin(url: str) -> tuple[str, str, int | None]:
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        default_port = 443 if scheme == "https" else 80 if scheme == "http" else None
        return scheme, (parsed.hostname or "").lower(), parsed.port or default_port
    except ValueError as exc:
        raise StudioDeploymentCheckError("a health endpoint returned an invalid redirect") from exc


def _endpoint_name(url: str) -> str:
    path = urlsplit(url).path
    return path if path in {"/api/ready", "/api/health"} else "the Studio health endpoint"


def _mapping(payload: JsonMapping, key: str) -> JsonMapping:
    value = payload.get(key)
    return cast(JsonMapping, value) if isinstance(value, dict) else {}


def _remaining_work(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ("Studio did not report its remaining production work.",)
    remaining: list[str] = []
    for item in value[:20]:
        if not isinstance(item, str) or not item.strip():
            continue
        normalized = " ".join(item.split())[:200]
        remaining.append(_BLOCKER_GUIDANCE.get(normalized, normalized.rstrip(".") + "."))
    return tuple(remaining)


def _safe_text(value: object, *, fallback: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    return " ".join(value.split())[:40]
