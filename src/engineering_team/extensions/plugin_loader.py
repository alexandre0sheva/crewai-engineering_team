"""Finding, checking, and building plugin tools (the API is ``engineering_team.plugins``).

Two sources: installed packages that declare an entry point in the ``engineering_team.tools``
group (on unless ``plugins.entry_points`` is false), and the project's own
``.engineering-team/tools/*.py`` (off unless ``allow_project_plugins``; it executes whatever is
there). Loading imports the code, so the result is cached per configuration and file state. Any
problem is a :class:`PluginError` (a usage error) that names the plugin and the fix: a broken
plugin never turns into a surprise in the middle of a run.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import inspect
import re
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from crewai.tools import BaseTool
from crewai.tools import tool as crewai_tool

from engineering_team.extensions.config import NAME
from engineering_team.plugins import PluginContext, PluginTool
from engineering_team.settings import Settings
from engineering_team.tools.support import ToolEnv, ToolError, bounded

if TYPE_CHECKING:
    from engineering_team.tools.registry import ToolSpec

ENTRY_POINT_GROUP = "engineering_team.tools"
PROJECT_PLUGIN_DIR = Path(".engineering-team") / "tools"
TOOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9 &_.-]{1,38}[A-Za-z0-9]")
MIN_WORDS, MAX_WORDS = 8, 120


class PluginError(ValueError):
    """A plugin that cannot be loaded or registered."""


@dataclass(frozen=True)
class LoadedTool:
    tool: PluginTool
    source: str  # "entry point NAME" or the project-relative file path
    summary: str


@dataclass(frozen=True)
class PluginSet:
    """The plugin tools of a run. ``notes`` are warnings for the person starting it."""

    tools: tuple[LoadedTool, ...] = ()
    notes: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()  # project plugin files present but not loaded (and why)

    @property
    def groups(self) -> frozenset[str]:
        return frozenset(item.tool.group for item in self.tools)

    def specs(self) -> tuple[ToolSpec, ...]:
        """Catalogue entries for the tools, in discovery order (after the built-ins)."""

        from engineering_team.tools.registry import ToolSpec

        return tuple(
            ToolSpec(
                name=item.tool.name,
                group=item.tool.group,
                read_only=item.tool.read_only,
                needs_network=item.tool.needs_network,
                needs_command_gate=False,
                summary=item.summary,
                factory=plugin_factory(item.tool),
            )
            for item in self.tools
        )


# -- building a tool -----------------------------------------------------------------------------


def plugin_factory(tool: PluginTool) -> Callable[[ToolEnv], Mapping[str, BaseTool]]:
    """A catalogue factory for one plugin tool, run through ``ToolEnv.run`` like every tool."""

    signature = inspect.signature(tool.func)
    takes_context = "ctx" in signature.parameters
    parameters = [p for name, p in signature.parameters.items() if name != "ctx"]

    def factory(env: ToolEnv) -> Mapping[str, BaseTool]:
        def run(**arguments: Any) -> str:
            def operation() -> str:
                try:
                    result = (
                        tool.func(PluginContext(env), **arguments)
                        if takes_context
                        else tool.func(**arguments)
                    )
                except ToolError:
                    raise
                except Exception as exc:  # a plugin bug is an error result, never a crash
                    raise ToolError(
                        f"plugin tool failed ({type(exc).__name__}: {exc}). Check the arguments; "
                        "if they are right, the plugin has a bug."
                    ) from exc
                return bounded(str(result))

            return env.run(tool.name, operation, arguments=arguments)

        run.__signature__ = signature.replace(  # type: ignore[attr-defined]
            parameters=parameters, return_annotation=str
        )
        run.__doc__ = tool.description
        run.__name__ = re.sub(r"\W+", "_", tool.name.lower())
        return {tool.name: crewai_tool(tool.name)(run)}

    return factory


def _summary(tool: PluginTool) -> str:
    text = " ".join((tool.summary or tool.description).split())
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    first = first[:117] + "..." if len(first) > 120 else first
    return first if first.endswith((".", "!", "?")) else first + "."


def check_tool(tool: object, source: str, taken: set[str], builtin: set[str]) -> PluginTool:
    """Raise :class:`PluginError` unless ``tool`` follows the tool design rules."""

    if not isinstance(tool, PluginTool):
        raise PluginError(
            f"{source}: found {type(tool).__name__}, expected a tool made with @plugin_tool."
        )
    where = f"{source}, tool '{tool.name}'"
    if not TOOL_NAME.fullmatch(tool.name):
        raise PluginError(
            f"{where}: the name must be 3-40 letters, digits, spaces or & _ . - (for example "
            "'Word Count')."
        )
    if tool.name in builtin or tool.name in taken:
        raise PluginError(
            f"{where}: that name is already used by another tool; choose a different name."
        )
    if not NAME.fullmatch(tool.group):
        raise PluginError(
            f"{where}: the group {tool.group!r} must be lowercase letters, digits and _ "
            "(a built-in group such as 'knowledge', or a name of your own)."
        )
    if not callable(tool.func):
        raise PluginError(f"{where}: the tool function is not callable.")
    words = len(tool.description.split())
    if not MIN_WORDS <= words <= MAX_WORDS:
        raise PluginError(
            f"{where}: the description (the docstring) is {words} words; write "
            f"{MIN_WORDS}-{MAX_WORDS}: when to use the tool, what each argument means, its "
            "limits, one short example."
        )
    for name, parameter in inspect.signature(tool.func).parameters.items():
        if name == "ctx":
            continue
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            raise PluginError(f"{where}: *args and **kwargs cannot be described to a model.")
        if parameter.annotation is parameter.empty:
            raise PluginError(f"{where}: parameter '{name}' needs a type hint (str, int, ...).")
    return tool


# -- discovery -------------------------------------------------------------------------------------


def discover_entry_points() -> list[importlib.metadata.EntryPoint]:
    """The installed ``engineering_team.tools`` entry points (replaced in tests)."""

    return sorted(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP), key=lambda e: e.name)


def _tools_in(obj: object) -> list[object]:
    """The tools an entry point or module offers: one, a list, or a module's ``TOOLS`` /
    module-level :class:`PluginTool` objects."""

    if isinstance(obj, PluginTool):
        return [obj]
    if inspect.ismodule(obj):
        declared = getattr(obj, "TOOLS", None)
        if declared is not None:
            return list(declared)
        return [value for value in vars(obj).values() if isinstance(value, PluginTool)]
    if isinstance(obj, Iterable) and not isinstance(obj, str | bytes):
        return list(obj)
    return [obj]


def _import_file(path: Path, label: str) -> object:
    stem = re.sub(r"\W", "_", path.stem)
    name = f"engineering_team_project_plugin_{stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PluginError(f"{label}: cannot be imported as a Python module.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # decorators such as dataclasses look the module up by name
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(name, None)
        raise PluginError(
            f"{label} failed to load ({type(exc).__name__}: {exc}). Fix it, or skip it with "
            f"plugins.disable = [{path.stem!r}]."
        ) from exc
    return module


_CACHE: dict[tuple[Any, ...], PluginSet] = {}


def clear_plugin_cache() -> None:
    _CACHE.clear()


def _project_files(base: Path) -> list[Path]:
    directory = base / PROJECT_PLUGIN_DIR
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.glob("*.py")
        if path.is_file() and not path.is_symlink() and not path.name.startswith("_")
    )


def load_plugins(settings: Settings, base: Path | None = None) -> PluginSet:
    """Discover the plugin tools for ``settings`` (project files are looked for under ``base``,
    default the current directory). Raises :class:`PluginError` for any plugin that is broken."""

    root = base or Path.cwd()
    files = _project_files(root)
    allowed_files = settings.allow_project_plugins
    disabled = frozenset(settings.plugins.disable)
    key = (
        str(root),
        settings.plugins.entry_points,
        allowed_files,
        disabled,
        tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files),
    )
    if key not in _CACHE:
        _CACHE[key] = _load(settings, root, files, disabled)
    return _CACHE[key]


def _load(settings: Settings, root: Path, files: list[Path], disabled: frozenset[str]) -> PluginSet:
    from engineering_team.tools.registry import CATALOGUE

    builtin = {spec.name for spec in CATALOGUE}
    taken: set[str] = set()
    loaded: list[LoadedTool] = []
    notes: list[str] = []
    skipped: list[str] = []

    def add(offered: Iterable[object], source: str) -> None:
        for item in offered:
            tool = check_tool(item, source, taken, builtin)
            taken.add(tool.name)
            loaded.append(LoadedTool(tool, source, _summary(tool)))

    if settings.plugins.entry_points:
        for entry in discover_entry_points():
            if entry.name in disabled:
                continue
            label = f"plugin entry point '{entry.name}' ({entry.value})"
            try:
                target = entry.load()
            except Exception as exc:
                raise PluginError(
                    f"{label} failed to load ({type(exc).__name__}: {exc}). Uninstall the "
                    f"package, or skip it with plugins.disable = [{entry.name!r}]."
                ) from exc
            add(_tools_in(target), label)
    if settings.allow_project_plugins:
        for path in files:
            if path.stem in disabled:
                continue
            relative = (PROJECT_PLUGIN_DIR / path.name).as_posix()
            add(_tools_in(_import_file(path, relative)), relative)
        if files:
            notes.append(
                f"allow_project_plugins is on: {len(files)} file(s) in {PROJECT_PLUGIN_DIR} run "
                "as code with your privileges, outside the write scope and the sandbox."
            )
    elif files:
        skipped.extend(
            f"{(PROJECT_PLUGIN_DIR / p.name).as_posix()} (set allow_project_plugins = true to "
            "load it: it runs as code)"
            for p in files
        )
    return PluginSet(tuple(loaded), tuple(notes), tuple(skipped))
