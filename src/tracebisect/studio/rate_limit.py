"""Process-local and PostgreSQL-backed request limiting for Studio."""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Protocol

from tracebisect.schema import JsonObject
from tracebisect.studio.managed_database import (
    StudioDatabaseConnection,
    StudioManagedDatabase,
    studio_database_connection,
)
from tracebisect.studio.storage import StudioPersistenceError

_RATE_LIMIT_LOCK_SEED = 882_014_773


class StudioRateLimiter(Protocol):
    """Async limiter contract used by request and identity guardrails."""

    async def allow(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, int]: ...

    async def reset(self) -> None: ...

    def runtime_status(self) -> JsonObject: ...


class InMemoryStudioRateLimiter:
    """Exact sliding-window limiter for local and single-node Studio modes."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def allow(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        _validate_limit(limit=limit, window_seconds=window_seconds)
        now = time.monotonic()
        async with self._lock:
            bucket = self._hits.setdefault(key, deque())
            while bucket and now - bucket[0] >= window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, math.ceil(window_seconds - (now - bucket[0])))
                return False, retry_after
            bucket.append(now)
            return True, 0

    async def reset(self) -> None:
        async with self._lock:
            self._hits.clear()

    def runtime_status(self) -> JsonObject:
        return {
            "kind": "memory",
            "distributed": False,
            "algorithm": "sliding_window",
            "stores_raw_client_keys": False,
        }


class PostgresStudioRateLimiter:
    """Exact shared sliding-window limiter using one bounded row per client bucket."""

    def __init__(
        self,
        database: StudioManagedDatabase,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        self._clock = clock

    async def allow(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        _validate_limit(limit=limit, window_seconds=window_seconds)
        return await asyncio.to_thread(
            self._allow_sync,
            key,
            limit=limit,
            window_seconds=window_seconds,
        )

    async def reset(self) -> None:
        await asyncio.to_thread(self._reset_sync)

    def runtime_status(self) -> JsonObject:
        return {
            "kind": "postgres",
            "distributed": True,
            "algorithm": "sliding_window",
            "stores_raw_client_keys": False,
        }

    def _allow_sync(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        advisory_key = f"{key_hash}:{window_seconds}"
        try:
            with studio_database_connection(self._database) as connection:
                current = (
                    _utc_now(self._clock())
                    if self._clock is not None
                    else _database_time(connection)
                )
                cutoff = current - timedelta(seconds=window_seconds)
                expires = current + timedelta(seconds=window_seconds)
                connection.execute(
                    """
                    DELETE FROM studio_rate_limit_buckets
                    WHERE expires_at <= ?
                      AND (bucket_key_hash, window_seconds) IN (
                          SELECT bucket_key_hash, window_seconds
                          FROM studio_rate_limit_buckets
                          WHERE expires_at <= ?
                          ORDER BY expires_at
                          LIMIT 128
                      )
                    """,
                    (current, current),
                )
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(?, ?))",
                    (advisory_key, _RATE_LIMIT_LOCK_SEED),
                )
                connection.execute(
                    """
                    INSERT INTO studio_rate_limit_buckets (
                        bucket_key_hash, window_seconds, hit_times, updated_at, expires_at
                    ) VALUES (?, ?, ARRAY[]::timestamptz[], ?, ?)
                    ON CONFLICT (bucket_key_hash, window_seconds) DO NOTHING
                    """,
                    (
                        key_hash,
                        window_seconds,
                        current,
                        expires,
                    ),
                )
                row = connection.execute(
                    """
                    UPDATE studio_rate_limit_buckets
                    SET hit_times = ARRAY(
                            SELECT hit
                            FROM unnest(studio_rate_limit_buckets.hit_times) AS hit
                            WHERE hit > ?
                            ORDER BY hit
                        ),
                        updated_at = ?, expires_at = ?
                    WHERE bucket_key_hash = ? AND window_seconds = ?
                    RETURNING cardinality(hit_times), hit_times[1]
                    """,
                    (
                        cutoff,
                        current,
                        expires,
                        key_hash,
                        window_seconds,
                    ),
                ).fetchone()
                if row is None:
                    raise StudioPersistenceError("rate-limit bucket could not be loaded")
                hit_count = int(str(row[0]))
                earliest = None if row[1] is None else _parse_timestamp(row[1])
                if hit_count >= limit:
                    if earliest is None:
                        raise StudioPersistenceError("rate-limit bucket is inconsistent")
                    retry_after = max(
                        1,
                        math.ceil(window_seconds - (current - earliest).total_seconds()),
                    )
                    return False, retry_after
                connection.execute(
                    """
                    UPDATE studio_rate_limit_buckets
                    SET hit_times = array_append(hit_times, ?),
                        updated_at = ?, expires_at = ?
                    WHERE bucket_key_hash = ? AND window_seconds = ?
                    """,
                    (
                        current,
                        current,
                        expires,
                        key_hash,
                        window_seconds,
                    ),
                )
        except StudioPersistenceError:
            raise
        except (TypeError, ValueError) as exc:
            raise StudioPersistenceError("could not apply the shared request limit") from exc
        return True, 0

    def _reset_sync(self) -> None:
        try:
            with studio_database_connection(self._database) as connection:
                connection.execute("DELETE FROM studio_rate_limit_buckets")
        except StudioPersistenceError:
            raise
        except (TypeError, ValueError) as exc:
            raise StudioPersistenceError("could not reset the shared request limit") from exc


def create_studio_rate_limiter(
    managed_database: StudioManagedDatabase | None,
) -> StudioRateLimiter:
    if managed_database is not None:
        return PostgresStudioRateLimiter(managed_database)
    return InMemoryStudioRateLimiter()


def _validate_limit(*, limit: int, window_seconds: int) -> None:
    if limit < 1 or window_seconds < 1:
        raise ValueError("rate-limit size and window must be positive")


def _utc_now(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("rate-limit clock must include a timezone")
    return value.astimezone(timezone.utc)


def _database_time(connection: StudioDatabaseConnection) -> datetime:
    row = connection.execute("SELECT clock_timestamp()").fetchone()
    if row is None or len(row) != 1:
        raise StudioPersistenceError("rate-limit database time is unavailable")
    return _parse_timestamp(row[0])


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("stored rate-limit timestamp is invalid")
    if parsed.tzinfo is None:
        raise ValueError("stored rate-limit timestamp has no timezone")
    return parsed.astimezone(timezone.utc)
