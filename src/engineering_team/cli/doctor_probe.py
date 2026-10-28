"""``doctor --probe-tools``: can each configured model call tools, or does it only answer in text?

A model that answers in plain text drives nothing: the team's file and command tools are all tool
calls. Small local models (Ollama, LM Studio) often do exactly that, and a run then fails in a way
that looks like a bug in the team. The probe sends one tiny request per distinct model with one
tool offered and reads the reply the way the agent executor does (CrewAI's own
``is_tool_call_list``), so what it reports is what a run would see. It costs cents, never runs
in tests (they inject ``ping``), and is opt-in.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from engineering_team.cli.doctor import Check
from engineering_team.model_routing import ResolvedModel
from engineering_team.settings import Settings

PROBE_TOOL = "probe_echo"
PROBE_PROMPT = (
    f"Call the tool {PROBE_TOOL} once with word set to ping. Do not answer in text; call the tool."
)
PROBE_MAX_TOKENS = 256  # enough for a call, a hard stop for a model that rambles
MAX_ANSWER = 60

_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": PROBE_TOOL,
        "description": "Echoes the word it is given. Call it to show that you can call tools.",
        "parameters": {
            "type": "object",
            "properties": {"word": {"type": "string", "description": "Any word."}},
            "required": ["word"],
        },
    },
}


@dataclass(frozen=True)
class ToolProbe:
    """What one model did with the probe request."""

    called: bool
    detail: str = ""  # the tool it called, or the start of its text answer


ToolPing = Callable[[ResolvedModel, Settings], ToolProbe]


def classify(response: object) -> ToolProbe:
    """Read a model's reply: a tool call (any provider's format) or text."""

    from crewai.utilities.agent_utils import extract_tool_call_info, is_tool_call_list

    if isinstance(response, (list, tuple)) and response and is_tool_call_list(list(response)):
        info = extract_tool_call_info(response[0])
        if info is not None:
            return ToolProbe(True, f"called {info[1]}")
    text = " ".join(str(response or "").split())
    return ToolProbe(False, text[:MAX_ANSWER] or "(empty answer)")


def ping_tools(resolved: ResolvedModel, settings: Settings) -> ToolProbe:
    """One live request to ``resolved`` that offers a tool. Raises on any provider failure."""

    from engineering_team.llm import build_llm

    llm = build_llm(resolved, settings)
    if hasattr(llm, "max_tokens"):
        object.__setattr__(llm, "max_tokens", PROBE_MAX_TOKENS)
    # No ``available_functions``: the reply is the raw tool call, as the agent executor gets it.
    return classify(llm.call(PROBE_PROMPT, tools=[_TOOL]))


def probe_checks(settings: Settings, ping: ToolPing = ping_tools) -> list[Check]:
    """One ``Check`` per distinct model: ``ok`` when it called the tool, ``fail`` when it only
    answered in text (or the request failed)."""

    out: list[Check] = []
    seen: set[str] = set()
    for resolved in settings.resolved_models():
        if resolved.model in seen:
            continue
        seen.add(resolved.model)
        name = f"Tools {resolved.model}"
        try:
            probe = ping(resolved, settings)
        except Exception as exc:  # any provider failure is a finding, not a crash
            out.append(
                Check(
                    name, "fail", f"{type(exc).__name__}: {str(exc)[:120]}", f"tier {resolved.tier}"
                )
            )
            continue
        if probe.called:
            out.append(Check(name, "ok", f"can call tools ({resolved.tier}): {probe.detail}"))
        else:
            out.append(
                Check(
                    name,
                    "fail",
                    f"answers in text only ({resolved.tier}): {probe.detail}",
                    "The team's tools are tool calls, so this model cannot drive it. Choose a "
                    "model with tool calling (docs/CONFIGURATION.md, Models).",
                )
            )
    return out
