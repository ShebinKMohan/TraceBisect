"""Fail-closed malware scanning for untrusted Studio trace uploads."""

from __future__ import annotations

import os
import socket
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from tracebisect.studio.storage import StudioConfigurationError

UPLOAD_SCANNER_ENV = "TRACEBISECT_STUDIO_UPLOAD_SCANNER"
CLAMAV_HOST_ENV = "TRACEBISECT_STUDIO_CLAMAV_HOST"
CLAMAV_PORT_ENV = "TRACEBISECT_STUDIO_CLAMAV_PORT"
CLAMAV_CONNECT_TIMEOUT_ENV = "TRACEBISECT_STUDIO_CLAMAV_CONNECT_TIMEOUT_SECONDS"
CLAMAV_READ_TIMEOUT_ENV = "TRACEBISECT_STUDIO_CLAMAV_READ_TIMEOUT_SECONDS"
CLAMAV_STREAM_CHUNK_BYTES = 64 * 1024
MAX_CLAMAV_RESPONSE_BYTES = 4096


class StudioUploadScannerError(RuntimeError):
    """Base error for a required upload security scan."""


class StudioUploadThreatDetected(StudioUploadScannerError):
    """Raised when the scanner rejects uploaded bytes as malicious."""


class StudioUploadScannerUnavailable(StudioUploadScannerError):
    """Raised when a required scanner cannot return a trustworthy result."""


class StudioUploadScanner(Protocol):
    """Small scanner contract used by the upload route and readiness checks."""

    @property
    def enabled(self) -> bool: ...

    def scan(self, content: bytes) -> None: ...

    def check_health(self) -> bool: ...

    def runtime_status(self, *, ready: bool) -> dict[str, str | bool]: ...


@dataclass(frozen=True, slots=True)
class DisabledStudioUploadScanner:
    """Explicit local-mode scanner boundary."""

    enabled: bool = False

    def scan(self, content: bytes) -> None:
        del content

    def check_health(self) -> bool:
        return True

    def runtime_status(self, *, ready: bool) -> dict[str, str | bool]:
        del ready
        return {
            "enabled": False,
            "provider": "none",
            "ready": True,
            "fail_closed": False,
            "scan_before_parse": False,
        }


@dataclass(frozen=True, slots=True)
class ClamAVStudioUploadScanner:
    """Stream bounded upload bytes to a private clamd service."""

    host: str
    port: int = 3310
    connect_timeout_seconds: float = 2.0
    read_timeout_seconds: float = 10.0
    enabled: bool = True

    def scan(self, content: bytes) -> None:
        try:
            with socket.create_connection(
                (self.host, self.port),
                timeout=self.connect_timeout_seconds,
            ) as connection:
                connection.settimeout(self.read_timeout_seconds)
                connection.sendall(b"zINSTREAM\0")
                for offset in range(0, len(content), CLAMAV_STREAM_CHUNK_BYTES):
                    chunk = content[offset : offset + CLAMAV_STREAM_CHUNK_BYTES]
                    connection.sendall(struct.pack("!I", len(chunk)))
                    connection.sendall(chunk)
                connection.sendall(struct.pack("!I", 0))
                response = _read_clamd_response(connection)
        except StudioUploadScannerError:
            raise
        except OSError as exc:
            raise StudioUploadScannerUnavailable(
                "upload security scanning is temporarily unavailable"
            ) from exc
        _require_clean_response(response)

    def check_health(self) -> bool:
        try:
            with socket.create_connection(
                (self.host, self.port),
                timeout=self.connect_timeout_seconds,
            ) as connection:
                connection.settimeout(self.read_timeout_seconds)
                connection.sendall(b"zPING\0")
                return _read_clamd_response(connection) == b"PONG"
        except (OSError, StudioUploadScannerError):
            return False

    def runtime_status(self, *, ready: bool) -> dict[str, str | bool]:
        return {
            "enabled": True,
            "provider": "clamav",
            "ready": ready,
            "fail_closed": True,
            "scan_before_parse": True,
        }


def create_studio_upload_scanner(
    env: Mapping[str, str] | None = None,
) -> StudioUploadScanner:
    """Build the configured scanner without exposing its network location."""
    values = os.environ if env is None else env
    mode = values.get(UPLOAD_SCANNER_ENV, "none").strip().lower()
    if mode == "none":
        return DisabledStudioUploadScanner()
    if mode != "clamav":
        raise StudioConfigurationError(f"{UPLOAD_SCANNER_ENV} must be none or clamav")
    host = values.get(CLAMAV_HOST_ENV, "").strip()
    if not _valid_host(host):
        raise StudioConfigurationError(
            f"{CLAMAV_HOST_ENV} must be a DNS name or IP address without a URL or path"
        )
    port = _bounded_int(values, CLAMAV_PORT_ENV, default=3310, minimum=1, maximum=65535)
    connect_timeout = _bounded_float(
        values,
        CLAMAV_CONNECT_TIMEOUT_ENV,
        default=2.0,
        minimum=0.1,
        maximum=30.0,
    )
    read_timeout = _bounded_float(
        values,
        CLAMAV_READ_TIMEOUT_ENV,
        default=10.0,
        minimum=0.1,
        maximum=120.0,
    )
    return ClamAVStudioUploadScanner(
        host=host,
        port=port,
        connect_timeout_seconds=connect_timeout,
        read_timeout_seconds=read_timeout,
    )


def _read_clamd_response(connection: socket.socket) -> bytes:
    response = bytearray()
    while len(response) < MAX_CLAMAV_RESPONSE_BYTES:
        chunk = connection.recv(min(1024, MAX_CLAMAV_RESPONSE_BYTES - len(response)))
        if not chunk:
            break
        response.extend(chunk)
        terminator = response.find(0)
        if terminator >= 0:
            return bytes(response[:terminator])
    raise StudioUploadScannerUnavailable("upload security scanner returned an invalid response")


def _require_clean_response(response: bytes) -> None:
    if response.endswith(b": OK"):
        return
    if response.endswith(b" FOUND"):
        raise StudioUploadThreatDetected("upload was rejected by the security scanner")
    raise StudioUploadScannerUnavailable("upload security scanner returned an invalid result")


def _valid_host(value: str) -> bool:
    return bool(
        value
        and len(value) <= 253
        and not any(character.isspace() for character in value)
        and "://" not in value
        and "/" not in value
        and "\\" not in value
    )


def _bounded_int(
    values: Mapping[str, str],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = values.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise StudioConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise StudioConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bounded_float(
    values: Mapping[str, str],
    name: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw = values.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise StudioConfigurationError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise StudioConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value
