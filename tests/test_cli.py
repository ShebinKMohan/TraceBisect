from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tracebisect import __version__
from tracebisect.cli import build_parser, main
from tracebisect.jsonl import read_trace
from tracebisect.schema import EventType, ToolCallPayload

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
OTEL_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "otel" / "openinference_refund_search.json"
NATIVE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "refund_search_baseline.tbtrace"


def test_version_string_is_pep440_alpha() -> None:
    assert __version__ == "0.0.1a0"


def test_version_flag_via_main(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "tracebisect 0.0.1a0" in captured.out


def test_no_args_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "usage:" in captured.out.lower()


def test_demo_command_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["demo"])
    captured = capsys.readouterr()
    assert exit_code == 0
    out = captured.out
    assert "First divergence at event 7" in out
    assert "tool_call.search_database" in out
    assert "CRITICAL" in out
    assert "Direct effects:" in out
    assert "Source metadata:" in out


def test_demo_mentions_repo_url(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["demo"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "github.com" in captured.out.lower()


@pytest.mark.parametrize(
    ("argv", "expected_command"),
    [
        (["demo"], "demo"),
        (["ingest", "in.json", "out.tbtrace"], "ingest"),
        (["record", "--output", "/tmp/x.tbtrace"], "record"),
        (["diff", "b.tbtrace", "c.tbtrace"], "diff"),
        (["export-pytest", "b.tbtrace", "out.py"], "export-pytest"),
    ],
)
def test_parser_exposes_all_subcommands(argv: list[str], expected_command: str) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    assert args.command == expected_command


def test_ingest_otel_json_writes_canonical_tbtrace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "out.tbtrace"
    exit_code = main(["ingest", str(OTEL_FIXTURE), str(output)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Wrote canonical trace" in captured.out
    trace = read_trace(output)
    assert trace.source_convention == "openinference"
    assert [event.type for event in trace.events] == [
        EventType.RUN_START,
        EventType.LLM_CALL,
        EventType.TOOL_CALL,
        EventType.RUN_END,
    ]
    tool = trace.events[2]
    assert isinstance(tool.payload, ToolCallPayload)
    assert tool.payload.arguments == {"query": "users WHERE active = true"}


def test_ingest_native_tbtrace_round_trips(tmp_path: Path) -> None:
    output = tmp_path / "native-out.tbtrace"
    exit_code = main(["ingest", str(NATIVE_FIXTURE), str(output)])
    assert exit_code == 0
    assert read_trace(output) == read_trace(NATIVE_FIXTURE)


def test_ingest_invalid_source_reports_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "bad.json"
    source.write_text("{not json", encoding="utf-8")
    output = tmp_path / "out.tbtrace"
    exit_code = main(["ingest", str(source), str(output)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "tracebisect ingest failed" in captured.err
    assert not output.exists()


def test_record_runs_command_and_writes_valid_trace(tmp_path: Path) -> None:
    script = tmp_path / "scenario.py"
    script.write_text(
        "\n".join(
            [
                "import os",
                "import shutil",
                f"shutil.copyfile({str(NATIVE_FIXTURE)!r}, os.environ['TRACEBISECT_OUTPUT'])",
            ]
        ),
        encoding="utf-8",
    )
    output = tmp_path / "recorded.tbtrace"

    exit_code = main(["record", "--output", str(output), "--", sys.executable, str(script)])

    assert exit_code == 0
    assert read_trace(output) == read_trace(NATIVE_FIXTURE)


def test_record_live_requires_allow_live(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "recorded.tbtrace"
    exit_code = main(
        [
            "record",
            "--output",
            str(output),
            "--side-effects",
            "live",
            "--",
            sys.executable,
            "-c",
            "pass",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "allow-live" in captured.err


def test_record_requires_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "recorded.tbtrace"
    exit_code = main(["record", "--output", str(output)])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "command" in captured.err


def test_diff_renders_first_divergence(capsys: pytest.CaptureFixture[str]) -> None:
    candidate = (
        REPO_ROOT / "tests" / "fixtures" / "refund_search_candidate_changed_tool_args.tbtrace"
    )
    exit_code = main(["diff", str(NATIVE_FIXTURE), str(candidate)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "First divergence" in captured.out
    assert "changed_tool_args" in captured.out
    assert "users WHERE active = true" in captured.out
    assert "users WHERE active = true AND deleted = false" in captured.out


def test_diff_returns_zero_when_traces_match(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["diff", str(NATIVE_FIXTURE), str(NATIVE_FIXTURE)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "No divergences found" in captured.out


def test_export_pytest_accepts_locked_signature() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "export-pytest",
            "baseline.tbtrace",
            "out.py",
            "--scenario",
            "echo hello",
        ]
    )
    assert args.command == "export-pytest"
    assert args.baseline == "baseline.tbtrace"
    assert args.output == "out.py"
    assert args.scenario == "echo hello"
    # --assert is destinated to assert_dimensions because `assert` is reserved.
    assert args.assert_dimensions is None
    assert args.cost_threshold == 1.5
    # The locked design forbids a candidate positional on export-pytest.
    assert not hasattr(args, "candidate")


def test_export_pytest_writes_live_capture_test(tmp_path: Path) -> None:
    output = tmp_path / "test_refund_regression.py"
    exit_code = main(
        [
            "export-pytest",
            str(NATIVE_FIXTURE),
            str(output),
            "--scenario",
            "python examples/refund_agent.py --case refund_042",
            "--assert",
            "tool_args,final_output,cost",
        ]
    )
    assert exit_code == 0
    generated = output.read_text(encoding="utf-8")
    assert "from tracebisect.testing import" in generated
    assert "capture_trace" in generated
    assert "load_baseline" in generated
    assert "assert_aligned" in generated
    assert "SCENARIO_CMD = [" in generated
    assert f'BASELINE_PATH = "{NATIVE_FIXTURE}"' in generated
    assert 'assertions=["tool_args", "final_output", "cost"]' in generated
    assert "candidate =" not in generated.split("def test_tracebisect_regression():", maxsplit=1)[0]


def test_export_pytest_requires_scenario(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "test_refund_regression.py"
    exit_code = main(["export-pytest", str(NATIVE_FIXTURE), str(output)])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "--scenario is required" in captured.err
    assert not output.exists()


def test_python_dash_m_invocation_works() -> None:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC_DIR) + (os.pathsep + existing if existing else "")
    result = subprocess.run(
        [sys.executable, "-m", "tracebisect", "--version"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "tracebisect 0.0.1a0" in result.stdout
