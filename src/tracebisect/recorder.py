"""Native Python recorder helpers for demo and generated TraceBisect scenarios."""

from __future__ import annotations

import functools
import json
import os
import time
import traceback
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, ParamSpec, TypeVar

from tracebisect.jsonl import write_trace
from tracebisect.schema import (
    SCHEMA_VERSION,
    ErrorPayload,
    Event,
    EventPayload,
    EventType,
    JsonObject,
    JsonValue,
    LLMCallPayload,
    RunEndPayload,
    RunStartPayload,
    ToolCallPayload,
    Trace,
)

__all__ = ["TraceRecorder", "record_trace", "wrap_tool"]

P = ParamSpec("P")
R = TypeVar("R")

_ACTIVE_RECORDER: ContextVar[TraceRecorder | None] = ContextVar(
    "tracebisect_active_recorder",
    default=None,
)


def record_trace(
    *,
    user_input: str,
    output_path: str | Path | None = None,
    trace_id: str | None = None,
    agent_name: str | None = None,
    agent_version: str | None = None,
    run_metadata: JsonObject | None = None,
    prompt_version: str | None = None,
    code_sha: str | None = None,
) -> TraceRecorder:
    """Create a native recorder context manager.

    If ``output_path`` is omitted, the context reads ``TRACEBISECT_OUTPUT``.
    This is the same contract used by ``tracebisect record`` and generated
    pytest tests.
    """
    return TraceRecorder(
        user_input=user_input,
        output_path=output_path,
        trace_id=trace_id,
        agent_name=agent_name,
        agent_version=agent_version,
        run_metadata=run_metadata,
        prompt_version=prompt_version,
        code_sha=code_sha,
    )


def wrap_tool(
    func: Callable[P, R] | None = None,
    *,
    name: str | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]] | Callable[P, R]:
    """Record a function call as a TOOL_CALL when a TraceRecorder is active."""

    def decorate(inner: Callable[P, R]) -> Callable[P, R]:
        tool_name = name or inner.__name__

        @functools.wraps(inner)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            recorder = _ACTIVE_RECORDER.get()
            if recorder is None:
                return inner(*args, **kwargs)
            return recorder.record_tool_call(
                tool_name=tool_name,
                func=inner,
                args=args,
                kwargs=kwargs,
            )

        return wrapper

    if func is None:
        return decorate
    return decorate(func)


