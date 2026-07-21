"""Managed PostgreSQL storage for horizontally deployed Studio core data.

This backend intentionally owns traces, comparison reports, regression cases,
and demo metadata only. Managed keys, human identity, and invitation delivery
remain on the SQLite path until their repositories are migrated as one
transactional security boundary.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, cast

from psycopg import Connection, Error
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from tracebisect.demo import build_refund_baseline_trace, build_refund_candidate_trace
from tracebisect.jsonl import dumps_trace, loads_trace
from tracebisect.schema import JsonObject, Trace
from tracebisect.studio.service import (
    DEFAULT_ASSERTIONS,
    DEFAULT_MAX_STORED_CASES,
    DEFAULT_MAX_STORED_REPORTS,
    DEFAULT_MAX_STORED_TRACES,
    DEFAULT_SCENARIO_CMD,
    StudioStore,
    StudioStoreFullError,
    _case_from_report,
    _float_value,
    _format_datetime,
    _report_summary,
    _string_list,
    _string_value,
    _trace_summary,
    build_comparison_report,
)
from tracebisect.studio.storage import (
    StudioPersistenceError,
    _load_json_object,
    _required_json_string,
    validate_workspace_id,
)

POSTGRES_SCHEMA_VERSION = 1
_SCHEMA_LOCK_ID = 882_014_771
_WORKSPACE_LOCK_SEED = 882_014_771

POSTGRES_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS studio_postgres_schema (
        singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
        version integer NOT NULL CHECK (version > 0),
        installed_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_traces (
        workspace_id text NOT NULL,
        trace_key text NOT NULL,
        display_name text NOT NULL,
        trace_jsonl text NOT NULL,
        created_at timestamptz NOT NULL,
        PRIMARY KEY (workspace_id, trace_key),
        CHECK (workspace_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS studio_traces_workspace_created_idx
    ON studio_traces (workspace_id, created_at, trace_key)
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_reports (
        workspace_id text NOT NULL,
        report_id text NOT NULL,
        payload_json jsonb NOT NULL,
        created_at timestamptz NOT NULL,
        PRIMARY KEY (workspace_id, report_id),
        CHECK (workspace_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'),
        CHECK (jsonb_typeof(payload_json) = 'object')
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS studio_reports_workspace_created_idx
    ON studio_reports (workspace_id, created_at DESC, report_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_cases (
        workspace_id text NOT NULL,
        case_id text NOT NULL,
        payload_json jsonb NOT NULL,
        updated_at timestamptz NOT NULL,
        PRIMARY KEY (workspace_id, case_id),
        CHECK (workspace_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'),
        CHECK (jsonb_typeof(payload_json) = 'object')
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS studio_cases_workspace_updated_idx
    ON studio_cases (workspace_id, updated_at DESC, case_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_metadata (
        workspace_id text NOT NULL,
        key text NOT NULL,
        value text NOT NULL,
        PRIMARY KEY (workspace_id, key),
        CHECK (workspace_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
    )
    """,
)


