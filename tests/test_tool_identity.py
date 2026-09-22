"""Tests for the tool-identity helpers shared by alignment and divergence detection."""

from __future__ import annotations

import pytest

from tracebisect.align import normalize_tool_name, same_tool


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("search_database", "search_database"),
        ("  search_database\t", "search_database"),
        ("﻿ search_database", "search_database"),
        ("search​_database", "search_database"),
        ("café", "café"),
        ("cafe​́", "café"),
        ("Search", "Search"),
    ],
)
def test_normalize_tool_name(raw: str, normalized: str) -> None:
    assert normalize_tool_name(raw) == normalized


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (("search",), ("search",), True),
        (("search",), ("lookup",), False),
        ((None,), ("search",), True),
        (("search",), (None,), True),
        (("files", "read_file"), (None, "read_file"), True),
        (("files", "read_file"), ("shell", "read_file"), False),
        (("files", None), ("files", "write_file"), True),
        (("files",), ("files", "read_file"), False),
        (None, None, True),
        (None, ("search",), False),
    ],
)
def test_same_tool(
    left: tuple[str | None, ...] | None,
    right: tuple[str | None, ...] | None,
    expected: bool,
) -> None:
    assert same_tool(left, right) is expected
