"""A scripted, offline LLM for deterministic tests of CrewAI agent loops.

``ScriptedLLM`` is a real ``crewai.BaseLLM``, so an unmodified ``Agent`` drives it through the
same executor, tool dispatch, iteration limit, and event bus it uses with a paid model. Each
call to the model consumes the next item of the script; running out of script is an error, so a
test that loops more than expected fails loudly instead of hanging.
"""

from __future__ import annotations

import copy
import json
import threading
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from crewai import BaseLLM
from crewai.events.types.llm_events import LLMCallType
from pydantic import PrivateAttr


@dataclass(frozen=True)
class ToolCall:
    """One scripted request from the model to run a tool."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str | None = None


# What one scripted turn may say: a final answer (or raw ReAct text), one tool call, or several
# parallel tool calls.
Reply = str | ToolCall | Sequence[ToolCall]


@dataclass(frozen=True)
class Turn:
    """A reply with explicit token usage, for tests of cost and budget accounting."""

    reply: Reply | Callable[[list[dict[str, Any]], list[Any] | None], Reply]
    prompt_tokens: int = 10
    completion_tokens: int = 5


ScriptItem = Reply | Turn | Callable[[list[dict[str, Any]], list[Any] | None], Reply]


class ScriptExhausted(AssertionError):
    """The agent called the model more often than the script allows."""


@dataclass(frozen=True)
class RecordedCall:
    """What the model was sent on one call (copied, because the executor keeps mutating it)."""

    messages: list[dict[str, Any]]
    tools: list[Any] | None

    @property
    def prompt(self) -> str:
        """All message text joined, for ``assert "..." in call.prompt``."""

        return "\n".join(str(message.get("content") or "") for message in self.messages)


class ScriptedLLM(BaseLLM):
    """Return scripted replies, in order, to an agent's model calls.

    ``native_tools=True`` (the default) behaves like a provider with native function calling:
    a :class:`ToolCall` becomes a structured tool-call list and a string is the final answer.
    ``native_tools=False`` uses CrewAI's text ReAct protocol: a ``ToolCall`` becomes
    ``Action:`` / ``Action Input:`` text, a plain string is wrapped as ``Final Answer:``, and a
    string that already contains ``Action:`` or ``Final Answer:`` is passed through verbatim.

    Replies may be callables ``(messages, tools) -> reply`` to react to the conversation, and
    ``Turn`` attaches token counts (``usage`` supplies the default per call).
    """

    model: str = "scripted/fake"
    provider: str = "scripted"
    llm_type: str = "scripted"

    _script: deque[ScriptItem] = PrivateAttr(default_factory=deque)
    _native_tools: bool = PrivateAttr(default=True)
    _usage: tuple[int, int] = PrivateAttr(default=(10, 5))
    _calls: list[RecordedCall] = PrivateAttr(default_factory=list)
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    _issued: int = PrivateAttr(default=0)

    def __init__(
        self,
        responses: Sequence[ScriptItem],
        *,
        native_tools: bool = True,
        usage: tuple[int, int] = (10, 5),
        model: str = "scripted/fake",
    ) -> None:
        super().__init__(model=model, provider="scripted")
        self._script = deque(responses)
        self._native_tools = native_tools
        self._usage = usage

    # -- inspection ---------------------------------------------------------------------

    @property
    def calls(self) -> list[RecordedCall]:
        """Every call made so far, oldest first."""

        return list(self._calls)

    @property
    def remaining(self) -> int:
        """Scripted items not yet consumed."""

        return len(self._script)

    def assert_exhausted(self) -> None:
        """Fail if the agent stopped before using the whole script."""

        if self._script:
            raise AssertionError(f"{len(self._script)} scripted response(s) were never used.")

    # -- BaseLLM interface --------------------------------------------------------------

    def supports_function_calling(self) -> bool:
        return self._native_tools

    def supports_stop_words(self) -> bool:
        return False

    def get_context_window_size(self) -> int:
        return 1_000_000

    def call(
        self,
        messages: Any,
        tools: list[Any] | None = None,
        callbacks: list[Any] | None = None,
        available_functions: dict[str, Any] | None = None,
        from_task: Any = None,
        from_agent: Any = None,
        response_model: Any = None,
    ) -> Any:
        formatted = list(self._format_messages(messages))
        self._emit_call_started_event(
            messages=formatted,
            tools=tools,
            callbacks=callbacks,
            available_functions=available_functions,
            from_task=from_task,
            from_agent=from_agent,
        )
        with self._lock:
            self._calls.append(
                RecordedCall(copy.deepcopy(cast(list[dict[str, Any]], formatted)), copy.copy(tools))
            )
            if not self._script:
                raise ScriptExhausted(
                    f"The script has no response for call #{len(self._calls)}; "
                    f"the last message was: {str(formatted[-1].get('content'))[:200]!r}"
                )
            item = self._script.popleft()

        reply, (prompt_tokens, completion_tokens) = self._resolve(
            item, cast(list[dict[str, Any]], formatted), tools
        )
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        self._track_token_usage_internal(usage)
        result = self._render(reply)
        self._emit_call_completed_event(
            response=result,
            call_type=LLMCallType.LLM_CALL,
            from_task=from_task,
            from_agent=from_agent,
            messages=formatted,
            usage=usage,
        )
        return result

    # -- internals ----------------------------------------------------------------------

    def _resolve(
        self, item: ScriptItem, messages: list[dict[str, Any]], tools: list[Any] | None
    ) -> tuple[Reply, tuple[int, int]]:
        usage = self._usage
        if isinstance(item, Turn):
            usage = (item.prompt_tokens, item.completion_tokens)
            item = item.reply
        if callable(item) and not isinstance(item, ToolCall):
            item = item(messages, tools)
        return item, usage

    def _render(self, reply: Reply) -> Any:
        if isinstance(reply, str):
            if self._native_tools or "Action:" in reply or "Final Answer:" in reply:
                return reply
            return f"Thought: I now know the final answer\nFinal Answer: {reply}"
        calls = [reply] if isinstance(reply, ToolCall) else list(reply)

        if self._native_tools:
            return [
                {
                    "id": call.id or f"call_{self._next_id()}",
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in calls
            ]
        if len(calls) != 1:
            raise ValueError("The ReAct protocol allows one tool call per turn.")
        call = calls[0]
        return (
            f"Thought: I should use {call.name}\n"
            f"Action: {call.name}\n"
            f"Action Input: {json.dumps(call.arguments)}"
        )

    def _next_id(self) -> int:
        with self._lock:
            self._issued += 1
            return self._issued
