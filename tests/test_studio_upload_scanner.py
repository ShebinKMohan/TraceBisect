"""Tests for fail-closed Studio upload malware scanning."""

from __future__ import annotations

import socket
import struct
from collections.abc import Iterator

import pytest

from tracebisect.studio.storage import StudioConfigurationError
from tracebisect.studio.upload_scanner import (
    CLAMAV_STREAM_CHUNK_BYTES,
    ClamAVStudioUploadScanner,
    DisabledStudioUploadScanner,
    StudioUploadScannerUnavailable,
    StudioUploadThreatDetected,
    create_studio_upload_scanner,
)


class _FakeSocket:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses: Iterator[bytes] = iter(responses)
        self.sent: list[bytes] = []
        self.timeout: float | None = None

    def __enter__(self) -> _FakeSocket:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def sendall(self, value: bytes) -> None:
        self.sent.append(value)

    def recv(self, _size: int) -> bytes:
        return next(self.responses, b"")


def test_clamav_scanner_uses_bounded_instream_framing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeSocket([b"stream: OK\0"])
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: connection)
    content = b"a" * (CLAMAV_STREAM_CHUNK_BYTES + 7)
    scanner = ClamAVStudioUploadScanner(
        host="clamav",
        connect_timeout_seconds=1.5,
        read_timeout_seconds=4.0,
    )

    scanner.scan(content)

    assert connection.timeout == 4.0
    assert connection.sent == [
        b"zINSTREAM\0",
        struct.pack("!I", CLAMAV_STREAM_CHUNK_BYTES),
        content[:CLAMAV_STREAM_CHUNK_BYTES],
        struct.pack("!I", 7),
        content[-7:],
        struct.pack("!I", 0),
    ]


def test_clamav_scanner_rejects_threats_without_exposing_the_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeSocket([b"stream: Eicar-Test-Signature FOUND\0"])
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: connection)

    with pytest.raises(StudioUploadThreatDetected) as exc_info:
        ClamAVStudioUploadScanner(host="clamav").scan(b"untrusted bytes")

    assert "Eicar" not in str(exc_info.value)


@pytest.mark.parametrize(
    "response",
    [
        b"stream: size limit exceeded. ERROR\0",
        b"unexpected response\0",
        b"stream: OK",
        b"",
    ],
)
def test_clamav_scanner_fails_closed_on_untrusted_results(
    monkeypatch: pytest.MonkeyPatch,
    response: bytes,
) -> None:
    connection = _FakeSocket([response])
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: connection)

    with pytest.raises(StudioUploadScannerUnavailable):
        ClamAVStudioUploadScanner(host="clamav").scan(b"untrusted bytes")


def test_clamav_health_requires_an_exact_pong(monkeypatch: pytest.MonkeyPatch) -> None:
    healthy = _FakeSocket([b"PONG\0"])
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: healthy)
    scanner = ClamAVStudioUploadScanner(host="clamav")

    assert scanner.check_health() is True
    assert healthy.sent == [b"zPING\0"]

    unhealthy = _FakeSocket([b"not-pong\0"])
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: unhealthy)
    assert scanner.check_health() is False


def test_scanner_configuration_is_explicit_and_secret_safe() -> None:
    disabled = create_studio_upload_scanner({})
    assert isinstance(disabled, DisabledStudioUploadScanner)
    assert disabled.runtime_status(ready=True) == {
        "enabled": False,
        "provider": "none",
        "ready": True,
        "fail_closed": False,
        "scan_before_parse": False,
    }

    enabled = create_studio_upload_scanner(
        {
            "TRACEBISECT_STUDIO_UPLOAD_SCANNER": "clamav",
            "TRACEBISECT_STUDIO_CLAMAV_HOST": "private-scanner",
            "TRACEBISECT_STUDIO_CLAMAV_PORT": "3311",
            "TRACEBISECT_STUDIO_CLAMAV_CONNECT_TIMEOUT_SECONDS": "3.5",
            "TRACEBISECT_STUDIO_CLAMAV_READ_TIMEOUT_SECONDS": "12",
        }
    )
    assert isinstance(enabled, ClamAVStudioUploadScanner)
    assert enabled.port == 3311
    assert enabled.connect_timeout_seconds == 3.5
    assert enabled.read_timeout_seconds == 12.0
    assert enabled.runtime_status(ready=True) == {
        "enabled": True,
        "provider": "clamav",
        "ready": True,
        "fail_closed": True,
        "scan_before_parse": True,
    }
    assert "private-scanner" not in str(enabled.runtime_status(ready=True))


@pytest.mark.parametrize(
    "env",
    [
        {"TRACEBISECT_STUDIO_UPLOAD_SCANNER": "unknown"},
        {
            "TRACEBISECT_STUDIO_UPLOAD_SCANNER": "clamav",
            "TRACEBISECT_STUDIO_CLAMAV_HOST": "https://scanner.example.com",
        },
        {
            "TRACEBISECT_STUDIO_UPLOAD_SCANNER": "clamav",
            "TRACEBISECT_STUDIO_CLAMAV_HOST": "clamav",
            "TRACEBISECT_STUDIO_CLAMAV_PORT": "70000",
        },
        {
            "TRACEBISECT_STUDIO_UPLOAD_SCANNER": "clamav",
            "TRACEBISECT_STUDIO_CLAMAV_HOST": "clamav",
            "TRACEBISECT_STUDIO_CLAMAV_READ_TIMEOUT_SECONDS": "0",
        },
    ],
)
def test_scanner_rejects_unsafe_configuration(env: dict[str, str]) -> None:
    with pytest.raises(StudioConfigurationError):
        create_studio_upload_scanner(env)
