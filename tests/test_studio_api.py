from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tracebisect.studio.api import STORE, app
from tracebisect.studio.service import build_demo_report

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / "tests" / "fixtures" / "refund_search_baseline.tbtrace"
CANDIDATE = REPO_ROOT / "tests" / "fixtures" / "refund_search_candidate_changed_tool_args.tbtrace"


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
    client = TestClient(app)

    response = client.get("/api/demo-report")

    assert response.status_code == 200
    payload = response.json()
    assert payload["baseline"]["display_name"] == "Refund baseline"
    assert payload["candidate"]["display_name"] == "Refund regression"
    assert payload["first_divergence"]["type"] == "changed_tool_args"


def test_studio_api_uploads_and_compares_tbtrace_files() -> None:
    STORE.traces.clear()
    STORE.trace_names.clear()
    STORE.reports.clear()
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
