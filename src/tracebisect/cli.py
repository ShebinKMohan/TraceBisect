"""Command-line interface for TraceBisect.

The V1 CLI ships the locked command surface so generated regression tests and
downstream tooling can be written against stable shapes:

- ``tracebisect --version`` and ``tracebisect demo`` are real commands.
- ``ingest`` converts OTel/OpenInference JSON or native ``.tbtrace`` input
  into canonical ``.tbtrace`` JSONL.
- ``diff`` renders a terminal comparison of two canonical traces.
- ``export-pytest`` writes a live-capture regression test file.
- ``record`` runs a scenario command with the V1 capture environment contract.
- ``studio`` provides safe backup, verification, and non-destructive restore
  commands for the durable Studio database.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from colorama import init as colorama_init

from tracebisect.align import AlignmentError, align
from tracebisect.demo import build_refund_baseline_trace, build_refund_candidate_trace
from tracebisect.diff import detect_divergences
from tracebisect.jsonl import read_trace, write_trace
from tracebisect.otel import import_otel_json
from tracebisect.render import render_terminal_diff
from tracebisect.schema import TraceBisectSchemaError
from tracebisect.testing import capture_trace
from tracebisect.version import __version__

if TYPE_CHECKING:
    from tracebisect.studio.backup import StudioBackupInspection


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
        help="Run the built-in refund-search V1 demo.",
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
    diff.add_argument(
        "--mode",
        choices=["permissive", "strict", "ci"],
        default="permissive",
        help="Determinism mode for comparison (default: permissive).",
    )

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

    studio = subparsers.add_parser(
        "studio",
        help="Back up, verify, or safely restore a durable Studio database.",
    )
    studio_commands = studio.add_subparsers(
        dest="studio_command",
        metavar="<studio-command>",
    )
    studio.set_defaults(studio_parser=studio)

    studio_backup = studio_commands.add_parser(
        "backup",
        help="Create a consistent snapshot while Studio is running.",
    )
    studio_backup.add_argument(
        "--database",
        required=True,
        help="Current TRACEBISECT_STUDIO_SQLITE_PATH value.",
    )
    studio_backup.add_argument(
        "--output",
        required=True,
        help="New backup file path. Existing files are never replaced.",
    )

    studio_verify = studio_commands.add_parser(
        "verify",
        help="Check backup integrity, compatibility, and record counts.",
    )
    studio_verify.add_argument(
        "--backup",
        required=True,
        help="Backup file to verify.",
    )

    studio_restore = studio_commands.add_parser(
        "restore",
        help="Restore a verified backup to a new database file.",
    )
    studio_restore.add_argument(
        "--backup",
        required=True,
        help="Verified backup file to restore.",
    )
    studio_restore.add_argument(
        "--database",
        required=True,
        help="New database path. Existing files are never replaced.",
    )

    return parser


def run_demo() -> int:
    colorama_init()
    artifact_dir = Path(tempfile.mkdtemp(prefix="tracebisect-demo-"))
    baseline_path = artifact_dir / "baseline.tbtrace"
    candidate_path = artifact_dir / "candidate.tbtrace"
    scenario_path = artifact_dir / "scenario_current.py"
    test_path = artifact_dir / "test_refund_regression.py"

    baseline = build_refund_baseline_trace()
    candidate = build_refund_candidate_trace()
    write_trace(baseline, baseline_path)
    write_trace(candidate, candidate_path)
    scenario_path.write_text(
        "\n".join(
            [
                "import os",
                "import shutil",
                "from pathlib import Path",
                f"source = Path({str(baseline_path)!r})",
                "target = Path(os.environ['TRACEBISECT_OUTPUT'])",
                "shutil.copyfile(source, target)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    test_path.write_text(
        _pytest_template(
            baseline_path=str(baseline_path),
            scenario_cmd=[sys.executable, str(scenario_path)],
            assertions=["tool_args", "final_output", "cost"],
            cost_threshold=1.5,
        ),
        encoding="utf-8",
    )

    matches = align(baseline, candidate)
    divergences = detect_divergences(matches, baseline, candidate)

    print("TraceBisect V1 demo")
    print("===================")
    print()
    print("Scenario: refund agent changed its search_database filter.")
    print()
    print(render_terminal_diff(divergences, use_color=sys.stdout.isatty()))
    print()
    print("Generated pytest regression test:")
    print(f"  {test_path}")
    print()
    print("Try it:")
    print(f"  python -m tracebisect diff {baseline_path} {candidate_path}")
    print(f"  pytest {test_path}")
    print()
    print(f"Artifacts: {artifact_dir}")
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


def run_diff(baseline: str, candidate: str, *, mode: str = "permissive") -> int:
    if mode not in {"permissive", "strict", "ci"}:
        print(
            "tracebisect diff failed: mode must be permissive, strict, or ci",
            file=sys.stderr,
        )
        return 2
    try:
        baseline_trace = read_trace(baseline)
        candidate_trace = read_trace(candidate)
        matches = align(baseline_trace, candidate_trace)
        divergences = detect_divergences(matches, baseline_trace, candidate_trace)
    except (OSError, TraceBisectSchemaError, AlignmentError) as exc:
        print(f"tracebisect diff failed: {exc}", file=sys.stderr)
        return 1

    colorama_init()
    print(render_terminal_diff(divergences, use_color=sys.stdout.isatty()))
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


def run_studio_backup(database: str, output: str) -> int:
    from tracebisect.studio.backup import create_studio_backup

    inspection = create_studio_backup(database, output)
    _print_studio_backup_summary("Studio backup created", Path(output), inspection)
    print()
    print("Next: copy this backup away from the Studio server, then verify it with:")
    print(f"  tracebisect studio verify --backup {shlex.quote(str(Path(output)))}")
    return 0


def run_studio_verify(backup: str) -> int:
    from tracebisect.studio.backup import inspect_studio_backup

    inspection = inspect_studio_backup(backup)
    _print_studio_backup_summary("Studio backup is healthy", Path(backup), inspection)
    return 0


def run_studio_restore(backup: str, database: str) -> int:
    from tracebisect.studio.backup import restore_studio_backup

    inspection = restore_studio_backup(backup, database)
    _print_studio_backup_summary("Studio backup restored", Path(database), inspection)
    print()
    print("Next: set TRACEBISECT_STUDIO_SQLITE_PATH to this new file and restart Studio.")
    return 0


def _print_studio_backup_summary(
    title: str,
    path: Path,
    inspection: StudioBackupInspection,
) -> None:
    print(title)
    print(f"  File: {path.expanduser().resolve()}")
    print(f"  Schema: {inspection.schema_version}")
    print(f"  Workspaces: {inspection.workspace_count}")
    print(f"  Traces: {inspection.trace_count}")
    print(f"  Comparisons: {inspection.report_count}")
    print(f"  Guardrails: {inspection.case_count}")
    print(f"  Size: {inspection.size_bytes} bytes")
    print(f"  SHA-256: {inspection.sha256}")


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
        return run_diff(args.baseline, args.candidate, mode=args.mode)

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

    if command == "studio":
        if args.studio_command is None:
            args.studio_parser.print_help()
            return 0

        from tracebisect.studio.backup import StudioBackupError

        try:
            if args.studio_command == "backup":
                return run_studio_backup(args.database, args.output)
            if args.studio_command == "verify":
                return run_studio_verify(args.backup)
            if args.studio_command == "restore":
                return run_studio_restore(args.backup, args.database)
        except StudioBackupError as exc:
            print(f"tracebisect studio {args.studio_command} failed: {exc}", file=sys.stderr)
            return 2

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