class PostgresStudioStore(StudioStore):
    """Multi-instance-safe core workspace store backed by a shared pool."""

    runtime_kind = "postgres"
    runtime_durable = True
    __slots__ = ("_owns_pool", "_pool")

    def __init__(
        self,
        database_url: str | None,
        *,
        workspace_id: str,
        max_traces: int = DEFAULT_MAX_STORED_TRACES,
        max_reports: int = DEFAULT_MAX_STORED_REPORTS,
        max_cases: int = DEFAULT_MAX_STORED_CASES,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
        pool_timeout_seconds: int = 5,
        pool_max_waiting: int = 32,
        connect_timeout_seconds: int = 5,
        statement_timeout_seconds: int = 30,
        idle_transaction_timeout_seconds: int = 30,
        pool: ConnectionPool[Any] | None = None,
        initialize_schema: bool = True,
    ) -> None:
        super().__init__(
            workspace_id=validate_workspace_id(workspace_id),
            max_traces=max_traces,
            max_reports=max_reports,
            max_cases=max_cases,
        )
        if pool is None:
            if not database_url:
                raise StudioPersistenceError("a PostgreSQL database URL is required")
            self._pool = create_postgres_pool(
                database_url,
                min_size=pool_min_size,
                max_size=pool_max_size,
                timeout_seconds=pool_timeout_seconds,
                max_waiting=pool_max_waiting,
                connect_timeout_seconds=connect_timeout_seconds,
                statement_timeout_seconds=statement_timeout_seconds,
                idle_transaction_timeout_seconds=idle_transaction_timeout_seconds,
            )
            self._owns_pool = True
        else:
            self._pool = pool
            self._owns_pool = False
        if initialize_schema:
            try:
                ensure_postgres_schema(self._pool)
            except Exception:
                if self._owns_pool:
                    self._pool.close()
                raise

    def for_workspace(self, workspace_id: str) -> PostgresStudioStore:
        """Create a lightweight workspace view sharing this process pool."""
        return PostgresStudioStore(
            None,
            workspace_id=workspace_id,
            max_traces=self.max_traces,
            max_reports=self.max_reports,
            max_cases=self.max_cases,
            pool=self._pool,
            initialize_schema=False,
        )

    def add_trace(self, trace: Trace, *, name: str | None = None) -> str:
        trace_key = trace.trace_id or f"trace-{uuid.uuid4().hex[:12]}"
        try:
            with self._pool.connection() as connection:
                _lock_workspace(connection, self.workspace_id)
                existing = connection.execute(
                    """
                    SELECT 1 FROM studio_traces
                    WHERE workspace_id = %s AND trace_key = %s
                    """,
                    (self.workspace_id, trace_key),
                ).fetchone()
                trace_count = _table_count(connection, "studio_traces", self.workspace_id)
                if existing is None and trace_count >= self.max_traces:
                    raise StudioStoreFullError(
                        "trace store is full; increase TRACEBISECT_STUDIO_MAX_STORED_TRACES"
                    )
                _upsert_trace(connection, self.workspace_id, trace_key, trace, name)
        except StudioStoreFullError:
            raise
        except Error as exc:
            raise StudioPersistenceError("could not save trace to PostgreSQL") from exc
        return trace_key

    def get_trace(self, trace_key: str) -> Trace:
        trace, _name = self._trace_record(trace_key)
        return trace

    def get_trace_name(self, trace_key: str) -> str:
        _trace, name = self._trace_record(trace_key)
        return name

    def list_traces(self) -> list[JsonObject]:
        try:
            with self._pool.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT trace_key, display_name, trace_jsonl
                    FROM studio_traces
                    WHERE workspace_id = %s
                    ORDER BY created_at, trace_key
                    """,
                    (self.workspace_id,),
                ).fetchall()
        except Error as exc:
            raise StudioPersistenceError("could not list traces from PostgreSQL") from exc
        return [
            _trace_summary(
                loads_trace(str(trace_jsonl)),
                trace_key=str(trace_key),
                display_name=str(display_name),
            )
            for trace_key, display_name, trace_jsonl in rows
        ]

    def add_report(self, report: JsonObject) -> str:
        try:
            with self._pool.connection() as connection:
                _lock_workspace(connection, self.workspace_id)
                return self._store_report(connection, report)
        except Error as exc:
            raise StudioPersistenceError("could not save comparison report to PostgreSQL") from exc

    def get_report(self, report_id: str) -> JsonObject:
        try:
            with self._pool.connection() as connection:
                row = connection.execute(
                    """
                    SELECT payload_json FROM studio_reports
                    WHERE workspace_id = %s AND report_id = %s
                    """,
                    (self.workspace_id, report_id),
                ).fetchone()
        except Error as exc:
            raise StudioPersistenceError(
                "could not read comparison report from PostgreSQL"
            ) from exc
        if row is None:
            raise KeyError(f"unknown run id: {report_id}")
        return _postgres_json_object(row[0])

    def list_report_summaries(self) -> list[JsonObject]:
        try:
            with self._pool.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT payload_json FROM studio_reports
                    WHERE workspace_id = %s
                    ORDER BY created_at DESC, report_id DESC
                    """,
                    (self.workspace_id,),
                ).fetchall()
        except Error as exc:
            raise StudioPersistenceError("could not list reports from PostgreSQL") from exc
        return [_report_summary(_postgres_json_object(row[0])) for row in rows]

    def add_case_from_report(
        self,
        *,
        name: str,
        description: str,
        tags: list[str],
        baseline_trace_id: str,
        candidate_trace_id: str,
        scenario_cmd: list[str],
        assertions: list[str],
        cost_threshold: float,
    ) -> JsonObject:
        try:
            with self._pool.connection() as connection:
                _lock_workspace(connection, self.workspace_id)
                if _table_count(connection, "studio_cases", self.workspace_id) >= self.max_cases:
                    raise StudioStoreFullError(
                        "regression case store is full; increase "
                        "TRACEBISECT_STUDIO_MAX_STORED_CASES"
                    )
                baseline, baseline_name = _trace_record(
                    connection,
                    self.workspace_id,
                    baseline_trace_id,
                )
                candidate, candidate_name = _trace_record(
                    connection,
                    self.workspace_id,
                    candidate_trace_id,
                )
                report = build_comparison_report(
                    baseline,
                    candidate,
                    baseline_name=baseline_name,
                    candidate_name=candidate_name,
                    scenario_cmd=scenario_cmd,
                    assertions=assertions,
                    cost_threshold=cost_threshold,
                )
                self._store_report(connection, report)
                now = _format_datetime(datetime.now(timezone.utc))
                case = _case_from_report(
                    case_id=f"case_{uuid.uuid4().hex[:12]}",
                    name=name,
                    description=description,
                    created_at=now,
                    updated_at=now,
                    tags=tags,
                    baseline_trace_id=baseline_trace_id,
                    candidate_trace_id=candidate_trace_id,
                    scenario_cmd=scenario_cmd,
                    assertions=assertions,
                    cost_threshold=cost_threshold,
                    report=report,
                )
                _upsert_case(connection, self.workspace_id, case)
                return case
        except StudioStoreFullError:
            raise
        except KeyError:
            raise
        except Error as exc:
            raise StudioPersistenceError("could not save regression case to PostgreSQL") from exc

    def get_case(self, case_id: str) -> JsonObject:
        try:
            with self._pool.connection() as connection:
                row = connection.execute(
                    """
                    SELECT payload_json FROM studio_cases
                    WHERE workspace_id = %s AND case_id = %s
                    """,
                    (self.workspace_id, case_id),
                ).fetchone()
        except Error as exc:
            raise StudioPersistenceError("could not read regression case from PostgreSQL") from exc
        if row is None:
            raise KeyError(f"unknown regression case id: {case_id}")
        return _postgres_json_object(row[0])

    def list_cases(self) -> list[JsonObject]:
        try:
            with self._pool.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT payload_json FROM studio_cases
                    WHERE workspace_id = %s
                    ORDER BY updated_at, case_id
                    """,
                    (self.workspace_id,),
                ).fetchall()
        except Error as exc:
            raise StudioPersistenceError("could not list regression cases from PostgreSQL") from exc
        return [_postgres_json_object(row[0]) for row in rows]

    def run_case(
        self,
        case_id: str,
        *,
        candidate_trace_id: str,
        scenario_cmd: list[str],
    ) -> tuple[JsonObject, JsonObject]:
        try:
            with self._pool.connection() as connection:
                _lock_workspace(connection, self.workspace_id)
                row = connection.execute(
                    """
                    SELECT payload_json FROM studio_cases
                    WHERE workspace_id = %s AND case_id = %s
                    FOR UPDATE
                    """,
                    (self.workspace_id, case_id),
                ).fetchone()
                if row is None:
                    raise KeyError(f"unknown regression case id: {case_id}")
                existing = _postgres_json_object(row[0])
                baseline_trace_id = _string_value(existing["baseline_trace_id"])
                baseline, baseline_name = _trace_record(
                    connection,
                    self.workspace_id,
                    baseline_trace_id,
                )
                candidate, candidate_name = _trace_record(
                    connection,
                    self.workspace_id,
                    candidate_trace_id,
                )
                assertions = _string_list(existing["assertions"])
                cost_threshold = _float_value(existing["cost_threshold"])
                report = build_comparison_report(
                    baseline,
                    candidate,
                    baseline_name=baseline_name,
                    candidate_name=candidate_name,
                    scenario_cmd=scenario_cmd,
                    assertions=assertions,
                    cost_threshold=cost_threshold,
                )
                self._store_report(connection, report)
                updated = _case_from_report(
                    case_id=case_id,
                    name=_string_value(existing["name"]),
                    description=_string_value(existing["description"]),
                    created_at=_string_value(existing["created_at"]),
                    updated_at=_format_datetime(datetime.now(timezone.utc)),
                    tags=_string_list(existing["tags"]),
                    baseline_trace_id=baseline_trace_id,
                    candidate_trace_id=candidate_trace_id,
                    scenario_cmd=scenario_cmd,
                    assertions=assertions,
                    cost_threshold=cost_threshold,
                    report=report,
                )
                _upsert_case(connection, self.workspace_id, updated)
                return updated, report
        except KeyError:
            raise
        except Error as exc:
            raise StudioPersistenceError("could not rerun regression case in PostgreSQL") from exc

    def set_demo_report_id(self, report_id: str | None) -> None:
        try:
            with self._pool.connection() as connection:
                _lock_workspace(connection, self.workspace_id)
                _set_demo_report_id(connection, self.workspace_id, report_id)
        except Error as exc:
            raise StudioPersistenceError("could not save Studio metadata to PostgreSQL") from exc

    def seed_demo_report(self) -> JsonObject:
        try:
            with self._pool.connection() as connection:
                _lock_workspace(connection, self.workspace_id)
                existing = connection.execute(
                    """
                    SELECT reports.payload_json
                    FROM studio_metadata AS metadata
                    JOIN studio_reports AS reports
                      ON reports.workspace_id = metadata.workspace_id
                     AND reports.report_id = metadata.value
                    WHERE metadata.workspace_id = %s
                      AND metadata.key = 'demo_report_id'
                    """,
                    (self.workspace_id,),
                ).fetchone()
                if existing is not None:
                    return _postgres_json_object(existing[0])

                baseline = build_refund_baseline_trace()
                candidate = build_refund_candidate_trace()
                _ensure_trace_capacity(
                    connection,
                    self.workspace_id,
                    baseline.trace_id,
                    self.max_traces,
                )
                _upsert_trace(
                    connection,
                    self.workspace_id,
                    baseline.trace_id,
                    baseline,
                    "Refund baseline",
                )
                _ensure_trace_capacity(
                    connection,
                    self.workspace_id,
                    candidate.trace_id,
                    self.max_traces,
                )
                _upsert_trace(
                    connection,
                    self.workspace_id,
                    candidate.trace_id,
                    candidate,
                    "Refund regression",
                )
                report = build_comparison_report(
                    baseline,
                    candidate,
                    baseline_name="Refund baseline",
                    candidate_name="Refund regression",
                    scenario_cmd=DEFAULT_SCENARIO_CMD,
                    assertions=DEFAULT_ASSERTIONS,
                )
                report_id = self._store_report(connection, report)
                _set_demo_report_id(connection, self.workspace_id, report_id)
                return report
        except StudioStoreFullError:
            raise
        except Error as exc:
            raise StudioPersistenceError("could not seed the Studio demo in PostgreSQL") from exc

    def clear(self) -> None:
        try:
            with self._pool.connection() as connection:
                _lock_workspace(connection, self.workspace_id)
                for table in (
                    "studio_metadata",
                    "studio_cases",
                    "studio_reports",
                    "studio_traces",
                ):
                    connection.execute(
                        f"DELETE FROM {table} WHERE workspace_id = %s",
                        (self.workspace_id,),
                    )
        except Error as exc:
            raise StudioPersistenceError("could not clear the PostgreSQL workspace") from exc

    def check_health(self) -> bool:
        try:
            with self._pool.connection() as connection:
                row = connection.execute("SELECT 1").fetchone()
                return bool(row == (1,))
        except Error:
            return False

    def runtime_status(self) -> JsonObject:
        try:
            with self._pool.connection() as connection:
                row = connection.execute(
                    """
                    SELECT
                      (SELECT count(*) FROM studio_traces WHERE workspace_id = %s),
                      (SELECT count(*) FROM studio_reports WHERE workspace_id = %s),
                      (SELECT count(*) FROM studio_cases WHERE workspace_id = %s)
                    """,
                    (self.workspace_id, self.workspace_id, self.workspace_id),
                ).fetchone()
        except Error as exc:
            raise StudioPersistenceError("could not read PostgreSQL workspace status") from exc
        if row is None:
            raise StudioPersistenceError("PostgreSQL workspace status returned no row")
        return {
            "kind": self.runtime_kind,
            "durable": self.runtime_durable,
            "workspace_id": self.workspace_id,
            "trace_count": int(row[0]),
            "report_count": int(row[1]),
            "case_count": int(row[2]),
        }

    def close(self) -> None:
        """Close only the pool-owning store; workspace views share that pool."""
        if self._owns_pool:
            self._pool.close()

    def _trace_record(self, trace_key: str) -> tuple[Trace, str]:
        try:
            with self._pool.connection() as connection:
                return _trace_record(connection, self.workspace_id, trace_key)
        except KeyError:
            raise
        except Error as exc:
            raise StudioPersistenceError("could not read trace from PostgreSQL") from exc

    def _store_report(self, connection: Connection[Any], report: JsonObject) -> str:
        report_id = _required_json_string(report, "report_id")
        exists = connection.execute(
            """
            SELECT 1 FROM studio_reports
            WHERE workspace_id = %s AND report_id = %s
            """,
            (self.workspace_id, report_id),
        ).fetchone()
        report_count = _table_count(connection, "studio_reports", self.workspace_id)
        if exists is None and report_count >= self.max_reports:
            connection.execute(
                """
                DELETE FROM studio_reports
                WHERE workspace_id = %s AND report_id = (
                    SELECT report_id FROM studio_reports
                    WHERE workspace_id = %s
                    ORDER BY created_at, report_id
                    LIMIT 1
                )
                """,
                (self.workspace_id, self.workspace_id),
            )
        connection.execute(
            """
            INSERT INTO studio_reports (workspace_id, report_id, payload_json, created_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (workspace_id, report_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                created_at = excluded.created_at
            """,
            (
                self.workspace_id,
                report_id,
                Jsonb(report),
                _required_json_string(report, "created_at"),
            ),
        )
        return report_id


def create_postgres_pool(
    database_url: str,
    *,
    min_size: int,
    max_size: int,
    timeout_seconds: int,
    max_waiting: int,
    connect_timeout_seconds: int,
    statement_timeout_seconds: int,
    idle_transaction_timeout_seconds: int,
) -> ConnectionPool[Any]:
    """Create a bounded pool and fail startup before accepting traffic."""

    def configure(connection: Connection[Any]) -> None:
        connection.execute(
            "SELECT set_config('statement_timeout', %s, false)",
            (str(statement_timeout_seconds * 1000),),
        )
        connection.execute(
            "SELECT set_config('idle_in_transaction_session_timeout', %s, false)",
            (str(idle_transaction_timeout_seconds * 1000),),
        )
        connection.commit()

    pool: ConnectionPool[Any] | None = None
    try:
        pool = ConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            open=False,
            timeout=timeout_seconds,
            max_waiting=max_waiting,
            max_idle=300,
            max_lifetime=1800,
            reconnect_timeout=30,
            check=ConnectionPool.check_connection,
            configure=configure,
            kwargs={
                "application_name": "tracebisect-studio",
                "connect_timeout": connect_timeout_seconds,
                "prepare_threshold": None,
            },
            name="tracebisect-studio",
        )
        pool.open(wait=True, timeout=timeout_seconds)
    except (Error, ValueError) as exc:
        if pool is not None:
            pool.close()
        raise StudioPersistenceError(
            "could not connect to the configured PostgreSQL store"
        ) from exc
    return pool


def ensure_postgres_schema(pool: ConnectionPool[Any]) -> None:
    """Install the idempotent core schema under a transaction advisory lock."""
    try:
        with pool.connection() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_ID,))
            connection.execute(POSTGRES_SCHEMA_STATEMENTS[0])
            row = connection.execute(
                "SELECT version FROM studio_postgres_schema WHERE singleton = true FOR UPDATE"
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO studio_postgres_schema (singleton, version)
                    VALUES (true, %s)
                    """,
                    (POSTGRES_SCHEMA_VERSION,),
                )
            elif row != (POSTGRES_SCHEMA_VERSION,):
                raise StudioPersistenceError(
                    f"unsupported PostgreSQL Studio schema version {row[0]!r}"
                )
            for statement in POSTGRES_SCHEMA_STATEMENTS[1:]:
                connection.execute(statement)
    except StudioPersistenceError:
        raise
    except Error as exc:
        raise StudioPersistenceError("could not initialize the PostgreSQL Studio schema") from exc


def _lock_workspace(connection: Connection[Any], workspace_id: str) -> None:
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, %s))",
        (workspace_id, _WORKSPACE_LOCK_SEED),
    )


