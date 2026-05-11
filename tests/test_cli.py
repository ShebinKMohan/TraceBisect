from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tracebisect import __version__
from tracebisect.cli import build_parser, main

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"


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
    ("argv", "subcommand"),
    [
        (["ingest", "in.json", "out.tbtrace"], "ingest"),
        (["record", "--output", "/tmp/x.tbtrace"], "record"),
        (["diff", "b.tbtrace", "c.tbtrace"], "diff"),
        (["export-pytest", "b.tbtrace", "out.py"], "export-pytest"),
    ],
)
def test_unimplemented_commands_exit_with_alpha_message(
    argv: list[str],
    subcommand: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(argv)
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "alpha" in captured.err.lower()
    assert subcommand in captured.err


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
