"""Tiny TraceBisect refund-agent demo scenario.

This is not a real agent implementation. It is a deterministic scenario script
used by README examples and generated-test demos: the script uses the native
TraceBisect recorder and writes one canonical `.tbtrace` file to the path
provided by TRACEBISECT_OUTPUT.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from tracebisect.recorder import record_trace, wrap_tool


@wrap_tool
def search_database(query: str) -> list[dict[str, int | str]]:
    if query == "users WHERE active = true AND deleted = false":
        return [{"id": 1, "name": "alice"}, {"id": 3, "name": "ravi"}]
    return [
        {"id": 1, "name": "alice"},
        {"id": 2, "name": "carol"},
        {"id": 3, "name": "ravi"},
    ]


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

    is_regressed = args.variant == "regressed"
    query = (
        "users WHERE active = true AND deleted = false"
        if is_regressed
        else "users WHERE active = true"
    )
    trace_id = "trc_demo_refund_candidate" if is_regressed else "trc_demo_refund_baseline"

    with record_trace(
        user_input="Find active users eligible for refund follow-up.",
        trace_id=trace_id,
        agent_name="refund-agent",
        agent_version="1.0",
        run_metadata={"example": True, "case": args.case},
        prompt_version="refund_search:v3",
        code_sha="a3f9c1d",
    ) as recorder:
        with recorder.llm_call(
            model="gpt-4o-mini",
            provider="openai",
            messages=[
                {
                    "role": "user",
                    "content": "Find active users eligible for refund follow-up.",
                }
            ],
            response_text="I will query the user database.",
            response_tool_calls=[
                {
                    "name": "search_database",
                    "arguments": {"query": query},
                }
            ],
            input_tokens=84,
            output_tokens=22,
            cost_usd=0.00028 if is_regressed else 0.00021,
            temperature=0.0,
            seed=42,
        ):
            users = search_database(query=query)
        names = ", ".join(str(user["name"]) for user in users)
        descriptor = "active non-deleted" if is_regressed else "active"
        recorder.finish(
            final_output=(
                f"Found {len(users)} {descriptor} users eligible for refund follow-up: {names}."
            ),
            total_input_tokens=84,
            total_output_tokens=22,
            total_cost_usd=0.00028 if is_regressed else 0.00021,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
