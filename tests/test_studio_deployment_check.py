"""Tests for the beginner-readable live Studio deployment check."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

import tracebisect.studio.deployment_check as deployment_check
from tracebisect.cli import build_parser, main
from tracebisect.studio.deployment_check import (
    StudioDeploymentCheckError,
    check_studio_deployment,
)


def _ready_payload() -> dict[str, object]:
    return {"ready": True, "checks": {"storage": "ok", "upload_scanner": "ok"}}


def _hosted_health_payload() -> dict[str, object]:
    return {
        "ok": True,
        "product": "TraceBisect Studio",
        "runtime": {"kind": "postgres", "durable": True},
        "auth": {
            "required": True,
            "browser_sessions": True,
            "browser_session_cookie_secure": True,
        },
        "uploads": {
            "enabled": True,
            "ready": True,
            "fail_closed": True,
            "scan_before_parse": True,
        },
        "audit": {"enabled": True},
        "errors": {
            "enabled": True,
            "includes_exception_messages": False,
            "request_id_join": True,
            "durable_retention": True,
        },
        "metrics": {"access": "dedicated_token"},
        "rate_limiting": {
            "algorithm": "sliding_window",
            "stores_raw_client_keys": False,
        },
        "readiness": {
            "production_saas_ready": False,
            "blockers": [
                "scheduled encrypted off-site backups and recovery drills",
                "hosted deployment observability",
            ],
        },
    }


def _fetcher(
    ready: Mapping[str, object],
    health: Mapping[str, object],
    *,
    ready_status: int = 200,
    health_status: int = 200,
) -> deployment_check.EndpointFetcher:
    def fetch(url: str, _timeout: float) -> tuple[int, Mapping[str, object]]:
        if url.endswith("/api/ready"):
            return ready_status, ready
        if url.endswith("/api/health"):
            return health_status, health
        raise AssertionError(f"unexpected URL {url}")

    return fetch


def test_deployment_check_proves_the_hosted_core_without_overclaiming_saas() -> None:
    report = check_studio_deployment(
        "https://studio.example.com/",
        fetcher=_fetcher(_ready_payload(), _hosted_health_payload()),
    )

    assert report.base_url == "https://studio.example.com"
    assert report.hosted_core_ready is True
    assert report.full_saas_ready is False
    assert {check.key: check.status for check in report.checks} == {
        "connection": "pass",
        "api": "pass",
        "storage": "pass",
        "access": "pass",
        "browser_sessions": "pass",
        "uploads": "pass",
        "audit": "pass",
        "errors": "pass",
        "metrics": "pass",
        "rate_limits": "pass",
    }
    assert report.remaining_saas_work == (
        "Schedule encrypted off-site backups and practise restoring them.",
        "Send metrics, alerts, and operational history to managed off-host services.",
    )


def test_deployment_check_reports_full_saas_only_when_the_api_proves_it() -> None:
    health = _hosted_health_payload()
    health["readiness"] = {"production_saas_ready": True, "blockers": []}

    report = check_studio_deployment(
        "https://studio.example.com",
        fetcher=_fetcher(_ready_payload(), health),
    )

    assert report.hosted_core_ready is True
    assert report.full_saas_ready is True
    assert report.remaining_saas_work == ()


def test_deployment_check_does_not_hide_an_unnamed_saas_gap() -> None:
    health = _hosted_health_payload()
    health["readiness"] = {"production_saas_ready": False, "blockers": []}

    report = check_studio_deployment(
        "https://studio.example.com",
        fetcher=_fetcher(_ready_payload(), health),
    )

    assert report.hosted_core_ready is True
    assert report.full_saas_ready is False
    assert report.remaining_saas_work == (
        "Studio reports that full SaaS readiness is incomplete but did not name "
        "the remaining work.",
    )


def test_deployment_check_explains_every_unsafe_local_default() -> None:
    health = _hosted_health_payload()
    health.update(
        {
            "runtime": {"kind": "memory", "durable": False},
            "auth": {
                "required": False,
                "browser_sessions": False,
                "browser_session_cookie_secure": False,
            },
            "uploads": {
                "enabled": False,
                "ready": True,
                "fail_closed": False,
                "scan_before_parse": False,
            },
            "metrics": {"access": "open_local"},
        }
    )

    report = check_studio_deployment(
        "http://127.0.0.1:8000",
        fetcher=_fetcher(_ready_payload(), health),
    )

    statuses = {check.key: check.status for check in report.checks}
    assert report.hosted_core_ready is False
    assert statuses["connection"] == "warning"
    assert statuses["storage"] == "fail"
    assert statuses["access"] == "fail"
    assert statuses["browser_sessions"] == "fail"
    assert statuses["uploads"] == "fail"
    assert statuses["metrics"] == "fail"


@pytest.mark.parametrize(
    "url",
    [
        "http://studio.example.com",
        "https://user:secret@studio.example.com",
        "https://studio.example.com/private/path",
        "https://studio.example.com?token=secret",
        "https://studio.exa mple.com",
        "https://stúdio.example.com",
        "not-a-url",
    ],
)
def test_deployment_check_rejects_unsafe_or_ambiguous_urls(url: str) -> None:
    with pytest.raises(StudioDeploymentCheckError):
        check_studio_deployment(
            url,
            fetcher=_fetcher(_ready_payload(), _hosted_health_payload()),
        )


@pytest.mark.parametrize("timeout", [0.0, 31.0, float("inf"), float("nan")])
def test_deployment_check_rejects_unsafe_timeouts(timeout: float) -> None:
    with pytest.raises(StudioDeploymentCheckError, match="timeout seconds"):
        check_studio_deployment(
            "https://studio.example.com",
            timeout_seconds=timeout,
            fetcher=_fetcher(_ready_payload(), _hosted_health_payload()),
        )


def test_deployment_check_fails_when_readiness_or_health_is_not_healthy() -> None:
    ready = {"ready": False, "checks": {"storage": "unavailable"}}
    health = _hosted_health_payload()
    health["ok"] = False

    report = check_studio_deployment(
        "https://studio.example.com",
        fetcher=_fetcher(ready, health, ready_status=503),
    )

    api_check = next(check for check in report.checks if check.key == "api")
    assert report.hosted_core_ready is False
    assert api_check.status == "fail"
    assert "Keep Studio out of user traffic" in api_check.detail


def test_deployment_check_rejects_a_different_product_health_shape() -> None:
    health = _hosted_health_payload()
    health["product"] = "Different service"

    report = check_studio_deployment(
        "https://studio.example.com",
        fetcher=_fetcher(_ready_payload(), health),
    )

    api_check = next(check for check in report.checks if check.key == "api")
    assert report.hosted_core_ready is False
    assert api_check.status == "fail"


class _FakeResponse:
    def __init__(self, payload: bytes, final_url: str) -> None:
        self.payload = payload
        self.final_url = final_url

    def geturl(self) -> str:
        return self.final_url

    def read(self, size: int) -> bytes:
        return self.payload[:size]


def test_health_response_reader_rejects_cross_origin_redirects_and_unsafe_data() -> None:
    with pytest.raises(StudioDeploymentCheckError, match="different origin"):
        deployment_check._read_json_response(
            _FakeResponse(b"{}", "https://other.example.com/api/health"),
            expected_url="https://studio.example.com/api/health",
        )

    with pytest.raises(StudioDeploymentCheckError, match="too much data"):
        deployment_check._read_json_response(
            _FakeResponse(
                b"x" * (deployment_check.MAX_HEALTH_RESPONSE_BYTES + 1),
                "https://studio.example.com/api/health",
            ),
            expected_url="https://studio.example.com/api/health",
        )

    with pytest.raises(StudioDeploymentCheckError, match="valid JSON"):
        deployment_check._read_json_response(
            _FakeResponse(b"not-json", "https://studio.example.com/api/health"),
            expected_url="https://studio.example.com/api/health",
        )


def test_health_response_reader_allows_an_explicit_default_tls_port() -> None:
    payload = deployment_check._read_json_response(
        _FakeResponse(b'{"ok": true}', "https://studio.example.com:443/api/health"),
        expected_url="https://studio.example.com/api/health",
    )

    assert payload == {"ok": True}


def test_deployment_check_cli_prints_plain_result_and_one_next_action(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        deployment_check,
        "_fetch_json",
        _fetcher(_ready_payload(), _hosted_health_payload()),
    )

    assert (
        main(
            [
                "studio",
                "deployment-check",
                "--url",
                "https://studio.example.com",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Studio deployment check" in output
    assert "Hosted core: READY" in output
    assert "Full SaaS: NOT READY" in output
    assert "[PASS] Uploads fail closed" in output
    assert output.count("Next:") == 1
    assert "Schedule encrypted off-site backups" in output


def test_deployment_check_cli_returns_failure_for_an_unsafe_hosted_core(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    health = _hosted_health_payload()
    health["runtime"] = {"kind": "memory", "durable": False}
    monkeypatch.setattr(
        deployment_check,
        "_fetch_json",
        _fetcher(_ready_payload(), health),
    )

    assert (
        main(
            [
                "studio",
                "deployment-check",
                "--url",
                "https://studio.example.com",
            ]
        )
        == 2
    )
    output = capsys.readouterr().out
    assert "Hosted core: NOT READY" in output
    assert "[FAIL] Saved work survives restarts" in output
    assert "Next: Studio is using temporary memory storage" in output


def test_deployment_check_cli_reports_configuration_errors_without_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "studio",
                "deployment-check",
                "--url",
                "http://public.example.com",
            ]
        )
        == 2
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "tracebisect studio deployment-check failed" in captured.err
    assert "public Studio checks require HTTPS" in captured.err
    assert "Traceback" not in captured.err


def test_studio_parser_exposes_the_deployment_check() -> None:
    args = build_parser().parse_args(
        [
            "studio",
            "deployment-check",
            "--url",
            "https://studio.example.com",
            "--timeout-seconds",
            "3",
        ]
    )

    assert args.studio_command == "deployment-check"
    assert args.url == "https://studio.example.com"
    assert args.timeout_seconds == 3.0
