from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tracebisect.cli import main
from tracebisect.jsonl import read_trace
from tracebisect.schema import EventType, RunEndPayload, ToolCallPayload, Trace

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
EXAMPLE = REPO_ROOT / "examples" / "refund_agent.py"


def _python_env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC_DIR) + (os.pathsep + existing if existing else "")
    return env


def _run_example(tmp_path: Path, *, variant: str = "fixed") -> Trace:
    output = tmp_path / f"{variant}.tbtrace"
    env = _python_env()
    env["TRACEBISECT_OUTPUT"] = str(output)
    command = [sys.executable, str(EXAMPLE), "--case", "refund_042"]
    if variant != "fixed":
        command.extend(["--variant", variant])

    result = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    return read_trace(output)


def _tool_query(trace: Trace) -> str:
    for event in trace.events:
        if isinstance(event.payload, ToolCallPayload):
            query = event.payload.arguments["query"]
            assert isinstance(query, str)
            return query
    raise AssertionError("trace has no TOOL_CALL payload")


def _final_output(trace: Trace) -> str:
    for event in reversed(trace.events):
        if isinstance(event.payload, RunEndPayload):
            return event.payload.final_output
    raise AssertionError("trace has no RUN_END payload")


@pytest.mark.parametrize(
    ("variant", "trace_id", "query", "final_output"),
    [
        (
            "fixed",
            "trc_demo_refund_baseline",
            "users WHERE active = true",
            "Found 3 active users eligible for refund follow-up: alice, carol, ravi.",
        ),
        (
            "regressed",
            "trc_demo_refund_candidate",
            "users WHERE active = true AND deleted = false",
            "Found 2 active non-deleted users eligible for refund follow-up: alice, ravi.",
        ),
    ],
)
def test_refund_agent_example_emits_expected_trace_variant(
    tmp_path: Path,
    variant: str,
    trace_id: str,
    query: str,
    final_output: str,
) -> None:
    trace = _run_example(tmp_path, variant=variant)

    assert trace.trace_id == trace_id
    assert [event.type for event in trace.events] == [
        EventType.RUN_START,
        EventType.LLM_CALL,
        EventType.TOOL_CALL,
        EventType.RUN_END,
    ]
    assert _tool_query(trace) == query
    assert _final_output(trace) == final_output


def test_refund_agent_example_requires_tracebisect_output() -> None:
    env = _python_env()
    env.pop("TRACEBISECT_OUTPUT", None)
    result = subprocess.run(
        [sys.executable, str(EXAMPLE), "--case", "refund_042"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "TRACEBISECT_OUTPUT is required" in result.stderr


def test_record_command_accepts_refund_agent_example(tmp_path: Path) -> None:
    output = tmp_path / "recorded.tbtrace"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tracebisect",
            "record",
            "--output",
            str(output),
            "--",
            sys.executable,
            str(EXAMPLE),
            "--case",
            "refund_042",
        ],
        cwd=str(REPO_ROOT),
        env=_python_env(),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert read_trace(output).trace_id == "trc_demo_refund_baseline"


def test_exported_pytest_passes_against_refund_agent_example(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.tbtrace"
    env = _python_env()
    env["TRACEBISECT_OUTPUT"] = str(baseline_path)
    subprocess.run(
        [sys.executable, str(EXAMPLE), "--case", "refund_042"],
        cwd=str(REPO_ROOT),
        env=env,
        check=True,
        timeout=30,
    )

    generated = tmp_path / "test_refund_regression.py"
    scenario = " ".join(
        [
            shlex.quote(sys.executable),
            shlex.quote(str(EXAMPLE)),
            "--case",
            "refund_042",
        ]
    )

    exit_code = main(
        [
            "export-pytest",
            str(baseline_path),
            str(generated),
            "--scenario",
            scenario,
            "--assert",
            "tool_args,final_output,cost",
        ]
    )

    assert exit_code == 0
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(generated), "-q"],
        cwd=str(REPO_ROOT),
        env=_python_env(),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
