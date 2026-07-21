"""Contract tests for the managed PostgreSQL core storage boundary."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass

import pytest
from psycopg_pool import PoolTimeout

import tracebisect.studio.api as studio_api
import tracebisect.studio.postgres_storage as postgres_storage
from tracebisect.demo import build_refund_baseline_trace
from tracebisect.studio.auth import StudioAuthConfig
from tracebisect.studio.postgres_storage import (
    POSTGRES_SCHEMA_STATEMENTS,
    PostgresStudioStore,
    create_postgres_pool,
)
from tracebisect.studio.service import StudioStore, build_demo_report
from tracebisect.studio.storage import (
    StudioConfigurationError,
    StudioPersistenceError,
    create_studio_store,
)


@dataclass
class _Execution:
    contains: str
    one: object = None
    many: tuple[tuple[object, ...], ...] = ()


class _Result:
    def __init__(self, execution: _Execution) -> None:
        self._execution = execution

    def fetchone(self) -> object:
        return self._execution.one

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._execution.many)


class _Connection:
    def __init__(self, executions: list[_Execution]) -> None:
        self.executions = executions
        self.calls: list[tuple[str, object]] = []

    def execute(self, statement: str, params: object = None) -> _Result:
        normalized = " ".join(statement.split())
        if not self.executions:
            raise AssertionError(f"unexpected PostgreSQL statement: {normalized}")
        execution = self.executions.pop(0)
        assert execution.contains in normalized
        self.calls.append((normalized, params))
        return _Result(execution)


class _ConnectionContext:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def __enter__(self) -> _Connection:
        return self.connection

    def __exit__(self, *_args: object) -> None:
        return None


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection
        self.closed = False

    def connection(self) -> _ConnectionContext:
        return _ConnectionContext(self._connection)

    def close(self) -> None:
        self.closed = True


def _store(executions: list[_Execution]) -> tuple[PostgresStudioStore, _Connection, _Pool]:
    connection = _Connection(executions)
    pool = _Pool(connection)
    store = PostgresStudioStore(
        None,
        workspace_id="workspace-a",
        pool=pool,  # type: ignore[arg-type]
        initialize_schema=False,
    )
    return store, connection, pool


def test_postgres_schema_uses_jsonb_timestamps_constraints_and_workspace_indexes() -> None:
    schema = "\n".join(POSTGRES_SCHEMA_STATEMENTS).lower()

    assert "jsonb not null" in schema
    assert "timestamptz not null" in schema
    assert "singleton boolean primary key" in schema
    assert "primary key (workspace_id, trace_key)" in schema
    assert "workspace_id ~" in schema
    assert "studio_reports_workspace_created_idx" in schema
    assert "studio_cases_workspace_updated_idx" in schema


def test_postgres_pool_is_bounded_fail_fast_and_disables_prepared_statements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _CreatedPool:
        @staticmethod
        def check_connection(_connection: object) -> None:
            return None

        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def open(self, *, wait: bool, timeout: int) -> None:
            captured["opened"] = (wait, timeout)

        def close(self) -> None:
            captured["closed"] = True

    class _ConfiguredConnection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []
            self.committed = False

        def execute(self, statement: str, params: object) -> None:
            self.calls.append((statement, params))

        def commit(self) -> None:
            self.committed = True

    monkeypatch.setattr(postgres_storage, "ConnectionPool", _CreatedPool)
    pool = create_postgres_pool(
        "postgresql://db.example/studio",
        min_size=2,
        max_size=7,
        timeout_seconds=4,
        max_waiting=18,
        connect_timeout_seconds=3,
        statement_timeout_seconds=11,
        idle_transaction_timeout_seconds=13,
    )

    assert isinstance(pool, _CreatedPool)
    assert captured["min_size"] == 2
    assert captured["max_size"] == 7
    assert captured["max_waiting"] == 18
    assert captured["opened"] == (True, 4)
    assert captured["kwargs"] == {
        "application_name": "tracebisect-studio",
        "connect_timeout": 3,
        "prepare_threshold": None,
    }
    configured = _ConfiguredConnection()
    configure = captured["configure"]
    assert callable(configure)
    configure(configured)
    assert configured.committed is True
    assert configured.calls[0][1] == ("11000",)
    assert configured.calls[1][1] == ("13000",)


def test_postgres_pool_closes_after_a_startup_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = False

    class _TimedOutPool:
        @staticmethod
        def check_connection(_connection: object) -> None:
            return None

        def __init__(self, **_kwargs: object) -> None:
            return None

        def open(self, *, wait: bool, timeout: int) -> None:
            assert (wait, timeout) == (True, 4)
            raise PoolTimeout("test timeout")

        def close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(postgres_storage, "ConnectionPool", _TimedOutPool)

    with pytest.raises(
        postgres_storage.StudioPersistenceError,
        match="could not connect",
    ):
        create_postgres_pool(
            "postgresql://db.example/studio",
            min_size=1,
            max_size=2,
            timeout_seconds=4,
            max_waiting=8,
            connect_timeout_seconds=3,
            statement_timeout_seconds=11,
            idle_transaction_timeout_seconds=13,
        )

    assert closed is True


def test_postgres_store_add_trace_locks_capacity_and_uses_parameterized_upsert() -> None:
    store, connection, _pool = _store(
        [
            _Execution("pg_advisory_xact_lock"),
            _Execution("SELECT 1 FROM studio_traces", one=None),
            _Execution("SELECT count(*) FROM studio_traces", one=(0,)),
            _Execution("INSERT INTO studio_traces"),
        ]
    )
    trace = build_refund_baseline_trace()

    assert store.add_trace(trace, name="Refund baseline") == trace.trace_id
    assert not connection.executions
    upsert, params = connection.calls[-1]
    assert "%s" in upsert
    assert params is not None
    assert "Refund baseline" in params


def test_postgres_workspace_views_share_one_pool_and_query_fresh_counts() -> None:
    store, _connection, pool = _store(
        [
            _Execution(
                "SELECT (SELECT count(*) FROM studio_traces",
                one=(2, 3, 4),
            )
        ]
    )
    other = store.for_workspace("workspace-b")

    assert other.runtime_status() == {
        "kind": "postgres",
        "durable": True,
        "workspace_id": "workspace-b",
        "trace_count": 2,
        "report_count": 3,
        "case_count": 4,
    }
    other.close()
    assert pool.closed is False
    store._owns_pool = True
    store.close()
    assert pool.closed is True


def test_postgres_demo_seed_returns_existing_database_report_without_reseeding() -> None:
    report = build_demo_report()
    store, connection, _pool = _store(
        [
            _Execution("pg_advisory_xact_lock"),
            _Execution("FROM studio_metadata AS metadata", one=(report,)),
        ]
    )

    assert store.seed_demo_report() == report
    assert len(connection.calls) == 2


def test_postgres_factory_validates_and_passes_bounded_pool_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _CapturedStore(StudioStore):
        def __init__(self, database_url: str, **kwargs: object) -> None:
            captured["database_url"] = database_url
            captured.update(kwargs)
            super().__init__(workspace_id=str(kwargs["workspace_id"]))

    monkeypatch.setattr(postgres_storage, "PostgresStudioStore", _CapturedStore)
    store = create_studio_store(
        {
            "TRACEBISECT_STUDIO_STORAGE": "postgres",
            "TRACEBISECT_STUDIO_DATABASE_URL": "postgresql://user:secret@db.example/studio",
            "TRACEBISECT_STUDIO_WORKSPACE_ID": "workspace-a",
            "TRACEBISECT_STUDIO_POSTGRES_POOL_MIN": "2",
            "TRACEBISECT_STUDIO_POSTGRES_POOL_MAX": "8",
            "TRACEBISECT_STUDIO_POSTGRES_POOL_MAX_WAITING": "24",
        }
    )

    assert store.workspace_id == "workspace-a"
    assert captured["pool_min_size"] == 2
    assert captured["pool_max_size"] == 8
    assert captured["pool_max_waiting"] == 24


@pytest.mark.parametrize(
    ("env", "message"),
    [
        (
            {"TRACEBISECT_STUDIO_STORAGE": "postgres"},
            "TRACEBISECT_STUDIO_DATABASE_URL is required",
        ),
        (
            {
                "TRACEBISECT_STUDIO_STORAGE": "postgres",
                "TRACEBISECT_STUDIO_DATABASE_URL": "mysql://db.example/studio",
            },
            "must be a PostgreSQL URL",
        ),
        (
            {
                "TRACEBISECT_STUDIO_STORAGE": "postgres",
                "TRACEBISECT_STUDIO_DATABASE_URL": "postgresql://db.example/studio",
                "TRACEBISECT_STUDIO_POSTGRES_POOL_MIN": "9",
                "TRACEBISECT_STUDIO_POSTGRES_POOL_MAX": "4",
            },
            "POOL_MIN cannot exceed",
        ),
    ],
)
def test_postgres_factory_fails_fast_before_connecting(
    env: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(StudioConfigurationError, match=message):
        create_studio_store(env)


def test_static_workspace_keys_can_protect_postgres_but_managed_identity_stays_gated() -> None:
    static_key = "workspace-a-secret-key-0000000001"
    base = {
        "TRACEBISECT_STUDIO_STORAGE": "postgres",
        "TRACEBISECT_STUDIO_DATABASE_URL": "postgresql://db.example/studio",
        "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
    }
    config = StudioAuthConfig.from_env(
        {
            **base,
            "TRACEBISECT_STUDIO_API_KEYS": json.dumps({static_key: "workspace-a"}),
        }
    )

    assert config.workspace_for_authorization(f"Bearer {static_key}") == "workspace-a"
    with pytest.raises(StudioConfigurationError, match="currently require.*sqlite"):
        StudioAuthConfig.from_env(
            {
                **base,
                "TRACEBISECT_STUDIO_API_KEY_PEPPER": "p" * 40,
            }
        )


def test_production_readiness_does_not_claim_sqlite_backup_for_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RuntimeStore(StudioStore):
        def runtime_status(self) -> dict[str, object]:
            return {
                "kind": "postgres",
                "durable": True,
                "workspace_id": "protected",
                "trace_count": 0,
                "report_count": 0,
                "case_count": 0,
            }

    monkeypatch.setattr(studio_api, "STORE", _RuntimeStore())
    readiness = studio_api._production_readiness(storage_ok=True)

    assert "pooled multi-instance PostgreSQL core workspace storage" in readiness["completed"]
    assert "verified local backup and non-destructive restore tooling" not in readiness["completed"]
    assert "PostgreSQL repositories for managed identity" in readiness["blockers"][0]


def test_health_remains_available_when_postgres_runtime_counts_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _UnavailablePostgresStore(StudioStore):
        runtime_kind = "postgres"
        runtime_durable = True

        def check_health(self) -> bool:
            return False

        def runtime_status(self) -> dict[str, object]:
            raise StudioPersistenceError("database unavailable")

    monkeypatch.setattr(studio_api, "STORE", _UnavailablePostgresStore(workspace_id="team-a"))

    payload = studio_api.health()

    assert payload["ok"] is False
    assert payload["runtime"] == {
        "kind": "postgres",
        "durable": True,
        "workspace_id": "team-a",
        "trace_count": 0,
        "report_count": 0,
        "case_count": 0,
    }
    readiness = payload["readiness"]
    assert isinstance(readiness, dict)
    assert readiness["api_ready"] is False


@pytest.mark.skipif(
    not os.environ.get("TRACEBISECT_TEST_POSTGRES_URL"),
    reason="set TRACEBISECT_TEST_POSTGRES_URL to run live PostgreSQL parity",
)
def test_live_postgres_store_shares_fresh_data_and_isolates_workspaces() -> None:
    database_url = os.environ["TRACEBISECT_TEST_POSTGRES_URL"]
    workspace_id = f"test-{uuid.uuid4().hex[:16]}"
    other_workspace_id = f"test-{uuid.uuid4().hex[:16]}"
    first = PostgresStudioStore(database_url, workspace_id=workspace_id)
    restored: PostgresStudioStore | None = None
    other: PostgresStudioStore | None = None
    try:
        report = first.seed_demo_report()
        baseline = report["baseline"]
        candidate = report["candidate"]
        assert isinstance(baseline, dict)
        assert isinstance(candidate, dict)
        case = first.add_case_from_report(
            name="Refund guardrail",
            description="Protect the refund lookup flow.",
            tags=["refund"],
            baseline_trace_id=str(baseline["id"]),
            candidate_trace_id=str(candidate["id"]),
            scenario_cmd=["python", "examples/refund_agent.py"],
            assertions=["tool_args", "final_output"],
            cost_threshold=1.25,
        )
        restored = PostgresStudioStore(database_url, workspace_id=workspace_id)
        assert restored.runtime_status()["trace_count"] == 2
        assert restored.get_report(str(report["report_id"])) == report
        assert restored.get_case(str(case["case_id"])) == case
        assert restored.seed_demo_report()["report_id"] == report["report_id"]

        other = PostgresStudioStore(database_url, workspace_id=other_workspace_id)
        assert other.list_traces() == []
        assert other.list_report_summaries() == []
        assert other.list_cases() == []
    finally:
        try:
            try:
                (restored or first).clear()
            finally:
                if restored is not None:
                    restored.close()
                first.close()
        finally:
            if other is not None:
                try:
                    other.clear()
                finally:
                    other.close()
