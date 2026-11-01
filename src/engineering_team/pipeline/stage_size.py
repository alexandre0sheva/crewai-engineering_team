"""Measuring a stage session's first prompt before its agent starts (``prompt_sizes.py``)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from engineering_team.runtime.prompt_sizes import PromptSize, announce, measure

if TYPE_CHECKING:
    from crewai import Agent

    from engineering_team.pipeline.stages import StageRequest


def context_window(request: StageRequest, agent: Agent) -> tuple[int, str]:
    """The model's context window in tokens and where the figure comes from (0: not known)."""

    ctx, teammate = request.ctx, request.teammate
    member = ctx.team.get(teammate)
    resolved = ctx.settings.resolve_model(
        teammate, tier=request.tier or member.tier, max_iter=member.max_iter
    )
    if resolved.context_window:
        return resolved.context_window, resolved.sources.get("context_window", "model facts")
    getter = getattr(agent.llm, "get_context_window_size", None)
    return (int(getter()), "crewai") if callable(getter) else (0, "unknown")


def announce_size(
    request: StageRequest, agent: Agent, description: str, inputs: Mapping[str, Any]
) -> PromptSize | None:
    """Record the size of the session the agent is about to start (an event; a warning event
    when it starts above ``context.warn_fraction`` of the window). Never raises: a failed
    measurement must not stop the stage."""

    ctx = request.ctx
    try:
        window, source = context_window(request, agent)
        size = measure(
            role=agent.role,
            goal=agent.goal,
            backstory=agent.backstory,
            description=description,
            inputs=inputs,
            tools=agent.tools or [],
            window=window,
            window_source=source,
        )
        model = str(getattr(agent.llm, "model", "") or "")
        announce(ctx, request.stage.name, request.teammate, model, size)
        return size
    except Exception as exc:
        ctx.events.emit("context.error", error=f"{type(exc).__name__}: {exc}"[:300])
        return None
