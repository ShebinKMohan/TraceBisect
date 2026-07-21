"""Contract tests for the managed PostgreSQL core storage boundary."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb
from psycopg_pool import PoolTimeout
from svix.webhooks import Webhook

import tracebisect.studio.api as studio_api
import tracebisect.studio.postgres_storage as postgres_storage
from tracebisect.demo import build_refund_baseline_trace
from tracebisect.studio.access_keys import (
    create_studio_api_key,
    principal_for_managed_api_key,
    revoke_studio_api_key,
)
from tracebisect.studio.access_sessions import (
    issue_studio_browser_session,
    principal_for_studio_browser_session,
    revoke_studio_browser_session,
)
from tracebisect.studio.auth import StudioAuthConfig
from tracebisect.studio.email_delivery import StudioEmailDelivery, StudioEmailMessage
from tracebisect.studio.error_reporting import (
    StudioErrorReporter,
    list_studio_error_events,
)
from tracebisect.studio.identity import (
    IssuedStudioInvitation,
    StudioInvitationRecord,
    accept_studio_invitation,
    create_studio_invitation,
    login_studio_identity,
    principal_for_studio_identity_session,
    recover_studio_identity,
)
from tracebisect.studio.ingestion_tokens import (
    create_studio_ingestion_token,
    principal_for_managed_ingestion_token,
    revoke_studio_ingestion_token,
)
from tracebisect.studio.postgres_migration import MIGRATION_TABLES, migrate_sqlite_to_postgres
from tracebisect.studio.postgres_storage import (
    POSTGRES_SCHEMA_STATEMENTS,
    PostgresStudioStore,
    create_postgres_pool,
    ensure_postgres_schema,
)
from tracebisect.studio.rate_limit import PostgresStudioRateLimiter
from tracebisect.studio.service import (
    StudioStore,
    StudioTraceConflictError,
    build_demo_report,
)
from tracebisect.studio.storage import (
    StudioConfigurationError,
    StudioPersistenceError,
    create_studio_store,
    ensure_studio_schema,
)


@dataclass
class _Execution:
    contains: str
    one: object = None
    many: tuple[tuple[object, ...], ...] = ()
    rowcount: int = 1


class _Result:
    def __init__(self, execution: _Execution) -> None:
        self._execution = execution

    def fetchone(self) -> object:
        return self._execution.one

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._execution.many)

    @property
    def rowcount(self) -> int:
        return self._execution.rowcount

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._execution.many)


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection
        self._result: _Result | None = None

    def executemany(self, statement: str, params: object) -> None:
        self._result = self._connection.execute(statement, params)

    @property
    def rowcount(self) -> int:
        return self._result.rowcount if self._result is not None else -1

    def fetchone(self) -> object:
        return self._result.fetchone() if self._result is not None else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._result.fetchall() if self._result is not None else []

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._result) if self._result is not None else iter(())


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

    def cursor(self) -> _Cursor:
        return _Cursor(self)


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


class _LiveEmailTransport:
    def __init__(self, provider_message_id: str) -> None:
        self.provider_message_id = provider_message_id
        self.messages: list[StudioEmailMessage] = []

    def send(self, message: StudioEmailMessage, *, idempotency_key: str) -> str:
        assert idempotency_key.startswith("tracebisect-invite-")
        self.messages.append(message)
        return self.provider_message_id


def test_postgres_schema_uses_jsonb_timestamps_constraints_and_workspace_indexes() -> None:
    schema = "\n".join(POSTGRES_SCHEMA_STATEMENTS).lower()

    assert "jsonb not null" in schema
    assert "timestamptz not null" in schema
    assert "singleton boolean primary key" in schema
    assert "primary key (workspace_id, trace_key)" in schema
    assert "workspace_id ~" in schema
    assert "studio_reports_workspace_created_idx" in schema
    assert "studio_cases_workspace_updated_idx" in schema
    assert "create table if not exists studio_api_keys" in schema
    assert "create table if not exists studio_ingestion_tokens" in schema
    assert "scope text not null check (scope = 'trace:write')" in schema
    assert "studio_ingestion_tokens_active_workspace_expiry_idx" in schema
    assert "create table if not exists studio_error_events" in schema
    assert "studio_error_events_request_idx" in schema
    assert "studio_error_events_fingerprint_idx" in schema
    assert "studio_error_events_retention_idx" in schema
    assert "studio_error_events_workspace_retention_idx" in schema
    assert "create table if not exists studio_browser_sessions" in schema
    assert "references studio_api_keys(key_id) on delete cascade" in schema
    assert "create table if not exists studio_users" in schema
    assert "create table if not exists studio_workspace_memberships" in schema
    assert "create table if not exists studio_invitations" in schema
    assert "create table if not exists studio_recovery_codes" in schema
    assert "create table if not exists studio_identity_sessions" in schema
    assert "references studio_users(user_id) on delete cascade" in schema
    assert "references studio_workspace_memberships(workspace_id, user_id)" in schema
    assert "studio_memberships_user_workspace_idx" in schema
    assert "studio_invitations_pending_workspace_email_idx" in schema
    assert "studio_recovery_codes_active_user_idx" in schema
    assert "studio_identity_sessions_active_expiry_idx" in schema
    assert "create table if not exists studio_email_outbox" in schema
    assert "references studio_invitations(invitation_id) on delete cascade" in schema
    assert "studio_email_outbox_due_idx" in schema
    assert "create table if not exists studio_email_webhook_events" in schema
    assert "studio_email_webhook_provider_idx" in schema
    assert "create table if not exists studio_rate_limit_buckets" in schema
    assert "timestamptz[] not null" in schema
    assert "studio_rate_limit_buckets_expiry_idx" in schema
    assert "where revoked_at is null" in schema


def test_postgres_schema_upgrades_core_v1_to_error_retention_v7() -> None:
    statements = [" ".join(statement.split()) for statement in POSTGRES_SCHEMA_STATEMENTS]
    connection = _Connection(
        [
            _Execution("pg_advisory_xact_lock"),
            _Execution(statements[0]),
            _Execution("SELECT version FROM studio_postgres_schema", one=(1,)),
            *[_Execution(statement) for statement in statements[1:]],
            _Execution("UPDATE studio_postgres_schema SET version"),
        ]
    )
    pool = _Pool(connection)

    ensure_postgres_schema(pool)  # type: ignore[arg-type]

    assert connection.calls[-1][1] == (postgres_storage.POSTGRES_SCHEMA_VERSION,)
    assert not connection.executions


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


def test_postgres_append_only_trace_upload_cannot_replace_existing_evidence() -> None:
    store, connection, _pool = _store(
        [
            _Execution("pg_advisory_xact_lock"),
            _Execution("SELECT 1 FROM studio_traces", one=(1,)),
        ]
    )
    trace = build_refund_baseline_trace()

    with pytest.raises(StudioTraceConflictError, match="already exists"):
        store.add_trace(trace, name="Agent upload", replace_existing=False)

    assert all("INSERT INTO studio_traces" not in call[0] for call in connection.calls)
    assert not connection.executions


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


def test_postgres_managed_key_creation_and_resolution_reuse_the_shared_pool() -> None:
    store, connection, _pool = _store(
        [
            _Execution("pg_advisory_xact_lock"),
            _Execution("SELECT COUNT(*) FROM studio_api_keys", one=(0,)),
            _Execution("INSERT INTO studio_api_keys"),
        ]
    )
    pepper = "managed-postgres-pepper-with-at-least-32-characters"

    issued = create_studio_api_key(
        store,
        workspace_id="workspace-a",
        role="admin",
        label="First PostgreSQL admin",
        expires_in_days=30,
        pepper=pepper,
    )
    insert_params = connection.calls[-1][1]
    assert isinstance(insert_params, tuple)
    stored_hash = insert_params[4]
    connection.executions.extend(
        [
            _Execution("SET TRANSACTION READ ONLY"),
            _Execution(
                "SELECT workspace_id, role, key_hash, expires_at, revoked_at",
                one=(
                    "workspace-a",
                    "admin",
                    stored_hash,
                    issued.record.expires_at,
                    None,
                ),
            ),
        ]
    )

    principal = principal_for_managed_api_key(
        store,
        api_key=issued.api_key,
        pepper=pepper,
    )

    assert principal is not None
    assert principal.workspace_id == "workspace-a"
    assert principal.role == "admin"
    assert not connection.executions


def test_postgres_ingestion_token_creation_and_resolution_reuse_the_shared_pool() -> None:
    store, connection, _pool = _store(
        [
            _Execution("pg_advisory_xact_lock"),
            _Execution("SELECT COUNT(*) FROM studio_ingestion_tokens", one=(0,)),
            _Execution("INSERT INTO studio_ingestion_tokens"),
        ]
    )
    pepper = "managed-postgres-pepper-with-at-least-32-characters"

    issued = create_studio_ingestion_token(
        store,
        workspace_id="workspace-a",
        label="Production agent",
        expires_in_days=30,
        pepper=pepper,
    )
    insert_params = connection.calls[-1][1]
    assert isinstance(insert_params, tuple)
    stored_hash = insert_params[4]
    assert issued.token not in str(insert_params)
    connection.executions.extend(
        [
            _Execution("SET TRANSACTION READ ONLY"),
            _Execution(
                "SELECT workspace_id, scope, token_hash, expires_at, revoked_at",
                one=(
                    "workspace-a",
                    "trace:write",
                    stored_hash,
                    issued.record.expires_at,
                    None,
                ),
            ),
        ]
    )

    principal = principal_for_managed_ingestion_token(
        store,
        token=issued.token,
        pepper=pepper,
    )

    assert principal is not None
    assert principal.workspace_id == "workspace-a"
    assert principal.scope == "trace:write"
    assert all("?" not in statement for statement, _params in connection.calls)
    assert not connection.executions


def test_postgres_error_retention_and_search_reuse_the_shared_pool() -> None:
    store, connection, _pool = _store(
        [
            _Execution("pg_advisory_xact_lock"),
            _Execution("DELETE FROM studio_error_events WHERE occurred_at"),
            _Execution("INSERT INTO studio_error_events"),
            _Execution("workspace_id IS NOT DISTINCT FROM"),
            _Execution("DELETE FROM studio_error_events"),
        ]
    )
    reporter = StudioErrorReporter(managed_database=store)
    try:
        raise RuntimeError("secret customer message")
    except RuntimeError as error:
        reporter.emit_unhandled(
            request_id="request-postgres-1234",
            method="POST",
            path="/api/compare",
            workspace_id="workspace-a",
            error=error,
            now=datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc),
        )
    insert_params = next(
        params
        for statement, params in connection.calls
        if "INSERT INTO studio_error_events" in statement
    )
    assert isinstance(insert_params, tuple)
    assert "secret customer message" not in str(insert_params)
    connection.executions.append(
        _Execution(
            "SELECT event_id, request_id, workspace_id, action, method",
            many=(
                (
                    insert_params[0],
                    "request-postgres-1234",
                    "workspace-a",
                    "trace_compare",
                    "POST",
                    500,
                    "RuntimeError",
                    insert_params[7],
                    insert_params[8],
                    insert_params[9],
                    1,
                ),
            ),
        )
    )

    records = list_studio_error_events(store, request_id="request-postgres-1234")

    assert len(records) == 1
    assert records[0].request_id == "request-postgres-1234"
    assert records[0].workspace_id == "workspace-a"
    assert reporter.runtime_status()["retention_kind"] == "shared_postgres"
    assert all("?" not in statement for statement, _params in connection.calls)
    assert not connection.executions


def test_postgres_browser_session_is_bounded_resolvable_and_revocable() -> None:
    now = datetime(2026, 7, 21, 5, 0, tzinfo=timezone.utc)
    pepper = "managed-postgres-pepper-with-at-least-32-characters"
    store, connection, _pool = _store(
        [
            _Execution("pg_advisory_xact_lock"),
            _Execution("SELECT COUNT(*) FROM studio_api_keys", one=(0,)),
            _Execution("INSERT INTO studio_api_keys"),
        ]
    )
    issued_key = create_studio_api_key(
        store,
        workspace_id="workspace-a",
        role="editor",
        label="Browser owner",
        expires_in_days=30,
        pepper=pepper,
        now=now,
    )
    key_insert = connection.calls[-1][1]
    assert isinstance(key_insert, tuple)
    key_hash = key_insert[4]
    connection.executions.extend(
        [
            _Execution("SET TRANSACTION READ ONLY"),
            _Execution(
                "SELECT workspace_id, role, key_hash, expires_at, revoked_at",
                one=(
                    "workspace-a",
                    "editor",
                    key_hash,
                    issued_key.record.expires_at,
                    None,
                ),
            ),
            _Execution("pg_advisory_xact_lock"),
            _Execution("DELETE FROM studio_browser_sessions WHERE expires_at"),
            _Execution("DELETE FROM studio_browser_sessions WHERE key_id IN"),
            _Execution("SELECT session_id FROM studio_browser_sessions", many=()),
            _Execution("DELETE FROM studio_browser_sessions WHERE session_id"),
            _Execution("INSERT INTO studio_browser_sessions"),
        ]
    )

    issued_session = issue_studio_browser_session(
        store,
        api_key=issued_key.api_key,
        pepper=pepper,
        ttl_seconds=3600,
        now=now,
    )
    session_insert = connection.calls[-1][1]
    assert isinstance(session_insert, tuple)
    session_hash = session_insert[2]
    assert all("LIMIT -1" not in statement for statement, _params in connection.calls)
    connection.executions.extend(
        [
            _Execution("SET TRANSACTION READ ONLY"),
            _Execution(
                "FROM studio_browser_sessions AS session",
                one=(
                    session_hash,
                    issued_session.principal.expires_at,
                    None,
                    issued_key.record.key_id,
                    "workspace-a",
                    "editor",
                    issued_key.record.expires_at,
                    None,
                ),
            ),
            _Execution(
                "SELECT session_hash, revoked_at",
                one=(session_hash, None),
            ),
            _Execution("UPDATE studio_browser_sessions SET revoked_at"),
        ]
    )

    principal = principal_for_studio_browser_session(
        store,
        session_token=issued_session.session_token,
        pepper=pepper,
        now=now,
    )
    revoked = revoke_studio_browser_session(
        store,
        session_token=issued_session.session_token,
        pepper=pepper,
        now=now,
    )

    assert principal is not None
    assert principal.workspace_id == "workspace-a"
    assert principal.role == "editor"
    assert revoked is True
    assert not connection.executions


def test_postgres_manual_invitation_reuses_shared_identity_rules_and_pool() -> None:
    store, connection, _pool = _store(
        [
            _Execution("pg_advisory_xact_lock"),
            _Execution("SELECT COUNT(*) FROM studio_workspace_memberships", one=(0,)),
            _Execution("SELECT COUNT(*) FROM studio_invitations", one=(0,)),
            _Execution("SELECT invitation_id FROM studio_invitations", many=()),
            _Execution("DELETE FROM studio_invitations WHERE invitation_id"),
            _Execution("UPDATE studio_invitations SET revoked_at"),
            _Execution("INSERT INTO studio_invitations"),
        ]
    )

    invitation = create_studio_invitation(
        store,
        workspace_id="workspace-a",
        email="New.Owner@Example.com",
        role="admin",
        expires_in_days=7,
        identity_secret_value="identity-postgres-secret-with-at-least-32-characters",
    )

    assert invitation.record.email == "new.owner@example.com"
    assert invitation.invitation_token.startswith("tbiv_")
    assert not connection.executions
    assert all("?" not in statement for statement, _params in connection.calls)
    assert all("LIMIT -1" not in statement for statement, _params in connection.calls)


def test_postgres_email_outbox_reuses_shared_pool_and_encrypts_payload() -> None:
    expires_at = "2026-07-22T08:00:00Z"
    store, connection, _pool = _store(
        [
            _Execution("pg_advisory_xact_lock"),
            _Execution(
                "SELECT expires_at, accepted_at, revoked_at FROM studio_invitations",
                one=(expires_at, None, None),
            ),
            _Execution("SELECT 1 FROM studio_email_outbox", one=None),
            _Execution("SELECT message_id FROM studio_email_outbox", many=()),
            _Execution("DELETE FROM studio_email_outbox"),
            _Execution("SELECT COUNT(*) FROM studio_email_outbox", one=(0,)),
            _Execution("INSERT INTO studio_email_outbox"),
        ]
    )
    identity_secret = "postgres-email-secret-with-at-least-thirty-two-characters"
    delivery = StudioEmailDelivery.from_env(
        {
            "TRACEBISECT_STUDIO_STORAGE": "postgres",
            "TRACEBISECT_STUDIO_IDENTITY_SECRET": identity_secret,
            "TRACEBISECT_STUDIO_EMAIL_PROVIDER": "resend",
            "TRACEBISECT_STUDIO_EMAIL_FROM": "TraceBisect <invites@example.com>",
            "TRACEBISECT_STUDIO_PUBLIC_URL": "https://studio.example.com",
            "RESEND_API_KEY": "re_postgres_queue_key_long_enough",
        },
        managed_database=store,
    )
    issued = IssuedStudioInvitation(
        record=StudioInvitationRecord(
            invitation_id="invite123456",
            workspace_id="workspace-a",
            email="new.person@example.com",
            role="viewer",
            created_at="2026-07-21T08:00:00Z",
            expires_at=expires_at,
            accepted_at=None,
            revoked_at=None,
        ),
        invitation_token="tbiv_invite123456_secret-value-never-store-plaintext",
    )

    queued = delivery.queue_invitation(
        issued,
        now=datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc),
    )

    assert queued.status == "pending"
    insert_params = connection.calls[-1][1]
    assert isinstance(insert_params, tuple)
    assert str(insert_params[4]).startswith("v1.")
    assert issued.invitation_token not in str(insert_params)
    assert all("?" not in statement for statement, _params in connection.calls)
    assert all("LIMIT -1" not in statement for statement, _params in connection.calls)
    assert not connection.executions


def test_postgres_migration_uses_one_shared_transaction_and_parameterized_inserts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as source_connection:
        ensure_studio_schema(source_connection)
        source_connection.execute(
            "INSERT INTO studio_reports VALUES (?, ?, ?, ?)",
            (
                "workspace-a",
                "report-migration",
                '{"report_id":"report-migration"}',
                "2026-07-21T08:00:00Z",
            ),
        )
        source_connection.execute(
            "INSERT INTO studio_metadata VALUES (?, ?, ?)",
            ("workspace-a", "migration-proof", "copied"),
        )

    executions = [_Execution("pg_advisory_xact_lock")]
    executions.extend(
        _Execution(f"SELECT COUNT(*) FROM {table.name}", one=(0,))
        for table in MIGRATION_TABLES
    )
    executions.extend(
        [
            _Execution("INSERT INTO studio_reports"),
            _Execution("INSERT INTO studio_metadata"),
        ]
    )
    executions.extend(
        _Execution(
            f"SELECT {', '.join(table.columns)} FROM {table.name}",
            many=(
                (
                    (
                        "workspace-a",
                        "report-migration",
                        {"report_id": "report-migration"},
                        "2026-07-21T08:00:00Z",
                    ),
                )
                if table.name == "studio_reports"
                else (
                    (("workspace-a", "migration-proof", "copied"),)
                    if table.name == "studio_metadata"
                    else ()
                )
            ),
        )
        for table in MIGRATION_TABLES
    )
    store, connection, _pool = _store(executions)

    report = migrate_sqlite_to_postgres(source, store)

    assert report.workspace_count == 1
    assert report.total_rows == 2
    report_insert = next(
        params
        for statement, params in connection.calls
        if "INSERT INTO studio_reports" in statement
    )
    assert isinstance(report_insert, list)
    assert isinstance(report_insert[0][2], Jsonb)
    assert all("?" not in statement for statement, _params in connection.calls)
    assert not connection.executions


def test_managed_postgres_auth_enables_browser_sessions_and_optional_identity() -> None:
    store, _connection, _pool = _store([])
    pepper = "managed-postgres-pepper-with-at-least-32-characters"
    base = {
        "TRACEBISECT_STUDIO_STORAGE": "postgres",
        "TRACEBISECT_STUDIO_DATABASE_URL": "postgresql://db.example/studio",
        "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
        "TRACEBISECT_STUDIO_API_KEY_PEPPER": pepper,
    }
    config = StudioAuthConfig.from_env(base, managed_database=store)

    assert config.access_management_enabled is True
    assert config.browser_sessions_enabled is True
    assert config.identity_enabled is False
    identity_config = StudioAuthConfig.from_env(
        {
            **base,
            "TRACEBISECT_STUDIO_IDENTITY_SECRET": "i" * 40,
        },
        managed_database=store,
    )
    assert identity_config.identity_enabled is True


def test_static_workspace_keys_can_still_protect_postgres() -> None:
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

    assert (
        "pooled multi-instance PostgreSQL workspace and managed-security storage"
        in readiness["completed"]
    )
    assert (
        "atomic reconciled SQLite-to-PostgreSQL cutover tooling"
        in readiness["completed"]
    )
    assert "verified local backup and non-destructive restore tooling" not in readiness["completed"]
    assert "PostgreSQL invitation email outbox" in readiness["blockers"][0]


def test_production_readiness_claims_shared_postgres_rate_limiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed_store, _connection, _pool = _store([])
    monkeypatch.setattr(
        studio_api,
        "RATE_LIMITER",
        PostgresStudioRateLimiter(managed_store),
    )

    readiness = studio_api._production_readiness(
        storage_ok=True,
        runtime={
            "kind": "postgres",
            "durable": True,
            "workspace_id": "protected",
            "trace_count": 0,
            "report_count": 0,
            "case_count": 0,
        },
    )

    assert "shared PostgreSQL sliding-window rate limiting" in readiness["completed"]
    assert (
        "distributed rate limiting across API replicas" not in readiness["blockers"]
    )


def test_production_readiness_reports_postgres_human_identity_without_email_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed_store, _connection, _pool = _store([])
    auth = StudioAuthConfig.from_env(
        {
            "TRACEBISECT_STUDIO_STORAGE": "postgres",
            "TRACEBISECT_STUDIO_DATABASE_URL": "postgresql://db.example/studio",
            "TRACEBISECT_STUDIO_AUTH_MODE": "api-key",
            "TRACEBISECT_STUDIO_API_KEY_PEPPER": (
                "managed-postgres-pepper-with-at-least-32-characters"
            ),
            "TRACEBISECT_STUDIO_IDENTITY_SECRET": "i" * 40,
        },
        managed_database=managed_store,
    )

    monkeypatch.setattr(studio_api, "AUTH_CONFIG", auth)
    readiness = studio_api._production_readiness(
        storage_ok=True,
        runtime={
            "kind": "postgres",
            "durable": True,
            "workspace_id": "protected",
            "trace_count": 0,
            "report_count": 0,
            "case_count": 0,
        },
    )

    assert "Argon2id human accounts with invitation-only enrollment" in readiness["completed"]
    assert "workspace-scoped team membership administration" in readiness["completed"]
    assert not any(
        "managed user accounts" in blocker for blocker in readiness["blockers"]
    )
    assert any("transactional invitation email" in blocker for blocker in readiness["blockers"])
    assert any("PostgreSQL invitation email outbox" in blocker for blocker in readiness["blockers"])


def test_production_readiness_claims_postgres_email_only_with_shared_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed_store, _connection, _pool = _store([])
    env = {
        "TRACEBISECT_STUDIO_STORAGE": "postgres",
        "TRACEBISECT_STUDIO_IDENTITY_SECRET": "i" * 40,
        "TRACEBISECT_STUDIO_EMAIL_PROVIDER": "resend",
        "TRACEBISECT_STUDIO_EMAIL_FROM": "TraceBisect <invites@example.com>",
        "TRACEBISECT_STUDIO_PUBLIC_URL": "https://studio.example.com",
        "RESEND_API_KEY": "re_postgres_test_key_long_enough",
    }
    delivery = StudioEmailDelivery.from_env(env, managed_database=managed_store)
    monkeypatch.setattr(studio_api, "EMAIL_DELIVERY", delivery)

    readiness = studio_api._production_readiness(
        storage_ok=True,
        runtime={
            "kind": "postgres",
            "durable": True,
            "workspace_id": "protected",
            "trace_count": 0,
            "report_count": 0,
            "case_count": 0,
        },
    )

    assert (
        "PostgreSQL invitation email outbox, worker leases, and delivery reconciliation"
        in readiness["completed"]
    )
    assert not any(
        "PostgreSQL invitation email outbox" in blocker
        for blocker in readiness["blockers"]
    )


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
    accepted_user_id: str | None = None
    provider_message_id: str | None = None
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

        pepper = "live-postgres-test-pepper-with-at-least-32-characters"
        issued_key = create_studio_api_key(
            first,
            workspace_id=workspace_id,
            role="admin",
            label="Live parity admin",
            expires_in_days=1,
            pepper=pepper,
        )
        issued_session = issue_studio_browser_session(
            first,
            api_key=issued_key.api_key,
            pepper=pepper,
            ttl_seconds=3600,
        )
        session_principal = principal_for_studio_browser_session(
            restored,
            session_token=issued_session.session_token,
            pepper=pepper,
        )
        assert session_principal is not None
        assert session_principal.workspace_id == workspace_id
        revoke_studio_api_key(first, key_id=issued_key.record.key_id)
        assert (
            principal_for_studio_browser_session(
                restored,
                session_token=issued_session.session_token,
                pepper=pepper,
            )
            is None
        )

        issued_ingestion = create_studio_ingestion_token(
            first,
            workspace_id=workspace_id,
            label="Live production agent",
            expires_in_days=1,
            pepper=pepper,
        )
        ingestion_principal = principal_for_managed_ingestion_token(
            restored,
            token=issued_ingestion.token,
            pepper=pepper,
        )
        assert ingestion_principal is not None
        assert ingestion_principal.workspace_id == workspace_id
        assert ingestion_principal.scope == "trace:write"
        revoke_studio_ingestion_token(
            first,
            token_id=issued_ingestion.record.token_id,
        )
        assert (
            principal_for_managed_ingestion_token(
                restored,
                token=issued_ingestion.token,
                pepper=pepper,
            )
            is None
        )

        error_request_id = f"request-live-{workspace_id}"
        reporter = StudioErrorReporter(managed_database=first)
        try:
            raise RuntimeError("live customer content must not be retained")
        except RuntimeError as error:
            reporter.emit_unhandled(
                request_id=error_request_id,
                method="POST",
                path="/api/compare",
                workspace_id=workspace_id,
                error=error,
            )
        retained_errors = list_studio_error_events(
            restored,
            request_id=error_request_id,
        )
        assert len(retained_errors) == 1
        assert retained_errors[0].workspace_id == workspace_id
        assert retained_errors[0].action == "trace_compare"

        identity_secret = "live-postgres-identity-secret-with-at-least-32-characters"
        invitation = create_studio_invitation(
            first,
            workspace_id=workspace_id,
            email=f"owner-{uuid.uuid4().hex[:12]}@example.com",
            role="admin",
            expires_in_days=1,
            identity_secret_value=identity_secret,
        )
        accepted = accept_studio_invitation(
            restored,
            invitation_token=invitation.invitation_token,
            display_name="Live PostgreSQL Owner",
            password="correct horse battery staple",
            identity_secret_value=identity_secret,
        )
        accepted_user_id = accepted.user.user_id
        assert len(accepted.recovery_codes) == 8
        signed_in = login_studio_identity(
            first,
            email=accepted.user.email,
            password="correct horse battery staple",
            identity_secret_value=identity_secret,
        )
        assert signed_in.session is not None
        identity_session = signed_in.session
        assert (
            principal_for_studio_identity_session(
                restored,
                session_token=identity_session.session_token,
                identity_secret_value=identity_secret,
            )
            == identity_session.principal
        )
        recovered = recover_studio_identity(
            restored,
            email=accepted.user.email,
            recovery_code=accepted.recovery_codes[0],
            new_password="a newly replaced postgres password",
            identity_secret_value=identity_secret,
        )
        assert recovered.accepted is True
        assert (
            principal_for_studio_identity_session(
                first,
                session_token=identity_session.session_token,
                identity_secret_value=identity_secret,
            )
            is None
        )
        assert (
            login_studio_identity(
                first,
                email=accepted.user.email,
                password="a newly replaced postgres password",
                identity_secret_value=identity_secret,
            ).session
            is not None
        )

        email_invitation = create_studio_invitation(
            first,
            workspace_id=workspace_id,
            email=f"email-{uuid.uuid4().hex[:12]}@example.com",
            role="viewer",
            expires_in_days=1,
            identity_secret_value=identity_secret,
        )
        webhook_secret = "whsec_" + base64.b64encode(
            b"tracebisect-live-postgres-webhook-secret"
        ).decode()
        email_env = {
            "TRACEBISECT_STUDIO_STORAGE": "postgres",
            "TRACEBISECT_STUDIO_IDENTITY_SECRET": identity_secret,
            "TRACEBISECT_STUDIO_EMAIL_PROVIDER": "resend",
            "TRACEBISECT_STUDIO_EMAIL_FROM": "TraceBisect <invites@example.com>",
            "TRACEBISECT_STUDIO_PUBLIC_URL": "https://studio.example.com",
            "RESEND_API_KEY": "re_live_postgres_key_long_enough",
            "RESEND_WEBHOOK_SECRET": webhook_secret,
        }
        delivery = StudioEmailDelivery.from_env(email_env, managed_database=first)
        restored_delivery = StudioEmailDelivery.from_env(
            email_env,
            managed_database=restored,
        )
        queued = delivery.queue_invitation(email_invitation)
        provider_message_id = f"live-provider-{uuid.uuid4().hex[:16]}"
        transport = _LiveEmailTransport(provider_message_id)
        sent = restored_delivery.deliver_message(queued.message_id, transport=transport)
        assert sent is not None
        assert sent.status == "sent"
        assert transport.messages[0].to == email_invitation.record.email
        assert email_invitation.invitation_token in transport.messages[0].text

        webhook_time = datetime.now(timezone.utc)
        event_id = f"live-event-{uuid.uuid4().hex[:16]}"
        webhook_body = json.dumps(
            {
                "type": "email.delivered",
                "created_at": webhook_time.isoformat().replace("+00:00", "Z"),
                "data": {"email_id": provider_message_id},
            },
            separators=(",", ":"),
        )
        webhook_headers = {
            "svix-id": event_id,
            "svix-timestamp": str(int(webhook_time.timestamp())),
            "svix-signature": Webhook(webhook_secret).sign(
                event_id,
                webhook_time,
                webhook_body,
            ),
        }
        reconciled = delivery.process_webhook(webhook_body.encode(), webhook_headers)
        assert reconciled.matched is True
        assert delivery.process_webhook(webhook_body.encode(), webhook_headers).duplicate is True
        latest = restored_delivery.latest_for_workspace(workspace_id)
        assert latest[email_invitation.record.invitation_id].provider_status == "delivered"

        other = PostgresStudioStore(database_url, workspace_id=other_workspace_id)
        assert other.list_traces() == []
        assert other.list_report_summaries() == []
        assert other.list_cases() == []
    finally:
        try:
            try:
                (restored or first).clear()
                with first.managed_connection() as connection:
                    if provider_message_id is not None:
                        connection.execute(
                            "DELETE FROM studio_email_webhook_events "
                            "WHERE provider_message_id = ?",
                            (provider_message_id,),
                        )
                    connection.execute(
                        "DELETE FROM studio_api_keys WHERE workspace_id = ?",
                        (workspace_id,),
                    )
                    connection.execute(
                        "DELETE FROM studio_ingestion_tokens WHERE workspace_id = ?",
                        (workspace_id,),
                    )
                    connection.execute(
                        "DELETE FROM studio_error_events WHERE workspace_id = ?",
                        (workspace_id,),
                    )
                    connection.execute(
                        "DELETE FROM studio_invitations WHERE workspace_id = ?",
                        (workspace_id,),
                    )
                    if accepted_user_id is not None:
                        connection.execute(
                            "DELETE FROM studio_users WHERE user_id = ?",
                            (accepted_user_id,),
                        )
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
