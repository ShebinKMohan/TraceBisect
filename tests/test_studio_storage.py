"""Tests for restart-safe, workspace-scoped Studio persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracebisect.studio.service import seed_demo_report
from tracebisect.studio.storage import (
    SQLiteStudioStore,
    StudioConfigurationError,
    create_studio_store,
)


def test_store_factory_keeps_zero_configuration_memory_mode() -> None:
    store = create_studio_store({})

    assert store.runtime_status() == {
        "kind": "memory",
        "durable": False,
        "workspace_id": "local",
        "trace_count": 0,
        "report_count": 0,
        "case_count": 0,
    }


def test_memory_store_reports_configured_workspace_id() -> None:
    store = create_studio_store({"TRACEBISECT_STUDIO_WORKSPACE_ID": "developer-a"})

    assert store.runtime_status()["workspace_id"] == "developer-a"


@pytest.mark.parametrize(
    ("env", "message"),
    [
        (
            {"TRACEBISECT_STUDIO_STORAGE": "postgres"},
            "TRACEBISECT_STUDIO_STORAGE must be either 'memory' or 'sqlite'",
        ),
        (
            {"TRACEBISECT_STUDIO_STORAGE": "sqlite"},
            "TRACEBISECT_STUDIO_SQLITE_PATH is required",
        ),
        (
            {"TRACEBISECT_STUDIO_WORKSPACE_ID": "bad workspace"},
            "TRACEBISECT_STUDIO_WORKSPACE_ID must be",
        ),
        (
            {"TRACEBISECT_STUDIO_MAX_STORED_TRACES": "0"},
            "TRACEBISECT_STUDIO_MAX_STORED_TRACES must be a positive integer",
        ),
    ],
)
def test_store_factory_fails_fast_for_invalid_configuration(
    env: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(StudioConfigurationError, match=message):
        create_studio_store(env)


def test_sqlite_store_survives_restart_and_restores_demo_and_cases(tmp_path: Path) -> None:
    database_path = tmp_path / "studio.sqlite3"
    first = SQLiteStudioStore(database_path, workspace_id="workspace-a")
    report = seed_demo_report(first)
    baseline = report["baseline"]
    candidate = report["candidate"]
    assert isinstance(baseline, dict)
    assert isinstance(candidate, dict)
    baseline_trace_id = baseline["id"]
    candidate_trace_id = candidate["id"]
    assert isinstance(baseline_trace_id, str)
    assert isinstance(candidate_trace_id, str)
    case = first.add_case_from_report(
        name="Refund guardrail",
        description="Protect the refund lookup flow.",
        tags=["refund"],
        baseline_trace_id=baseline_trace_id,
        candidate_trace_id=candidate_trace_id,
        scenario_cmd=["python", "examples/refund_agent.py", "--case", "refund_042"],
        assertions=["tool_args", "final_output"],
        cost_threshold=1.25,
    )
    first.close()

    restored = SQLiteStudioStore(database_path, workspace_id="workspace-a")

    assert restored.check_health() is True
    assert restored.runtime_status() == {
        "kind": "sqlite",
        "durable": True,
        "workspace_id": "workspace-a",
        "trace_count": 2,
        "report_count": 2,
        "case_count": 1,
    }
    assert restored.get_report(str(report["report_id"])) == report
    assert restored.get_case(str(case["case_id"])) == case
    assert seed_demo_report(restored)["report_id"] == report["report_id"]
    restored.close()


def test_sqlite_store_isolates_workspaces_in_one_database(tmp_path: Path) -> None:
    database_path = tmp_path / "studio.sqlite3"
    workspace_a = SQLiteStudioStore(database_path, workspace_id="workspace-a")
    seed_demo_report(workspace_a)
    workspace_a.close()

    workspace_b = SQLiteStudioStore(database_path, workspace_id="workspace-b")

    assert workspace_b.list_traces() == []
    assert workspace_b.list_report_summaries() == []
    assert workspace_b.list_cases() == []
    workspace_b.close()
