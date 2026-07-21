"""Contract tests for local and shared Studio request limiting."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from tracebisect.studio.managed_database import StudioDatabaseConnection
from tracebisect.studio.postgres_storage import PostgresStudioStore
from tracebisect.studio.rate_limit import (
    InMemoryStudioRateLimiter,
    PostgresStudioRateLimiter,
    create_studio_rate_limiter,
)


class _Result:
    def __init__(self, row: tuple[object, ...] | None = None) -> None:
        self._row = row

    @property
    def rowcount(self) -> int:
        return 1

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row

    def fetchall(self) -> list[tuple[object, ...]]:
        return []

    def __iter__(self) -> Iterator[tuple[object, ...]]:
        return iter(())


class _Connection:
    dialect = "postgres"

    def __init__(
        self,
        *,
        pruned_row: tuple[object, ...],
        database_now: datetime | None = None,
    ) -> None:
        self.pruned_row = pruned_row
        self.database_now = database_now
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> _Result:
        normalized = " ".join(statement.split())
        self.calls.append((normalized, tuple(parameters)))
        if "SELECT clock_timestamp()" in normalized:
            assert self.database_now is not None
            return _Result((self.database_now,))
        if "RETURNING cardinality(hit_times)" in normalized:
            return _Result(self.pruned_row)
        return _Result()

    def executemany(
        self,
        _statement: str,
        _parameters: Iterable[Sequence[object]],
    ) -> _Result:
        raise AssertionError("the rate limiter must not use executemany")


class _Database:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    @contextmanager
    def managed_connection(
        self,
        *,
        read_only: bool = False,
    ) -> Iterator[StudioDatabaseConnection]:
        assert read_only is False
        yield self.connection


def test_factory_keeps_local_modes_process_local() -> None:
    limiter = create_studio_rate_limiter(None)

    assert isinstance(limiter, InMemoryStudioRateLimiter)
    assert limiter.runtime_status() == {
        "kind": "memory",
        "distributed": False,
        "algorithm": "sliding_window",
        "stores_raw_client_keys": False,
    }


def test_postgres_limiter_allows_with_hashed_key_and_typed_timestamps() -> None:
    now = datetime(2026, 7, 21, 10, 30, tzinfo=timezone.utc)
    connection = _Connection(
        pruned_row=(1, "2026-07-21T10:29:50Z"),
        database_now=now,
    )
    limiter = PostgresStudioRateLimiter(_Database(connection))
    raw_key = "198.51.100.8:POST:/api/identity/login:user@example.com"

    allowed, retry_after = asyncio.run(limiter.allow(raw_key, limit=2, window_seconds=60))

    assert allowed is True
    assert retry_after == 0
    assert len(connection.calls) == 6
    statements = "\n".join(statement for statement, _params in connection.calls)
    assert "SELECT clock_timestamp()" in statements
    assert "DELETE FROM studio_rate_limit_buckets WHERE expires_at <=" in statements
    assert "pg_advisory_xact_lock(hashtextextended" in statements
    assert "array_append(hit_times" in statements
    all_params = [value for _statement, params in connection.calls for value in params]
    assert raw_key not in repr(all_params)
    assert hashlib.sha256(raw_key.encode()).hexdigest() in all_params
    assert any(isinstance(value, datetime) for value in all_params)
    assert all(value.tzinfo is not None for value in all_params if isinstance(value, datetime))
    assert limiter.runtime_status() == {
        "kind": "postgres",
        "distributed": True,
        "algorithm": "sliding_window",
        "stores_raw_client_keys": False,
    }


def test_postgres_limiter_denies_without_appending_and_returns_exact_retry() -> None:
    now = datetime(2026, 7, 21, 10, 30, 50, tzinfo=timezone.utc)
    connection = _Connection(
        pruned_row=(2, "2026-07-21T10:30:00Z"),
    )
    limiter = PostgresStudioRateLimiter(_Database(connection), clock=lambda: now)

    allowed, retry_after = asyncio.run(limiter.allow("shared-client", limit=2, window_seconds=60))

    assert allowed is False
    assert retry_after == 10
    assert len(connection.calls) == 4
    assert not any("array_append(hit_times" in call[0] for call in connection.calls)


@pytest.mark.parametrize(("limit", "window_seconds"), [(0, 60), (1, 0)])
def test_rate_limit_configuration_must_be_positive(
    limit: int,
    window_seconds: int,
) -> None:
    limiter = InMemoryStudioRateLimiter()

    with pytest.raises(ValueError, match="must be positive"):
        asyncio.run(limiter.allow("client", limit=limit, window_seconds=window_seconds))


@pytest.mark.skipif(
    not os.environ.get("TRACEBISECT_TEST_POSTGRES_URL"),
    reason="set TRACEBISECT_TEST_POSTGRES_URL to run live PostgreSQL rate limiting",
)
def test_live_postgres_rate_limit_is_shared_across_store_instances() -> None:
    database_url = os.environ["TRACEBISECT_TEST_POSTGRES_URL"]
    workspace_id = f"rate-limit-{uuid.uuid4().hex[:16]}"
    key = f"live-rate-limit:{uuid.uuid4().hex}"
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    first = PostgresStudioStore(database_url, workspace_id=workspace_id)
    second = PostgresStudioStore(database_url, workspace_id=workspace_id)
    first_limiter = PostgresStudioRateLimiter(first)
    second_limiter = PostgresStudioRateLimiter(second)
    try:
        assert asyncio.run(first_limiter.allow(key, limit=2, window_seconds=60)) == (True, 0)
        assert asyncio.run(second_limiter.allow(key, limit=2, window_seconds=60)) == (True, 0)
        allowed, retry_after = asyncio.run(first_limiter.allow(key, limit=2, window_seconds=60))
        assert allowed is False
        assert retry_after >= 1
    finally:
        try:
            with first.managed_connection() as connection:
                connection.execute(
                    "DELETE FROM studio_rate_limit_buckets "
                    "WHERE bucket_key_hash = ? AND window_seconds = ?",
                    (key_hash, 60),
                )
        finally:
            second.close()
            first.close()
