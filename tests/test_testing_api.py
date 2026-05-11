from __future__ import annotations

import pytest

from tracebisect import testing


def test_capture_trace_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="alpha"):
        testing.capture_trace(["echo", "hello"])


def test_load_baseline_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="alpha"):
        testing.load_baseline("baseline.tbtrace")


def test_assert_aligned_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="alpha"):
        testing.assert_aligned(
            baseline=None,
            candidate=None,
            assertions=["tool_args"],
        )


def test_public_api_surface_is_locked() -> None:
    expected = ["Trace", "assert_aligned", "capture_trace", "load_baseline"]
    assert sorted(testing.__all__) == expected
    assert list(testing.__all__) == expected
    for name in expected:
        assert hasattr(testing, name)


def test_alpha_message_points_to_repo() -> None:
    invocations: list[tuple[object, tuple[object, ...], dict[str, object]]] = [
        (testing.capture_trace, (["echo", "hello"],), {}),
        (testing.load_baseline, ("baseline.tbtrace",), {}),
        (
            testing.assert_aligned,
            (),
            {"baseline": None, "candidate": None, "assertions": ["tool_args"]},
        ),
    ]
    for func, args, kwargs in invocations:
        with pytest.raises(NotImplementedError) as exc_info:
            func(*args, **kwargs)  # type: ignore[operator]
        assert "github.com" in str(exc_info.value).lower()
