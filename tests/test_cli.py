from __future__ import annotations

from tracebisect import __version__
from tracebisect.cli import main


def test_version_is_alpha() -> None:
    assert __version__ == "0.0.1a0"


def test_demo_prints_alpha_message(capsys) -> None:  # type: ignore[no-untyped-def]
    exit_code = main(["demo"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "TraceBisect alpha" in captured.out
    assert "Git bisect for AI agent traces" in captured.out


def test_real_demo_is_reserved(capsys) -> None:  # type: ignore[no-untyped-def]
    exit_code = main(["demo", "--real"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "not available in the alpha scaffold yet" in captured.out

