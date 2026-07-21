"""Shared database contract for managed Studio security repositories.

SQLite remains the complete single-node backend. PostgreSQL-backed stores expose
the same narrow connection protocol so access, identity, email, ingestion-token,
and error-retention business rules stay identical across both databases.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Protocol, TypeAlias, runtime_checkable

from tracebisect.studio.storage import ensure_studio_schema


class StudioDatabaseCursor(Protocol):
    """Result operations used by the managed security repositories."""

    @property
    def rowcount(self) -> int: ...

    def fetchone(self) -> tuple[object, ...] | None: ...

    def fetchall(self) -> list[tuple[object, ...]]: ...

    def __iter__(self) -> Iterator[tuple[object, ...]]: ...


class StudioDatabaseConnection(Protocol):
    """Small synchronous SQL surface shared by SQLite and PostgreSQL."""

    dialect: str

    def execute(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> StudioDatabaseCursor: ...

    def executemany(
        self,
        statement: str,
        parameters: Iterable[Sequence[object]],
    ) -> StudioDatabaseCursor: ...


@runtime_checkable
class StudioManagedDatabase(Protocol):
    """Provider-owned connection source, normally a shared PostgreSQL pool."""

    def managed_connection(
        self,
        *,
        read_only: bool = False,
    ) -> AbstractContextManager[StudioDatabaseConnection]: ...


StudioDatabaseTarget: TypeAlias = str | Path | StudioManagedDatabase


class _SQLiteConnection:
    """Typed adapter around sqlite3 without changing its runtime behavior."""

    dialect = "sqlite"

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> sqlite3.Cursor:
        return self._connection.execute(statement, parameters)

    def executemany(
        self,
        statement: str,
        parameters: Iterable[Sequence[object]],
    ) -> sqlite3.Cursor:
        return self._connection.executemany(statement, parameters)

    @property
    def raw_connection(self) -> sqlite3.Connection:
        return self._connection


@contextmanager
def studio_database_connection(
    database: StudioDatabaseTarget,
    *,
    read_only: bool = False,
) -> Iterator[StudioDatabaseConnection]:
    """Open one short transaction against a supported managed database."""
    if isinstance(database, StudioManagedDatabase):
        with database.managed_connection(read_only=read_only) as connection:
            yield connection
        return

    path = Path(database).expanduser().resolve()
    if read_only:
        raw = sqlite3.connect(
            f"{path.as_uri()}?mode=ro",
            uri=True,
            timeout=5,
        )
    else:
        raw = sqlite3.connect(path, timeout=5)
    try:
        yield _SQLiteConnection(raw)
        raw.commit()
    except BaseException:
        raw.rollback()
        raise
    finally:
        raw.close()


def ensure_managed_database_schema(connection: StudioDatabaseConnection) -> None:
    """Initialize SQLite; PostgreSQL schema installation happens at pool startup."""
    if connection.dialect == "sqlite":
        if not isinstance(connection, _SQLiteConnection):
            raise TypeError("SQLite managed connection adapter is invalid")
        ensure_studio_schema(connection.raw_connection)
