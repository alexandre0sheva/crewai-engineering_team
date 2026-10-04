"""The roster: who can work, with which prompt, tools, and model tier.

Built-in teammates are defined in ``config/agents.yaml`` (the one place their prompts live). On top
of them, in this order, come ``.engineering-team/team.yaml`` (or ``team_file``) and the
``[team.<key>]`` tables of the configuration: each changes the fields it sets, field by field,
and a key that is not built in adds a new teammate. The result is validated once, with errors that
name the teammate, the field, and where the value came from, so a mistake is a usage error at the
start of a run and never a surprise in the middle of one.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from engineering_team.extensions.plugin_loader import load_plugins
from engineering_team.intake.templates import MODES
from engineering_team.settings import TEAM_KEY, Settings, TeamOverride
from engineering_team.tools import GROUPS
from engineering_team.tools.browser_tools import available as browser_available

DEFAULT_TEAM_FILE = Path(".engineering-team") / "team.yaml"
MCP_GROUP = "mcp:docs"
VALID_GROUPS = (*GROUPS, MCP_GROUP)
# What a new teammate may use when it does not say: look, think, coordinate, never change anything.
DEFAULT_TOOL_GROUPS = (
    "fs_read", "search", "code_intel", "git_read", "knowledge", "board", "notes", "human",
)  # fmt: skip
REQUIRED_FOR_NEW = ("role", "goal", "backstory")
GENERALIST = "generalist_engineer"
WRITERS = frozenset({"fs_write", "command"})


class TeamError(ValueError):
    """A roster that cannot be built or used; the message says what to fix."""


@dataclass(frozen=True)
class Teammate:
    key: str
    role: str
    goal: str
    backstory: str
    tier: str = "worker"
    tool_groups: tuple[str, ...] = DEFAULT_TOOL_GROUPS
    allow_delegation: bool = False
    max_iter: int | None = None
    enabled: bool = True
    modes: tuple[str, ...] = ()  # empty: every mode
    stages: tuple[str, ...] = ()  # extra stages this teammate may be given by role
    origin: str = "built-in"  # built-in, then the layers that changed it; "custom (...)" if new

    @property
    def builtin(self) -> bool:
        return self.origin.startswith("built-in")

    @property
    def groups(self) -> tuple[str, ...]:
        """The tool groups (``mcp:`` entries are not tool groups)."""

        return tuple(g for g in self.tool_groups if not g.startswith("mcp:"))

    @property
    def uses_docs_mcp(self) -> bool:
        return MCP_GROUP in self.tool_groups

    def role_for(self, project_name: str) -> str:
        """The role with ``{project_name}`` filled in (CrewAI does the same for the agent)."""

        return self.role.replace("{project_name}", project_name)

    def works_in(self, mode: str | None) -> bool:
        return not self.modes or mode is None or mode in self.modes


@dataclass
class Roster:
    """The validated team. ``assign`` picks who works a stage when the one asked for cannot."""

    members: dict[str, Teammate]
    _noted: set[tuple[str, str]] = field(default_factory=set, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def all(self) -> list[Teammate]:
        return list(self.members.values())

    def get(self, key: str) -> Teammate:
        try:
            return self.members[key]
        except KeyError:
            raise TeamError(
                f"Unknown teammate '{key}'; the team has: {', '.join(self.members)}. Add one "
                "under [team.<key>] (docs/TEAM.md)."
            ) from None

    def enabled(self) -> list[Teammate]:
        return [m for m in self.members.values() if m.enabled]

    def usable(self, key: str, mode: str | None = None) -> bool:
        member = self.members.get(key)
        return member is not None and member.enabled and member.works_in(mode)

    def assign(
        self, preferred: Sequence[str], stage: str, mode: str | None = None
    ) -> tuple[str, str]:
        """Who works ``stage``: the first usable of ``preferred``, else the nearest enabled
        generalist. Returns ``(key, why)``; ``why`` is empty when the first choice works."""

        for index, key in enumerate(preferred):
            if self.usable(key, mode):
                return key, "" if index == 0 else f"{preferred[0]} cannot work it"
        wanted = preferred[0] if preferred else "?"
        reason = self._unavailable(wanted, mode)
        if self.usable(GENERALIST, mode):
            return GENERALIST, reason
        reference = set(self.members[wanted].groups) if wanted in self.members else WRITERS
        candidates = [
            m
            for m in self.enabled()
            if m.works_in(mode) and set(m.groups) >= WRITERS and m.key != wanted
        ]
        if not candidates:
            raise TeamError(
                f"No enabled teammate can work the '{stage}' stage ({reason}). Enable "
                f"'{wanted}' or '{GENERALIST}' under [team.<key>] enabled = true."
            )
        best = max(candidates, key=lambda m: (len(reference & set(m.groups)), m.key))
        return best.key, reason

    def note_fallback(self, stage: str, wanted: str, used: str) -> bool:
        """True the first time this fallback is reported (so events are not repeated)."""

        with self._lock:
            if (stage, wanted) in self._noted:
                return False
            self._noted.add((stage, wanted))
            return True

    def _unavailable(self, key: str, mode: str | None) -> str:
        member = self.members.get(key)
        if member is None:
            return f"teammate '{key}' does not exist"
        if not member.enabled:
            return f"teammate '{key}' is disabled"
        return f"teammate '{key}' does not work in the '{mode}' mode"


# -- building ----------------------------------------------------------------------------------


def _builtin_layer() -> dict[str, dict[str, Any]]:
    text = (resources.files("engineering_team") / "config" / "agents.yaml").read_text(
        encoding="utf-8"
    )
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def _read_team_file(path: Path) -> dict[str, dict[str, Any]]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TeamError(f"Cannot read the team file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise TeamError(f"The team file {path} is not valid YAML: {exc}") from exc
    if data is None:
        return {}
    if isinstance(data, dict) and set(data) == {"team"} and isinstance(data["team"], dict):
        data = data["team"]
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        raise TeamError(
            f"The team file {path} must map each teammate key to its fields, for example:\n"
            "data_engineer:\n  role: ...\n  goal: ...\n  backstory: ..."
        )
    return data


def _validated(key: str, values: Mapping[str, Any], source: str) -> dict[str, Any]:
    """The fields ``values`` sets for ``key`` (checked by ``TeamOverride``)."""

    if not TEAM_KEY.fullmatch(str(key)):
        raise TeamError(
            f"Teammate key {key!r} in {source} must be lowercase letters, digits and _ "
            "(for example 'data_engineer')."
        )
    try:
        parsed = TeamOverride.model_validate(dict(values))
    except ValidationError as exc:
        first = exc.errors()[0]
        field_name = ".".join(str(part) for part in first["loc"])
        raise TeamError(
            f"Teammate '{key}' ({source}), {field_name or 'entry'}: {first['msg']}."
        ) from exc
    return parsed.model_dump(exclude_unset=True, exclude_none=True)


def _check(
    key: str,
    fields: dict[str, Any],
    *,
    custom: bool,
    origin: str,
    valid_groups: Sequence[str] = VALID_GROUPS,
) -> Teammate:
    if custom:
        missing = [name for name in REQUIRED_FOR_NEW if not str(fields.get(name, "")).strip()]
        if missing:
            raise TeamError(
                f"Teammate '{key}' is new ({origin}), so it needs {', '.join(REQUIRED_FOR_NEW)}; "
                f"missing: {', '.join(missing)}. To change a built-in instead, check the key "
                "against `engineering-team team list`."
            )
    groups = fields.get("tool_groups", DEFAULT_TOOL_GROUPS)
    unknown = [g for g in groups if g not in valid_groups]
    if unknown:
        raise TeamError(
            f"Teammate '{key}' ({origin}) names unknown tool group(s): {', '.join(unknown)}. "
            f"Known: {', '.join(valid_groups)}. (mcp:<name> needs an [mcp.<name>] server; a "
            "plugin's group needs the plugin: docs/CONFIGURATION.md.)"
        )
    bad_modes = [m for m in fields.get("modes", []) if m not in MODES]
    if bad_modes:
        raise TeamError(
            f"Teammate '{key}' ({origin}) names unknown mode(s): {', '.join(bad_modes)}. "
            f"Known: {', '.join(MODES)}."
        )
    bad_stages = [s for s in fields.get("stages", []) if not TEAM_KEY.fullmatch(s)]
    if bad_stages:
        raise TeamError(
            f"Teammate '{key}' ({origin}) has invalid stage name(s): {', '.join(bad_stages)}; "
            "use a recipe's stage names (spec, plan, implement, ...)."
        )
    return Teammate(
        key=key,
        role=" ".join(str(fields["role"]).split()),
        goal=" ".join(str(fields["goal"]).split()),
        backstory=" ".join(str(fields["backstory"]).split()),
        tier=fields.get("tier", "worker"),
        tool_groups=tuple(dict.fromkeys(groups)),
        allow_delegation=bool(fields.get("allow_delegation", False)),
        max_iter=fields.get("max_iter"),
        enabled=bool(fields.get("enabled", True)),
        modes=tuple(fields.get("modes", ())),
        stages=tuple(fields.get("stages", ())),
        origin=origin,
    )


def _assemble(
    layers: list[tuple[str, Mapping[str, Mapping[str, Any]]]],
    valid_groups: Sequence[str] = VALID_GROUPS,
) -> Roster:
    merged: dict[str, dict[str, Any]] = {}
    touched: dict[str, list[str]] = {}
    for source, layer in layers:
        for key, values in layer.items():
            fields = _validated(key, values, source) if source != "built-in" else dict(values)
            merged.setdefault(key, {}).update(fields)
            if source != "built-in":
                touched.setdefault(key, []).append(source)
    members: dict[str, Teammate] = {}
    for key, fields in merged.items():
        builtin = key in layers[0][1]
        sources = touched.get(key, [])
        origin = (
            " + ".join(["built-in", *sources]) if builtin else f"custom ({' + '.join(sources)})"
        )
        members[key] = _check(
            key, fields, custom=not builtin, origin=origin, valid_groups=valid_groups
        )
    return Roster(members)


def builtin_roster() -> Roster:
    """Only the built-in teammates (no settings, no project files)."""

    return _assemble([("built-in", _builtin_layer())])


def valid_groups_for(settings: Settings, plugin_groups: Iterable[str] = ()) -> tuple[str, ...]:
    """Every tool group a teammate may list: the built-in ones, ``mcp:docs`` and one
    ``mcp:<name>`` per ``[mcp.<name>]`` server, and the groups plugin tools declare."""

    mcp = [f"mcp:{name}" for name in settings.mcp if f"mcp:{name}" != MCP_GROUP]
    extra = [group for group in sorted(plugin_groups) if group not in GROUPS]
    return (*VALID_GROUPS, *mcp, *extra)


def build_roster(
    settings: Settings, *, cwd: Path | None = None, plugin_groups: Iterable[str] | None = None
) -> Roster:
    """The team for ``settings``: built-ins, then the team file, then ``[team.*]``.

    ``plugin_groups`` are the groups plugin tools declare (default: discovered from ``settings``
    and ``cwd``). Raises :class:`TeamError` (a ``ValueError``, so a usage error) for anything
    invalid.
    """

    layers: list[tuple[str, Mapping[str, Mapping[str, Any]]]] = [("built-in", _builtin_layer())]
    base = cwd or Path.cwd()
    configured = settings.team_file
    path = (base / configured) if configured else base / DEFAULT_TEAM_FILE
    if path.is_file():
        layers.append((str(configured or DEFAULT_TEAM_FILE), _read_team_file(path)))
    elif configured:
        raise TeamError(f"The team file {path} (setting team_file) does not exist.")
    if settings.team:
        layers.append(
            (
                "config",
                {
                    key: override.model_dump(exclude_unset=True, exclude_none=True)
                    for key, override in settings.team.items()
                },
            )
        )
    try:
        groups = plugin_groups if plugin_groups is not None else load_plugins(settings, base).groups
    except ValueError as exc:  # PluginError: say it here, where the team is being defined
        raise TeamError(str(exc)) from exc
    roster = _assemble(layers, valid_groups_for(settings, groups))
    for name, server in settings.mcp.items():
        missing = [role for role in server.roles if role not in roster.members]
        if missing:
            raise TeamError(
                f"[mcp.{name}] roles names unknown teammate(s): {', '.join(missing)}; the team "
                f"has: {', '.join(roster.members)}."
            )
    return roster


def group_notes(member: Teammate) -> list[str]:
    """Tool groups ``member`` asks for that this machine or setup cannot provide."""

    notes: list[str] = []
    if "browser" in member.groups and not browser_available():
        notes.append(
            f"{member.key}: browser tools are not installed (pip install 'engineering_team"
            "[browser]' and run `playwright install chromium`); the teammate works without them."
        )
    return notes
