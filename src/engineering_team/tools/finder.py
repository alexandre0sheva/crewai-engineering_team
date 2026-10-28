"""Find Tool and Use Tool: the rarely used tools of a teammate, behind one lookup.

Every model call sends the schema of every tool the agent has, so an agent with a dozen tool
groups paid for ~12,000 tokens of schemas per call while using a handful of tools. The catalogue
marks the rarely used tools ``lazy``; ``build_tools`` holds them back and gives the agent these
two instead: ``Find Tool`` lists the held-back tools that match a query with their arguments,
``Use Tool`` calls one by name. The agent's tool list never changes during a session, so the
start of every prompt stays the same and a provider's prompt cache keeps working.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from engineering_team.tools.support import ToolEnv, ToolError

MAX_MATCHES = 8
MAX_DESCRIPTION = 300


def _summary(tool: BaseTool) -> str:
    """The tool's own description without CrewAI's generated header, cut to a few lines."""

    text = tool.description.split("Tool Description:", 1)[-1]
    return " ".join(text.split())[:MAX_DESCRIPTION]


def _arguments(tool: BaseTool) -> str:
    schema = tool.args_schema.model_json_schema()
    required = set(schema.get("required", []))
    rows = []
    for name, field in schema.get("properties", {}).items():
        kind = field.get("type") or "/".join(
            str(option.get("type", "")) for option in field.get("anyOf", []) if option.get("type")
        )
        default = "" if name in required else f" = {json.dumps(field.get('default'))}"
        rows.append(f"{name} ({kind or 'any'}){default}")
    return ", ".join(rows) or "none"


def describe(tool: BaseTool) -> str:
    return f"{tool.name}: {_summary(tool)}\n  arguments: {_arguments(tool)}"


class FindToolInput(BaseModel):
    query: str = Field(
        default="",
        description="Words to look for in the held-back tools' names and descriptions "
        "(for example 'git history' or 'process logs'); empty lists all of them.",
    )


class UseToolInput(BaseModel):
    # Models often put the tool's arguments next to ``name`` instead of inside ``arguments``, or
    # send ``arguments`` as a JSON string (the first live run did): both are accepted.
    model_config = ConfigDict(extra="allow")

    name: str = Field(description="The exact name of a held-back tool.")
    arguments: dict[str, Any] | str = Field(
        default_factory=dict, description="That tool's arguments, as an object."
    )


class FindTool(BaseTool):
    name: str = "Find Tool"
    description: str = ""
    args_schema: type[BaseModel] = FindToolInput
    _env: ToolEnv = PrivateAttr()
    _held: list[BaseTool] = PrivateAttr()

    def __init__(self, env: ToolEnv, held: Sequence[BaseTool]) -> None:
        names = ", ".join(tool.name for tool in held)
        super().__init__(
            name="Find Tool",
            description=(
                "Look up the tools held back to keep prompts small, with their arguments; call "
                f"one with Use Tool. Held back: {names}. Example: query='run single test'."
            ),
        )
        self._env = env
        self._held = list(held)

    def _run(self, query: str = "") -> str:
        def operation() -> str:
            words = [word for word in query.lower().split() if word]
            scored = []
            for tool in self._held:
                text = f"{tool.name} {_summary(tool)}".lower()
                score = sum(text.count(word) for word in words) + (
                    5 * sum(word in tool.name.lower() for word in words)
                )
                if score or not words:
                    scored.append((-score, tool.name, tool))
            if not scored:
                names = ", ".join(tool.name for tool in self._held)
                raise ToolError(f"No held-back tool matches {query!r}. They are: {names}.")
            found = [describe(tool) for _, _, tool in sorted(scored)[:MAX_MATCHES]]
            more = len(scored) - len(found)
            tail = f"\n({more} more tools match; narrow the query.)" if more > 0 else ""
            return "Call these with Use Tool(name, arguments):\n" + "\n".join(found) + tail

        return self._env.run("Find Tool", operation, arguments={"query": query})


USE_TOOL = (
    "Call a held-back tool (one Find Tool lists, or one your task names that is not among your "
    "tools) by its exact name with its arguments as an object. Example: name='Git History "
    "Search', arguments={'pattern': 'parse_date'}."
)


class UseTool(BaseTool):
    name: str = "Use Tool"
    description: str = USE_TOOL
    args_schema: type[BaseModel] = UseToolInput
    _env: ToolEnv = PrivateAttr()
    _held: dict[str, BaseTool] = PrivateAttr()

    def __init__(self, env: ToolEnv, held: Sequence[BaseTool]) -> None:
        super().__init__(name="Use Tool", description=USE_TOOL)
        self._env = env
        self._held = {tool.name.lower(): tool for tool in held}

    def _run(self, name: str, arguments: dict[str, Any] | str | None = None, **extra: Any) -> str:
        given = _parse_arguments(arguments)
        tool = self._held.get(name.strip().lower())
        if tool is None or given is None:

            def refused() -> str:
                if given is None:
                    raise ToolError(
                        "arguments must be an object (or a JSON object as text), for example "
                        "{'card_id': 'K-001'}."
                    )
                names = ", ".join(t.name for t in self._held.values())
                raise ToolError(
                    f"'{name}' is not a held-back tool. Held back: {names}. Tools you were "
                    "given directly are called by their own name, not through Use Tool."
                )

            return self._env.run("Use Tool", refused, arguments={"name": name})
        given = {**extra, **given}
        try:
            # The tool reports its own call (a ``tool.call`` event under its own name).
            return str(tool.run(**given))
        except Exception as exc:  # arguments that do not fit the tool's schema
            problem = " ".join(str(exc).split())[:300]

            def invalid() -> str:
                raise ToolError(
                    f"{tool.name} cannot take these arguments ({problem}). "
                    f"Its arguments: {_arguments(tool)}."
                )

            return self._env.run(
                "Use Tool", invalid, arguments={"name": name, "arguments": sorted(given)}
            )


def _parse_arguments(arguments: dict[str, Any] | str | None) -> dict[str, Any] | None:
    """The arguments as an object (``None`` when they are text that is not a JSON object)."""

    if not arguments:
        return {}
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def make_finder_tools(env: ToolEnv, held: Sequence[BaseTool]) -> list[BaseTool]:
    """Find Tool and Use Tool over ``held`` (nothing when no tool is held back)."""

    if not held:
        return []
    return [FindTool(env, held), UseTool(env, held)]
