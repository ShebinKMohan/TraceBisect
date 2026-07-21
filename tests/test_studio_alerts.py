"""Contract tests for the vendor-neutral Studio alert rules and runbook."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml

from tracebisect.studio.metrics import StudioMetrics

REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = REPO_ROOT / "deploy" / "prometheus" / "tracebisect-alerts.yml"
PROMETHEUS_CONFIG_PATH = REPO_ROOT / "deploy" / "prometheus" / "prometheus.yml"
COMPOSE_PATH = REPO_ROOT / "deploy" / "compose.production.yml"
RUNBOOK_PATH = REPO_ROOT / "docs" / "operations" / "studio-alert-runbook.md"

EXPECTED_ALERTS = {
    "TraceBisectStudioUnavailable": ("critical", "5m"),
    "TraceBisectStudioStorageUnavailable": ("critical", "2m"),
    "TraceBisectStudioServerErrorRateHigh": ("critical", "10m"),
    "TraceBisectStudioInteractiveLatencyHigh": ("warning", "10m"),
    "TraceBisectStudioWorkflowLatencyHigh": ("warning", "15m"),
    "TraceBisectStudioRateLimitingHigh": ("warning", "5m"),
    "TraceBisectStudioAuthenticationFailuresHigh": ("warning", "5m"),
}


def _rules() -> list[dict[str, Any]]:
    payload = cast(dict[str, Any], yaml.safe_load(RULES_PATH.read_text(encoding="utf-8")))
    groups = cast(list[dict[str, Any]], payload["groups"])
    assert len(groups) == 1
    assert groups[0]["name"] == "tracebisect-studio"
    assert groups[0]["interval"] == "30s"
    return cast(list[dict[str, Any]], groups[0]["rules"])


def _emitted_metric_names() -> set[str]:
    output = StudioMetrics(
        monotonic_clock=lambda: 0.0,
        wall_clock=lambda: 0.0,
    ).render_prometheus(storage_ready=True)
    metric_types = dict(re.findall(r"^# TYPE (\S+) (\S+)$", output, flags=re.MULTILINE))
    emitted = set(metric_types)
    for name, metric_type in metric_types.items():
        if metric_type == "histogram":
            emitted.update({f"{name}_bucket", f"{name}_count", f"{name}_sum"})
    return emitted


def test_alert_file_is_structured_actionable_and_linked_to_runbook() -> None:
    rules = _rules()
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

    assert {str(rule["alert"]) for rule in rules} == set(EXPECTED_ALERTS)
    for rule in rules:
        alert = str(rule["alert"])
        severity, pending_for = EXPECTED_ALERTS[alert]
        labels = cast(dict[str, Any], rule["labels"])
        annotations = cast(dict[str, Any], rule["annotations"])

        assert labels == {"severity": severity, "service": "tracebisect-studio"}
        assert rule["for"] == pending_for
        assert set(annotations) == {"summary", "description", "action", "runbook"}
        assert all(str(value).strip() for value in annotations.values())
        assert annotations["runbook"] == (
            f"docs/operations/studio-alert-runbook.md#{alert.lower()}"
        )
        assert f"## {alert}" in runbook


def test_alert_expressions_use_only_emitted_bounded_metrics() -> None:
    allowed_metrics = _emitted_metric_names()
    forbidden_dimensions = {
        "workspace_id",
        "request_id",
        "trace_id",
        "report_id",
        "case_id",
        "filename",
        "user_id",
    }

    for rule in _rules():
        expression = str(rule["expr"])
        metric_names = set(re.findall(r"\btracebisect_studio_[a-z0-9_]+\b", expression))
        assert metric_names <= allowed_metrics
        assert not any(dimension in expression for dimension in forbidden_dimensions)
        assert "TODO" not in expression


def test_alert_thresholds_have_noise_protection_and_match_service_targets() -> None:
    rules = {str(rule["alert"]): rule for rule in _rules()}

    assert rules["TraceBisectStudioUnavailable"]["expr"] == ('up{job="tracebisect-studio"} == 0')
    assert rules["TraceBisectStudioStorageUnavailable"]["expr"] == (
        "tracebisect_studio_storage_ready < 1"
    )

    server_errors = str(rules["TraceBisectStudioServerErrorRateHigh"]["expr"])
    assert 'result="server_error"' in server_errors
    assert "> 0.05" in server_errors
    assert ">= 20" in server_errors
    assert "sum by (action)" in server_errors

    interactive = str(rules["TraceBisectStudioInteractiveLatencyHigh"]["expr"])
    assert 'le="1"' in interactive
    assert "< 0.95" in interactive
    assert ">= 20" in interactive

    workflows = str(rules["TraceBisectStudioWorkflowLatencyHigh"]["expr"])
    assert 'le="5"' in workflows
    assert "< 0.95" in workflows
    assert ">= 10" in workflows

    rate_limits = str(rules["TraceBisectStudioRateLimitingHigh"]["expr"])
    assert 'result="rate_limited"' in rate_limits
    assert "> 20" in rate_limits

    auth_failures = str(rules["TraceBisectStudioAuthenticationFailuresHigh"]["expr"])
    assert 'outcome="rejected"' in auth_failures
    assert "> 25" in auth_failures


def test_runbook_is_beginner_safe_and_does_not_overclaim_an_sla() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

    assert "not a customer SLA" in runbook
    assert "Do not reuse a workspace key" in runbook
    assert "Do not delete, overwrite, or restore the database" in runbook
    assert "promtool check rules deploy/prometheus/tracebisect-alerts.yml" in runbook
    assert "credentials_file: /run/secrets/tracebisect_metrics_token" in runbook
    assert "credentials: " not in runbook
    assert "TODO" not in runbook


def test_bundled_prometheus_config_scrapes_only_the_private_api_with_a_secret_file() -> None:
    config = cast(
        dict[str, Any],
        yaml.safe_load(PROMETHEUS_CONFIG_PATH.read_text(encoding="utf-8")),
    )

    assert config["rule_files"] == ["/etc/prometheus/rules/tracebisect-alerts.yml"]
    jobs = cast(list[dict[str, Any]], config["scrape_configs"])
    assert len(jobs) == 1
    job = jobs[0]
    assert job["job_name"] == "tracebisect-studio"
    assert job["metrics_path"] == "/api/metrics"
    assert job["authorization"] == {
        "type": "Bearer",
        "credentials_file": "/run/secrets/tracebisect_metrics_token",
    }
    assert job["static_configs"] == [{"targets": ["api:8000"]}]
    assert "credentials" not in job["authorization"]


def test_single_node_compose_adds_private_bounded_monitoring() -> None:
    compose = cast(dict[str, Any], yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8")))
    services = cast(dict[str, dict[str, Any]], compose["services"])
    prometheus = services["prometheus"]

    assert prometheus["profiles"] == ["monitoring"]
    assert prometheus["image"] == "prom/prometheus:v3.12.0-distroless"
    assert prometheus["ports"] == ["127.0.0.1:9090:9090"]
    assert "--storage.tsdb.retention.time=30d" in prometheus["command"]
    assert "--storage.tsdb.retention.size=2GB" in prometheus["command"]
    assert prometheus["networks"] == ["backend"]
    assert prometheus["read_only"] is True
    assert prometheus["cap_drop"] == ["ALL"]
    assert services["api"]["environment"]["TRACEBISECT_STUDIO_METRICS_TOKEN_FILE"] == (
        "/run/secrets/tracebisect_metrics_token"
    )
    assert services["api"]["secrets"] == [
        {"source": "tracebisect_metrics_token", "target": "tracebisect_metrics_token"}
    ]


def test_single_node_compose_scans_uploads_on_a_private_clamav_service() -> None:
    compose = cast(dict[str, Any], yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8")))
    services = cast(dict[str, dict[str, Any]], compose["services"])
    api = services["api"]
    clamav = services["clamav"]

    assert api["environment"]["TRACEBISECT_STUDIO_UPLOAD_SCANNER"] == "clamav"
    assert api["environment"]["TRACEBISECT_STUDIO_CLAMAV_HOST"] == "clamav"
    assert api["environment"]["TRACEBISECT_STUDIO_CLAMAV_PORT"] == "3310"
    assert api["depends_on"]["clamav"] == {"condition": "service_healthy"}
    assert clamav["image"] == "clamav/clamav:1.5.2"
    assert clamav["networks"] == ["backend", "egress"]
    assert clamav["volumes"] == ["clamav-data:/var/lib/clamav"]
    assert "ports" not in clamav
