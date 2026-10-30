"""The tool catalogue: every agent tool, its group, and how a run's tools are assembled.

``docs/TOOLS.md`` lists the same tools; a test keeps the two in step. New tools add a
``ToolSpec`` to the catalogue module of their kind (``catalogue_*.py``) and a row there."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from crewai.tools import BaseTool

from engineering_team.tools import browser_tools
from engineering_team.tools.catalogue_dev import DEV_SPECS
from engineering_team.tools.catalogue_external import EXTERNAL_SPECS
from engineering_team.tools.catalogue_project import PROJECT_SPECS
from engineering_team.tools.catalogue_team import TEAM_SPECS
from engineering_team.tools.finder import make_finder_tools
from engineering_team.tools.scope import WriteScope
from engineering_team.tools.spec import GROUPS, PROJECT_GROUPS, ToolFactory, ToolSpec
from engineering_team.tools.support import ToolEnv, never_cache

if TYPE_CHECKING:
    from engineering_team.runtime.context import RunContext
    from engineering_team.settings import Settings


__all__ = [
    "CATALOGUE",
    "EAGER_TOOLS",
    "FINDER_TOOLS",
    "GROUPS",
    "LAZY_TOOLS",
    "PROJECT_GROUPS",
    "ToolFactory",
    "ToolSpec",
    "build_tools",
    "hold_back",
    "web_tools_allowed",
]

# The tools every agent gets as tools of their own (when its groups include them): the ones the
# run profiles show in use in almost every session. Every other built-in tool is ``lazy``: held
# back behind Find Tool and Use Tool, so its schema is not sent with every model call.
EAGER_TOOLS = frozenset(
    {
        "List Project Files",
        "Read Project File",
        "Read File Range",
        "Read Many Files",
        "Search Project Files",
        "Find Files",
        "Write Project File",
        "Replace In Project File",
        "Apply Patch",
        "Run Project Command",
        "Run Tests",
        "Comment On Card",
        "Block Card",
        "Ask Human",
        # Only the lead has the ``team`` group; its calls are the reason it exists.
        "Delegate",
        "Delegate Parallel",
        "Ask Reviewer",
        "Ask Security Review",
        "Ask Debugger",
        "Ask Architect",
        "Ask Analyst",
    }
)

CATALOGUE: tuple[ToolSpec, ...] = tuple(
    replace(spec, lazy=spec.name not in EAGER_TOOLS)
    for spec in (*PROJECT_SPECS, *DEV_SPECS, *EXTERNAL_SPECS, *TEAM_SPECS)
)
LAZY_TOOLS = frozenset(spec.name for spec in CATALOGUE if spec.lazy)
# Not in a group of their own: ``build_tools`` adds them whenever it held tools back.
FINDER_TOOLS = ("Find Tool", "Use Tool")


def hold_back(
    ctx: RunContext,
    tools: Iterable[BaseTool],
    *,
    agent: str | None = None,
    lane: int | str | None = None,
) -> list[BaseTool]:
    """``tools`` with the catalogue's lazy ones held back behind Find Tool and Use Tool, which
    come last (for a tool set assembled from several ``build_tools(..., lazy=False)`` calls)."""

    tools = list(tools)
    held = [tool for tool in tools if tool.name in LAZY_TOOLS]
    finders = make_finder_tools(ToolEnv(ctx, None, agent, lane), held)
    for finder in finders:
        finder.cache_function = never_cache
    return [*(tool for tool in tools if tool.name not in LAZY_TOOLS), *finders]


def web_tools_allowed(settings: Settings, agent: str | None) -> bool:
    """Whether the ``web`` group exists for this teammate: enabled, and in ``web.roles`` if set."""

    if not settings.web.enabled:
        return False
    roles = settings.web.roles
    return not roles or (agent or "").strip().lower().replace(" ", "_") in {
        role.replace(" ", "_") for role in roles
    }


def build_tools(
    ctx: RunContext,
    *,
    groups: Iterable[str] | None = None,
    write_scope: WriteScope | None = None,
    read_only: bool = False,
    agent: str | None = None,
    lane: int | str | None = None,
    lazy: bool | None = None,
) -> list[BaseTool]:
    """Build one run's tools, in catalogue order.

    ``groups`` selects tool groups (default: all). ``write_scope`` limits which paths the
    write tools may change; reads stay unrestricted. ``read_only`` leaves out every tool
    that changes files or runs commands (the board, notes, and human tools only change the
    run's own state, so read-only teammates keep them). The ``web`` group is left out unless
    ``web.enabled`` is set (and, with ``web.roles``, the teammate is listed). ``agent`` is the
    teammate the tools belong to: the board and notes tools act, comment, and are logged as
    that teammate. Plugin tools (``ctx.plugins``) follow the built-ins, in discovery order, and
    are picked by their declared group like any other. ``lane`` is the parallel lane the tools
    work in: it tags their events and keeps the teammate's browser session and processes apart
    from a same-role teammate's. With ``lazy`` (default: ``tools.lazy``) the catalogue's lazy
    tools are held back behind Find Tool and Use Tool, which come last.
    """

    plugin_groups = sorted(ctx.plugins.groups - set(GROUPS))
    known = (*GROUPS, *plugin_groups)
    wanted = tuple(known if groups is None else groups)
    web_allowed = web_tools_allowed(ctx.settings, agent)
    browser_present = browser_tools.available()
    unknown = sorted(set(wanted) - set(known))
    if unknown:
        raise ValueError(f"Unknown tool group(s): {', '.join(unknown)}. Known: {', '.join(known)}.")
    env = ToolEnv(ctx, write_scope, agent, lane)
    bundles: dict[ToolFactory, Mapping[str, BaseTool]] = {}
    tools: list[BaseTool] = []
    for spec in (*CATALOGUE, *ctx.plugins.specs()):
        if spec.group not in wanted or (read_only and not spec.read_only):
            continue
        if spec.group == "web" and not web_allowed:
            continue  # off unless enabled: a disabled run has no network tool registered at all
        if spec.group == "browser" and not browser_present:
            continue  # the optional Playwright extra is not installed
        if spec.factory not in bundles:
            bundles[spec.factory] = spec.factory(env)
        built = bundles[spec.factory][spec.name]
        built.cache_function = never_cache
        tools.append(built)
    hold = ctx.settings.tools.lazy if lazy is None else lazy
    return hold_back(ctx, tools, agent=agent, lane=lane) if hold else tools
