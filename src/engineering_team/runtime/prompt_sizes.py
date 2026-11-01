"""How big a stage session's prompt is when it starts, against the model's context window.

Before a session's first model call the controller measures what it will send, in estimated tokens
(four characters each, like the profiler's ``prompt_parts.py``):

* ``always_on``: the teammate's role, goal and backstory (with the skill list);
* ``pack``: the controller's hand-off pack (``pipeline/handoff.py``, whose context pack is held to
  ``context.stage_tokens``) and the project context (conventions, codebase map);
* ``task``: the rest of the stage prompt (the request, the contracts it is handed);
* ``tools``: the tool schemas every call carries.

Each session emits one ``context.sized`` event, plus a ``context.warning`` when it starts above
``context.warn_fraction`` (60 %) of the window. The history a session builds up is only known
afterwards: :func:`prompt_rows` joins the events with the largest history any of its model calls
carried (``llm.request``) into the rows the manifest and the report show.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from engineering_team.runtime.prompt_parts import CHARS_PER_TOKEN

if TYPE_CHECKING:
    from engineering_team.contracts import Event
    from engineering_team.runtime.context import RunContext

PLACEHOLDER = re.compile(r"(?<!\{)\{([a-z_]+)\}(?!\})")
PACK_INPUTS = ("handoff", "context")  # prompt inputs that are the controller's pack
SIZED = "context.sized"


def tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


@dataclass(frozen=True)
class PromptSize:
    """What a session starts with, in estimated tokens."""

    always_on: int
    pack: int
    task: int
    tools: int
    window: int
    window_source: str  # "model facts", "settings" or "crewai"

    @property
    def start(self) -> int:
        return self.always_on + self.pack + self.task + self.tools

    @property
    def fraction(self) -> float:
        return self.start / self.window if self.window else 0.0


def render(description: str, inputs: Mapping[str, Any]) -> str:
    """The task description with each ``{name}`` replaced the way CrewAI does."""

    return PLACEHOLDER.sub(
        lambda match: str(inputs[match.group(1)]) if match.group(1) in inputs else match.group(0),
        description,
    )


def tool_chars(tool: Any) -> int:
    """The size of one tool as the model sees it: name, description and argument schema."""

    size = len(str(getattr(tool, "name", ""))) + len(str(getattr(tool, "description", "")))
    schema = getattr(tool, "args_schema", None)
    with contextlib.suppress(Exception):  # a schema that cannot be dumped is counted without it
        size += len(json.dumps(schema.model_json_schema())) if schema is not None else 0
    return size


def measure(
    *,
    role: str,
    goal: str,
    backstory: str,
    description: str,
    inputs: Mapping[str, Any],
    tools: Iterable[Any],
    window: int,
    window_source: str,
) -> PromptSize:
    """The size of a session's first prompt."""

    prompt = render(description, inputs)
    pack = sum(len(str(inputs.get(name) or "")) for name in PACK_INPUTS)
    return PromptSize(
        always_on=tokens(role + goal + backstory),
        pack=pack // CHARS_PER_TOKEN,
        task=max(0, len(prompt) - pack) // CHARS_PER_TOKEN,
        tools=sum(tool_chars(tool) for tool in tools) // CHARS_PER_TOKEN,
        window=window,
        window_source=window_source,
    )


def announce(ctx: RunContext, stage: str, agent: str, model: str, size: PromptSize) -> str | None:
    """Record ``size`` in the event log; the warning text when the session starts above
    ``context.warn_fraction`` of the window (also recorded), else ``None``."""

    limit = ctx.settings.context.warn_fraction
    warned = size.fraction > limit
    ctx.events.emit(
        SIZED,
        stage=stage,
        agent=agent,
        model=model,
        always_on=size.always_on,
        pack=size.pack,
        task=size.task,
        tools=size.tools,
        window=size.window,
        window_source=size.window_source,
        warned=warned,
    )
    if not warned:
        return None
    message = (
        f"Stage {stage} ({agent}) starts at {size.fraction:.0%} of {model}'s {size.window:,}-token "
        f"context window ({size.start:,} tokens: always-on {size.always_on:,}, pack "
        f"{size.pack:,}, task {size.task:,}, tools {size.tools:,}); the history comes on top. "
        "Lower context.stage_tokens, narrow the request, or use a model with a larger window."
    )
    ctx.events.emit("context.warning", stage=stage, agent=agent, message=message)
    return message


def _lane(event: Event) -> str | None:
    return None if event.lane is None else str(event.lane)


def prompt_rows(events: Iterable[Event]) -> list[dict[str, Any]]:
    """One row per session that was sized, in the order they started: the sizes of
    :class:`PromptSize`, ``start`` and ``fraction``, and ``history`` (the most tokens of history
    and tool results any of its model calls carried; 0 when none was logged)."""

    log = sorted(events, key=lambda event: event.seq)
    rows: list[dict[str, Any]] = []
    latest: dict[tuple[str | None, str | None, str | None], dict[str, Any]] = {}
    for event in log:
        key = (event.stage, event.agent, _lane(event))
        if event.type == SIZED:
            data = event.data
            row: dict[str, Any] = {
                "stage": event.stage,
                "agent": event.agent,
                "lane": _lane(event),
                "model": str(data.get("model") or ""),
                **{name: int(data.get(name) or 0) for name in ("always_on", "pack", "task")},
                "tools": int(data.get("tools") or 0),
                "window": int(data.get("window") or 0),
                "window_source": str(data.get("window_source") or ""),
                "warned": bool(data.get("warned")),
                "history": 0,
            }
            row["start"] = row["always_on"] + row["pack"] + row["task"] + row["tools"]
            row["fraction"] = round(row["start"] / row["window"], 4) if row["window"] else 0.0
            rows.append(row)
            latest[key] = row
        elif event.type == "llm.request" and key in latest:
            sent = event.data.get("prompt_chars") or {}
            carried = (int(sent.get("history") or 0) + int(sent.get("results") or 0)) // (
                CHARS_PER_TOKEN
            )
            latest[key]["history"] = max(latest[key]["history"], carried)
    return rows
