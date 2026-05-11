"""Command-line interface for TraceBisect.

The alpha (0.0.1a0) ships the locked CLI surface so generated regression
tests and downstream tooling can be written against stable shapes:

- ``tracebisect --version`` and ``tracebisect demo`` are real commands.
- ``ingest``, ``record``, ``diff``, and ``export-pytest`` parse their locked
  signatures via argparse but exit with code 2 and an alpha message; the
  real implementations land in V1.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from colorama import Fore, Style
from colorama import init as colorama_init

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
        help="Convert a source trace into the canonical .tbtrace format (alpha stub).",
    )
    ingest.add_argument("source", help="Path to the source trace.")
    ingest.add_argument("output", help="Destination .tbtrace path.")

    record = subparsers.add_parser(
        "record",
        help="Run a command with the built-in recorder (alpha stub).",
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
        help="Align two traces and render the divergence diff (alpha stub).",
    )
    diff.add_argument("baseline", help="Baseline .tbtrace path.")
    diff.add_argument("candidate", help="Candidate .tbtrace path.")

    # export-pytest LOCKED SIGNATURE.
    # Positionals: baseline, output. There is intentionally NO candidate
    # positional — the candidate trace is captured fresh in CI by running the
    # command supplied via --scenario. See spec section 4.5.
    export = subparsers.add_parser(
        "export-pytest",
        help="Generate a pytest regression test from a baseline (alpha stub).",
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    command = args.command

    if command is None:
        parser.print_help()
        return 0

    if command == "demo":
        return run_demo()

    if command in ("ingest", "record", "diff", "export-pytest"):
        return _alpha_stub(command)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
