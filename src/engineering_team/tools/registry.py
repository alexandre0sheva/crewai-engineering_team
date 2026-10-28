"""The tool catalogue: every agent tool, its group, and how a run's tools are assembled.

``docs/TOOLS.md`` lists the same tools; a test keeps the two in step. New tools add a
``ToolSpec`` to the catalogue module of their kind (``catalogue_*.py``) and a row there."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from crewai.tools import BaseTool

from engineering_team.tools import browser_tools
from engineering_team.tools.catalogue_dev import DEV_SPECS
from engineering_team.tools.catalogue_external import EXTERNAL_SPECS
from engineering_team.tools.catalogue_project import PROJECT_SPECS
from engineering_team.tools.catalogue_team import TEAM_SPECS
from engineering_team.tools.scope import WriteScope
from engineering_team.tools.spec import GROUPS, PROJECT_GROUPS, ToolFactory, ToolSpec
from engineering_team.tools.support import ToolEnv, never_cache

if TYPE_CHECKING:
    from engineering_team.runtime.context import RunContext
    from engineering_team.settings import Settings


__all__ = [
    "CATALOGUE",
    "GROUPS",
    "PROJECT_GROUPS",
    "ToolFactory",
    "ToolSpec",
    "build_tools",
    "web_tools_allowed",
]

CATALOGUE: tuple[ToolSpec, ...] = (
    *PROJECT_SPECS,
    *DEV_SPECS,
    *EXTERNAL_SPECS,
    *TEAM_SPECS,
)


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
    from a same-role teammate's.
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
    return tools
