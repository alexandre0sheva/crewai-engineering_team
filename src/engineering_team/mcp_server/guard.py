"""What the MCP server refuses: a repository nobody allowed, and a budget above the caps.

The calling agent is not trusted with either decision. Repositories come from ``--repo`` and
``mcp_serve.repos`` (the person who configured the server), and the caps from ``mcp_serve`` and
``budget``. A refusal is a :class:`Refused` whose message tells the caller what to do, and is
returned as the tool's error; nothing has been started when one is raised.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from engineering_team.settings import Settings
from engineering_team.ui.launcher import Budget

BUDGET_LIMITS = ("max_cost_usd", "max_tokens", "max_wall_seconds", "max_tool_calls")


class Refused(ValueError):
    """A call the server will not make; the message is what the calling agent is told."""


def resolve_roots(paths: Iterable[str]) -> list[Path]:
    """The allowed directories, as absolute resolved paths (a missing one is an error, so a typo
    in the configuration is not silently an empty allowlist)."""

    roots: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if not path.is_dir():
            raise ValueError(f"The allowed repository {raw!r} is not an existing directory.")
        resolved = path.resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots


def inside(path: Path, roots: Iterable[Path]) -> bool:
    """Whether ``path`` (resolved) is one of ``roots`` or lies below one."""

    return any(path == root or root in path.parents for root in roots)


def check_repo(repo: str, roots: list[Path]) -> Path:
    """The resolved project directory, if it is inside an allowed one."""

    if not repo or "\0" in repo:
        raise Refused("repo is required: the absolute path of the project directory.")
    if not roots:
        raise Refused(
            "No repository is allowed. The person running the server must start it with "
            "`engineering-team mcp serve --repo DIR` (or set mcp_serve.repos)."
        )
    path = Path(repo).expanduser()
    if not path.is_absolute():
        raise Refused(f"repo {repo!r} is not an absolute path.")
    if not path.is_dir():
        raise Refused(f"repo {repo!r} is not an existing directory.")
    resolved = path.resolve()  # symlinks and `..` cannot lead out of an allowed directory
    if not inside(resolved, roots):
        allowed = ", ".join(str(root) for root in roots)
        raise Refused(
            f"repo {repo!r} is outside the repositories this server may act in ({allowed}). "
            "Ask the person running the server to allow it."
        )
    return resolved


def caps(settings: Settings) -> dict[str, float | int | None]:
    """The most one run may use: the smaller of ``budget.*`` and (for the cost) ``mcp_serve``."""

    found: dict[str, float | int | None] = {
        name: getattr(settings.budget, name) for name in BUDGET_LIMITS
    }
    cost = [c for c in (found["max_cost_usd"], settings.mcp_serve.max_cost_usd) if c is not None]
    found["max_cost_usd"] = min(cost) if cost else None
    return found


def clamp_budget(requested: Budget | None, settings: Settings) -> Budget:
    """The budget the run gets: what was asked for, or the cap; asking for more than a cap is
    refused (silently lowering it would leave the caller believing in a bigger budget)."""

    limits = caps(settings)
    asked = requested.model_dump() if requested is not None else {}
    chosen: dict[str, float | int] = {}
    for name in BUDGET_LIMITS:
        cap, want = limits[name], asked.get(name)
        if want is not None and cap is not None and want > cap:
            raise Refused(
                f"budget.{name} {want} is above this server's limit of {cap}. Ask for {cap} or "
                "less, or have the person running the server raise the limit "
                "(mcp_serve.max_cost_usd, budget.*)."
            )
        value = want if want is not None else cap
        if value is not None:
            chosen[name] = value
    return Budget.model_validate(chosen)
