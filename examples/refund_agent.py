"""Tiny TraceBisect refund-agent demo scenario.

This is not a real agent implementation. It is a deterministic scenario script
used by README examples and generated-test demos: the script writes one
canonical `.tbtrace` file to the path provided by TRACEBISECT_OUTPUT.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from tracebisect.demo import build_refund_baseline_trace, build_refund_candidate_trace
from tracebisect.jsonl import write_trace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Emit a refund-agent demo trace.")
    parser.add_argument(
        "--case",
        default="refund_042",
        choices=["refund_042"],
        help="Demo case to emit.",
    )
    parser.add_argument(
        "--variant",
        default="fixed",
        choices=["fixed", "regressed"],
        help=("Trace variant: fixed matches the baseline; regressed contains the tool-args drift."),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = os.environ.get("TRACEBISECT_OUTPUT")
    if output is None:
        raise SystemExit("TRACEBISECT_OUTPUT is required")

    trace = (
        build_refund_candidate_trace()
        if args.variant == "regressed"
        else build_refund_baseline_trace()
    )
    write_trace(trace, Path(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
