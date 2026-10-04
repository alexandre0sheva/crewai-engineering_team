"""The plugin API: add an agent tool without touching this package.

A plugin is a Python function with a docstring and type hints, wrapped by :func:`plugin_tool`::

    from engineering_team.plugins import PluginContext, ToolError, plugin_tool

    @plugin_tool(name="Word Count", group="fs_read", read_only=True)
    def word_count(ctx: PluginContext, path: str) -> str:
        '''Count the words in a project text file. Use it to size a document before
        summarising it. path is relative to the project root. Example: path="README.md".'''
        text = ctx.resolve(path).read_text(encoding="utf-8")
        return f"{len(text.split())} words"

Put it in ``.engineering-team/tools/*.py`` (needs ``allow_project_plugins = true``) or publish it
under the ``engineering_team.tools`` entry point group; a module may define ``TOOLS`` (a list) or
simply have :class:`PluginTool` objects at module level. A teammate gets the tool when it lists the
tool's ``group`` (a built-in one or your own name) in ``tool_groups``.

**A plugin is trusted code.** It runs inside the controller process with your privileges: the
write scope, path protection, and the Docker sandbox do not apply to what its Python does.
``read_only`` and ``needs_network`` are declarations the team relies on, not enforcement.
The tool's description is the docstring (or ``description=``): write it for the model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from engineering_team.tools.support import ToolError

if TYPE_CHECKING:
    from engineering_team.tools.support import ToolEnv

__all__ = ["PluginContext", "PluginTool", "ToolError", "plugin_tool"]


@dataclass(frozen=True)
class PluginTool:
    """A plugin's tool, as :func:`plugin_tool` builds it."""

    name: str
    func: Callable[..., Any]
    group: str
    summary: str = ""
    description: str = ""
    read_only: bool = False
    needs_network: bool = False


def plugin_tool(
    *,
    name: str,
    group: str,
    summary: str = "",
    description: str = "",
    read_only: bool = False,
    needs_network: bool = False,
) -> Callable[[Callable[..., Any]], PluginTool]:
    """Declare a function as an agent tool.

    ``name`` is what the agent sees (and must differ from every built-in tool). ``group`` is the
    tool group that grants it: a built-in group such as ``knowledge`` or ``web`` (which then
    follows that group's rules, so a ``web`` plugin is off unless ``web.enabled``), or a name of
    your own that teammates list under ``tool_groups``. ``read_only=True`` lets read-only
    teammates (reviewers) use it; leave it ``False`` for a tool that changes anything.
    """

    def wrap(func: Callable[..., Any]) -> PluginTool:
        return PluginTool(
            name=name,
            func=func,
            group=group,
            summary=summary,
            description=description or (func.__doc__ or ""),
            read_only=read_only,
            needs_network=needs_network,
        )

    return wrap


class PluginContext:
    """What a plugin function may ask of its run (the first parameter, named ``ctx``)."""

    def __init__(self, env: ToolEnv) -> None:
        self._env = env

    @property
    def run_id(self) -> str:
        return self._env.ctx.run_id

    @property
    def agent(self) -> str | None:
        """The teammate calling the tool."""

        return self._env.agent

    @property
    def workspace_root(self) -> Path:
        return self._env.workspace.root

    def resolve(self, path: str, *, must_exist: bool = True) -> Path:
        """A path inside the project. Paths outside it, ``.git``, and the controller's
        ``.engineering-team/`` raise :class:`ToolError` (the agent sees ``ERROR: ...``)."""

        from engineering_team.tools.workspace import WorkspaceError

        try:
            return self._env.workspace.resolve(path, must_exist=must_exist)
        except WorkspaceError as exc:
            raise ToolError(str(exc)) from exc
