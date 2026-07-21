"""Durable, workspace-scoped storage for TraceBisect Studio.

The browser product still defaults to an in-memory store so a first-time user
can run it without configuration. Setting ``TRACEBISECT_STUDIO_STORAGE=sqlite``
and an explicit database path enables restart-safe local or single-node
self-hosted storage.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from threading import RLock
from typing import cast

from tracebisect.jsonl import dumps_trace, loads_trace
from tracebisect.schema import JsonObject, JsonValue, Trace
from tracebisect.studio.service import (
    DEFAULT_MAX_STORED_CASES,
    DEFAULT_MAX_STORED_REPORTS,
    DEFAULT_MAX_STORED_TRACES,
    StudioStore,
)

SCHEMA_VERSION = 5
_WORKSPACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_STORAGE_SETTING_NAMES = (
    "TRACEBISECT_STUDIO_STORAGE",
    "TRACEBISECT_STUDIO_SQLITE_PATH",
    "TRACEBISECT_STUDIO_WORKSPACE_ID",
    "TRACEBISECT_STUDIO_MAX_STORED_TRACES",
    "TRACEBISECT_STUDIO_MAX_STORED_REPORTS",
    "TRACEBISECT_STUDIO_MAX_STORED_CASES",
)


class StudioConfigurationError(ValueError):
    """Raised when Studio storage environment variables are unsafe or invalid."""


class StudioPersistenceError(RuntimeError):
    """Raised when the durable store cannot read or commit Studio data."""


class SQLiteStudioStore(StudioStore):
    """Restart-safe Studio store isolated to one configured workspace."""

    __slots__ = ("database_path", "_connection")

    def __init__(
        self,
        database_path: str | Path,
        *,
        workspace_id: str,
        max_traces: int = DEFAULT_MAX_STORED_TRACES,
        max_reports: int = DEFAULT_MAX_STORED_REPORTS,
        max_cases: int = DEFAULT_MAX_STORED_CASES,
    ) -> None:
        validated_workspace_id = validate_workspace_id(workspace_id)
        super().__init__(
            workspace_id=validated_workspace_id,
            max_traces=max_traces,
            max_reports=max_reports,
            max_cases=max_cases,
        )
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        database_was_created = not self.database_path.exists()
        try:
            self._connection = sqlite3.connect(
                self.database_path,
                check_same_thread=False,
                timeout=5,
            )
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = NORMAL")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            if database_was_created:
                self.database_path.chmod(0o600)
            self._initialize_schema()
            self._load_workspace()
        except (OSError, sqlite3.DatabaseError, ValueError) as exc:
            raise StudioPersistenceError(
                "could not initialize the configured SQLite Studio store"
            ) from exc

    def add_trace(self, trace: Trace, *, name: str | None = None) -> str:
        with self._lock:
            trace_key = trace.trace_id
            previous_trace = self.traces.get(trace_key)
            previous_name = self.trace_names.get(trace_key)
            persisted_key = super().add_trace(trace, name=name)
            try:
                with self._connection:
                    self._connection.execute(
                        """
                        INSERT INTO studio_traces (
                            workspace_id, trace_key, display_name, trace_jsonl, created_at
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(workspace_id, trace_key) DO UPDATE SET
                            display_name = excluded.display_name,
                            trace_jsonl = excluded.trace_jsonl,
                            created_at = excluded.created_at
                        """,
                        (
                            self.workspace_id,
                            persisted_key,
                            self.trace_names[persisted_key],
                            dumps_trace(trace),
                            trace.created_at.isoformat(),
                        ),
                    )
            except sqlite3.DatabaseError as exc:
                if previous_trace is None:
                    self.traces.pop(persisted_key, None)
                    self.trace_names.pop(persisted_key, None)
                else:
                    self.traces[persisted_key] = previous_trace
                    if previous_name is not None:
                        self.trace_names[persisted_key] = previous_name
                raise StudioPersistenceError("could not save trace to SQLite") from exc
            return persisted_key

    def add_report(self, report: JsonObject) -> str:
        with self._lock:
            previous_reports = self.reports.copy()
            report_id = super().add_report(report)
            removed_report_ids = previous_reports.keys() - self.reports.keys()
            try:
                with self._connection:
                    for removed_report_id in removed_report_ids:
                        self._connection.execute(
                            "DELETE FROM studio_reports WHERE workspace_id = ? AND report_id = ?",
                            (self.workspace_id, removed_report_id),
                        )
                    self._connection.execute(
                        """
                        INSERT INTO studio_reports (
                            workspace_id, report_id, payload_json, created_at
                        ) VALUES (?, ?, ?, ?)
                        ON CONFLICT(workspace_id, report_id) DO UPDATE SET
                            payload_json = excluded.payload_json,
                            created_at = excluded.created_at
                        """,
                        (
                            self.workspace_id,
                            report_id,
                            _dump_json_object(report),
                            _required_json_string(report, "created_at"),
                        ),
                    )
            except sqlite3.DatabaseError as exc:
                self.reports = previous_reports
                raise StudioPersistenceError("could not save comparison report to SQLite") from exc
            return report_id

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
        case = super().add_case_from_report(
            name=name,
            description=description,
            tags=tags,
            baseline_trace_id=baseline_trace_id,
            candidate_trace_id=candidate_trace_id,
            scenario_cmd=scenario_cmd,
            assertions=assertions,
            cost_threshold=cost_threshold,
        )
        case_id = _required_json_string(case, "case_id")
        try:
            self._persist_case(case)
        except StudioPersistenceError:
            with self._lock:
                self.cases.pop(case_id, None)
            raise
        return case

    def run_case(
        self,
        case_id: str,
        *,
        candidate_trace_id: str,
        scenario_cmd: list[str],
    ) -> tuple[JsonObject, JsonObject]:
        with self._lock:
            previous_case = self.get_case(case_id)
            updated, report = super().run_case(
                case_id,
                candidate_trace_id=candidate_trace_id,
                scenario_cmd=scenario_cmd,
            )
            try:
                self._persist_case(updated)
            except StudioPersistenceError:
                self.cases[case_id] = previous_case
                raise
            return updated, report

    def set_demo_report_id(self, report_id: str | None) -> None:
        with self._lock:
            previous_report_id = self.demo_report_id
            super().set_demo_report_id(report_id)
            try:
                with self._connection:
                    if report_id is None:
                        self._connection.execute(
                            "DELETE FROM studio_metadata WHERE workspace_id = ? AND key = ?",
                            (self.workspace_id, "demo_report_id"),
                        )
                    else:
                        self._connection.execute(
                            """
                            INSERT INTO studio_metadata (workspace_id, key, value)
                            VALUES (?, ?, ?)
                            ON CONFLICT(workspace_id, key) DO UPDATE SET value = excluded.value
                            """,
                            (self.workspace_id, "demo_report_id", report_id),
                        )
            except sqlite3.DatabaseError as exc:
                self.demo_report_id = previous_report_id
                raise StudioPersistenceError("could not save Studio metadata to SQLite") from exc

    def clear(self) -> None:
        with self._lock:
            try:
                with self._connection:
                    for statement in (
                        "DELETE FROM studio_traces WHERE workspace_id = ?",
                        "DELETE FROM studio_reports WHERE workspace_id = ?",
                        "DELETE FROM studio_cases WHERE workspace_id = ?",
                        "DELETE FROM studio_metadata WHERE workspace_id = ?",
                    ):
                        self._connection.execute(
                            statement,
                            (self.workspace_id,),
                        )
            except sqlite3.DatabaseError as exc:
                raise StudioPersistenceError("could not clear the SQLite workspace") from exc
            super().clear()

    def check_health(self) -> bool:
        with self._lock:
            try:
                row = self._connection.execute("SELECT 1").fetchone()
            except sqlite3.DatabaseError:
                return False
            return cast(tuple[int], row) == (1,)

    def runtime_status(self) -> JsonObject:
        with self._lock:
            return {
                "kind": "sqlite",
                "durable": True,
                "workspace_id": self.workspace_id,
                "trace_count": len(self.traces),
                "report_count": len(self.reports),
                "case_count": len(self.cases),
            }

    def close(self) -> None:
        """Close the SQLite connection after an application or test shuts down."""
        with self._lock:
            self._connection.close()

    def _initialize_schema(self) -> None:
        ensure_studio_schema(self._connection)

    def _load_workspace(self) -> None:
        with self._lock:
            self.traces.clear()
            self.trace_names.clear()
            self.reports.clear()
            self.cases.clear()
            for trace_key, display_name, trace_jsonl in self._connection.execute(
                """
                SELECT trace_key, display_name, trace_jsonl
                FROM studio_traces
                WHERE workspace_id = ?
                ORDER BY created_at, rowid
                """,
                (self.workspace_id,),
            ):
                key = str(trace_key)
                self.traces[key] = loads_trace(str(trace_jsonl))
                self.trace_names[key] = str(display_name)
            for report_id, payload_json in self._connection.execute(
                """
                SELECT report_id, payload_json
                FROM studio_reports
                WHERE workspace_id = ?
                ORDER BY created_at, rowid
                """,
                (self.workspace_id,),
            ):
                self.reports[str(report_id)] = _load_json_object(str(payload_json))
            for case_id, payload_json in self._connection.execute(
                """
                SELECT case_id, payload_json
                FROM studio_cases
                WHERE workspace_id = ?
                ORDER BY updated_at, rowid
                """,
                (self.workspace_id,),
            ):
                self.cases[str(case_id)] = _load_json_object(str(payload_json))
            metadata = self._connection.execute(
                """
                SELECT value FROM studio_metadata
                WHERE workspace_id = ? AND key = ?
                """,
                (self.workspace_id, "demo_report_id"),
            ).fetchone()
            self.demo_report_id = str(metadata[0]) if metadata is not None else None

    def _persist_case(self, case: JsonObject) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO studio_cases (workspace_id, case_id, payload_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(workspace_id, case_id) DO UPDATE SET
                        payload_json = excluded.payload_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        self.workspace_id,
                        _required_json_string(case, "case_id"),
                        _dump_json_object(case),
                        _required_json_string(case, "updated_at"),
                    ),
                )
        except sqlite3.DatabaseError as exc:
            raise StudioPersistenceError("could not save regression case to SQLite") from exc


def ensure_studio_schema(connection: sqlite3.Connection) -> None:
    """Create the current Studio schema and migrate supported older databases."""
    with connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS studio_schema (
                version INTEGER NOT NULL
            )
            """
        )
        row = connection.execute("SELECT version FROM studio_schema LIMIT 1").fetchone()
        if row is None:
            database_version = SCHEMA_VERSION
            connection.execute(
                "INSERT INTO studio_schema (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
        elif not isinstance(row[0], int) or row[0] not in {1, 2, 3, 4, SCHEMA_VERSION}:
            raise StudioPersistenceError(f"unsupported Studio database schema version {row[0]!r}")
        else:
            database_version = row[0]
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS studio_traces (
                workspace_id TEXT NOT NULL,
                trace_key TEXT NOT NULL,
                display_name TEXT NOT NULL,
                trace_jsonl TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (workspace_id, trace_key)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS studio_reports (
                workspace_id TEXT NOT NULL,
                report_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (workspace_id, report_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS studio_cases (
                workspace_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (workspace_id, case_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS studio_metadata (
                workspace_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (workspace_id, key)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS studio_api_keys (
                key_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('viewer', 'editor', 'admin')),
                label TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            )
            """,
        )
        key_columns = {
            str(column[1]) for column in connection.execute("PRAGMA table_info(studio_api_keys)")
        }
        if "role" not in key_columns:
            connection.execute(
                "ALTER TABLE studio_api_keys ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'"
            )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS studio_api_keys_workspace_idx
            ON studio_api_keys (workspace_id)
            """,
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS studio_browser_sessions (
                session_id TEXT PRIMARY KEY,
                key_id TEXT NOT NULL,
                session_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                FOREIGN KEY (key_id) REFERENCES studio_api_keys (key_id) ON DELETE CASCADE
            )
            """,
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS studio_browser_sessions_key_idx
            ON studio_browser_sessions (key_id)
            """,
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS studio_browser_sessions_expiry_idx
            ON studio_browser_sessions (expires_at)
            """,
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS studio_users (
                user_id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                session_epoch INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                password_changed_at TEXT NOT NULL,
                disabled_at TEXT
            )
            """,
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS studio_workspace_memberships (
                workspace_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('viewer', 'editor', 'admin')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (workspace_id, user_id),
                FOREIGN KEY (user_id) REFERENCES studio_users (user_id) ON DELETE CASCADE
            )
            """,
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS studio_memberships_user_idx
            ON studio_workspace_memberships (user_id)
            """,
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS studio_invitations (
                invitation_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                email TEXT NOT NULL COLLATE NOCASE,
                role TEXT NOT NULL CHECK (role IN ('viewer', 'editor', 'admin')),
                token_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                accepted_at TEXT,
                revoked_at TEXT
            )
            """,
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS studio_invitations_workspace_idx
            ON studio_invitations (workspace_id, created_at)
            """,
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS studio_recovery_codes (
                code_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                used_at TEXT,
                FOREIGN KEY (user_id) REFERENCES studio_users (user_id) ON DELETE CASCADE
            )
            """,
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS studio_recovery_codes_user_idx
            ON studio_recovery_codes (user_id)
            """,
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS studio_identity_sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                session_hash TEXT NOT NULL,
                session_epoch INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                FOREIGN KEY (user_id) REFERENCES studio_users (user_id) ON DELETE CASCADE,
                FOREIGN KEY (workspace_id, user_id)
                    REFERENCES studio_workspace_memberships (workspace_id, user_id)
                    ON DELETE CASCADE
            )
            """,
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS studio_identity_sessions_user_idx
            ON studio_identity_sessions (user_id)
            """,
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS studio_identity_sessions_expiry_idx
            ON studio_identity_sessions (expires_at)
            """,
        )
        if database_version < SCHEMA_VERSION:
            connection.execute("UPDATE studio_schema SET version = ?", (SCHEMA_VERSION,))


class StudioStoreRegistry:
    """Create and cache one isolated store per validated request workspace."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        values = os.environ if env is None else env
        self._settings = {name: values[name] for name in _STORAGE_SETTING_NAMES if name in values}
        self._lock = RLock()
        default_store = create_studio_store(self._settings)
        self._stores: dict[str, StudioStore] = {
            default_store.workspace_id: default_store,
        }
        self.default_store = default_store

    def get(self, workspace_id: str) -> StudioStore:
        """Return the cached store for ``workspace_id``, creating it atomically."""
        validated_workspace_id = validate_workspace_id(workspace_id)
        with self._lock:
            existing = self._stores.get(validated_workspace_id)
            if existing is not None:
                return existing
            workspace_settings = {
                **self._settings,
                "TRACEBISECT_STUDIO_WORKSPACE_ID": validated_workspace_id,
            }
            store = create_studio_store(workspace_settings)
            self._stores[validated_workspace_id] = store
            return store

    def close(self) -> None:
        """Close every cached durable store connection."""
        with self._lock:
            for store in self._stores.values():
                if isinstance(store, SQLiteStudioStore):
                    store.close()
            self._stores.clear()


def create_studio_store(env: Mapping[str, str] | None = None) -> StudioStore:
    """Build the configured Studio store, failing fast on unsafe configuration."""
    values = os.environ if env is None else env
    kind = values.get("TRACEBISECT_STUDIO_STORAGE", "memory").strip().lower()
    workspace_id = validate_workspace_id(values.get("TRACEBISECT_STUDIO_WORKSPACE_ID", "local"))
    max_traces = _positive_int_setting(
        values,
        "TRACEBISECT_STUDIO_MAX_STORED_TRACES",
        DEFAULT_MAX_STORED_TRACES,
    )
    max_reports = _positive_int_setting(
        values,
        "TRACEBISECT_STUDIO_MAX_STORED_REPORTS",
        DEFAULT_MAX_STORED_REPORTS,
    )
    max_cases = _positive_int_setting(
        values,
        "TRACEBISECT_STUDIO_MAX_STORED_CASES",
        DEFAULT_MAX_STORED_CASES,
    )
    if kind == "memory":
        return StudioStore(
            workspace_id=workspace_id,
            max_traces=max_traces,
            max_reports=max_reports,
            max_cases=max_cases,
        )
    if kind != "sqlite":
        raise StudioConfigurationError(
            "TRACEBISECT_STUDIO_STORAGE must be either 'memory' or 'sqlite'"
        )
    raw_path = values.get("TRACEBISECT_STUDIO_SQLITE_PATH", "").strip()
    if not raw_path:
        raise StudioConfigurationError(
            "TRACEBISECT_STUDIO_SQLITE_PATH is required when storage is 'sqlite'"
        )
    return SQLiteStudioStore(
        raw_path,
        workspace_id=workspace_id,
        max_traces=max_traces,
        max_reports=max_reports,
        max_cases=max_cases,
    )


def validate_workspace_id(value: str) -> str:
    """Validate and normalize an externally configured workspace identifier."""
    workspace_id = value.strip()
    if not _WORKSPACE_ID_PATTERN.fullmatch(workspace_id):
        raise StudioConfigurationError(
            "TRACEBISECT_STUDIO_WORKSPACE_ID must be 1-64 letters, numbers, dots, dashes, "
            "or underscores and must start with a letter or number"
        )
    return workspace_id


def _positive_int_setting(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name, str(default)).strip()
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise StudioConfigurationError(f"{name} must be a positive integer") from exc
    if parsed < 1:
        raise StudioConfigurationError(f"{name} must be a positive integer")
    return parsed


def _dump_json_object(value: JsonObject) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _load_json_object(value: str) -> JsonObject:
    parsed: object = json.loads(value)
    if not isinstance(parsed, dict):
        raise StudioPersistenceError("stored Studio JSON payload is not an object")
    return cast(JsonObject, parsed)


def _required_json_string(value: JsonObject, key: str) -> str:
    item: JsonValue = value[key]
    if not isinstance(item, str):
        raise StudioPersistenceError(f"Studio payload field {key!r} is not a string")
    return item
