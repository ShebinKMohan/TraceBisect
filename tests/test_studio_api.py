from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import tracebisect.studio.api as studio_api
from tracebisect.studio.api import RATE_LIMITER, STORE, app
from tracebisect.studio.service import build_demo_report

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / "tests" / "fixtures" / "refund_search_baseline.tbtrace"
CANDIDATE = REPO_ROOT / "tests" / "fixtures" / "refund_search_candidate_changed_tool_args.tbtrace"


def reset_studio_state() -> None:
    STORE.clear()
    asyncio.run(RATE_LIMITER.reset())


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
        }
    ]


def test_studio_api_sets_security_headers() -> None:
    reset_studio_state()
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["limits"]["max_upload_bytes"] == studio_api.MAX_UPLOAD_BYTES


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
    assert response.json()["detail"] == "trace store is full; delete traces or restart Studio"


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
