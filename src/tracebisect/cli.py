"""Command-line interface for TraceBisect.

The alpha (0.0.1a0) ships the locked CLI surface so generated regression
tests and downstream tooling can be written against stable shapes:

- ``tracebisect --version`` and ``tracebisect demo`` are real commands.
- ``ingest`` converts OTel/OpenInference JSON or native ``.tbtrace`` input
  into canonical ``.tbtrace`` JSONL.
- ``diff`` renders a terminal comparison of two canonical traces.
- ``export-pytest`` writes a live-capture regression test file.
- ``record`` runs a scenario command with the V1 capture environment contract.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from colorama import Fore, Style
from colorama import init as colorama_init

from tracebisect.align import AlignmentError, align
from tracebisect.diff import detect_divergences
from tracebisect.jsonl import read_trace, write_trace
from tracebisect.otel import import_otel_json
from tracebisect.render import render_terminal_diff
from tracebisect.schema import TraceBisectSchemaError
from tracebisect.testing import capture_trace
from tracebisect.version import __version__

REPO_URL = "https://github.com/ShebinKMohan/TraceBisect"

_ALPHA_MESSAGE_TEMPLATE = (
    "tracebisect {subcommand} is not implemented yet (alpha {version}). See {repo} for V1 progress."
)


def _alpha_message(subcommand: str) -> str:
    return _ALPHA_MESSAGE_TEMPLATE.format(
        subcommand=subcommand,
        version=__version__,
        repo=REPO_URL,
    )


def _alpha_stub(subcommand: str) -> int:
    print(_alpha_message(subcommand), file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracebisect",
        description="Git bisect for AI agent traces.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"tracebisect {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    subparsers.add_parser(
        "demo",
        help="Print the v1.3 money-shot output preview.",
    )

    ingest = subparsers.add_parser(
        "ingest",
        help="Convert a source trace into the canonical .tbtrace format.",
    )
    ingest.add_argument("source", help="Path to the source trace.")
    ingest.add_argument("output", help="Destination .tbtrace path.")

    record = subparsers.add_parser(
        "record",
        help="Run a command and capture the canonical trace it emits.",
    )
    record.add_argument(
        "--output",
        required=True,
        help="Destination .tbtrace path (REQUIRED).",
    )
    record.add_argument(
        "--side-effects",
        choices=["stub", "live"],
        default="stub",
        help="Side-effect policy for the recorded run (default: stub).",
    )
    record.add_argument(
        "--allow-live",
        action="store_true",
        help="Required to enable --side-effects live.",
    )
    record.add_argument(
        "record_command",
        nargs=argparse.REMAINDER,
        help="-- <command> to run under the recorder.",
    )

    diff = subparsers.add_parser(
        "diff",
        help="Align two traces and render the first meaningful divergence.",
    )
    diff.add_argument("baseline", help="Baseline .tbtrace path.")
    diff.add_argument("candidate", help="Candidate .tbtrace path.")

    # export-pytest LOCKED SIGNATURE.
    # Positionals: baseline, output. There is intentionally NO candidate
    # positional — the candidate trace is captured fresh in CI by running the
    # command supplied via --scenario. See spec section 4.5.
    export = subparsers.add_parser(
        "export-pytest",
        help="Generate a live-capture pytest regression test from a baseline.",
    )
    export.add_argument("baseline", help="Baseline .tbtrace path.")
    export.add_argument("output", help="Output .py path for the generated test.")
    export.add_argument(
        "--scenario",
        help="Command to run in CI to capture a fresh candidate trace (REQUIRED in V1).",
    )
    export.add_argument(
        "--assert",
        dest="assert_dimensions",
        help=(
            "Comma-separated assertion dimensions. V1 dimensions: "
            "tool_args, final_output, cost, branch, error_absence."
        ),
    )
    export.add_argument(
        "--cost-threshold",
        type=float,
        default=1.5,
        help="Maximum cost ratio relative to baseline (default: 1.5).",
    )

    return parser


def run_demo() -> int:
    colorama_init()
    red = Fore.RED
    green = Fore.GREEN
    yellow = Fore.YELLOW
    dim = Style.DIM
    reset = Style.RESET_ALL

    lines = [
        f"{red}✗ First divergence at event 7: tool_call.search_database{reset}",
        f"  Severity: {red}CRITICAL{reset}",
        "  Type:     changed_tool_args",
        "",
        f"  {green}Expected:{reset}",
        f'    {green}query = "users WHERE active = true"{reset}',
        "",
        f"  {red}Actual:{reset}",
        f'    {red}query = "users WHERE active = true AND deleted = false"{reset}',
        f"                                    {red}++++++++++++++++++++{reset}",
        "",
        f"  {yellow}Direct effects:{reset}",
        "    tool output changed",
        "    final answer changed",
        "    cost increased 18.4%",
        "    no errors introduced",
        "",
        f"  {dim}Source metadata:{reset}",
        f"    {dim}baseline trace recorded at commit a3f9c1d{reset}",
        f"    {dim}candidate trace recorded at commit b71e442{reset}",
        f"    {dim}prompt template `refund_search` differs between runs{reset}",
        "",
        "Exported test:",
        "  tests/test_refund_agent_regression_042.py",
        "",
        f"{dim}Static alpha preview. See {REPO_URL} for V1 progress.{reset}",
    ]
    for line in lines:
        print(line)
    return 0


def run_ingest(source: str, output: str) -> int:
    source_path = Path(source)
    output_path = Path(output)
    try:
        if source_path.suffix == ".tbtrace":
            trace = read_trace(source_path)
        else:
            trace = import_otel_json(source_path)
        write_trace(trace, output_path)
    except (OSError, TraceBisectSchemaError) as exc:
        print(f"tracebisect ingest failed: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote canonical trace to {output_path}")
    return 0


def run_record(
    output: str,
    *,
    record_command: list[str],
    side_effects: str,
    allow_live: bool,
) -> int:
    command = list(record_command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("tracebisect record failed: command is required after --", file=sys.stderr)
        return 2
    if side_effects == "live" and not allow_live:
        print(
            "tracebisect record failed: --side-effects live requires --allow-live",
            file=sys.stderr,
        )
        return 2
    try:
        trace = capture_trace(
            command,
            side_effects=side_effects,
            allow_live=allow_live,
        )
        write_trace(trace, output)
    except (OSError, TraceBisectSchemaError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"tracebisect record failed: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote recorded trace to {output}")
    return 0


def run_diff(baseline: str, candidate: str) -> int:
    try:
        baseline_trace = read_trace(baseline)
        candidate_trace = read_trace(candidate)
        matches = align(baseline_trace, candidate_trace)
        divergences = detect_divergences(matches, baseline_trace, candidate_trace)
    except (OSError, TraceBisectSchemaError, AlignmentError) as exc:
        print(f"tracebisect diff failed: {exc}", file=sys.stderr)
        return 1

    print(render_terminal_diff(divergences))
    return 1 if divergences else 0


def run_export_pytest(
    baseline: str,
    output: str,
    *,
    scenario: str | None,
    assert_dimensions: str | None,
    cost_threshold: float,
) -> int:
    if not scenario:
        print("tracebisect export-pytest failed: --scenario is required in V1", file=sys.stderr)
        return 2

    assertions = _parse_assertions(assert_dimensions)
    scenario_cmd = shlex.split(scenario)
    if not scenario_cmd:
        print("tracebisect export-pytest failed: --scenario must not be empty", file=sys.stderr)
        return 2

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _pytest_template(
            baseline_path=str(Path(baseline)),
            scenario_cmd=scenario_cmd,
            assertions=assertions,
            cost_threshold=cost_threshold,
        ),
        encoding="utf-8",
    )
    print(f"Wrote pytest regression test to {output_path}")
    return 0


def _parse_assertions(raw: str | None) -> list[str]:
    if raw is None:
        return ["tool_args", "final_output", "cost"]
    assertions = [part.strip() for part in raw.split(",") if part.strip()]
    if not assertions:
        raise ValueError("--assert must contain at least one assertion dimension")
    return assertions


def _pytest_template(
    *,
    baseline_path: str,
    scenario_cmd: list[str],
    assertions: list[str],
    cost_threshold: float,
) -> str:
    assertion_literal = "[" + ", ".join(json.dumps(item) for item in assertions) + "]"
    scenario_literal = json.dumps(scenario_cmd, indent=4)
    return f'''"""Generated TraceBisect regression test."""

from tracebisect.testing import (
    assert_aligned,
    capture_trace,
    load_baseline,
)

BASELINE_PATH = {json.dumps(baseline_path)}
SCENARIO_CMD = {scenario_literal}


def test_tracebisect_regression():
    baseline = load_baseline(BASELINE_PATH)
    candidate = capture_trace(SCENARIO_CMD)

    assert_aligned(
        baseline=baseline,
        candidate=candidate,
        assertions={assertion_literal},
        cost_threshold={cost_threshold!r},
        mode="ci",
    )
'''


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    command = args.command

    if command is None:
        parser.print_help()
        return 0

    if command == "demo":
        return run_demo()

    if command == "ingest":
        return run_ingest(args.source, args.output)

    if command == "record":
        return run_record(
            args.output,
            record_command=args.record_command,
            side_effects=args.side_effects,
            allow_live=args.allow_live,
        )

    if command == "diff":
        return run_diff(args.baseline, args.candidate)

    if command == "export-pytest":
        try:
            return run_export_pytest(
                args.baseline,
                args.output,
                scenario=args.scenario,
                assert_dimensions=args.assert_dimensions,
                cost_threshold=args.cost_threshold,
            )
        except ValueError as exc:
            print(f"tracebisect export-pytest failed: {exc}", file=sys.stderr)
            return 2

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
