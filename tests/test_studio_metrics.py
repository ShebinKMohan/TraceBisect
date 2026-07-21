"""Tests for secret-safe, low-cardinality Studio service metrics."""

from __future__ import annotations

import pytest

from tracebisect.studio.metrics import StudioMetrics, StudioMetricsAccess
from tracebisect.studio.storage import StudioConfigurationError


def test_metrics_access_uses_a_dedicated_constant_time_bearer_token() -> None:
    token = "metrics-token-with-at-least-32-characters"
    access = StudioMetricsAccess.from_env({"TRACEBISECT_STUDIO_METRICS_TOKEN": token})

    assert access.token_configured is True
    assert access.access_mode(auth_required=True) == "bearer_token"
    assert access.authorizes(f"Bearer {token}", auth_required=True) is True
    assert access.authorizes(f"bearer {token}", auth_required=True) is True
    assert access.authorizes("Bearer wrong-token", auth_required=True) is False
    assert access.authorizes(None, auth_required=True) is False
    assert token not in repr(access)


def test_metrics_access_is_open_only_when_workspace_auth_is_not_required() -> None:
    access = StudioMetricsAccess.from_env({})

    assert access.access_mode(auth_required=False) == "open_local"
    assert access.authorizes(None, auth_required=False) is True
    assert access.access_mode(auth_required=True) == "unavailable"
    assert access.authorizes(None, auth_required=True) is False


@pytest.mark.parametrize("token", ["short", "contains spaces" + "x" * 32, "x" * 257])
def test_metrics_access_rejects_unsafe_tokens(token: str) -> None:
    with pytest.raises(StudioConfigurationError, match="32-256 URL-safe"):
        StudioMetricsAccess.from_env({"TRACEBISECT_STUDIO_METRICS_TOKEN": token})


def test_prometheus_metrics_are_bounded_and_secret_safe() -> None:
    now = [100.0]
    metrics = StudioMetrics(
        monotonic_clock=lambda: now[0],
        wall_clock=lambda: 1_700_000_000.0,
    )
    metrics.request_started()
    metrics.request_finished(
        method="GET",
        path="/api/runs/private-report-id",
        status_code=404,
        duration_seconds=0.12,
        auth_outcome="authenticated",
    )
    now[0] = 105.0

    output = metrics.render_prometheus(storage_ready=True)

    assert "tracebisect_studio_process_start_time_seconds 1700000000" in output
    assert "tracebisect_studio_process_uptime_seconds 5" in output
    assert "tracebisect_studio_http_requests_in_flight 0" in output
    assert "tracebisect_studio_storage_ready 1" in output
    assert "tracebisect_studio_storage_ready 0" in metrics.render_prometheus(storage_ready=False)
    assert (
        'tracebisect_studio_http_requests_total{action="run_read",result="client_error"} 1'
        in output
    )
    assert (
        'tracebisect_studio_http_request_duration_seconds_bucket{action="run_read",'
        'le="0.1"} 0' in output
    )
    assert (
        'tracebisect_studio_http_request_duration_seconds_bucket{action="run_read",'
        'le="0.25"} 1' in output
    )
    assert 'tracebisect_studio_auth_outcomes_total{outcome="authenticated"} 1' in output
    assert "private-report-id" not in output
    assert "workspace" not in output
    assert output.endswith("\n")
