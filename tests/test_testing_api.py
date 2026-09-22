from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tracebisect import testing
from tracebisect.schema import EventType, ToolCallPayload, Trace

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / "tests" / "fixtures" / "refund_search_baseline.tbtrace"
CHANGED_TOOL_ARGS = (
    REPO_ROOT / "tests" / "fixtures" / "refund_search_candidate_changed_tool_args.tbtrace"
)
EXTRA_ERROR = REPO_ROOT / "tests" / "fixtures" / "refund_search_candidate_extra_error.tbtrace"


def test_load_baseline_reads_tbtrace() -> None:
    trace = testing.load_baseline(BASELINE)
    assert isinstance(trace, Trace)
    assert trace.trace_id == "trc_refund_baseline"


def test_capture_trace_runs_scenario_and_reads_trace_output(tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.py"
    scenario.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import os",
                "import shutil",
                f"source = Path({str(BASELINE)!r})",
                "target = Path(os.environ['TRACEBISECT_OUTPUT'])",
                "assert os.environ['TRACEBISECT_SIDE_EFFECTS'] == 'stub'",
                "shutil.copyfile(source, target)",
            ]
        ),
        encoding="utf-8",
    )

    trace = testing.capture_trace([sys.executable, str(scenario)])

    assert trace.trace_id == "trc_refund_baseline"


def test_capture_trace_rejects_live_side_effects_without_allow_live() -> None:
    with pytest.raises(ValueError, match="allow_live"):
        testing.capture_trace([sys.executable, "-c", "pass"], side_effects="live")


def test_assert_aligned_tool_args_fails_on_changed_arguments() -> None:
    baseline = testing.load_baseline(BASELINE)
    candidate = testing.load_baseline(CHANGED_TOOL_ARGS)

    with pytest.raises(AssertionError, match="changed_tool_args"):
        testing.assert_aligned(
            baseline=baseline,
            candidate=candidate,
            assertions=["tool_args"],
        )


def test_assert_aligned_tool_args_fails_when_a_different_tool_is_called() -> None:
    baseline = testing.load_baseline(BASELINE)
    events = [
        replace(
            event,
            semantic_name="cancel_order",
            payload=replace(event.payload, tool_name="cancel_order"),
        )
        if event.type is EventType.TOOL_CALL and isinstance(event.payload, ToolCallPayload)
        else event
        for event in baseline.events
    ]
    candidate = replace(
        baseline,
        trace_id="trc_refund_swapped_tool",
        root_event=events[0],
        events=events,
    )

    with pytest.raises(AssertionError, match="search_database replaced by cancel_order"):
        testing.assert_aligned(
            baseline=baseline,
            candidate=candidate,
            assertions=["tool_args"],
        )


def test_assert_aligned_final_output_fails_on_changed_run_end_output() -> None:
    baseline = testing.load_baseline(BASELINE)
    candidate = testing.load_baseline(CHANGED_TOOL_ARGS)

    with pytest.raises(AssertionError, match="final_output"):
        testing.assert_aligned(
            baseline=baseline,
            candidate=candidate,
            assertions=["final_output"],
        )


def test_assert_aligned_cost_uses_assertion_threshold_not_severity_threshold() -> None:
    baseline = testing.load_baseline(BASELINE)
    candidate = testing.load_baseline(CHANGED_TOOL_ARGS)

    testing.assert_aligned(
        baseline=baseline,
        candidate=candidate,
        assertions=["cost"],
        cost_threshold=1.5,
    )
    with pytest.raises(AssertionError, match="cost"):
        testing.assert_aligned(
            baseline=baseline,
            candidate=candidate,
            assertions=["cost"],
            cost_threshold=1.1,
        )


def test_assert_aligned_error_absence_fails_on_unmatched_candidate_error() -> None:
    baseline = testing.load_baseline(BASELINE)
    candidate = testing.load_baseline(EXTRA_ERROR)

    with pytest.raises(AssertionError, match="error_absence"):
        testing.assert_aligned(
            baseline=baseline,
            candidate=candidate,
            assertions=["error_absence"],
        )


def test_assert_aligned_unknown_dimension_fails_fast() -> None:
    baseline = testing.load_baseline(BASELINE)

    with pytest.raises(ValueError, match="retrieval"):
        testing.assert_aligned(
            baseline=baseline,
            candidate=baseline,
            assertions=["retrieval"],
        )


def test_public_api_surface_is_locked() -> None:
    expected = ["Trace", "assert_aligned", "capture_trace", "load_baseline"]
    assert sorted(testing.__all__) == expected
    assert list(testing.__all__) == expected
    for name in expected:
        assert hasattr(testing, name)


def test_capture_trace_merges_environment(tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.py"
    marker = tmp_path / "marker.txt"
    scenario.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import os",
                "import shutil",
                "Path("
                f"{str(marker)!r}"
                ").write_text(os.environ['TRACEBISECT_CUSTOM'], encoding='utf-8')",
                f"shutil.copyfile({str(BASELINE)!r}, os.environ['TRACEBISECT_OUTPUT'])",
            ]
        ),
        encoding="utf-8",
    )

    testing.capture_trace(
        [sys.executable, str(scenario)],
        env={"TRACEBISECT_CUSTOM": "present"},
        cwd=tmp_path,
    )

    assert marker.read_text(encoding="utf-8") == "present"


def test_capture_trace_accepts_string_command(tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.py"
    scenario.write_text(
        "\n".join(
            [
                "import os",
                "import shutil",
                f"shutil.copyfile({str(BASELINE)!r}, os.environ['TRACEBISECT_OUTPUT'])",
            ]
        ),
        encoding="utf-8",
    )

    trace = testing.capture_trace(f"{sys.executable} {scenario}")

    assert trace.trace_id == "trc_refund_baseline"


def test_assert_aligned_mode_values_are_locked() -> None:
    baseline = testing.load_baseline(BASELINE)

    with pytest.raises(ValueError, match="mode"):
        testing.assert_aligned(
            baseline=baseline,
            candidate=baseline,
            assertions=["tool_args"],
            mode="local",
        )


def test_testing_api_does_not_mutate_process_environment(tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.py"
    scenario.write_text(
        "\n".join(
            [
                "import os",
                "import shutil",
                f"shutil.copyfile({str(BASELINE)!r}, os.environ['TRACEBISECT_OUTPUT'])",
            ]
        ),
        encoding="utf-8",
    )
    before = os.environ.get("TRACEBISECT_OUTPUT")

    testing.capture_trace([sys.executable, str(scenario)])

    assert os.environ.get("TRACEBISECT_OUTPUT") == before
