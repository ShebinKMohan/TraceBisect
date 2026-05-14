from __future__ import annotations

import asyncio
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import tracebisect.studio.api as studio_api
from tracebisect.studio.api import RATE_LIMITER, STORE, app
from tracebisect.studio.service import build_demo_report, load_trace_from_path

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
    assert payload["report_id"] in STORE.reports
    assert payload["baseline"]["id"] in {trace["id"] for trace in STORE.list_traces()}
    assert payload["candidate"]["id"] in {trace["id"] for trace in STORE.list_traces()}

    second_response = client.get("/api/demo-report")

    assert second_response.status_code == 200
    assert len(STORE.list_traces()) == 2


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
        response.json()["detail"] == "regression case store is full; delete cases or restart Studio"
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