def _table_count(connection: Connection[Any], table: str, workspace_id: str) -> int:
    if table not in {"studio_traces", "studio_reports", "studio_cases"}:
        raise ValueError("unsupported Studio count table")
    row = connection.execute(
        f"SELECT count(*) FROM {table} WHERE workspace_id = %s",
        (workspace_id,),
    ).fetchone()
    if row is None:
        raise StudioPersistenceError("PostgreSQL count query returned no row")
    return int(row[0])


def _ensure_trace_capacity(
    connection: Connection[Any],
    workspace_id: str,
    trace_key: str,
    max_traces: int,
) -> None:
    exists = connection.execute(
        "SELECT 1 FROM studio_traces WHERE workspace_id = %s AND trace_key = %s",
        (workspace_id, trace_key),
    ).fetchone()
    if exists is None and _table_count(connection, "studio_traces", workspace_id) >= max_traces:
        raise StudioStoreFullError(
            "trace store is full; increase TRACEBISECT_STUDIO_MAX_STORED_TRACES"
        )


def _upsert_trace(
    connection: Connection[Any],
    workspace_id: str,
    trace_key: str,
    trace: Trace,
    name: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO studio_traces (
            workspace_id, trace_key, display_name, trace_jsonl, created_at
        ) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (workspace_id, trace_key) DO UPDATE SET
            display_name = excluded.display_name,
            trace_jsonl = excluded.trace_jsonl,
            created_at = excluded.created_at
        """,
        (
            workspace_id,
            trace_key,
            name or trace.trace_id,
            dumps_trace(trace),
            trace.created_at,
        ),
    )


def _trace_record(
    connection: Connection[Any],
    workspace_id: str,
    trace_key: str,
) -> tuple[Trace, str]:
    row = connection.execute(
        """
        SELECT trace_jsonl, display_name FROM studio_traces
        WHERE workspace_id = %s AND trace_key = %s
        """,
        (workspace_id, trace_key),
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown trace id: {trace_key}")
    return loads_trace(str(row[0])), str(row[1])


def _upsert_case(
    connection: Connection[Any],
    workspace_id: str,
    case: JsonObject,
) -> None:
    connection.execute(
        """
        INSERT INTO studio_cases (workspace_id, case_id, payload_json, updated_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (workspace_id, case_id) DO UPDATE SET
            payload_json = excluded.payload_json,
            updated_at = excluded.updated_at
        """,
        (
            workspace_id,
            _required_json_string(case, "case_id"),
            Jsonb(case),
            _required_json_string(case, "updated_at"),
        ),
    )


def _set_demo_report_id(
    connection: Connection[Any],
    workspace_id: str,
    report_id: str | None,
) -> None:
    if report_id is None:
        connection.execute(
            "DELETE FROM studio_metadata WHERE workspace_id = %s AND key = 'demo_report_id'",
            (workspace_id,),
        )
        return
    connection.execute(
        """
        INSERT INTO studio_metadata (workspace_id, key, value)
        VALUES (%s, 'demo_report_id', %s)
        ON CONFLICT (workspace_id, key) DO UPDATE SET value = excluded.value
        """,
        (workspace_id, report_id),
    )


def _postgres_json_object(value: object) -> JsonObject:
    if isinstance(value, str):
        return _load_json_object(value)
    if not isinstance(value, dict):
        raise StudioPersistenceError("stored PostgreSQL Studio JSON payload is not an object")
    return cast(JsonObject, value)
