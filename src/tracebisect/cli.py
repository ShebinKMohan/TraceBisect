"""Command-line interface for TraceBisect."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from tracebisect.version import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracebisect",
        description="Git bisect for AI agent traces.",
    )
    parser.add_argument("--version", action="version", version=f"tracebisect {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    demo = subparsers.add_parser("demo", help="Run the alpha demo preview.")
    demo.add_argument(
        "--real",
        action="store_true",
        help="Reserved for the V1 real fixture demo.",
    )

    return parser


def run_demo(*, real: bool = False) -> int:
    if real:
        print("tracebisect demo --real is not available in the alpha scaffold yet.")
        print("It will run the bundled refund-agent trace comparison in V1.")
        return 2

    print("TraceBisect alpha")
    print("Git bisect for AI agent traces")
    print()
    print("V1 will ingest traces, align events, find the first divergence,")
    print("and export pytest regression tests with fresh CI capture.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "demo":
        return run_demo(real=bool(args.real))

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

