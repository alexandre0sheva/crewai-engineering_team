"""Constructing CrewAI LLMs from resolved model routing."""

from __future__ import annotations

from typing import Any

from crewai import LLM

from engineering_team.model_routing import REASONING_EFFORT_PREFIXES, ResolvedModel
from engineering_team.settings import Settings

# CrewAI's own default context-window headroom (it uses 75% of a model's window).
CONTEXT_WINDOW_USAGE_RATIO = 0.75


def build_llm(resolved: ResolvedModel, settings: Settings) -> LLM:
    """Construct a CrewAI LLM from a resolved model, sending only parameters it accepts."""

    options: dict[str, Any] = {"model": resolved.model}
    prefix = resolved.provider_prefix
    if resolved.reasoning_effort is not None and prefix in REASONING_EFFORT_PREFIXES:
        options["reasoning_effort"] = resolved.reasoning_effort
    if resolved.temperature is not None:
        options["temperature"] = resolved.temperature
    if resolved.api is not None and prefix == "openai":
        options["api"] = resolved.api
    if prefix == "ollama":
        options["base_url"] = settings.ollama_base_url
    llm = LLM(**options)
    if resolved.context_window is not None:
        window = int(resolved.context_window * CONTEXT_WINDOW_USAGE_RATIO)
        # Not a constructor option: CrewAI's native providers (OpenAI, Anthropic, Ollama, ...) pass
        # unknown options on to the API, which rejects ``context_window_size`` in every call.
        object.__setattr__(llm, "get_context_window_size", lambda: window)
    return llm
