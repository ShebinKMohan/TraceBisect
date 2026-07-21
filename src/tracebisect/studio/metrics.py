"""Bounded, secret-safe Prometheus metrics for TraceBisect Studio."""

from __future__ import annotations

import os
import re
import secrets
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from tracebisect.studio.audit import AuditAuthOutcome, request_action, request_result
from tracebisect.studio.storage import StudioConfigurationError

METRICS_TOKEN_ENV = "TRACEBISECT_STUDIO_METRICS_TOKEN"
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
HTTP_DURATION_BUCKETS_SECONDS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{32,256}$")


def generate_metrics_token() -> str:
    """Generate a high-entropy token suitable for one monitoring scraper."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True, slots=True)
class StudioMetricsAccess:
    """Authorize monitoring scrapes without granting workspace data access."""

    token: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> StudioMetricsAccess:
        values = os.environ if env is None else env
        token = values.get(METRICS_TOKEN_ENV, "").strip() or None
        if token is not None and _TOKEN_PATTERN.fullmatch(token) is None:
            raise StudioConfigurationError(
                f"{METRICS_TOKEN_ENV} must contain 32-256 URL-safe characters"
            )
        return cls(token=token)

    @property
    def token_configured(self) -> bool:
        return self.token is not None

    def access_mode(self, *, auth_required: bool) -> str:
        if self.token is not None:
            return "bearer_token"
        if auth_required:
            return "unavailable"
        return "open_local"

    def authorizes(self, authorization: str | None, *, auth_required: bool) -> bool:
        """Allow open-local scrapes or require the dedicated operations token."""
        if self.token is None:
            return not auth_required
        if authorization is None:
            return False
        scheme, separator, candidate = authorization.partition(" ")
        if separator != " " or scheme.casefold() != "bearer" or not candidate:
            return False
        return secrets.compare_digest(candidate, self.token)


class StudioMetrics:
    """Collect low-cardinality HTTP counters and a classic latency histogram."""

    def __init__(
        self,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._clock = monotonic_clock
        self._lock = threading.Lock()
        self._started_monotonic = monotonic_clock()
        self._started_at_unix = wall_clock()
        self._requests: Counter[tuple[str, str]] = Counter()
        self._duration_buckets: Counter[tuple[str, float]] = Counter()
        self._duration_count: Counter[str] = Counter()
        self._duration_sum: dict[str, float] = {}
        self._auth_outcomes: Counter[str] = Counter()
        self._in_flight = 0

    def request_started(self) -> None:
        with self._lock:
            self._in_flight += 1

    def request_finished(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
        auth_outcome: AuditAuthOutcome,
    ) -> None:
        action = request_action(method, path)
        result = request_result(status_code)
        duration = max(0.0, duration_seconds)
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            self._requests[(action, result)] += 1
            self._duration_count[action] += 1
            self._duration_sum[action] = self._duration_sum.get(action, 0.0) + duration
            for bucket in HTTP_DURATION_BUCKETS_SECONDS:
                if duration <= bucket:
                    self._duration_buckets[(action, bucket)] += 1
            self._auth_outcomes[auth_outcome] += 1

    def render_prometheus(self, *, storage_ready: bool) -> str:
        """Render Prometheus text format 0.0.4 with deterministic ordering."""
        with self._lock:
            requests = self._requests.copy()
            duration_buckets = self._duration_buckets.copy()
            duration_count = self._duration_count.copy()
            duration_sum = self._duration_sum.copy()
            auth_outcomes = self._auth_outcomes.copy()
            in_flight = self._in_flight
            started_at_unix = self._started_at_unix
            uptime = max(0.0, self._clock() - self._started_monotonic)

        lines = [
            (
                "# HELP tracebisect_studio_process_start_time_seconds "
                "Unix time when this API process started."
            ),
            "# TYPE tracebisect_studio_process_start_time_seconds gauge",
            f"tracebisect_studio_process_start_time_seconds {_metric_number(started_at_unix)}",
            (
                "# HELP tracebisect_studio_process_uptime_seconds "
                "Seconds this API process has been running."
            ),
            "# TYPE tracebisect_studio_process_uptime_seconds gauge",
            f"tracebisect_studio_process_uptime_seconds {_metric_number(uptime)}",
            (
                "# HELP tracebisect_studio_http_requests_in_flight "
                "Requests currently being handled by this process."
            ),
            "# TYPE tracebisect_studio_http_requests_in_flight gauge",
            f"tracebisect_studio_http_requests_in_flight {in_flight}",
            "# HELP tracebisect_studio_storage_ready Whether configured storage is reachable.",
            "# TYPE tracebisect_studio_storage_ready gauge",
            f"tracebisect_studio_storage_ready {1 if storage_ready else 0}",
            (
                "# HELP tracebisect_studio_http_requests_total "
                "Completed HTTP requests by bounded action and result."
            ),
            "# TYPE tracebisect_studio_http_requests_total counter",
        ]
        for (action, result), count in sorted(requests.items()):
            lines.append(
                "tracebisect_studio_http_requests_total"
                f'{{action="{_label(action)}",result="{_label(result)}"}} {count}'
            )

        lines.extend(
            [
                (
                    "# HELP tracebisect_studio_http_request_duration_seconds "
                    "Request latency by bounded action."
                ),
                "# TYPE tracebisect_studio_http_request_duration_seconds histogram",
            ]
        )
        for action in sorted(duration_count):
            for bucket in HTTP_DURATION_BUCKETS_SECONDS:
                lines.append(
                    "tracebisect_studio_http_request_duration_seconds_bucket"
                    f'{{action="{_label(action)}",le="{_metric_number(bucket)}"}} '
                    f"{duration_buckets[(action, bucket)]}"
                )
            lines.append(
                "tracebisect_studio_http_request_duration_seconds_bucket"
                f'{{action="{_label(action)}",le="+Inf"}} {duration_count[action]}'
            )
            lines.append(
                "tracebisect_studio_http_request_duration_seconds_sum"
                f'{{action="{_label(action)}"}} {_metric_number(duration_sum[action])}'
            )
            lines.append(
                "tracebisect_studio_http_request_duration_seconds_count"
                f'{{action="{_label(action)}"}} {duration_count[action]}'
            )

        lines.extend(
            [
                (
                    "# HELP tracebisect_studio_auth_outcomes_total "
                    "Requests by bounded authentication outcome."
                ),
                "# TYPE tracebisect_studio_auth_outcomes_total counter",
            ]
        )
        for outcome, count in sorted(auth_outcomes.items()):
            lines.append(
                f'tracebisect_studio_auth_outcomes_total{{outcome="{_label(outcome)}"}} {count}'
            )
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """Clear observations while preserving process start metadata for tests."""
        with self._lock:
            self._requests.clear()
            self._duration_buckets.clear()
            self._duration_count.clear()
            self._duration_sum.clear()
            self._auth_outcomes.clear()
            self._in_flight = 0


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _metric_number(value: float) -> str:
    return format(value, ".12g")
