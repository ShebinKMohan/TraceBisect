from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import tracebisect.studio.api as studio_api
from tracebisect.studio.api import RATE_LIMITER, STORE, app
from tracebisect.studio.audit import AUDIT_LOGGER_NAME
from tracebisect.studio.service import build_demo_report, load_trace_from_path
from tracebisect.studio.upload_scanner import (
    StudioUploadScannerUnavailable,
    StudioUploadThreatDetected,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / "tests" / "fixtures" / "refund_search_baseline.tbtrace"
CANDIDATE = REPO_ROOT / "tests" / "fixtures" / "refund_search_candidate_changed_tool_args.tbtrace"


def reset_studio_state() -> None:
    STORE.clear()
    asyncio.run(RATE_LIMITER.reset())
    studio_api.METRICS.reset()


class _StubUploadScanner:
    def __init__(self, *, ready: bool = True, error: Exception | None = None) -> None:
        self.enabled = True
        self.ready = ready
        self.error = error
        self.scanned: list[bytes] = []

    def scan(self, content: bytes) -> None:
        self.scanned.append(content)
        if self.error is not None:
            raise self.error

    def check_health(self) -> bool:
        return self.ready

    def runtime_status(self, *, ready: bool) -> dict[str, str | bool]:
        return {
            "enabled": True,
            "provider": "clamav",
            "ready": ready,
            "fail_closed": True,
            "scan_before_parse": True,
        }


def test_build_demo_report_contains_real_first_divergence() -> None:
    report = build_demo_report()

    assert report["divergence_count"] == 3
    first = report["first_divergence"]
    assert isinstance(first, dict)
    assert first["type"] == "changed_tool_args"
    assert first["severity"] == "CRITICAL"
    assert first["expected"] == {"query": "users WHERE active = true"}
    assert first["actual"] == {"query": "users WHERE active = true AND deleted = false"}
    assert "assert_aligned" in str(report["pytest"])


def test_studio_api_serves_demo_report() -> None:
    reset_studio_state()
    client = TestClient(app)

    response = client.get("/api/demo-report")

    assert response.status_code == 200
    payload = response.json()
    assert payload["baseline"]["display_name"] == "Refund baseline"
    assert payload["candidate"]["display_name"] == "Refund regression"
    assert payload["first_divergence"]["type"] == "changed_tool_args"
    assert payload["report_id"] in STORE.reports
    assert payload["baseline"]["id"] in {trace["id"] for trace in STORE.list_traces()}
    assert payload["candidate"]["id"] in {trace["id"] for trace in STORE.list_traces()}

    second_response = client.get("/api/demo-report")

    assert second_response.status_code == 200
    assert len(STORE.list_traces()) == 2
    assert len(STORE.list_report_summaries()) == 1


def test_open_local_session_has_admin_access() -> None:
    reset_studio_state()
    client = TestClient(app)

    response = client.get("/api/session")

    assert response.status_code == 200
    assert response.json()["authenticated"] is False
    assert response.json()["role"] == "admin"


def test_studio_api_lists_demo_run_history_without_duplicates() -> None:
    reset_studio_state()
    client = TestClient(app)

    demo = client.get("/api/demo-report").json()
    second_demo = client.get("/api/demo-report").json()
    response = client.get("/api/runs")

    assert demo["report_id"] == second_demo["report_id"]
    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == {"limit": 50, "next_cursor": None}
    assert payload["runs"] == [
        {
            "report_id": demo["report_id"],
            "created_at": demo["created_at"],
            "baseline": {
                "id": demo["baseline"]["id"],
                "display_name": "Refund baseline",
                "source_convention": "native",
                "event_count": 4,
            },
            "candidate": {
                "id": demo["candidate"]["id"],
                "display_name": "Refund regression",
                "source_convention": "native",
                "event_count": 4,
            },
            "source_convention": "native",
            "divergence_count": 3,
            "status": "failing",
            "severity": "CRITICAL",
            "first_divergence_type": "changed_tool_args",
            "event_count": 8,
        }
    ]


def test_studio_api_gets_full_run_report() -> None:
    reset_studio_state()
    client = TestClient(app)

    demo = client.get("/api/demo-report").json()
    response = client.get(f"/api/runs/{demo['report_id']}")

    assert response.status_code == 200
    assert response.json() == demo


def test_studio_api_uploads_and_compares_tbtrace_files() -> None:
    reset_studio_state()
    client = TestClient(app)

    with BASELINE.open("rb") as fh:
        baseline_response = client.post(
            "/api/traces/upload",
            files={"file": ("baseline.tbtrace", fh, "application/octet-stream")},
        )
    with CANDIDATE.open("rb") as fh:
        candidate_response = client.post(
            "/api/traces/upload",
            files={"file": ("candidate.tbtrace", fh, "application/octet-stream")},
        )

    assert baseline_response.status_code == 200
    assert candidate_response.status_code == 200
    baseline_id = baseline_response.json()["trace"]["id"]
    candidate_id = candidate_response.json()["trace"]["id"]

    compare_response = client.post(
        "/api/compare",
        json={
            "baseline_trace_id": baseline_id,
            "candidate_trace_id": candidate_id,
            "scenario_cmd": ["python", "examples/refund_agent.py", "--case", "refund_042"],
        },
    )

    assert compare_response.status_code == 200
    report = compare_response.json()
    assert report["first_divergence"]["type"] == "changed_tool_args"
    assert report["baseline"]["display_name"] == "baseline.tbtrace"
    assert report["candidate"]["display_name"] == "candidate.tbtrace"

    runs_response = client.get("/api/runs")

    assert runs_response.status_code == 200
    runs = runs_response.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["report_id"] == report["report_id"]
    assert runs[0]["status"] == "failing"
    assert runs[0]["first_divergence_type"] == "changed_tool_args"


def test_studio_api_filters_run_history() -> None:
    reset_studio_state()
    client = TestClient(app)

    demo = client.get("/api/demo-report").json()
    passing_candidate_id = STORE.add_trace(
        replace(load_trace_from_path(BASELINE), trace_id="trc_refund_baseline_copy"),
        name="Passing candidate",
    )
    passing_response = client.post(
        "/api/compare",
        json={
            "baseline_trace_id": demo["baseline"]["id"],
            "candidate_trace_id": passing_candidate_id,
        },
    )
    passing_report = passing_response.json()

    passing_runs = client.get("/api/runs?status=passing").json()["runs"]
    failing_runs = client.get("/api/runs?status=failing").json()["runs"]
    query_runs = client.get("/api/runs?q=passing%20candidate").json()["runs"]
    severity_runs = client.get("/api/runs?severity=CRITICAL").json()["runs"]
    source_runs = client.get("/api/runs?source_convention=native").json()["runs"]
    type_runs = client.get("/api/runs?divergence_type=changed_tool_args").json()["runs"]

    assert [item["report_id"] for item in passing_runs] == [passing_report["report_id"]]
    assert [item["report_id"] for item in failing_runs] == [demo["report_id"]]
    assert [item["report_id"] for item in query_runs] == [passing_report["report_id"]]
    assert [item["report_id"] for item in severity_runs] == [demo["report_id"]]
    assert {item["report_id"] for item in source_runs} == {
        demo["report_id"],
        passing_report["report_id"],
    }
    assert [item["report_id"] for item in type_runs] == [demo["report_id"]]


def test_studio_api_run_history_rejects_invalid_filters() -> None:
    reset_studio_state()
    client = TestClient(app)

    assert client.get("/api/runs?status=unknown").status_code == 400
    assert client.get("/api/runs?severity=URGENT").status_code == 400
    assert client.get("/api/runs?limit=0").status_code == 400


def test_studio_api_get_run_history_rejects_unknown_report() -> None:
    reset_studio_state()
    client = TestClient(app)

    response = client.get("/api/runs/rpt_missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "unknown run id: rpt_missing"


def test_studio_api_replaces_duplicate_trace_uploads() -> None:
    reset_studio_state()
    client = TestClient(app)

    with BASELINE.open("rb") as fh:
        first_response = client.post(
            "/api/traces/upload",
            files={"file": ("first-baseline.tbtrace", fh, "application/octet-stream")},
        )
    with BASELINE.open("rb") as fh:
        second_response = client.post(
            "/api/traces/upload",
            files={"file": ("second-baseline.tbtrace", fh, "application/octet-stream")},
        )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["trace"]["id"] == second_response.json()["trace"]["id"]

    list_response = client.get("/api/traces")

    assert list_response.status_code == 200
    assert list_response.json()["traces"] == [
        {
            "id": "trc_refund_baseline",
            "trace_id": "trc_refund_baseline",
            "display_name": "second-baseline.tbtrace",
            "source_convention": "native",
            "created_at": "2026-05-11T06:52:01Z",
            "event_count": 4,
            "root_event": "run",
            "status": "success",
            "duration_ms": 1000.0,
            "total_input_tokens": 84,
            "total_output_tokens": 22,
            "total_tokens": 106,
            "total_cost_usd": 0.00021,
            "model": "gpt-4o-mini",
            "model_version": "gpt-4o-2024-08-06",
            "prompt_version": "v3",
            "code_sha": "a30ecca",
            "agent_name": "refund-support-bot",
            "agent_version": "1.4.0",
            "session_id": "refund_042",
        }
    ]


def test_studio_api_sets_security_headers(caplog: pytest.LogCaptureFixture) -> None:
    reset_studio_state()
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    client = TestClient(app)

    response = client.get("/api/health", headers={"X-Request-ID": "request-1234"})

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"] == "request-1234"
    assert response.json()["limits"]["max_upload_bytes"] == studio_api.MAX_UPLOAD_BYTES
    assert (
        response.json()["limits"]["max_active_workspace_keys"]
        == studio_api.MAX_ACTIVE_STUDIO_API_KEYS_PER_WORKSPACE
    )
    assert (
        response.json()["limits"]["max_active_ingestion_tokens"]
        == studio_api.MAX_ACTIVE_INGESTION_TOKENS_PER_WORKSPACE
    )
    assert response.json()["limits"]["max_stored_error_events"] == 0
    assert response.json()["limits"]["max_stored_error_events_per_workspace"] == 0
    assert response.json()["audit"] == {
        "enabled": True,
        "format": "json",
        "request_id_header": "X-Request-ID",
    }
    assert response.json()["uploads"] == {
        "enabled": False,
        "provider": "none",
        "ready": True,
        "fail_closed": False,
        "scan_before_parse": False,
    }
    assert response.json()["rate_limiting"] == {
        "kind": "memory",
        "distributed": False,
        "algorithm": "sliding_window",
        "stores_raw_client_keys": False,
    }
    assert response.json()["runtime"] == {
        "kind": "memory",
        "durable": False,
        "workspace_id": "local",
        "trace_count": 0,
        "report_count": 0,
        "case_count": 0,
    }
    assert response.json()["readiness"]["production_saas_ready"] is False
    assert "restart-safe durable storage" in response.json()["readiness"]["blockers"]
    assert (
        "distributed rate limiting across API replicas"
        in response.json()["readiness"]["blockers"]
    )
    assert (
        "verified local backup and non-destructive restore tooling"
        not in response.json()["readiness"]["completed"]
    )
    audit_payload = json.loads(caplog.records[-1].message)
    assert audit_payload["request_id"] == "request-1234"
    assert audit_payload["action"] == "health_check"
    assert audit_payload["auth_outcome"] == "not_required"


def test_studio_api_readiness_checks_storage() -> None:
    reset_studio_state()
    client = TestClient(app)

    response = client.get("/api/ready")

    assert response.status_code == 200
    assert response.json() == {"ready": True, "checks": {"storage": "ok"}}


def test_studio_health_fails_when_storage_status_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_studio_state()

    def unavailable_runtime_status(_store: object) -> dict[str, object]:
        raise studio_api.StudioPersistenceError("private database detail")

    monkeypatch.setattr(type(STORE), "runtime_status", unavailable_runtime_status)
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["runtime"]["trace_count"] == 0
    assert response.json()["readiness"]["api_ready"] is False
    assert "private database detail" not in response.text


def test_open_local_metrics_endpoint_exposes_prometheus_contract() -> None:
    reset_studio_state()
    client = TestClient(app)

    health_response = client.get("/api/health")
    response = client.get("/api/metrics")

    assert health_response.json()["metrics"] == {
        "format": "prometheus_text_0.0.4",
        "path": "/api/metrics",
        "access": "open_local",
        "scope": "process",
        "resets_on_restart": True,
    }
    assert response.status_code == 200
    assert response.headers["content-type"] == studio_api.PROMETHEUS_CONTENT_TYPE
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "# TYPE tracebisect_studio_http_requests_total counter" in response.text
    assert "tracebisect_studio_storage_ready 1" in response.text
    assert (
        'tracebisect_studio_http_requests_total{action="health_check",result="success"} 1'
        in response.text
    )
    assert "workspace_id" not in response.text
    assert "request_id" not in response.text


def test_studio_api_rejects_unsupported_upload_extension() -> None:
    reset_studio_state()
    client = TestClient(app)

    response = client.post(
        "/api/traces/upload",
        files={"file": ("malicious.html", BytesIO(b"<script>alert(1)</script>"), "text/html")},
    )

    assert response.status_code == 415
    assert (
        response.json()["detail"] == "Unsupported trace file type. Upload a .tbtrace or .json file."
    )


def test_studio_api_rejects_empty_upload() -> None:
    reset_studio_state()
    client = TestClient(app)

    response = client.post(
        "/api/traces/upload",
        files={"file": ("empty.tbtrace", BytesIO(b""), "application/octet-stream")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Uploaded trace file is empty."


def test_studio_api_rejects_oversized_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_studio_state()
    monkeypatch.setattr(studio_api, "MAX_UPLOAD_BYTES", 16)
    client = TestClient(app)

    response = client.post(
        "/api/traces/upload",
        files={"file": ("large.tbtrace", BytesIO(b"x" * 17), "application/octet-stream")},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Trace upload is too large. Maximum size is 16 bytes."


def test_studio_api_scans_upload_before_parsing_or_storing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_studio_state()
    scanner = _StubUploadScanner()
    monkeypatch.setattr(studio_api, "UPLOAD_SCANNER", scanner)
    client = TestClient(app)
    content = BASELINE.read_bytes()

    response = client.post(
        "/api/traces/upload",
        files={"file": ("baseline.tbtrace", BytesIO(content), "application/octet-stream")},
    )

    assert response.status_code == 200
    assert scanner.scanned == [content]
    assert len(STORE.list_traces()) == 1


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (
            StudioUploadThreatDetected("do not expose a signature"),
            422,
            "This file was rejected by Studio's security scan. No trace was stored.",
        ),
        (
            StudioUploadScannerUnavailable("private host details"),
            503,
            (
                "Upload security scanning is temporarily unavailable. "
                "No trace was stored. Please retry."
            ),
        ),
    ],
)
def test_studio_api_fails_closed_when_upload_scan_is_not_clean(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status_code: int,
    detail: str,
) -> None:
    reset_studio_state()
    scanner = _StubUploadScanner(error=error)
    monkeypatch.setattr(studio_api, "UPLOAD_SCANNER", scanner)
    client = TestClient(app)

    with BASELINE.open("rb") as baseline:
        response = client.post(
            "/api/traces/upload",
            files={"file": ("baseline.tbtrace", baseline, "application/octet-stream")},
        )

    assert response.status_code == status_code
    assert response.json()["detail"] == detail
    assert len(scanner.scanned) == 1
    assert STORE.list_traces() == []


def test_studio_readiness_fails_when_required_upload_scanner_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_studio_state()
    scanner = _StubUploadScanner(ready=False)
    monkeypatch.setattr(studio_api, "UPLOAD_SCANNER", scanner)
    client = TestClient(app)

    ready = client.get("/api/ready")
    health = client.get("/api/health")

    assert ready.status_code == 503
    assert ready.json() == {
        "ready": False,
        "checks": {"storage": "ok", "upload_scanner": "unavailable"},
    }
    assert health.status_code == 200
    assert health.json()["ok"] is False
    assert health.json()["uploads"] == scanner.runtime_status(ready=False)
    assert health.json()["readiness"]["api_ready"] is False
    assert (
        "available malware scanner for untrusted trace uploads"
        in health.json()["readiness"]["blockers"]
    )


def test_studio_api_rejects_upload_when_store_is_full(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_studio_state()
    monkeypatch.setattr(STORE, "max_traces", 0)
    client = TestClient(app)

    with BASELINE.open("rb") as fh:
        response = client.post(
            "/api/traces/upload",
            files={"file": ("baseline.tbtrace", fh, "application/octet-stream")},
        )

    assert response.status_code == 507
    assert (
        response.json()["detail"]
        == "trace store is full; increase TRACEBISECT_STUDIO_MAX_STORED_TRACES"
    )


def test_studio_api_rate_limits_repeated_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_studio_state()
    monkeypatch.setattr(studio_api, "RATE_LIMIT_REQUESTS", 2)
    monkeypatch.setattr(studio_api, "RATE_LIMIT_WINDOW_SECONDS", 60)
    client = TestClient(app)

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health").status_code == 200
    response = client.get("/api/health")

    assert response.status_code == 429
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["retry-after"]
    assert response.json()["detail"] == "Too many requests. Please wait before retrying."


def test_studio_api_random_paths_cannot_bypass_the_shared_action_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_studio_state()
    monkeypatch.setattr(studio_api, "RATE_LIMIT_REQUESTS", 2)
    monkeypatch.setattr(studio_api, "RATE_LIMIT_WINDOW_SECONDS", 60)
    client = TestClient(app)

    assert client.get("/api/missing-one").status_code == 404
    assert client.get("/api/missing-two").status_code == 404
    response = client.get("/api/missing-three")

    assert response.status_code == 429
    assert response.json()["detail"] == "Too many requests. Please wait before retrying."


def test_studio_api_fails_closed_when_request_protection_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_studio_state()

    class _UnavailableLimiter:
        async def allow(
            self,
            _key: str,
            *,
            limit: int,
            window_seconds: int,
        ) -> tuple[bool, int]:
            assert limit > 0
            assert window_seconds > 0
            raise studio_api.StudioPersistenceError("private database detail")

    monkeypatch.setattr(studio_api, "RATE_LIMITER", _UnavailableLimiter())
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.json() == {
        "detail": "Request protection is temporarily unavailable. Please retry."
    }
    assert "private database detail" not in response.text


def test_studio_api_rejects_same_trace_compare() -> None:
    reset_studio_state()
    client = TestClient(app)
    with BASELINE.open("rb") as fh:
        upload_response = client.post(
            "/api/traces/upload",
            files={"file": ("baseline.tbtrace", fh, "application/octet-stream")},
        )
    trace_id = upload_response.json()["trace"]["id"]

    response = client.post(
        "/api/compare",
        json={
            "baseline_trace_id": trace_id,
            "candidate_trace_id": trace_id,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Baseline and candidate traces must be different."


def test_studio_api_rejects_large_scenario_command() -> None:
    reset_studio_state()
    client = TestClient(app)

    response = client.post(
        "/api/compare",
        json={
            "baseline_trace_id": "baseline",
            "candidate_trace_id": "candidate",
            "scenario_cmd": ["python"] * (studio_api.MAX_SCENARIO_CMD_ITEMS + 1),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "scenario_cmd contains too many command items."


def test_studio_api_creates_lists_and_gets_regression_case() -> None:
    reset_studio_state()
    client = TestClient(app)
    with BASELINE.open("rb") as fh:
        baseline_response = client.post(
            "/api/traces/upload",
            files={"file": ("baseline.tbtrace", fh, "application/octet-stream")},
        )
    with CANDIDATE.open("rb") as fh:
        candidate_response = client.post(
            "/api/traces/upload",
            files={"file": ("candidate.tbtrace", fh, "application/octet-stream")},
        )
    baseline_id = baseline_response.json()["trace"]["id"]
    candidate_id = candidate_response.json()["trace"]["id"]

    create_response = client.post(
        "/api/regression-cases",
        json={
            "name": "Refund search regression",
            "description": "Protect the refund lookup flow.",
            "tags": ["refunds", "critical"],
            "baseline_trace_id": baseline_id,
            "candidate_trace_id": candidate_id,
            "scenario_cmd": ["python", "examples/refund_agent.py", "--case", "refund_042"],
            "assertions": ["tool_args", "final_output"],
            "cost_threshold": 1.25,
        },
    )

    assert create_response.status_code == 200
    created = create_response.json()["case"]
    assert created["case_id"].startswith("case_")
    assert created["name"] == "Refund search regression"
    assert created["description"] == "Protect the refund lookup flow."
    assert created["tags"] == ["refunds", "critical"]
    assert created["source_report_id"].startswith("rpt_")
    assert created["baseline_trace_id"] == baseline_id
    assert created["candidate_trace_id"] == candidate_id
    assert created["baseline"]["display_name"] == "baseline.tbtrace"
    assert created["candidate"]["display_name"] == "candidate.tbtrace"
    assert created["first_divergence"]["type"] == "changed_tool_args"
    assert created["divergence_count"] == 3
    assert created["assertions"] == ["tool_args", "final_output"]
    assert created["cost_threshold"] == 1.25
    assert created["scenario_cmd"] == ["python", "examples/refund_agent.py", "--case", "refund_042"]
    assert "assert_aligned" in created["pytest"]["source"]
    assert created["last_result"] == {
        "status": "failing",
        "report_id": created["source_report_id"],
        "divergence_count": 3,
        "severity": "CRITICAL",
        "checked_at": created["updated_at"],
    }

    list_response = client.get("/api/regression-cases")
    get_response = client.get(f"/api/regression-cases/{created['case_id']}")

    assert list_response.status_code == 200
    assert list_response.json()["cases"] == [created]
    assert get_response.status_code == 200
    assert get_response.json()["case"] == created


def test_studio_api_creates_regression_case_from_seeded_demo_report_ids() -> None:
    reset_studio_state()
    client = TestClient(app)

    demo_response = client.get("/api/demo-report")
    demo = demo_response.json()
    create_response = client.post(
        "/api/regression-cases",
        json={
            "name": "Seeded demo case",
            "baseline_trace_id": demo["baseline"]["id"],
            "candidate_trace_id": demo["candidate"]["id"],
        },
    )

    assert create_response.status_code == 200
    case = create_response.json()["case"]
    assert case["baseline_trace_id"] == demo["baseline"]["id"]
    assert case["candidate_trace_id"] == demo["candidate"]["id"]
    assert case["divergence_count"] == demo["divergence_count"]


def test_studio_api_rejects_regression_case_unknown_trace() -> None:
    reset_studio_state()
    client = TestClient(app)

    response = client.post(
        "/api/regression-cases",
        json={
            "name": "Unknown traces",
            "baseline_trace_id": "missing-baseline",
            "candidate_trace_id": "missing-candidate",
        },
    )

    assert response.status_code == 404
    assert "unknown trace id" in response.json()["detail"]


def test_studio_api_rejects_regression_case_same_trace() -> None:
    reset_studio_state()
    client = TestClient(app)
    with BASELINE.open("rb") as fh:
        upload_response = client.post(
            "/api/traces/upload",
            files={"file": ("baseline.tbtrace", fh, "application/octet-stream")},
        )
    trace_id = upload_response.json()["trace"]["id"]

    response = client.post(
        "/api/regression-cases",
        json={
            "name": "Same trace",
            "baseline_trace_id": trace_id,
            "candidate_trace_id": trace_id,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Baseline and candidate traces must be different."


def test_studio_api_runs_regression_case_and_updates_last_result() -> None:
    reset_studio_state()
    client = TestClient(app)
    with BASELINE.open("rb") as fh:
        baseline_response = client.post(
            "/api/traces/upload",
            files={"file": ("baseline.tbtrace", fh, "application/octet-stream")},
        )
    with CANDIDATE.open("rb") as fh:
        candidate_response = client.post(
            "/api/traces/upload",
            files={"file": ("candidate.tbtrace", fh, "application/octet-stream")},
        )
    baseline_id = baseline_response.json()["trace"]["id"]
    candidate_id = candidate_response.json()["trace"]["id"]
    baseline_copy = load_trace_from_path(BASELINE)
    passing_candidate_id = STORE.add_trace(
        replace(baseline_copy, trace_id="trc_refund_baseline_copy"),
        name="baseline-copy.tbtrace",
    )
    create_response = client.post(
        "/api/regression-cases",
        json={
            "name": "Refund search regression",
            "baseline_trace_id": baseline_id,
            "candidate_trace_id": candidate_id,
        },
    )
    case_id = create_response.json()["case"]["case_id"]

    run_response = client.post(
        f"/api/regression-cases/{case_id}/run",
        json={
            "candidate_trace_id": passing_candidate_id,
            "scenario_cmd": ["python", "examples/refund_agent.py", "--case", "refund_042"],
        },
    )

    assert run_response.status_code == 200
    payload = run_response.json()
    assert payload["report"]["divergence_count"] == 0
    updated = payload["case"]
    assert updated["candidate_trace_id"] == passing_candidate_id
    assert updated["divergence_count"] == 0
    assert updated["first_divergence"] is None
    assert updated["last_result"]["status"] == "passing"
    assert updated["last_result"]["report_id"] == payload["report"]["report_id"]
    assert updated["last_result"]["divergence_count"] == 0
    assert updated["last_result"]["severity"] is None
    assert updated["scenario_cmd"] == ["python", "examples/refund_agent.py", "--case", "refund_042"]


def test_studio_api_regression_case_run_rejects_unknown_case() -> None:
    reset_studio_state()
    client = TestClient(app)

    response = client.post(
        "/api/regression-cases/case_missing/run",
        json={"candidate_trace_id": "candidate"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "unknown regression case id: case_missing"


def test_studio_api_regression_case_uses_scenario_command_validation() -> None:
    reset_studio_state()
    client = TestClient(app)

    response = client.post(
        "/api/regression-cases",
        json={
            "name": "Bad scenario",
            "baseline_trace_id": "baseline",
            "candidate_trace_id": "candidate",
            "scenario_cmd": [],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "scenario_cmd must contain at least one command item."


def test_studio_api_regression_case_store_full_returns_507(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_studio_state()
    monkeypatch.setattr(STORE, "max_cases", 0)
    client = TestClient(app)

    demo_response = client.get("/api/demo-report")
    demo = demo_response.json()
    response = client.post(
        "/api/regression-cases",
        json={
            "name": "Too many cases",
            "baseline_trace_id": demo["baseline"]["id"],
            "candidate_trace_id": demo["candidate"]["id"],
        },
    )

    assert response.status_code == 507
    assert (
        response.json()["detail"]
        == "regression case store is full; increase TRACEBISECT_STUDIO_MAX_STORED_CASES"
    )


def test_studio_api_clear_clears_regression_cases() -> None:
    reset_studio_state()
    baseline = load_trace_from_path(BASELINE)
    candidate = load_trace_from_path(CANDIDATE)
    baseline_id = STORE.add_trace(baseline, name="baseline.tbtrace")
    candidate_id = STORE.add_trace(candidate, name="candidate.tbtrace")
    case = STORE.add_case_from_report(
        name="Stored case",
        description="",
        tags=[],
        baseline_trace_id=baseline_id,
        candidate_trace_id=candidate_id,
        scenario_cmd=["python", "examples/refund_agent.py", "--case", "refund_042"],
        assertions=["tool_args"],
        cost_threshold=1.5,
    )
    assert case["case_id"]

    STORE.clear()

    assert STORE.list_cases() == []
