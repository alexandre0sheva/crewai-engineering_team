"""The composer's data: the stage library and rules of ``modes/compose.yaml``, checked.

A problem in the file is reported one line per problem (``rules[3].when.primary.0: ...``,
``library[12] ('fix'): requires 'repro', which is not an earlier entry``), never as a traceback,
so a rule file someone edits says exactly what to fix.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from engineering_team.contracts import Intent, Risk, TaskProfile, TaskScale
from engineering_team.pipeline.recipes import StageSpec

Cost = Literal["free", "session", "sessions"]
SCALES: tuple[str, ...] = ("trivial", "small", "medium", "large")
RISKS: tuple[str, ...] = ("low", "medium", "high")


class ComposeError(ValueError):
    """The rule file is invalid, or no recipe can be composed for a profile; says why."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LibraryEntry(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    cost: Cost
    summary: str = Field(min_length=1)
    requires: list[str] = Field(default_factory=list)
    stage: StageSpec


class Condition(_Strict):
    """When a rule applies; every condition given must hold."""

    target: Literal["new", "existing"] | None = None
    primary: list[Intent] | None = None
    primary_not: list[Intent] | None = None
    intent: list[Intent] | None = None
    secondary: list[Intent] | None = None
    only: bool | None = None
    size_at_least: TaskScale | None = None
    risk_at_least: Risk | None = None

    def holds(self, profile: TaskProfile) -> bool:
        primary, secondary = profile.primary, profile.intents[1:]
        checks = [
            self.target is None or profile.target.kind == self.target,
            self.primary is None or primary in self.primary,
            self.primary_not is None or primary not in self.primary_not,
            self.intent is None or any(i in self.intent for i in profile.intents),
            self.secondary is None or any(i in self.secondary for i in secondary),
            self.only is None or (len(profile.intents) == 1) == self.only,
            self.size_at_least is None
            or SCALES.index(profile.size) >= SCALES.index(self.size_at_least),
            self.risk_at_least is None
            or RISKS.index(profile.risk) >= RISKS.index(self.risk_at_least),
        ]
        return all(checks)


class Rule(_Strict):
    when: Condition
    add: list[str] = Field(min_length=1)
    policies: list[str] = Field(default_factory=list)
    why: str = Field(min_length=1)


class ComposeRules(_Strict):
    library: list[LibraryEntry] = Field(min_length=1)
    rules: list[Rule] = Field(min_length=1)

    def entry(self, entry_id: str) -> LibraryEntry:
        return next(e for e in self.library if e.id == entry_id)

    def position(self, entry_id: str) -> int:
        return next(i for i, e in enumerate(self.library) if e.id == entry_id)


def bundled_rules_text() -> str:
    path = resources.files("engineering_team") / "modes" / "compose.yaml"
    return path.read_text(encoding="utf-8")


def load_rules(path: Path | None = None) -> ComposeRules:
    """The rules of ``path`` (default: the bundled ``modes/compose.yaml``)."""

    if path is None:
        return parse_rules(bundled_rules_text(), source="compose.yaml")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ComposeError(f"Cannot read {path}: {exc}") from exc
    return parse_rules(text, source=str(path))


def parse_rules(text: str, *, source: str) -> ComposeRules:
    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ComposeError(f"{source} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ComposeError(f"{source} must be a mapping with `library` and `rules`.")
    try:
        rules = ComposeRules.model_validate(data)
    except ValidationError as exc:
        raise ComposeError(
            f"{source} is invalid:\n"
            + "\n".join(f"  {_where(data, e['loc'])}: {e['msg']}" for e in exc.errors())
        ) from None
    problems = _cross_check(rules)
    if problems:
        raise ComposeError(f"{source} is invalid:\n" + "\n".join(f"  {p}" for p in problems))
    return rules


def _where(data: dict[str, Any], loc: tuple[int | str, ...]) -> str:
    """``library[3] ('fix').stage.kind``: the location, with the entry's id."""

    text = ""
    for index, part in enumerate(loc):
        if isinstance(part, int):
            text += f"[{part}]"
            items = data.get(str(loc[0])) if index == 1 else None
            item = items[part] if isinstance(items, list) and part < len(items) else None
            if isinstance(item, dict) and "id" in item:
                text += f" ({item['id']!r})"
        else:
            text += ("." if text else "") + str(part)
    return text or "top level"


def _cross_check(rules: ComposeRules) -> list[str]:
    from engineering_team.modes.policies import POLICY_NAMES

    problems: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(rules.library):
        where = f"library[{index}] ({entry.id!r})"
        if entry.id in seen:
            problems.append(f"{where}: the id is used twice")
        for needed in entry.requires:
            if needed not in seen:
                problems.append(f"{where}: requires {needed!r}, which is not an earlier entry")
        seen.add(entry.id)
    for index, rule in enumerate(rules.rules):
        unknown = [entry for entry in rule.add if entry not in seen]
        if unknown:
            problems.append(f"rules[{index}]: unknown library entries {', '.join(unknown)}")
        bad = [name for name in rule.policies if name not in POLICY_NAMES]
        if bad:
            problems.append(f"rules[{index}]: unknown policies {', '.join(bad)}")
    return problems
