"""Terminal rendering for TraceBisect divergence output."""

from __future__ import annotations

import json

from colorama import Fore, Style

from tracebisect.diff import Divergence, first_divergence

__all__ = ["render_terminal_diff"]


def render_terminal_diff(divergences: list[Divergence], *, use_color: bool = False) -> str:
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

    colors = _Colors.enabled() if use_color else _Colors.disabled()
    lines = [
        f"{colors.red}✗ First divergence at {event_label}{colors.reset}",
        f"  Severity: {colors.severity(first.severity)}{first.severity}{colors.reset}",
        f"  Type:     {first.type}",
        "",
        f"  {colors.green}Expected:{colors.reset}",
        f"    {colors.green}{_format_json(first.expected)}{colors.reset}",
        "",
        f"  {colors.red}Actual:{colors.reset}",
        f"    {colors.red}{_format_json(first.actual)}{colors.reset}",
        "",
        f"  {colors.yellow}Direct effects:{colors.reset}",
        f"    final answer changed: {_yes_no(first.impact.final_output_changed)}",
        f"    cost ratio: {first.impact.cost_delta_ratio:.2f}x",
        f"    downstream events affected: {first.impact.affected_event_count}",
        f"    errors introduced: {len(first.impact.errors_introduced)}",
        "",
        f"  {colors.dim}Source metadata:{colors.reset}",
    ]
    for key, value in sorted(first.source_metadata.items()):
        lines.append(f"    {colors.dim}{key}: {_format_json(value)}{colors.reset}")
    return "\n".join(lines)


def _format_json(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


class _Colors:
    def __init__(self, *, red: str, green: str, yellow: str, dim: str, reset: str) -> None:
        self.red = red
        self.green = green
        self.yellow = yellow
        self.dim = dim
        self.reset = reset

    @classmethod
    def enabled(cls) -> _Colors:
        return cls(
            red=Fore.RED,
            green=Fore.GREEN,
            yellow=Fore.YELLOW,
            dim=Style.DIM,
            reset=Style.RESET_ALL,
        )

    @classmethod
    def disabled(cls) -> _Colors:
        return cls(red="", green="", yellow="", dim="", reset="")

    def severity(self, severity: str) -> str:
        if severity == "CRITICAL":
            return self.red
        if severity == "HIGH":
            return self.yellow
        return self.dim
