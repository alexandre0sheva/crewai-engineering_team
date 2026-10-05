"""Bridge CrewAI's process-wide event bus into a run's own event log.

CrewAI emits events on a single global bus. Handlers are registered once per process and
route each event to the run bound to the *emitting* context: the bus runs handlers on a copy
of the emitter's ``contextvars``, so a run that wraps its work in :func:`bind_run` receives
exactly its own events, even with several runs in one process. Events emitted outside any
bound run (or from a thread that did not inherit the context) are ignored. Parallel workers
must therefore run under ``contextvars.copy_context()``.

CrewAI runs its handlers on a thread pool, so events emitted a moment apart (an agent's and its
task's completion) can be *recorded* in either order. Each bridged event therefore carries
``data["emission"]``, CrewAI's own emission sequence number; sort by it to get emission order.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from crewai.events.event_bus import crewai_event_bus
from crewai.events.types.agent_events import (
    AgentExecutionCompletedEvent,
    AgentExecutionErrorEvent,
    AgentExecutionStartedEvent,
)
from crewai.events.types.crew_events import (
    CrewKickoffCompletedEvent,
    CrewKickoffFailedEvent,
    CrewKickoffStartedEvent,
)
from crewai.events.types.llm_events import LLMCallCompletedEvent, LLMCallFailedEvent
from crewai.events.types.task_events import TaskCompletedEvent, TaskFailedEvent, TaskStartedEvent
from crewai.events.types.tool_usage_events import ToolUsageErrorEvent, ToolUsageFinishedEvent
from crewai.types.usage_metrics import UsageMetrics

from engineering_team.runtime.events import EventSink

MAX_TEXT = 500
FLUSH_TIMEOUT = 30.0


@dataclass(frozen=True)
class _Binding:
    run_id: str
    sink: EventSink


_current: ContextVar[_Binding | None] = ContextVar("engineering_team_run", default=None)
_install_lock = threading.Lock()
_installed = False


@contextlib.contextmanager
def bind_run(run_id: str, sink: EventSink) -> Iterator[None]:
    """Route CrewAI events emitted by this context (and contexts copied from it) to ``sink``."""

    install_bridge()
    token = _current.set(_Binding(run_id, sink))
    try:
        yield
    finally:
        _current.reset(token)


def flush_bridge() -> None:
    """Wait for CrewAI's handler pool to deliver pending events (call before closing a run)."""

    crewai_event_bus.flush(FLUSH_TIMEOUT)


def _clip(value: Any, limit: int = MAX_TEXT) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _role(candidate: Any) -> str | None:
    role = getattr(candidate, "role", None)
    return str(role) if role else None


def _agent(event: Any) -> str | None:
    """The acting agent's role, wherever this event type keeps it."""

    task = getattr(event, "task", None)
    for candidate in (
        getattr(event, "agent_role", None),
        _role(getattr(event, "agent", None)),
        _role(getattr(event, "from_agent", None)),
        _role(getattr(task, "agent", None)),
    ):
        if candidate:
            return str(candidate)
    return None


def _task(event: Any) -> str | None:
    task = getattr(event, "task", None)
    name = getattr(event, "task_name", None) or getattr(task, "name", None)
    if not name and task is not None:
        name = _clip(getattr(task, "description", ""), 120)
    return str(name) if name else None


def _raw(output: Any) -> str:
    return _clip(getattr(output, "raw", output))


def _usage(usage: Any) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {}
    metrics = UsageMetrics.from_provider_dict(usage)
    if metrics is None:
        return {}
    return {
        "prompt_tokens": metrics.prompt_tokens,
        "completion_tokens": metrics.completion_tokens,
        "total_tokens": metrics.total_tokens,
        "cached_prompt_tokens": metrics.cached_prompt_tokens,
        "reasoning_tokens": metrics.reasoning_tokens,
        "cache_creation_tokens": metrics.cache_creation_tokens,
    }


def _model(event: Any) -> str:
    """``provider/model``: CrewAI's event carries the bare model name, the agent's LLM knows
    the provider, and prices are keyed by both."""

    model = str(event.model)
    llm = getattr(getattr(event, "from_agent", None), "llm", None)
    provider = getattr(llm, "provider", None)
    return f"{provider}/{model}" if provider and "/" not in model else model


# event class -> (our event type, how to read its data)
_TRANSLATIONS: list[tuple[type, str, Callable[[Any], dict[str, Any]]]] = [
    (CrewKickoffStartedEvent, "crew.started", lambda e: {"crew": e.crew_name}),
    (
        CrewKickoffCompletedEvent,
        "crew.completed",
        lambda e: {"crew": e.crew_name, "total_tokens": e.total_tokens},
    ),
    (
        CrewKickoffFailedEvent,
        "crew.failed",
        lambda e: {"crew": e.crew_name, "error": _clip(e.error)},
    ),
    (TaskStartedEvent, "task.started", lambda e: {"task": _task(e), "task_id": e.task_id}),
    (
        TaskCompletedEvent,
        "task.completed",
        lambda e: {"task": _task(e), "task_id": e.task_id, "output": _raw(e.output)},
    ),
    (
        TaskFailedEvent,
        "task.failed",
        lambda e: {"task": _task(e), "task_id": e.task_id, "error": _clip(e.error)},
    ),
    (AgentExecutionStartedEvent, "agent.started", lambda e: {"task": _task(e)}),
    (
        AgentExecutionCompletedEvent,
        "agent.completed",
        lambda e: {"task": _task(e), "output": _clip(e.output)},
    ),
    (
        AgentExecutionErrorEvent,
        "agent.error",
        lambda e: {"task": _task(e), "error": _clip(e.error)},
    ),
    (
        ToolUsageFinishedEvent,
        "tool.finished",
        lambda e: {
            "tool": e.tool_name,
            "args": _clip(e.tool_args, 200),
            "from_cache": e.from_cache,
            "output_chars": len(str(e.output)),
        },
    ),
    (
        ToolUsageErrorEvent,
        "tool.error",
        lambda e: {"tool": e.tool_name, "args": _clip(e.tool_args, 200), "error": _clip(e.error)},
    ),
    (
        LLMCallCompletedEvent,
        "llm.call",
        lambda e: {
            "model": _model(e),
            "call_id": e.call_id,
            "finish_reason": e.finish_reason,
            "usage": _usage(e.usage),
        },
    ),
    (
        LLMCallFailedEvent,
        "llm.failed",
        lambda e: {"model": _model(e), "call_id": e.call_id, "error": _clip(e.error)},
    ),
]


def _handler(our_type: str, read: Callable[[Any], dict[str, Any]]) -> Callable[[Any, Any], None]:
    def handle(source: Any, event: Any) -> None:
        binding = _current.get()
        if binding is None:
            return
        try:
            data = read(event)
            emission = getattr(event, "emission_sequence", None)
            if emission:
                data["emission"] = emission
            binding.sink.emit(our_type, agent=_agent(event), **data)
        except Exception:  # a bridging bug must never break CrewAI's bus
            return

    handle.__name__ = f"bridge_{our_type.replace('.', '_')}"
    return handle


def install_bridge() -> None:
    """Register the translating handlers on CrewAI's bus, once per process."""

    global _installed
    with _install_lock:
        if _installed:
            return
        for event_class, our_type, read in _TRANSLATIONS:
            crewai_event_bus.on(event_class)(_handler(our_type, read))
        _installed = True
