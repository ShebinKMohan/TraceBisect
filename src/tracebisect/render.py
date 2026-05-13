"""Terminal rendering for TraceBisect divergence output."""

from __future__ import annotations

import json

from tracebisect.diff import Divergence, first_divergence

__all__ = ["render_terminal_diff"]


def render_terminal_diff(divergences: list[Divergence]) -> str:
    """Render a concise terminal diff focused on the first meaningful divergence."""
    if not divergences:
        return "No divergences found."

    first = first_divergence(divergences)
    if first is None:
        lines = [
            "No HIGH-or-CRITICAL first divergence found.",
            f"{len(divergences)} lower-severity divergence(s) detected.",
        ]
        for divergence in divergences:
            lines.append(f"- {divergence.type}: {divergence.description}")
        return "\n".join(lines)

    event = first.baseline_event or first.candidate_event
    event_label = "trace"
    if event is not None:
        event_label = (
            f"event {event.sequence_index}: {event.type.value.lower()}.{event.semantic_name}"
        )

    lines = [
        f"✗ First divergence at {event_label}",
        f"  Severity: {first.severity}",
        f"  Type:     {first.type}",
        "",
        "  Expected:",
        f"    {_format_json(first.expected)}",
        "",
        "  Actual:",
        f"    {_format_json(first.actual)}",
        "",
        "  Direct effects:",
        f"    final answer changed: {_yes_no(first.impact.final_output_changed)}",
        f"    cost ratio: {first.impact.cost_delta_ratio:.2f}x",
        f"    downstream events affected: {first.impact.affected_event_count}",
        f"    errors introduced: {len(first.impact.errors_introduced)}",
        "",
        "  Source metadata:",
    ]
    for key, value in sorted(first.source_metadata.items()):
        lines.append(f"    {key}: {_format_json(value)}")
    return "\n".join(lines)


def _format_json(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