class TraceRecorder:
    """Small native recorder that writes canonical V1 traces."""

    def __init__(
        self,
        *,
        user_input: str,
        output_path: str | Path | None,
        trace_id: str | None,
        agent_name: str | None,
        agent_version: str | None,
        run_metadata: JsonObject | None,
        prompt_version: str | None,
        code_sha: str | None,
    ) -> None:
        self._user_input = user_input
        self._output_path = Path(output_path) if output_path is not None else None
        self._trace_id = trace_id or f"trc_{uuid.uuid4().hex[:16]}"
        self._agent_name = agent_name
        self._agent_version = agent_version
        self._run_metadata = run_metadata or {}
        self._prompt_version = prompt_version
        self._code_sha = code_sha
        self._events: list[Event] = []
        self._parent_stack: list[str] = []
        self._token: Token[TraceRecorder | None] | None = None
        self._started = False
        self._finished = False
        self._written = False
        self._started_at = datetime.now(timezone.utc)

    def __enter__(self) -> TraceRecorder:
        if self._started:
            raise RuntimeError("TraceRecorder cannot be entered more than once")
        self._started = True
        self._append_run_start()
        self._parent_stack.append(self._events[0].id)
        self._token = _ACTIVE_RECORDER.set(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        exc_tb: object | None,
    ) -> Literal[False]:
        if exc is not None:
            self.record_error(exc)
        if not self._finished:
            self.finish(
                final_output="" if exc is None else str(exc),
                success=exc is None,
            )
        self.write()
        if self._token is not None:
            _ACTIVE_RECORDER.reset(self._token)
        return False

    @contextmanager
    def llm_call(
        self,
        *,
        model: str,
        provider: str,
        messages: list[JsonObject],
        response_text: str = "",
        response_tool_calls: list[JsonObject] | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        temperature: float = 0.0,
        seed: int | None = None,
        semantic_name: str | None = None,
        model_version: str | None = None,
        sampling_params: JsonObject | None = None,
    ) -> Iterator[Event]:
        """Record an LLM_CALL and parent nested tool calls under it."""
        event = self._append_event(
            event_type=EventType.LLM_CALL,
            semantic_name=semantic_name or model,
            parent_id=self._current_parent_id(),
            payload=LLMCallPayload(
                model=model,
                provider=provider,
                messages=messages,
                response_text=response_text,
                response_tool_calls=response_tool_calls or [],
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                temperature=temperature,
                seed=seed,
            ),
            duration_ms=None,
            model_version=model_version or model,
            sampling_params=sampling_params,
        )
        self._parent_stack.append(event.id)
        try:
            yield event
        finally:
            self._parent_stack.pop()

    def record_tool_call(
        self,
        *,
        tool_name: str,
        func: Callable[..., R],
        args: Sequence[object],
        kwargs: Mapping[str, object],
    ) -> R:
        """Run ``func`` and record its result or exception as a TOOL_CALL."""
        started = time.perf_counter()
        arguments = _call_arguments(func, args, kwargs)
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            self._append_tool_event(
                tool_name=tool_name,
                arguments=arguments,
                result=None,
                error=str(exc),
                duration_ms=_duration_since(started),
            )
            raise
        self._append_tool_event(
            tool_name=tool_name,
            arguments=arguments,
            result=_result_to_string(result),
            error=None,
            duration_ms=_duration_since(started),
        )
        return result

    def finish(
        self,
        *,
        final_output: str,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        total_cost_usd: float = 0.0,
        success: bool = True,
    ) -> None:
        """Append the RUN_END event. The trace is written on context exit."""
        if self._finished:
            raise RuntimeError("TraceRecorder.finish() called more than once")
        root_id = self._events[0].id
        self._append_event(
            event_type=EventType.RUN_END,
            semantic_name="run",
            parent_id=root_id,
            payload=RunEndPayload(
                final_output=final_output,
                total_input_tokens=total_input_tokens,
                total_output_tokens=total_output_tokens,
                total_cost_usd=total_cost_usd,
                success=success,
            ),
            duration_ms=None,
        )
        self._finished = True

    def record_error(self, exc: BaseException) -> None:
        """Append an ERROR event for an exception raised during recording."""
        self._append_event(
            event_type=EventType.ERROR,
            semantic_name=type(exc).__name__,
            parent_id=self._current_parent_id(),
            payload=ErrorPayload(
                error_type=type(exc).__name__,
                message=str(exc),
                stack_trace="".join(traceback.format_exception(exc)),
                recovered=False,
            ),
            duration_ms=None,
        )

    def trace(self) -> Trace:
        """Return the current canonical trace. Requires ``finish()`` first."""
        if not self._finished:
            raise RuntimeError("TraceRecorder.trace() requires finish() first")
        return Trace(
            schema_version=SCHEMA_VERSION,
            trace_id=self._trace_id,
            created_at=self._started_at,
            root_event=self._events[0],
            events=list(self._events),
            source_convention="native",
        )

    def write(self) -> None:
        """Write the recorded trace to the configured output path."""
        if self._written:
            return
        output_path = self._resolved_output_path()
        write_trace(self.trace(), output_path)
        self._written = True

    def _append_run_start(self) -> None:
        self._append_event(
            event_type=EventType.RUN_START,
            semantic_name="run",
            parent_id=None,
            payload=RunStartPayload(
                user_input=self._user_input,
                agent_name=self._agent_name,
                agent_version=self._agent_version,
                run_metadata=self._run_metadata,
            ),
            duration_ms=None,
        )

    def _append_tool_event(
        self,
        *,
        tool_name: str,
        arguments: JsonObject,
        result: str | None,
        error: str | None,
        duration_ms: float,
    ) -> None:
        self._append_event(
            event_type=EventType.TOOL_CALL,
            semantic_name=tool_name,
            parent_id=self._current_parent_id(),
            payload=ToolCallPayload(
                tool_name=tool_name,
                arguments=arguments,
                result=result,
                error=error,
            ),
            duration_ms=duration_ms,
        )

    def _append_event(
        self,
        *,
        event_type: EventType,
        semantic_name: str,
        parent_id: str | None,
        payload: EventPayload,
        duration_ms: float | None,
        model_version: str | None = None,
        sampling_params: JsonObject | None = None,
    ) -> Event:
        event_id = f"evt_{len(self._events) + 1:03d}"
        event = Event(
            id=event_id,
            parent_id=parent_id,
            sequence_index=len(self._events),
            type=event_type,
            semantic_name=semantic_name,
            timestamp=datetime.now(timezone.utc),
            duration_ms=duration_ms,
            payload=payload,
            source_format="native",
            source_event_id=event_id,
            model_version=model_version,
            prompt_version=self._prompt_version,
            code_sha=self._code_sha,
            sampling_params=sampling_params,
        )
        self._events.append(event)
        return event

    def _current_parent_id(self) -> str:
        if not self._parent_stack:
            return self._events[0].id
        return self._parent_stack[-1]

    def _resolved_output_path(self) -> Path:
        if self._output_path is not None:
            return self._output_path
        output = os.environ.get("TRACEBISECT_OUTPUT")
        if output is None:
            raise RuntimeError("TRACEBISECT_OUTPUT is required when output_path is omitted")
        return Path(output)


def _call_arguments(
    func: Callable[..., object],
    args: Sequence[object],
    kwargs: Mapping[str, object],
) -> JsonObject:
    try:
        import inspect

        bound = inspect.signature(func).bind(*args, **kwargs)
        bound.apply_defaults()
        raw = dict(bound.arguments)
    except (TypeError, ValueError):
        raw = {"args": list(args), "kwargs": dict(kwargs)}
    return {key: _to_json_value(value) for key, value in raw.items()}


def _to_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_to_json_value(item) for item in value]
    return repr(value)


def _result_to_string(result: object) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(_to_json_value(result), ensure_ascii=False, sort_keys=True)


def _duration_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)
